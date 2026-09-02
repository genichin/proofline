from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"
README = ROOT / "README.md"


def test_installer_is_valid_posix_shell() -> None:
    assert (
        subprocess.run(
            ["sh", "-n", str(INSTALLER)], capture_output=True, check=False
        ).returncode
        == 0
    )


def test_current_installer_is_exact_package_only_transition() -> None:
    text = INSTALLER.read_text()
    assert 'VERSION="0.10.0"' in text
    assert "uv venv --no-config" in text
    assert "uv pip install --no-config --python" in text
    assert "uv tool install --force --no-config" in text
    assert "sha256sum --check --strict SHA256SUMS" in text
    assert "site-packages" in text
    for retired in (
        "installer_transition",
        "home_writer",
        "proofline init",
        ".proofline.backup",
    ):
        assert retired not in text


def test_installer_refuses_implicit_existing_tool_replacement() -> None:
    text = INSTALLER.read_text()
    assert "ProofLine is already installed" in text
    assert "rerun with --force" in text
    assert text.index("already installed") < text.index("curl -fsSL")


def test_installer_checksum_and_stage_precede_mutation() -> None:
    text = INSTALLER.read_text()
    checksum = text.index("sha256sum --check --strict SHA256SUMS")
    stage = text.index("uv venv --no-config")
    mutation = text.index("uv tool install --force --no-config")
    assert checksum < stage < mutation


def test_readme_documents_exact_v091_to_v0100_transition() -> None:
    text = README.read_text()
    command = "curl -fsSL https://raw.githubusercontent.com/genichin/proofline/v0.10.0/install.sh | sh -s -- --force"
    assert command in text
    assert "v0.9.1" in text
    assert "status: discovery" in text
    assert "activity-log.md" in text
    assert "package만 교체" in text
    assert "과거 `~/.proofline/`" in text
