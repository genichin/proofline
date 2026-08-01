from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

import pytest

import proofline.home_writer as home_writer
import proofline.updater as updater
from proofline.home_writer import build_home_payload
from proofline.updater import Release, UpdateError


def resources(marker: str) -> dict[str, bytes]:
    return {
        "agent-context.md": f"agent-{marker}\n".encode(),
        "contracts/storage.md": f"contract-{marker}\n".encode(),
        "templates/schema-v1/artifacts/line.md": f"template-{marker}\n".encode(),
        "skills/proofline-start-line/SKILL.md": f"skill-{marker}\n".encode(),
    }


def write_payload(root: Path, payload: dict[str, bytes]) -> None:
    for relative, content in payload.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def make_wheel(root: Path, version: str, marker: str) -> tuple[Path, Path]:
    wheel = root / f"proofline-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("proofline_home/__init__.py", b"")
        for relative, content in resources(marker).items():
            archive.writestr(f"proofline_home/{relative}", content)
    checksum = root / f"SHA256SUMS-{version}"
    checksum.write_text(f"{hashlib.sha256(wheel.read_bytes()).hexdigest()}  {wheel.name}\n")
    return wheel, checksum


class Dist:
    def read_text(self, filename: str) -> str:
        assert filename == "direct_url.json"
        return json.dumps({"url": "https://example.invalid/proofline.whl", "archive_info": {}})


def configure_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    current: str = "0.3.0",
    target: str = "0.4.0",
    home_present: bool = True,
) -> tuple[Path, dict[str, bytes], dict[str, bytes], list[str]]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    old_payload = build_home_payload(current, resources("old"))
    new_payload = build_home_payload(target, resources("new"))
    if home_present:
        write_payload(home / ".proofline", old_payload)
    assets = tmp_path / "assets"
    assets.mkdir()
    old_wheel, old_sum = make_wheel(assets, current, "old")
    new_wheel, new_sum = make_wheel(assets, target, "new")
    releases = {
        current: Release(current, old_wheel.name, f"asset:{old_wheel}", f"asset:{old_sum}"),
        target: Release(target, new_wheel.name, f"asset:{new_wheel}", f"asset:{new_sum}"),
    }
    monkeypatch.setattr(updater.metadata, "version", lambda name: current)
    monkeypatch.setattr(updater.metadata, "distribution", lambda name: Dist())
    monkeypatch.setattr(updater, "packaged_home_payload", lambda: old_payload)
    monkeypatch.setattr(updater, "discover_release", lambda version: releases[target if version is None else version])
    monkeypatch.setattr(updater.shutil, "which", lambda name: "/fake/uv")
    tool_dir = tmp_path / "tools"
    bin_dir = tmp_path / "bin"
    (tool_dir / "proofline/bin").mkdir(parents=True)
    bin_dir.mkdir()
    monkeypatch.setattr(updater, "_uv_tool_paths", lambda uv, cwd: (tool_dir, bin_dir))
    monkeypatch.setattr(updater, "is_uv_tool_process", lambda tool_dir: True)

    def download(url: str, destination: Path) -> None:
        destination.write_bytes(Path(url.removeprefix("asset:")).read_bytes())

    monkeypatch.setattr(updater, "_download", download)
    installed = {"version": current}
    installs: list[str] = []

    def run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["/fake/uv", "tool", "install"]:
            version = Path(command[-1]).name.removeprefix("proofline-").removesuffix("-py3-none-any.whl")
            installed["version"] = version
            installs.append(version)
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[0].endswith("/python"):
            out = f"{installed['version']}\n/tmp/site-packages/proofline/__init__.py\n"
            return subprocess.CompletedProcess(command, 0, out, "")
        if command[0].endswith("/proofline"):
            return subprocess.CompletedProcess(command, 0, f"proofline {installed['version']}\n", "")
        raise AssertionError(command)

    monkeypatch.setattr(updater, "_run", run)
    return home, old_payload, new_payload, installs


