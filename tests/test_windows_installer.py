from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POSIX_INSTALLER = ROOT / "install.sh"
WINDOWS_INSTALLER = ROOT / "install.ps1"
README = ROOT / "README.md"
WINDOWS_GATE = ROOT / ".github/scripts/verify-windows-candidate.ps1"
PLAN = ROOT / ".github/resources/candidate-clean-runner-plan-v1.json"


def test_native_installers_match_home_retirement_release() -> None:
    windows = WINDOWS_INSTALLER.read_text()
    posix = POSIX_INSTALLER.read_text()
    assert '$VERSION = "0.8.0"' in windows
    assert 'VERSION="0.8.0"' in posix
    assert "genichin/proofline" in windows and "genichin/proofline" in posix


def test_windows_installer_is_checksum_first_and_fail_closed() -> None:
    text = WINDOWS_INSTALLER.read_text()
    for marker in (
        "Invoke-WebRequest",
        "Get-FileHash",
        "uv venv --no-config",
        "uv pip install --no-config --python",
        "uv tool install",
        "--no-config",
        "proofline.exe",
        "site-packages",
        "[Console]::Error.WriteLine",
    ):
        assert marker in text
    assert text.index("Get-FileHash") < text.index("uv tool install")
    assert "Start-Process -Verb RunAs" not in text
    assert "installer_transition" not in text
    assert "CorrectiveTransition" not in text


def test_readme_uses_immutable_windows_installer_file() -> None:
    text = README.read_text()
    assert (
        "https://raw.githubusercontent.com/genichin/proofline/v0.8.0/install.ps1"
        in text
    )
    assert "[System.IO.Path]::GetTempPath()" in text
    assert "& $InstallerPath -Force" in text
    assert "Remove-Item -LiteralPath $InstallerPath -Force" in text


def test_windows_candidate_gate_probes_package_resources_and_local_status() -> None:
    text = WINDOWS_GATE.read_text()
    assert "Get-FileHash" in text
    assert "status --json" in text
    assert "files('skills')" in text
    assert "proofline init" not in text
    assert "proofline_home" not in text


def test_clean_runner_plan_uses_test_dependency_language() -> None:
    plan = json.loads(PLAN.read_text())
    for platform in plan["platforms"].values():
        steps = [step["step_id"] for step in platform["steps"]]
        assert "provision-test-dependencies" in steps
        assert "provision-harness" not in steps
