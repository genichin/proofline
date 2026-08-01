from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"
README = ROOT / "README.md"


def make_fixture(tmp_path: Path) -> tuple[Path, Path]:
    assets = tmp_path / "assets"
    assets.mkdir()
    wheel = assets / "proofline-0.4.1-py3-none-any.whl"
    wheel.write_bytes(b"fixture-wheel")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    (assets / "SHA256SUMS").write_text(f"{digest}  {wheel.name}\n")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    (fake_bin / "curl").write_text(
        "#!/bin/sh\n"
        "out=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = '-o' ]; then out=$2; shift 2; else shift; fi\n"
        "done\n"
        "case \"$out\" in\n"
        "  */SHA256SUMS) cp \"$FAKE_ASSETS/SHA256SUMS\" \"$out\" ;;\n"
        "  *) cp \"$FAKE_ASSETS/proofline-0.4.1-py3-none-any.whl\" \"$out\" ;;\n"
        "esac\n"
    )
    (fake_bin / "uv").write_text(
        "#!/bin/sh\n"
        "if [ \"$1 $2\" = 'tool dir' ] && [ \"$#\" -eq 2 ]; then echo \"$FAKE_TOOL_DIR\"; exit 0; fi\n"
        "if [ \"$1 $2 $3\" = 'tool dir --bin' ]; then echo \"$FAKE_TOOL_BIN\"; exit 0; fi\n"
        "if [ \"$1 $2\" = 'tool install' ]; then\n"
        "  printf '%s\\n' \"$*\" >> \"$FAKE_UV_LOG\"\n"
        "  mkdir -p \"$FAKE_TOOL_BIN\"\n"
        "  printf '%s\\n' '#!/bin/sh' 'echo proofline 0.4.1' > \"$FAKE_TOOL_BIN/proofline\"\n"
        "  chmod +x \"$FAKE_TOOL_BIN/proofline\"\n"
        "  exit 0\n"
        "fi\n"
        "exit 2\n"
    )
    for command in ["curl", "uv"]:
        (fake_bin / command).chmod(0o755)
    return assets, fake_bin


def installer_env(tmp_path: Path, fake_bin: Path, assets: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        PATH=f"{fake_bin}:{env['PATH']}",
        FAKE_ASSETS=str(assets),
        FAKE_TOOL_BIN=str(tmp_path / "tool-bin"),
        FAKE_TOOL_DIR=str(tmp_path / "tools"),
        FAKE_UV_LOG=str(tmp_path / "uv.log"),
        TMPDIR=str(tmp_path / "tmp"),
    )
    Path(env["TMPDIR"]).mkdir()
    return env


def run_installer(tmp_path: Path, *args: str) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    assets, fake_bin = make_fixture(tmp_path)
    env = installer_env(tmp_path, fake_bin, assets)
    app = tmp_path / "application"
    app.mkdir()
    (app / "pyproject.toml").write_text("[project]\nname='app'\nversion='0.0.0'\n")
    (app / "uv.lock").write_text("version = 1\nrevision = 1\n")
    before = {p.name: p.read_bytes() for p in app.iterdir()}
    completed = subprocess.run(["sh", str(INSTALLER), *args], cwd=app, env=env, text=True, capture_output=True)
    after = {p.name: p.read_bytes() for p in app.iterdir()}
    assert after == before
    assert not (app / ".venv").exists()
    return completed, env


def test_installer_is_valid_posix_shell() -> None:
    assert subprocess.run(["sh", "-n", str(INSTALLER)], capture_output=True).returncode == 0


def test_installer_fresh_install_verifies_and_uses_uv_tool(tmp_path: Path) -> None:
    completed, env = run_installer(tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert "ProofLine 0.4.1 installed" in completed.stdout
    assert Path(env["FAKE_UV_LOG"]).read_text().strip().startswith("tool install --no-config ")
    assert not any(Path(env["TMPDIR"]).iterdir())


def test_installer_force_is_explicit_in_uv_command(tmp_path: Path) -> None:
    completed, env = run_installer(tmp_path, "--force")
    assert completed.returncode == 0, completed.stderr
    assert Path(env["FAKE_UV_LOG"]).read_text().strip().startswith("tool install --force --no-config ")


def test_installer_refuses_existing_proofline_before_download_or_uv_install(tmp_path: Path) -> None:
    assets, fake_bin = make_fixture(tmp_path)
    env = installer_env(tmp_path, fake_bin, assets)
    (Path(env["FAKE_TOOL_DIR"]) / "proofline").mkdir(parents=True)
    completed = subprocess.run(["sh", str(INSTALLER)], cwd=tmp_path, env=env, text=True, capture_output=True)
    assert completed.returncode != 0
    assert "already installed" in completed.stderr
    assert "--force" in completed.stderr
    assert not Path(env["FAKE_UV_LOG"]).exists()
    assert not any(Path(env["TMPDIR"]).iterdir())


def test_installer_wrong_checksum_never_invokes_uv(tmp_path: Path) -> None:
    assets, fake_bin = make_fixture(tmp_path)
    (assets / "SHA256SUMS").write_text("0" * 64 + "  proofline-0.4.1-py3-none-any.whl\n")
    env = installer_env(tmp_path, fake_bin, assets)
    completed = subprocess.run(["sh", str(INSTALLER)], cwd=tmp_path, env=env, text=True, capture_output=True)
    assert completed.returncode != 0
    assert not Path(env["FAKE_UV_LOG"]).exists()
    assert not any(Path(env["TMPDIR"]).iterdir())


def test_installer_rejects_unknown_option(tmp_path: Path) -> None:
    completed, env = run_installer(tmp_path, "--unknown")
    assert completed.returncode != 0
    assert "Usage:" in completed.stderr
    assert not Path(env["FAKE_UV_LOG"]).exists()


def test_installer_fails_before_download_when_uv_is_missing(tmp_path: Path) -> None:
    fake_bin = tmp_path / "limited-bin"
    fake_bin.mkdir()
    for command in ["curl", "sha256sum"]:
        target = shutil.which(command)
        assert target
        (fake_bin / command).symlink_to(target)
    env = os.environ.copy()
    env["PATH"] = str(fake_bin)
    completed = subprocess.run(["/bin/sh", str(INSTALLER)], env=env, text=True, capture_output=True)
    assert completed.returncode != 0
    assert "required command not found: uv" in completed.stderr


def test_readme_leads_with_versioned_one_line_installer_and_keeps_manual_verification() -> None:
    text = README.read_text()
    command = "curl -fsSL https://raw.githubusercontent.com/genichin/proofline/v0.4.1/install.sh | sh"
    assert command in text
    assert text.index(command) < text.index("sha256sum --check --strict SHA256SUMS")
    assert "sh -s -- --force" in text
