from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from proofline import transaction
from proofline.transaction import (
    RENAME_EXCHANGE,
    RENAME_NOREPLACE,
    commit_no_replace,
    read_regular,
    remove_owned_file,
    remove_owned_tree,
    snapshot_tree,
)


def test_read_regular_beneath_without_posix_dir_fd_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    target = root / "nested/artifact.md"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"canonical")
    monkeypatch.delattr(os, "O_DIRECTORY")

    snapshot = transaction.read_regular_beneath(root, "nested/artifact.md")

    assert snapshot.data == b"canonical"
    assert snapshot.identity == transaction.identity(target.stat())


def test_remove_owned_file_preserves_foreign_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = tmp_path / "owned"
    owned.write_bytes(b"owned")
    expected = read_regular(owned)
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    real = transaction._renameat2
    raced = False

    def replace_before_exchange(source_fd, source_name, target_fd, target_name, flags):
        nonlocal raced
        if not raced and flags == RENAME_EXCHANGE and target_name == "owned":
            raced = True
            owned.unlink()
            owned.write_bytes(b"foreign sentinel")
        return real(source_fd, source_name, target_fd, target_name, flags)

    monkeypatch.setattr(transaction, "_renameat2", replace_before_exchange)
    try:
        with pytest.raises(OSError):
            remove_owned_file(parent_fd, "owned", expected.identity, expected.data)
    finally:
        os.close(parent_fd)
    assert owned.read_bytes() == b"foreign sentinel"


def test_remove_owned_tree_preserves_foreign_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = tmp_path / "owned"
    owned.mkdir()
    child = owned / "child"
    child.write_bytes(b"owned")
    tree = snapshot_tree(owned)
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    real = transaction._renameat2
    raced = False

    def replace_before_exchange(source_fd, source_name, target_fd, target_name, flags):
        nonlocal raced
        if not raced and flags == RENAME_EXCHANGE and target_name == "owned":
            raced = True
            shutil.rmtree(owned)
            owned.mkdir()
            (owned / "foreign").write_bytes(b"tree sentinel")
        return real(source_fd, source_name, target_fd, target_name, flags)

    monkeypatch.setattr(transaction, "_renameat2", replace_before_exchange)
    try:
        with pytest.raises(OSError):
            remove_owned_tree(
                parent_fd, "owned", tree.identity, tree.files, tree.directories
            )
    finally:
        os.close(parent_fd)
    assert (owned / "foreign").read_bytes() == b"tree sentinel"


def test_remove_owned_tree_preserves_foreign_child_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = tmp_path / "owned"
    owned.mkdir()
    (owned / "child").write_bytes(b"owned")
    tree = snapshot_tree(owned)
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    real = transaction._renameat2
    raced = False

    def replace_child_before_exchange(source_fd, source_name, target_fd, target_name, flags):
        nonlocal raced
        if not raced and flags == RENAME_EXCHANGE and target_name == "child":
            raced = True
            quarantine = next(tmp_path.glob(".proofline-quarantine-*"))
            (quarantine / "child").unlink()
            (quarantine / "child").write_bytes(b"foreign sentinel")
        return real(source_fd, source_name, target_fd, target_name, flags)

    monkeypatch.setattr(transaction, "_renameat2", replace_child_before_exchange)
    try:
        with pytest.raises(OSError):
            remove_owned_tree(
                parent_fd, "owned", tree.identity, tree.files, tree.directories
            )
    finally:
        os.close(parent_fd)
    assert (owned / "child").read_bytes() == b"foreign sentinel"


