from __future__ import annotations

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/candidate-verification.yml"
WINDOWS_GATE = ROOT / ".github/scripts/verify-windows-candidate.ps1"
HOME_INIT_TESTS = ROOT / "tests/test_home_init.py"
WINDOWS_FIXTURE = ROOT / "tests/fixtures/valid-minimal"
IMPLEMENTATION_HISTORY_TESTS = ROOT / "tests/test_implementation_history.py"
WINDOWS_HISTORY_TESTS = ROOT / "tests/test_windows_history_runtime.py"
LINE_INIT_TESTS = ROOT / "tests/test_line_init.py"
JOBS = {"build-candidate", "ubuntu-python311", "windows-python311"}


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_candidate_workflow_has_only_candidate_branch_push_trigger() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"(?m)^on:\n  push:\n    branches:\n      - ['\"]?candidate/\*\*", text)
    for forbidden in ("pull_request:", "workflow_dispatch:", "schedule:"):
        assert forbidden not in text
    assert "Line 0017" not in text


def test_candidate_workflow_has_exact_required_jobs_and_hardened_checkout_first() -> None:
    workflow = _workflow()
    jobs = workflow["jobs"]
    assert set(jobs) == JOBS
    assert jobs["ubuntu-python311"]["runs-on"] == "ubuntu-latest"
    assert jobs["windows-python311"]["runs-on"] == "windows-latest"
    assert jobs["windows-python311"]["env"] == {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.autocrlf",
        "GIT_CONFIG_VALUE_0": "false",
    }
    assert jobs["ubuntu-python311"]["needs"] == "build-candidate"
    assert jobs["windows-python311"]["needs"] == "build-candidate"
    for key in JOBS:
        steps = jobs[key]["steps"]
        assert steps[0]["uses"] == "actions/checkout@v4"
        assert steps[0]["with"] == {
            "ref": "${{ github.sha }}",
            "fetch-depth": 0,
            "persist-credentials": False,
        }
        assert steps[1]["name"] == "Assert exact candidate HEAD"
        assert "github.sha" in steps[1]["run"]
        assert "rev-parse HEAD" in steps[1]["run"]


def test_builds_one_exact_attempt_qualified_artifact_with_strict_metadata() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    artifact = "proofline-candidate-${{ github.run_id }}-${{ github.run_attempt }}"
    assert text.count("uv build --wheel") == 1
    assert text.count("actions/upload-artifact@v4") == 1
    assert text.count("actions/download-artifact@v4") == 2
    assert text.count(artifact) == 3
    assert "CANDIDATE_PROVENANCE.json" in text
    assert "SHA256SUMS" in text
    for field in (
        "schema_version",
        "candidate_sha",
        "run_id",
        "run_attempt",
        "workflow_path",
        "artifact_name",
        "wheel_filename",
        "wheel_sha256",
    ):
        assert field in text
    assert "expected exactly one candidate wheel" in text


def test_ubuntu_and_windows_independently_verify_same_wheel_and_required_regressions() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    workflow = _workflow()
    assert "sha256sum --check --strict SHA256SUMS" in text
    assert "Get-FileHash" in text
    assert ".github/scripts/verify-windows-candidate.ps1" in text
    assert "PYTHONPATH=src uv run proofline validate" in text
    assert '"$PROOFLINE_INSTALLED_EXECUTABLE" validate' in text
    assert "uv run pytest -q" in text
    assert text.count("uv build --wheel") == 1
    assert "PROOFLINE_HOSTED_CANDIDATE_WHEEL" in text
    assert "uv run pytest -q -m candidate_build_only" in text
    assert text.count('uv run pytest -q -m "not candidate_build_only"') == 1
    assert "tests/test_windows_history_runtime.py" in text
    assert "tests/test_start_implementation_windows_runtime.py" in text
    assert "tests/test_implementation_history.py" in text
    windows_runs = "\n".join(
        step.get("run", "") for step in workflow["jobs"]["windows-python311"]["steps"]
    )
    assert 'uv run pytest -q -m "not candidate_build_only"' not in windows_runs
    assert (
        "uv run pytest -q tests/test_windows_history_runtime.py "
        "tests/test_start_implementation_windows_runtime.py "
        "tests/test_implementation_history.py"
    ) in windows_runs
    assert "PROOFLINE_INSTALLED_EXECUTABLE" in text
    assert text.count("CANDIDATE_PROVENANCE.json") >= 3
    assert 'cd "$GITHUB_WORKSPACE"' in text
    assert 'uv pip install --python "$env:RUNNER_TEMP\\proofline-wheel-env\\Scripts\\python.exe"' in text
    assert '$env:PROOFLINE_INSTALLED_EXECUTABLE = "$env:RUNNER_TEMP\\proofline-wheel-env\\Scripts\\proofline.exe"' in text
    assert "python -m compileall -q src tests" in text
    assert text.count("python-version: '3.11'") == 3
    assert "3.12" not in text
    for forbidden in ("release", "publish", "secrets.", "pull_request_target", "git push"):
        assert forbidden not in text.lower()


