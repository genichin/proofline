from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
import yaml

import proofline.home_writer as home_writer
from proofline.home_writer import HomeInitError, initialize_home


def _set_isolated_home(monkeypatch, home: Path) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


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
    _set_isolated_home(monkeypatch, home)
    monkeypatch.chdir(project)

    result = initialize_home()

    assert result.status == "created"
    target = home / ".proofline"
    assert (target / "manifest.yaml").is_file()
    for directory in ("contracts", "operations", "templates", "skills"):
        assert (target / directory).is_dir()
        assert any((target / directory).rglob("*"))
    operations_root = Path(__file__).resolve().parents[1] / "docs/operations"
    expected_operations = {
        path.name: path.read_bytes()
        for path in sorted(operations_root.glob("*.md"))
    }
    assert expected_operations
    assert {
        path.name: path.read_bytes()
        for path in sorted((target / "operations").glob("*.md"))
    } == expected_operations
    assert "~/.proofline/operations/" in result.paths
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
    _set_isolated_home(monkeypatch, home)

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
    _set_isolated_home(monkeypatch, home)
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
    _set_isolated_home(monkeypatch, home)
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
    _set_isolated_home(monkeypatch, home)

    def concurrent(stage: Path, target: Path) -> None:
        target.mkdir()
        (target / "owner.txt").write_text("concurrent\n", encoding="utf-8")
        raise FileExistsError("concurrent target")

    monkeypatch.setattr(home_writer, "_commit_directory", concurrent)
    with pytest.raises(HomeInitError, match="concurrent target"):
        initialize_home()

    assert (home / ".proofline/owner.txt").read_text(encoding="utf-8") == "concurrent\n"
    assert not list(home.glob(".proofline-update-*"))


def test_windows_fresh_commit_uses_native_no_overwrite_without_libc(
    tmp_path: Path, monkeypatch
) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "payload.txt").write_bytes(b"managed\n")
    target = tmp_path / "target"
    monkeypatch.setattr(home_writer, "_platform_name", lambda: "win32")

    def reject_libc(*args, **kwargs):
        raise AssertionError("Windows fresh commit must not load libc")

    monkeypatch.setattr(home_writer.ctypes, "CDLL", reject_libc)

    home_writer._commit_directory(stage, target)

    assert not stage.exists()
    assert (target / "payload.txt").read_bytes() == b"managed\n"


def test_windows_fresh_commit_preserves_a_concurrent_target(
    tmp_path: Path, monkeypatch
) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "payload.txt").write_bytes(b"managed\n")
    target = tmp_path / "target"
    target.mkdir()
    actor = target / "actor.txt"
    actor.write_bytes(b"actor-owned\n")
    actor.chmod(0o640)
    before = actor.stat().st_mode, actor.stat().st_mtime_ns, actor.read_bytes()
    monkeypatch.setattr(home_writer, "_platform_name", lambda: "win32")

    with pytest.raises(OSError):
        home_writer._commit_directory(stage, target)

    assert (actor.stat().st_mode, actor.stat().st_mtime_ns, actor.read_bytes()) == before
    assert (stage / "payload.txt").read_bytes() == b"managed\n"


