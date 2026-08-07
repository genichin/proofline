from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

import pytest

from proofline import home_writer
from proofline import installer_transition as transition


def _wheel(path: Path, marker: bytes) -> Path:
    resources = {
        "agent-context.md": marker + b"-agent",
        "contracts/contract.md": marker + b"-contract",
        "templates/template.md": marker + b"-template",
        "skills/proofline-test/SKILL.md": marker + b"-skill",
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("proofline_home/__init__.py", b"")
        for relative, content in resources.items():
            archive.writestr(f"proofline_home/{relative}", content)
    return path


def _target_payload(marker: bytes = b"target") -> dict[str, bytes]:
    return home_writer.build_home_payload(
        "0.6.3",
        {
            "agent-context.md": marker + b"-agent",
            "contracts/contract.md": marker + b"-contract",
            "operations/official-wheel-release.md": marker + b"-release",
            "operations/proofline-tool-environment.md": marker + b"-tool",
            "templates/template.md": marker + b"-template",
            "skills/proofline-test/SKILL.md": marker + b"-skill",
        },
    )


def _write(root: Path, payload: dict[str, bytes]) -> None:
    root.mkdir()
    for relative, content in payload.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    predecessor = _wheel(tmp_path / transition.PREDECESSOR_WHEEL, b"legacy")
    target = (tmp_path / "proofline-0.6.3-py3-none-any.whl")
    target.write_bytes(b"local-exact-candidate")
    legacy = transition._legacy_payload(predecessor)
    target_payload = _target_payload()
    _write(home / ".proofline", legacy)
    monkeypatch.setattr(transition, "PREDECESSOR_WHEEL_SHA256", hashlib.sha256(predecessor.read_bytes()).hexdigest())
    monkeypatch.setattr(transition, "_target_version", lambda wheel: "0.6.3")
    monkeypatch.setattr(transition, "_tool_paths", lambda uv, cwd: (tmp_path / "python", tmp_path / "proofline"))
    monkeypatch.setattr(transition, "_verify_archive_install", lambda *args, **kwargs: None)
    monkeypatch.setattr(transition, "_verify_console", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        transition, "_rename_no_replace", lambda source, destination: source.rename(destination)
    )
    monkeypatch.setattr(transition.home_writer, "_payload", lambda: target_payload)
    return home, predecessor, target, legacy, target_payload


def test_immutable_v060_identity_constants() -> None:
    assert transition.PREDECESSOR_VERSION == "0.6.0"
    assert transition.PREDECESSOR_WHEEL == "proofline-0.6.0-py3-none-any.whl"
    assert transition.PREDECESSOR_WHEEL_SHA256 == "e17fadeb8cc6bee5eef912cf3b0af97881a128280895ed58e8625cc23ec0ab06"
    assert transition.BACKUP_NAME == ".proofline.backup-v0.6.0"


def test_transition_commits_target_home_and_deterministic_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, predecessor, target, legacy, target_payload = _fixture(tmp_path, monkeypatch)
    installs: list[str] = []
    monkeypatch.setattr(transition, "_install", lambda uv, wheel, cwd: installs.append(wheel.name))

    assert transition.run_transition(target_wheel=target, predecessor_wheel=predecessor, home=home, uv="uv") == "0.6.3"

    assert installs == [target.name]
    assert _snapshot(home / ".proofline") == _snapshot_from_payload(target_payload)
    assert _snapshot(home / transition.BACKUP_NAME) == _snapshot_from_payload(legacy)
    assert not list(home.glob(".proofline-transition-*"))


def _snapshot_from_payload(payload: dict[str, bytes]) -> dict[str, bytes]:
    return dict(sorted(payload.items()))


@pytest.mark.parametrize("conflict", ["collision", "modified", "symlink", "unexpected"])
def test_preflight_conflicts_fail_without_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conflict: str
) -> None:
    home, predecessor, target, legacy, _ = _fixture(tmp_path, monkeypatch)
    if conflict == "collision":
        (home / transition.BACKUP_NAME).mkdir()
    elif conflict == "modified":
        (home / ".proofline/agent-context.md").write_bytes(b"modified")
    elif conflict == "symlink":
        path = home / ".proofline/agent-context.md"
        path.unlink()
        path.symlink_to(home / "outside")
    else:
        (home / ".proofline/unexpected").write_bytes(b"unexpected")
    before = _snapshot(home / ".proofline")
    monkeypatch.setattr(transition, "_install", lambda *args, **kwargs: pytest.fail("install must not run"))

    with pytest.raises(transition.InstallerTransitionError):
        transition.run_transition(target_wheel=target, predecessor_wheel=predecessor, home=home, uv="uv")

    assert _snapshot(home / ".proofline") == before
    if conflict != "collision":
        assert not (home / transition.BACKUP_NAME).exists()


