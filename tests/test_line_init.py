from __future__ import annotations

import os
import subprocess
import sys
import shutil
from pathlib import Path

import pytest

fcntl = pytest.importorskip("fcntl")

from proofline import line_writer
from proofline.identity_allocator import IdentityAllocator, decode_allocator, encode_allocator
from proofline.line_writer import LineInitError, initialize_line
from proofline.project_writer import initialize_project
from proofline.validator import validate_project
from proofline.validator import ValidationError

ROOT = Path(__file__).resolve().parents[1]


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def make_project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    initialize_project(root)
    return root


def snapshot(root: Path) -> dict[str, bytes | str]:
    result = {}
    for path in root.rglob("*"):
        if ".git" in path.relative_to(root).parts:
            continue
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = os.readlink(path)
        elif path.is_file():
            result[relative] = path.read_bytes()
    return result


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-c", "from proofline.cli import main; raise SystemExit(main())", *args],
        cwd=root, env=env, text=True, capture_output=True, check=False,
    )


def test_line_init_auto_allocates_consecutive_ids_and_valid_artifacts(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    first = initialize_line(root, "첫 Line")
    second = initialize_line(root, "Second")
    assert first.line_id == "line-0001"
    assert second.line_id == "line-0002"
    assert (root / first.paths[0]).read_text().startswith('---\nid: "line-0001"')
    assert "# 첫 Line" in (root / first.paths[1]).read_text()
    assert decode_allocator((root / ".proofline/identities.json").read_bytes()) == IdentityAllocator(3, 1)
    assert validate_project(root) == []


def test_line_init_dry_run_uses_same_plan_under_lock_without_mutation(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    before = snapshot(root)
    dry = initialize_line(root, "Planned", dry_run=True)
    assert dry.line_id == "line-0001"
    assert snapshot(root) == before
    actual = initialize_line(root, "Planned")
    assert actual.line_id == dry.line_id and actual.paths == dry.paths


@pytest.mark.parametrize("checkout", ["topic", "detached"])
def test_line_init_is_branch_independent(tmp_path: Path, checkout: str) -> None:
    root = make_project(tmp_path)
    git(root, "config", "user.email", "proofline@example.invalid")
    git(root, "config", "user.name", "ProofLine Test")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "baseline")
    if checkout == "topic":
        git(root, "switch", "-qc", "topic")
    else:
        git(root, "checkout", "--detach", "-q")
    assert initialize_line(root, checkout).line_id == "line-0001"


def test_old_positional_cli_is_argparse_usage_error(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    result = run(root, "line", "init", "line-0001", "--title", "old")
    assert result.returncode == 2
    assert "unrecognized arguments: line-0001" in result.stderr
    assert not (root / ".proofline/lines/line-0001").exists()


@pytest.mark.parametrize("title", ["", "   ", "bad\nvalue", "bad\x00value"])
def test_line_init_rejects_invalid_title_without_mutation(tmp_path: Path, title: str) -> None:
    root = make_project(tmp_path)
    before = snapshot(root)
    with pytest.raises(LineInitError, match="line.title"):
        initialize_line(root, title)
    assert snapshot(root) == before


def test_line_init_rejects_exhausted_allocator(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    allocator = root / ".proofline/identities.json"
    allocator.write_bytes(encode_allocator(IdentityAllocator(10000, 1)))
    with pytest.raises(LineInitError, match="allocator.line.exhausted"):
        initialize_line(root, "No ID")


def test_line_init_allocates_9999_then_writes_exhausted_sentinel(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    allocator = root / ".proofline/identities.json"
    allocator.write_bytes(encode_allocator(IdentityAllocator(9999, 1)))
    result = initialize_line(root, "Last Line")
    assert result.line_id == "line-9999"
    assert decode_allocator(allocator.read_bytes()) == IdentityAllocator(10000, 1)
    assert validate_project(root) == []


def test_line_init_rejects_symlink_allocator_without_touching_target(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    allocator = root / ".proofline/identities.json"
    outside = tmp_path / "outside"
    outside.write_bytes(allocator.read_bytes())
    allocator.unlink()
    allocator.symlink_to(outside)
    with pytest.raises(LineInitError, match="allocator.symlink"):
        initialize_line(root, "Unsafe")
    assert outside.read_bytes() == encode_allocator(IdentityAllocator(1, 1))


def test_line_init_rolls_back_line_when_allocator_commit_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_project(tmp_path)
    before = snapshot(root)
    monkeypatch.setattr(line_writer, "_replace_allocator", lambda *args: (_ for _ in ()).throw(LineInitError("injected", ".proofline/identities.json", "failure")))
    with pytest.raises(LineInitError, match="injected"):
        initialize_line(root, "Rollback")
    assert snapshot(root) == before


def test_line_commit_oserror_is_stable_and_non_mutating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project(tmp_path)
    before = snapshot(root)
    monkeypatch.setattr(line_writer, "_commit_path_at", lambda *args: (_ for _ in ()).throw(OSError("injected")))
    with pytest.raises(LineInitError, match="line.transaction.failed"):
        initialize_line(root, "Commit fault")
    assert snapshot(root) == before
    assert not list(root.glob(".line-0001-*"))


def test_line_stage_second_write_fault_cleans_exact_owned_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project(tmp_path)
    before = snapshot(root)
    real = Path.write_text

    def fail_discovery(path: Path, *args, **kwargs):
        if path.name == "dcy-0001.md" and path.parent.name.startswith(".line-0001-"):
            raise OSError("injected")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_discovery)
    with pytest.raises(LineInitError, match="line.transaction.failed"):
        initialize_line(root, "Stage fault")
    assert snapshot(root) == before
    assert not list(root.glob(".line-0001-*"))


def test_line_init_collision_from_regressed_counter_is_rejected_by_validator(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    initialize_line(root, "First")
    allocator = root / ".proofline/identities.json"
    allocator.write_bytes(encode_allocator(IdentityAllocator(1, 1)))
    with pytest.raises(LineInitError, match="allocator.line.regressed"):
        initialize_line(root, "Reuse")


def test_line_rollback_preserves_replaced_target_and_advanced_allocator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project(tmp_path)
    target = root / ".proofline/lines/line-0001"
    calls = 0
    real_validate = line_writer.validate_project

    def replace_on_post_validation(path: Path):
        nonlocal calls
        calls += 1
        if calls == 3:
            shutil.rmtree(target)
            target.mkdir()
            (target / "external").write_bytes(b"sentinel")
            return [ValidationError(".", "injected", "failure")]
        return real_validate(path)

    monkeypatch.setattr(line_writer, "validate_project", replace_on_post_validation)
    with pytest.raises(LineInitError, match="secondary: line.rollback.failed"):
        initialize_line(root, "Ownership race")
    assert (target / "external").read_bytes() == b"sentinel"
    assert decode_allocator((root / ".proofline/identities.json").read_bytes()) == IdentityAllocator(2, 1)


def test_line_rollback_preserves_externally_replaced_allocator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project(tmp_path)
    allocator = root / ".proofline/identities.json"
    calls = 0
    real_validate = line_writer.validate_project

    def replace_allocator_on_post(path: Path):
        nonlocal calls
        calls += 1
        if calls == 3:
            data = allocator.read_bytes()
            allocator.unlink()
            allocator.write_bytes(data)
            return [ValidationError(".", "injected", "failure")]
        return real_validate(path)

    monkeypatch.setattr(line_writer, "validate_project", replace_allocator_on_post)
    with pytest.raises(LineInitError, match="allocator.rollback.failed"):
        initialize_line(root, "Allocator race")
    assert not (root / ".proofline/lines/line-0001").exists()
    assert decode_allocator(allocator.read_bytes()) == IdentityAllocator(2, 1)


def test_allocator_exchange_restores_foreign_swap_and_rolls_back_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project(tmp_path)
    allocator = root / ".proofline/identities.json"
    real = line_writer.exchange_owned_file
    raced = False

    def swap_before_exchange(parent_fd, canonical, candidate, expected, replacement):
        nonlocal raced
        if not raced:
            raced = True
            allocator.unlink()
            allocator.write_bytes(b"foreign allocator sentinel")
        return real(parent_fd, canonical, candidate, expected, replacement)

    monkeypatch.setattr(line_writer, "exchange_owned_file", swap_before_exchange)
    with pytest.raises(LineInitError, match="line.transaction.failed"):
        initialize_line(root, "Allocator exchange race")
    assert allocator.read_bytes() == b"foreign allocator sentinel"
    assert not (root / ".proofline/lines/line-0001").exists()


def test_concurrent_line_processes_allocate_unique_ids(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    command = [
        sys.executable,
        "-c",
        "from pathlib import Path; from proofline.line_writer import initialize_line; "
        "print(initialize_line(Path.cwd(), __import__('sys').argv[1]).line_id)",
    ]
    processes = [
        subprocess.Popen(command + [title], cwd=root, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for title in ("Concurrent A", "Concurrent B")
    ]
    results = [process.communicate(timeout=30) for process in processes]
    assert [process.returncode for process in processes] == [0, 0], results
    assert {stdout.strip() for stdout, _ in results} == {"line-0001", "line-0002"}
    assert decode_allocator((root / ".proofline/identities.json").read_bytes()) == IdentityAllocator(3, 1)
    assert validate_project(root) == []
