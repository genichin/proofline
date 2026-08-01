from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
import yaml

import proofline.home_writer as home_writer
from proofline.home_writer import HomeInitError, initialize_home


def _snapshot(root: Path) -> dict[str, tuple[str, bytes | None]]:
    if not root.exists() and not root.is_symlink():
        return {}
    result: dict[str, tuple[str, bytes | None]] = {}
    for path in [root, *sorted(root.rglob("*"))]:
        relative = "." if path == root else path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = ("symlink", os.readlink(path).encode())
        elif path.is_dir():
            result[relative] = ("dir", None)
        else:
            result[relative] = ("file", path.read_bytes())
    return result


def test_fresh_init_creates_user_home_harness_and_preserves_project(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / ".proofline").mkdir()
    (project / ".proofline/line-marker").write_text("canonical\n", encoding="utf-8")
    before = _snapshot(project)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)

    result = initialize_home()

    assert result.status == "created"
    target = home / ".proofline"
    assert (target / "manifest.yaml").is_file()
    for directory in ("contracts", "templates", "skills"):
        assert (target / directory).is_dir()
        assert any((target / directory).rglob("*"))
    assert (target / "agent-context.md").is_file()
    manifest = yaml.safe_load((target / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["proofline_version"]
    for record in manifest["managed_files"]:
        content = (target / record["path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == record["sha256"]
    assert _snapshot(project) == before


def test_dry_run_and_exact_second_run_are_zero_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    dry = initialize_home(dry_run=True)
    assert dry.status == "would-create"
    assert _snapshot(home) == {".": ("dir", None)}

    initialize_home()
    before = _snapshot(home)
    second = initialize_home()
    assert second.status == "already-initialized"
    assert _snapshot(home) == before


@pytest.mark.parametrize("kind", ["mismatch", "unexpected", "symlink"])
def test_existing_conflict_is_preserved(
    tmp_path: Path, monkeypatch, kind: str
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    target = home / ".proofline"
    if kind == "symlink":
        outside = tmp_path / "outside"
        outside.mkdir()
        target.symlink_to(outside, target_is_directory=True)
    else:
        initialize_home()
        if kind == "mismatch":
            (target / "agent-context.md").write_text("changed\n", encoding="utf-8")
        else:
            (target / "unexpected.txt").write_text("keep\n", encoding="utf-8")
    before = _snapshot(home)

    with pytest.raises(HomeInitError, match="conflict|symlink"):
        initialize_home()

    assert _snapshot(home) == before


def test_staging_failure_leaves_no_target_or_temporary_directory(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    original = Path.write_bytes
    calls = 0

    def fail_write(path: Path, content: bytes) -> int:
        nonlocal calls
        if path.parent != home:
            calls += 1
            if calls == 2:
                raise OSError("injected write failure")
        return original(path, content)

    monkeypatch.setattr(Path, "write_bytes", fail_write)
    with pytest.raises(HomeInitError, match="injected write failure"):
        initialize_home()

    assert _snapshot(home) == {".": ("dir", None)}


def test_concurrent_target_is_not_overwritten_or_removed(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    def concurrent(stage: Path, target: Path) -> None:
        target.mkdir()
        (target / "owner.txt").write_text("concurrent\n", encoding="utf-8")
        raise FileExistsError("concurrent target")

    monkeypatch.setattr(home_writer, "_commit_directory", concurrent)
    with pytest.raises(HomeInitError, match="concurrent target"):
        initialize_home()

    assert (home / ".proofline/owner.txt").read_text(encoding="utf-8") == "concurrent\n"
    assert not list(home.glob(".proofline-init-*"))