def test_package_failure_rolls_back_predecessor_and_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, predecessor, target, legacy, _ = _fixture(tmp_path, monkeypatch)
    installs: list[str] = []

    def install(uv: str, wheel: Path, *, cwd: Path) -> None:
        installs.append(wheel.name)
        if wheel == target:
            raise transition.InstallerTransitionError("injected package failure")

    monkeypatch.setattr(transition, "_install", install)
    with pytest.raises(transition.InstallerTransitionError, match="injected package failure"):
        transition.run_transition(target_wheel=target, predecessor_wheel=predecessor, home=home, uv="uv")

    assert installs == [target.name, predecessor.name]
    assert _snapshot(home / ".proofline") == _snapshot_from_payload(legacy)
    assert not (home / transition.BACKUP_NAME).exists()


def test_home_commit_failure_rolls_back_package_and_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, predecessor, target, legacy, _ = _fixture(tmp_path, monkeypatch)
    installs: list[str] = []
    real_rename = Path.rename
    calls = 0

    def rename(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected commit failure")
        real_rename(source, destination)

    monkeypatch.setattr(transition, "_install", lambda uv, wheel, cwd: installs.append(wheel.name))
    monkeypatch.setattr(transition, "_rename_no_replace", rename)
    with pytest.raises(transition.InstallerTransitionError, match="injected commit failure"):
        transition.run_transition(target_wheel=target, predecessor_wheel=predecessor, home=home, uv="uv")

    assert installs == [target.name, predecessor.name]
    assert _snapshot(home / ".proofline") == _snapshot_from_payload(legacy)
    assert not (home / transition.BACKUP_NAME).exists()


def test_post_verification_failure_rolls_back_both_states(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, predecessor, target, legacy, _ = _fixture(tmp_path, monkeypatch)
    installs: list[str] = []
    target_checks = 0

    def verify(python: Path, version: str, digest: str, *, cwd: Path) -> None:
        nonlocal target_checks
        if version == "0.6.3":
            target_checks += 1
            if target_checks == 2:
                raise transition.InstallerTransitionError("injected verification failure")

    monkeypatch.setattr(transition, "_install", lambda uv, wheel, cwd: installs.append(wheel.name))
    monkeypatch.setattr(transition, "_verify_archive_install", verify)
    with pytest.raises(transition.InstallerTransitionError, match="injected verification failure"):
        transition.run_transition(target_wheel=target, predecessor_wheel=predecessor, home=home, uv="uv")

    assert installs == [target.name, predecessor.name]
    assert _snapshot(home / ".proofline") == _snapshot_from_payload(legacy)
    assert not (home / transition.BACKUP_NAME).exists()


def test_home_rollback_failure_preserves_recovery_trees(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, predecessor, target, legacy, _ = _fixture(tmp_path, monkeypatch)
    real_rename = Path.rename
    calls = 0
    target_checks = 0

    def rename(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("injected rollback failure")
        real_rename(source, destination)

    def verify(python: Path, version: str, digest: str, *, cwd: Path) -> None:
        nonlocal target_checks
        if version == "0.6.3":
            target_checks += 1
            if target_checks == 2:
                raise transition.InstallerTransitionError("injected verification failure")

    monkeypatch.setattr(transition, "_install", lambda *args, **kwargs: None)
    monkeypatch.setattr(transition, "_verify_archive_install", verify)
    monkeypatch.setattr(transition, "_rename_no_replace", rename)
    with pytest.raises(transition.InstallerTransitionError, match="HOME rollback failed"):
        transition.run_transition(target_wheel=target, predecessor_wheel=predecessor, home=home, uv="uv")

    assert _snapshot(home / transition.BACKUP_NAME) == _snapshot_from_payload(legacy)
    assert _snapshot(home / ".proofline") == _snapshot_from_payload(_target_payload())
    assert not list(home.glob(".proofline-transition-*"))


@pytest.mark.parametrize("version", ["0.6.1", "0.6.2"])
def test_published_intermediate_versions_are_rejected_as_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    wheel = tmp_path / f"proofline-{version}-py3-none-any.whl"
    wheel.write_bytes(b"candidate")
    monkeypatch.setattr(transition.metadata, "version", lambda name: version)
    with pytest.raises(transition.InstallerTransitionError, match="future exact corrective"):
        transition._target_version(wheel)