def test_windows_gate_exercises_exact_wheel_and_full_fresh_install_sequence() -> None:
    text = WINDOWS_GATE.read_text(encoding="utf-8")
    for item in (
        "$WheelPath", "$ChecksumPath", "Get-FileHash", "UV_TOOL_DIR",
        "USERPROFILE", "uv tool install --no-config", "site-packages",
        "init --dry-run", "already-initialized", "update --check",
        "already-current", "validate", "wrong checksum", "malformed checksum",
        "duplicate checksum", "wrong filename", "valid checksum plus malformed extra",
        "valid checksum plus unexpected extra", "post-verification failure",
    ):
        assert item in text
    assert "function Invoke-NativeCapture(" in text
    assert "$ChecksumLines = @(Get-Content -LiteralPath $ChecksumPath)" in text
    checksum_block = text[text.index("$ChecksumLines") : text.index("Write-Output \"candidate-wheel")]
    assert "Where-Object" not in checksum_block
    assert "Line 0017" not in text
    assert "line-0017" not in text
    assert "Start-Process -Verb RunAs" not in text
    assert "git push" not in text.lower()


def test_windows_gate_fixture_is_persisted_as_valid_terminal_history(tmp_path: Path) -> None:
    fixture_line = WINDOWS_FIXTURE / ".proofline/lines/line-0001/line-0001.md"
    assert "execution_status: verifying" in fixture_line.read_text(encoding="utf-8")
    gate = WINDOWS_GATE.read_text(encoding="utf-8")
    assert '$FixtureLineText.Replace("execution_status: verifying", "execution_status: delivered")' in gate
    disable_normalization = '& $GitPath -C $Application config core.autocrlf false'
    assert disable_normalization in gate
    assert gate.index(disable_normalization) < gate.index('& $GitPath -C $Application add -A')
    project = tmp_path / "windows-gate-fixture"
    shutil.copytree(WINDOWS_FIXTURE, project)
    copied_line = project / ".proofline/lines/line-0001/line-0001.md"
    copied_line.write_text(
        copied_line.read_text(encoding="utf-8").replace(
            "execution_status: verifying", "execution_status: delivered"
        ),
        encoding="utf-8",
    )
    subprocess.run(("git", "init", "-q", "-b", "main"), cwd=project, check=True)
    subprocess.run(("git", "config", "user.name", "ProofLine Gate"), cwd=project, check=True)
    subprocess.run(("git", "config", "user.email", "proofline@example.invalid"), cwd=project, check=True)
    subprocess.run(("git", "config", "core.autocrlf", "false"), cwd=project, check=True)
    subprocess.run(("git", "add", "-A"), cwd=project, check=True)
    subprocess.run(("git", "commit", "-qm", "fixture baseline"), cwd=project, check=True)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    validated = subprocess.run(
        (sys.executable, "-c", "from proofline.cli import main; raise SystemExit(main())", "validate"),
        cwd=project, env=environment, text=True, capture_output=True, check=False,
    )
    assert validated.returncode == 0, validated.stderr


def test_workflow_and_gate_preserve_governance_and_home_boundaries() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    gate = WINDOWS_GATE.read_text(encoding="utf-8")
    assert ".proofline/lines" not in workflow
    assert "git tag" not in gate.lower()
    home = HOME_INIT_TESTS.read_text(encoding="utf-8")
    assert 'monkeypatch.setenv("HOME", str(home))' in home
    assert 'monkeypatch.setenv("USERPROFILE", str(home))' in home


def test_windows_consumer_history_harness_is_platform_neutral() -> None:
    implementation = IMPLEMENTATION_HISTORY_TESTS.read_text(encoding="utf-8")
    windows_runtime = WINDOWS_HISTORY_TESTS.read_text(encoding="utf-8")
    line_init = LINE_INIT_TESTS.read_text(encoding="utf-8")
    assert 'git(path, "config", "core.autocrlf", "false")' in implementation
    assert 'if extra_env is None and os.name != "nt":' in implementation
    assert "path.chmod(path.stat().st_mode | stat.S_IWRITE)" in implementation
    assert "shutil.rmtree(git_dir, onerror=remove_readonly)" in implementation
    assert "sys.stdout.buffer.write" in windows_runtime
    assert "print('ok')" not in windows_runtime
    assert 'fcntl = pytest.importorskip("fcntl")' in line_init
