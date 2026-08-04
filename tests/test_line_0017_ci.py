from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/line-0017-candidate.yml"
WINDOWS_GATE = ROOT / ".github/scripts/verify-windows-candidate.ps1"
HOME_INIT_TESTS = ROOT / "tests/test_home_init.py"


def test_line_0017_workflow_has_exact_required_os_and_python_jobs() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    assert set(jobs) == {"build-candidate", "ubuntu-python311", "windows-python311"}
    assert jobs["ubuntu-python311"]["runs-on"] == "ubuntu-latest"
    assert jobs["windows-python311"]["runs-on"] == "windows-latest"
    assert jobs["ubuntu-python311"]["needs"] == "build-candidate"
    assert jobs["windows-python311"]["needs"] == "build-candidate"
    windows_gate_steps = [
        step
        for step in jobs["windows-python311"]["steps"]
        if step.get("name") == "Run tracked Windows candidate gate"
    ]
    assert len(windows_gate_steps) == 1
    assert windows_gate_steps[0]["shell"] == "powershell"

    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("python-version: '3.11'") == 3
    assert "3.12" not in text
    assert "upload-artifact" in text
    assert text.count("download-artifact") == 2
    assert ".github/scripts/verify-windows-candidate.ps1" in text
    assert "PYTHONPATH=src uv run proofline validate" in text
    assert "python -m compileall -q src tests" in text
    assert "uv run pytest -q" in text
    assert "tests/test_home_init.py tests/test_home_update.py" in text
    assert "shell: pwsh" not in text
    for forbidden in ("release", "publish", "gh ", "secrets.", "pull_request_target"):
        assert forbidden not in text.lower()


def test_windows_gate_exercises_exact_wheel_and_full_fresh_install_sequence() -> None:
    text = WINDOWS_GATE.read_text(encoding="utf-8")
    required = (
        "[Parameter(Mandatory = $true)]",
        "$WheelPath",
        "$ChecksumPath",
        "Get-FileHash",
        "UV_TOOL_DIR",
        "UV_TOOL_BIN_DIR",
        "USERPROFILE",
        "uv tool install --no-config",
        "importlib.metadata",
        "site-packages",
        "init --dry-run",
        "$ProofLine init | Out-String",
        "already-initialized",
        "managed_files",
        "SHA256",
        "update --check",
        "already-current",
        "validate",
        "pyproject.toml",
        "uv.lock",
        ".venv",
        "git status --porcelain=v1",
        "install.ps1",
        "SHA256SUMS",
        "wrong checksum",
        "-Force",
        "Get-Process -Id $PID",
        "Start-Job",
        "proofline-install-*",
        "unknown option",
        "missing uv prerequisite",
        "download failure",
        "malformed checksum",
        "missing checksum",
        "duplicate checksum",
        "wrong filename",
        "valid checksum plus malformed extra",
        "valid checksum plus unexpected extra",
        "uv install failure",
        "post-verification failure",
        "tests/test_home_init.py",
    )
    for item in required:
        assert item in text

    assert text.index("init --dry-run") < text.index("$ProofLine init | Out-String")
    assert text.index("$ProofLine init | Out-String") < text.index("update --check")
    assert "Start-Process -Verb RunAs" not in text
    assert "Start-Process -ArgumentList" not in text
    assert 'FilePath "pwsh"' not in text
    assert 'SetEnvironmentVariable($Name, $OriginalEnvironment[$Name], "Machine")' not in text
    assert "secrets" not in text.lower()
    assert "git push" not in text.lower()
    assert "gh release" not in text.lower()
    assert "Start-Sleep -Milliseconds 500" not in text


def test_windows_workflow_supplies_artifact_checksum_to_tracked_gate() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "$checksum =" in text
    assert "-ChecksumPath $checksum" in text


def test_workflow_and_windows_gate_do_not_change_release_or_governance_identity() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    gate = WINDOWS_GATE.read_text(encoding="utf-8")
    assert "pyproject.toml" not in workflow.replace(
        "uv run pytest -q", ""
    )
    assert ".proofline/lines" not in workflow
    assert "git tag" not in gate.lower()
    assert "git push" not in gate.lower()


def test_native_windows_home_tests_bind_both_home_environment_contracts() -> None:
    text = HOME_INIT_TESTS.read_text(encoding="utf-8")
    assert "def _set_isolated_home" in text
    assert 'monkeypatch.setenv("HOME", str(home))' in text
    assert 'monkeypatch.setenv("USERPROFILE", str(home))' in text
    assert text.count("_set_isolated_home(monkeypatch, home)") >= 9


def test_windows_gate_snapshots_application_across_installed_candidate_sequence() -> None:
    text = WINDOWS_GATE.read_text(encoding="utf-8")
    before = "$ApplicationBeforeCandidateSequence = Get-ApplicationSnapshot $Application"
    after = "installed candidate sequence changed application"
    assert before in text
    assert after in text
    assert text.index(before) < text.index("init --dry-run")
    assert text.index("$ProofLine validate | Out-String") < text.index(after)