@pytest.mark.parametrize(
    ("failure", "concurrent"),
    [
        (PermissionError(13, "permission denied"), False),
        (OSError(18, "cross-volume rename"), False),
        (OSError(32, "sharing violation"), True),
    ],
)
def test_windows_native_commit_failures_preserve_external_state_and_clean_owned_stage(
    tmp_path: Path, monkeypatch, failure: OSError, concurrent: bool
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _set_isolated_home(monkeypatch, home)
    monkeypatch.setattr(home_writer, "_platform_name", lambda: "win32")

    def fail_rename(stage: Path, target: Path) -> None:
        if concurrent:
            target.mkdir()
            (target / "actor.txt").write_bytes(b"actor-owned\n")
        raise failure

    monkeypatch.setattr(home_writer.os, "rename", fail_rename)

    with pytest.raises(HomeInitError) as caught:
        initialize_home()

    message = str(caught.value)
    assert f"home update commit failed: {failure}" in message
    target = home / ".proofline"
    if concurrent:
        assert "cleanup failed: home update target changed before rollback" in message
        assert (target / "actor.txt").read_bytes() == b"actor-owned\n"
    else:
        assert not target.exists()
    assert not list(home.glob(".proofline-update-*"))


def test_windows_replacement_is_rejected_before_staging_or_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    old = home_writer.build_home_payload(
        "0.4.0",
        {
            "agent-context.md": b"old\n",
            "contracts/a.md": b"old\n",
            "operations/legacy-nonterminal-history-migration.md": b"old\n",
            "operations/official-wheel-release.md": b"old\n",
            "operations/proofline-tool-environment.md": b"old\n",
            "templates/a.md": b"old\n",
            "skills/proofline-a/SKILL.md": b"old\n",
        },
    )
    new = home_writer.build_home_payload(
        "0.5.0",
        {
            "agent-context.md": b"new\n",
            "contracts/a.md": b"new\n",
            "operations/legacy-nonterminal-history-migration.md": b"new\n",
            "operations/official-wheel-release.md": b"new\n",
            "operations/proofline-tool-environment.md": b"new\n",
            "templates/a.md": b"new\n",
            "skills/proofline-a/SKILL.md": b"new\n",
        },
    )
    target = home / ".proofline"
    for relative, content in old.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    before = _snapshot(home)
    monkeypatch.setattr(home_writer, "_platform_name", lambda: "win32")

    with pytest.raises(
        HomeInitError, match="home update replacement is unsupported on Windows"
    ):
        home_writer.prepare_home_update(new, current_payload=old, home=home)

    assert _snapshot(home) == before
    assert not list(home.glob(".proofline-update-*"))


def test_dry_run_preflights_windows_fresh_commit_without_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _set_isolated_home(monkeypatch, home)
    monkeypatch.setattr(home_writer, "_platform_name", lambda: "win32")
    calls: list[tuple[str, Path, Path]] = []

    def unavailable(mode: str, active_home: Path, target: Path) -> None:
        calls.append((mode, active_home, target))
        raise HomeInitError("Windows fresh commit capability unavailable")

    monkeypatch.setattr(home_writer, "_preflight_commit", unavailable)

    with pytest.raises(HomeInitError, match="capability unavailable"):
        initialize_home(dry_run=True)

    assert calls == [("create", home, home / ".proofline")]
    assert _snapshot(home) == {".": ("dir", None)}


def test_post_commit_verification_failure_removes_only_owned_fresh_target(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _set_isolated_home(monkeypatch, home)

    def fail_verification(payload, *, home=None):
        raise HomeInitError("injected post-verification failure")

    monkeypatch.setattr(home_writer, "verify_home", fail_verification)

    with pytest.raises(HomeInitError, match="injected post-verification failure"):
        initialize_home()

    assert _snapshot(home) == {".": ("dir", None)}


@pytest.mark.parametrize("actor_action", ["add", "change"])
def test_post_commit_verification_failure_preserves_mixed_fresh_target(
    tmp_path: Path, monkeypatch, actor_action: str
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _set_isolated_home(monkeypatch, home)
    actor_bytes = b"actor-owned\n"

    def fail_after_actor_mutation(payload, *, home=None):
        target = Path(home) / ".proofline"
        actor_path = (
            target / "actor.txt"
            if actor_action == "add"
            else target / "agent-context.md"
        )
        actor_path.write_bytes(actor_bytes)
        raise HomeInitError("injected post-verification failure")

    monkeypatch.setattr(home_writer, "verify_home", fail_after_actor_mutation)

    with pytest.raises(HomeInitError) as caught:
        initialize_home()

    message = str(caught.value)
    assert message.startswith("init failed: injected post-verification failure")
    assert "cleanup failed: home update rollback ownership mismatch" in message
    assert (home / ".proofline").is_dir()
    actor_path = (
        home / ".proofline/actor.txt"
        if actor_action == "add"
        else home / ".proofline/agent-context.md"
    )
    assert actor_path.read_bytes() == actor_bytes
    assert not list(home.glob(".proofline-update-*"))


def test_post_commit_failure_preserves_identical_bytes_from_actor_replacement(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _set_isolated_home(monkeypatch, home)
    actor_identity: tuple[int, int] | None = None

    def replace_with_identical_bytes_then_fail(payload, *, home=None):
        nonlocal actor_identity
        target = Path(home) / ".proofline"
        managed = target / "agent-context.md"
        replacement = target / "actor-replacement.tmp"
        replacement.write_bytes(managed.read_bytes())
        os.replace(replacement, managed)
        state = managed.stat(follow_symlinks=False)
        actor_identity = state.st_dev, state.st_ino
        raise HomeInitError("injected post-verification failure")

    monkeypatch.setattr(home_writer, "verify_home", replace_with_identical_bytes_then_fail)

    with pytest.raises(HomeInitError, match="rollback ownership mismatch"):
        initialize_home()

    managed = home / ".proofline/agent-context.md"
    state = managed.stat(follow_symlinks=False)
    assert (state.st_dev, state.st_ino) == actor_identity
    assert not list(home.glob(".proofline-update-*"))


def test_post_commit_failure_preserves_actor_metadata_change(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _set_isolated_home(monkeypatch, home)
    actor_mtime_ns: int | None = None

    def change_metadata_then_fail(payload, *, home=None):
        nonlocal actor_mtime_ns
        managed = Path(home) / ".proofline/agent-context.md"
        state = managed.stat(follow_symlinks=False)
        actor_mtime_ns = state.st_mtime_ns + 2_000_000_000
        os.utime(managed, ns=(state.st_atime_ns, actor_mtime_ns))
        raise HomeInitError("injected post-verification failure")

    monkeypatch.setattr(home_writer, "verify_home", change_metadata_then_fail)

    with pytest.raises(HomeInitError, match="rollback ownership mismatch"):
        initialize_home()

    managed = home / ".proofline/agent-context.md"
    assert managed.stat(follow_symlinks=False).st_mtime_ns == actor_mtime_ns
    assert not list(home.glob(".proofline-update-*"))