def test_update_converges_package_and_existing_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, _, new_payload, installs = configure_update(tmp_path, monkeypatch)

    result = updater.run_update()

    assert result.status == "updated"
    assert installs == ["0.4.0"]
    for relative, content in new_payload.items():
        assert (home / ".proofline" / relative).read_bytes() == content
    assert not list(home.glob(".proofline-update-*"))


def test_already_current_update_creates_missing_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, old_payload, _, installs = configure_update(
        tmp_path, monkeypatch, current="0.3.0", target="0.3.0", home_present=False
    )

    result = updater.run_update()

    assert result.status == "updated"
    assert installs == []
    for relative, content in old_payload.items():
        assert (home / ".proofline" / relative).read_bytes() == content


def test_harness_conflict_fails_before_uv_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, _, _, installs = configure_update(tmp_path, monkeypatch)
    (home / ".proofline/agent-context.md").write_text("user edit\n")

    with pytest.raises(UpdateError, match="home preflight"):
        updater.run_update()

    assert installs == []
    assert (home / ".proofline/agent-context.md").read_text() == "user edit\n"


def test_home_commit_failure_rolls_back_package_and_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, old_payload, _, installs = configure_update(tmp_path, monkeypatch)
    original = home_writer.HomeUpdateTransaction.commit

    def fail_after_exchange(transaction: home_writer.HomeUpdateTransaction) -> None:
        original(transaction)
        raise home_writer.HomeInitError("injected commit failure")

    monkeypatch.setattr(home_writer.HomeUpdateTransaction, "commit", fail_after_exchange)

    with pytest.raises(UpdateError, match="injected commit failure"):
        updater.run_update()

    assert installs == ["0.4.0", "0.3.0"]
    for relative, content in old_payload.items():
        assert (home / ".proofline" / relative).read_bytes() == content


def test_finalize_cleanup_failure_keeps_target_package_and_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, _, new_payload, installs = configure_update(tmp_path, monkeypatch)

    def fail_finalize(transaction: home_writer.HomeUpdateTransaction) -> None:
        raise home_writer.HomeInitError("cleanup failed")

    monkeypatch.setattr(home_writer.HomeUpdateTransaction, "finalize", fail_finalize)
    with pytest.raises(UpdateError, match="committed but old harness cleanup failed"):
        updater.run_update()

    assert installs == ["0.4.0"]
    for relative, content in new_payload.items():
        assert (home / ".proofline" / relative).read_bytes() == content


def test_home_rollback_failure_does_not_downgrade_target_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, _, new_payload, installs = configure_update(tmp_path, monkeypatch)
    original_commit = home_writer.HomeUpdateTransaction.commit

    def fail_after_commit(transaction: home_writer.HomeUpdateTransaction) -> None:
        original_commit(transaction)
        raise home_writer.HomeInitError("commit follow-up failed")

    def fail_rollback(transaction: home_writer.HomeUpdateTransaction) -> None:
        raise home_writer.HomeInitError("rollback blocked")

    monkeypatch.setattr(home_writer.HomeUpdateTransaction, "commit", fail_after_commit)
    monkeypatch.setattr(home_writer.HomeUpdateTransaction, "rollback", fail_rollback)
    with pytest.raises(UpdateError, match="home rollback failed"):
        updater.run_update()

    assert installs == ["0.4.0"]
    for relative, content in new_payload.items():
        assert (home / ".proofline" / relative).read_bytes() == content


def test_update_check_with_missing_harness_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, _, _, installs = configure_update(tmp_path, monkeypatch, home_present=False)

    result = updater.run_update(check=True)

    assert result.status == "update-available"
    assert installs == []
    assert not (home / ".proofline").exists()
    assert not list(home.glob(".proofline-update-*"))


def test_target_install_failure_removes_stage_and_preserves_old_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, old_payload, _, installs = configure_update(tmp_path, monkeypatch)

    def fail_install(*args, **kwargs) -> None:
        raise UpdateError("install failed")

    monkeypatch.setattr(updater, "_install", fail_install)

    with pytest.raises(UpdateError, match="install failed"):
        updater.run_update()

    assert installs == []
    for relative, content in old_payload.items():
        assert (home / ".proofline" / relative).read_bytes() == content
    assert not list(home.glob(".proofline-update-*"))