def test_remove_owned_tree_restores_prior_children_after_later_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = tmp_path / "owned"
    owned.mkdir()
    (owned / "a").write_bytes(b"owned a")
    (owned / "b").write_bytes(b"owned b")
    tree = snapshot_tree(owned)
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    real = transaction._renameat2
    raced = False

    def replace_later_child(source_fd, source_name, target_fd, target_name, flags):
        nonlocal raced
        if not raced and flags == RENAME_EXCHANGE and target_name == "b":
            raced = True
            quarantine = next(
                path
                for path in tmp_path.glob(".proofline-quarantine-*")
                if path.is_dir()
            )
            (quarantine / "b").unlink()
            (quarantine / "b").write_bytes(b"foreign b")
        return real(source_fd, source_name, target_fd, target_name, flags)

    monkeypatch.setattr(transaction, "_renameat2", replace_later_child)
    try:
        with pytest.raises(OSError):
            remove_owned_tree(
                parent_fd, "owned", tree.identity, tree.files, tree.directories
            )
    finally:
        os.close(parent_fd)
    assert (owned / "a").read_bytes() == b"owned a"
    assert (owned / "b").read_bytes() == b"foreign b"


def test_remove_owned_tree_does_not_restore_into_foreign_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = tmp_path / "owned"
    (owned / "a").mkdir(parents=True)
    (owned / "a/x").write_bytes(b"owned x")
    (owned / "b").mkdir()
    (owned / "b/y").write_bytes(b"owned y")
    tree = snapshot_tree(owned)
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    real = transaction._renameat2
    raced = False

    def replace_directories(source_fd, source_name, target_fd, target_name, flags):
        nonlocal raced
        if not raced and flags == RENAME_EXCHANGE and target_name == "b":
            raced = True
            quarantine = next(
                path
                for path in tmp_path.glob(".proofline-quarantine-*")
                if path.is_dir() and (path / "b").exists()
            )
            (quarantine / "a").mkdir()
            (quarantine / "a/external").write_bytes(b"foreign a")
            shutil.rmtree(quarantine / "b")
            (quarantine / "b").mkdir()
            (quarantine / "b/external").write_bytes(b"foreign b")
        return real(source_fd, source_name, target_fd, target_name, flags)

    monkeypatch.setattr(transaction, "_renameat2", replace_directories)
    try:
        with pytest.raises(OSError):
            remove_owned_tree(
                parent_fd, "owned", tree.identity, tree.files, tree.directories
            )
    finally:
        os.close(parent_fd)
    assert (owned / "a/external").read_bytes() == b"foreign a"
    assert (owned / "b/external").read_bytes() == b"foreign b"
    assert not (owned / "a/x").exists()
    assert not (owned / "b/y").exists()


def test_remove_owned_tree_restore_does_not_install_into_mkdir_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = tmp_path / "owned"
    (owned / "a").mkdir(parents=True)
    (owned / "a/x").write_bytes(b"owned x")
    (owned / "b").mkdir()
    (owned / "b/y").write_bytes(b"owned y")
    tree = snapshot_tree(owned)
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    real = transaction._renameat2
    child_raced = restore_raced = False

    def race_restore_install(source_fd, source_name, target_fd, target_name, flags):
        nonlocal child_raced, restore_raced
        quarantine = next(
            (
                path
                for path in tmp_path.glob(".proofline-quarantine-*")
                if path.is_dir() and (path / "b").exists()
            ),
            None,
        )
        if (
            not child_raced
            and quarantine is not None
            and flags == RENAME_EXCHANGE
            and target_name == "b"
        ):
            child_raced = True
            shutil.rmtree(quarantine / "b")
            (quarantine / "b").mkdir()
            (quarantine / "b/external").write_bytes(b"foreign b")
        if (
            child_raced
            and not restore_raced
            and flags == RENAME_NOREPLACE
            and target_name == "a"
            and quarantine is not None
            and not (quarantine / "a").exists()
        ):
            restore_raced = True
            (quarantine / "a").mkdir()
            (quarantine / "a/external").write_bytes(b"foreign a")
        return real(source_fd, source_name, target_fd, target_name, flags)

    monkeypatch.setattr(transaction, "_renameat2", race_restore_install)
    try:
        with pytest.raises(OSError):
            remove_owned_tree(
                parent_fd, "owned", tree.identity, tree.files, tree.directories
            )
    finally:
        os.close(parent_fd)
    assert restore_raced
    assert (owned / "a/external").read_bytes() == b"foreign a"
    assert not (owned / "a/x").exists()


