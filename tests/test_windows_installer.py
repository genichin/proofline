from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POSIX_INSTALLER = ROOT / "install.sh"
WINDOWS_INSTALLER = ROOT / "install.ps1"
README = ROOT / "README.md"
WINDOWS_GATE = ROOT / ".github/scripts/verify-windows-candidate.ps1"
PLAN = ROOT / "skills/proofline-run-dqc/resources/candidate-clean-runner-plan-v1.json"


def _constant(text: str, name: str, *, powershell: bool) -> str:
    if powershell:
        match = re.search(rf'^\${name}\s*=\s*"([^"]+)"$', text, re.MULTILINE)
    else:
        match = re.search(rf'^{name}="([^"]+)"$', text, re.MULTILINE)
    assert match, f"missing {name}"
    return match.group(1)


def test_native_windows_installer_exists_and_matches_release_identity() -> None:
    windows = WINDOWS_INSTALLER.read_text(encoding="utf-8")
    posix = POSIX_INSTALLER.read_text(encoding="utf-8")

    assert _constant(windows, "VERSION", powershell=True) == _constant(
        posix, "VERSION", powershell=False
    )
    assert _constant(windows, "REPOSITORY", powershell=True) == _constant(
        posix, "REPOSITORY", powershell=False
    )
    assert '$WHEEL = "proofline-${VERSION}-py3-none-any.whl"' in windows
    assert '$BASE_URL = "https://github.com/${REPOSITORY}/releases/download/v${VERSION}"' in windows


def test_windows_installer_is_native_fail_closed_and_checksum_first() -> None:
    text = WINDOWS_INSTALLER.read_text(encoding="utf-8")

    for required in (
        "[System.IO.Path]::GetTempPath()",
        "[guid]::NewGuid()",
        "Invoke-WebRequest",
        "Get-FileHash",
        "Get-Command uv",
        "uv tool install",
        "--no-config",
        '@("tool", "dir", "--bin")',
        "proofline.exe",
        "importlib.metadata",
        "site-packages",
        "Remove-Item -LiteralPath $TempDir -Recurse -Force",
    ):
        assert required in text
    assert text.index("Get-FileHash") < text.index("uv tool install")
    assert "--force --no-config" in text
    assert "Start-Process -Verb RunAs" not in text
    assert "SetEnvironmentVariable" not in text
    assert "Registry" not in text
    assert "cygpath" not in text
    assert "/tmp" not in text
    assert "GITHUB_TOKEN" not in text
    assert "PROOFLINE_BASE_URL" not in text
    assert "exit 1" not in text
    assert "throw" in text
    assert "$ChecksumLines = @(Get-Content -LiteralPath $ChecksumsPath)" in text
    assert "$ChecksumLines.Count -ne 1" in text
    assert '$ChecksumLines[0] -notmatch' in text
    assert "Where-Object" not in text[text.index("$ChecksumLines") : text.index("$ActualHash")]


def test_windows_installer_errors_do_not_exit_the_calling_powershell_host() -> None:
    text = WINDOWS_INSTALLER.read_text(encoding="utf-8")

    assert "[Console]::Error.WriteLine" in text
    assert re.search(r"catch\s*\{[^}]*throw", text, re.DOTALL)
    assert not re.search(r"catch\s*\{[^}]*exit\s+1", text, re.DOTALL)


def test_readme_documents_immutable_tagged_windows_install_from_a_temporary_file() -> None:
    text = README.read_text(encoding="utf-8")
    installer_url = "https://raw.githubusercontent.com/genichin/proofline/v0.6.1/install.ps1"

    assert text.count(installer_url) == 2
    assert "Invoke-WebRequest" in text
    assert "[System.IO.Path]::GetTempPath()" in text
    assert "& $InstallerPath" in text
    assert "& $InstallerPath -Force" in text
    assert "Remove-Item -LiteralPath $InstallerPath -Force" in text
    assert ".\\install.ps1" not in text
    assert "repository root" not in text
    assert "candidate `install.ps1`" not in text
    assert "관리자 권한" not in text
    assert "Git Bash" not in text
    assert "machine PATH" not in text
    assert "registry" not in text


def test_readme_install_verification_sections_use_initialized_approved_order() -> None:
    text = README.read_text(encoding="utf-8")
    sections = (
        text[text.index("Windows 11") : text.index("### 수동 strict verification")],
        text[text.index("## 설치 확인") : text.index("## 업데이트")],
    )

    for section in sections:
        commands = [
            line.strip()
            for line in section.splitlines()
            if line.strip().startswith("proofline ")
        ]
        assert commands[:5] == [
            "proofline --version",
            "proofline init --dry-run",
            "proofline init",
            "proofline update --check",
            "proofline validate",
        ]

    assert "absent" not in sections[1].lower()


def test_windows_candidate_gate_declares_contract_only_shared_plan() -> None:
    text = WINDOWS_GATE.read_text(encoding="utf-8")
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    windows_steps = plan["platforms"]["windows-python311"]["steps"]

    assert '$CleanRunnerPlanId = "candidate-clean-runner-v1"' in text
    assert (
        '$CleanRunnerPlanResource = "skills/proofline-run-dqc/resources/'
        'candidate-clean-runner-plan-v1.json"'
    ) in text
    assert '$CleanRunnerPlanOutcome = "contract_only"' in text
    assert '$CleanRunnerEndpoint = "https://pypi.org/simple"' in text
    assert '$CleanRunnerVersionSource = "uv.lock"' in text
    assert '$CleanRunnerNetworkMode = "online-offline"' in text
    assert '$CleanRunnerPublicationPrerequisite = "none"' in text
    assert '$CleanRunnerStepOrder = @(' in text
    assert plan["plan_id"] == "candidate-clean-runner-v1"
    assert [step["step_id"] for step in windows_steps] == [
        "verify-wheel",
        "verify-checksum",
        "create-environment",
        "provision-harness",
        "contract-probe",
    ]
    assert text.index('"verify-wheel"') < text.index('"verify-checksum"')
    assert all(step["publication_prerequisite"] == "none" for step in windows_steps)
    assert "hosted_result" not in text.lower()
    assert "dqc_result" not in text.lower()
    assert "hosted_pass" not in text.lower()