def test_remove_owned_tree_rollback_preserves_replaced_root_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = tmp_path / "owned"
    owned.mkdir()
    (owned / "child").write_bytes(b"owned")
    tree = snapshot_tree(owned)
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    real = transaction._renameat2
    external_fd: int | None = None

    def replace_before_rollback(source_fd, source_name, target_fd, target_name, flags):
        nonlocal external_fd
        if flags == RENAME_EXCHANGE and target_name == "child":
            quarantine = next(
                path
                for path in tmp_path.glob(".proofline-quarantine-*")
                if path.is_dir() and (path / "child").exists()
            )
            (quarantine / "child").unlink()
            (quarantine / "child").write_bytes(b"foreign")
        if (
            external_fd is None
            and flags == RENAME_NOREPLACE
            and source_name == "owned"
        ):
            owned.rmdir()
            owned.mkdir()
            external_fd = os.open(owned, os.O_RDONLY | os.O_DIRECTORY)
        return real(source_fd, source_name, target_fd, target_name, flags)

    monkeypatch.setattr(transaction, "_renameat2", replace_before_rollback)
    try:
        with pytest.raises(OSError):
            remove_owned_tree(
                parent_fd, "owned", tree.identity, tree.files, tree.directories
            )
        assert external_fd is not None
        external_state = os.fstat(external_fd)
        current = owned.stat(follow_symlinks=False)
        assert (external_state.st_dev, external_state.st_ino) == (
            current.st_dev,
            current.st_ino,
        )
        assert external_state.st_nlink > 0
    finally:
        if external_fd is not None:
            os.close(external_fd)
        os.close(parent_fd)


def test_commit_no_replace_never_accepts_swapped_stage_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "stage"
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    source.write_bytes(b"expected")
    expected = read_regular(source)
    target_fd = os.open(target_dir, os.O_RDONLY | os.O_DIRECTORY)
    real = transaction._renameat2
    raced = False

    def swap_source(source_fd, source_name, target_fd, target_name, flags):
        nonlocal raced
        if not raced and flags == RENAME_NOREPLACE and target_name == "canonical":
            raced = True
            source.unlink()
            source.write_bytes(b"foreign payload")
        return real(source_fd, source_name, target_fd, target_name, flags)

    monkeypatch.setattr(transaction, "_renameat2", swap_source)
    try:
        with pytest.raises(OSError):
            commit_no_replace(source, target_fd, "canonical", expected)
    finally:
        os.close(target_fd)
    assert not (target_dir / "canonical").exists()
    quarantined = list(target_dir.glob(".proofline-quarantine-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"foreign payload"


def test_tree_commit_never_accepts_swapped_stage_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "stage-tree"
    source.mkdir()
    (source / "expected").write_bytes(b"expected")
    expected = snapshot_tree(source)
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target_fd = os.open(target_dir, os.O_RDONLY | os.O_DIRECTORY)
    real = transaction._renameat2
    raced = False

    def swap_source(source_fd, source_name, target_fd, target_name, flags):
        nonlocal raced
        if not raced and flags == RENAME_NOREPLACE and target_name == "canonical":
            raced = True
            shutil.rmtree(source)
            source.mkdir()
            (source / "foreign").write_bytes(b"tree payload")
        return real(source_fd, source_name, target_fd, target_name, flags)

    monkeypatch.setattr(transaction, "_renameat2", swap_source)
    try:
        with pytest.raises(OSError):
            commit_no_replace(source, target_fd, "canonical", expected)
    finally:
        os.close(target_fd)
    assert not (target_dir / "canonical").exists()
    quarantined = list(target_dir.glob(".proofline-quarantine-*"))
    assert len(quarantined) == 1
    assert (quarantined[0] / "foreign").read_bytes() == b"tree payload"
