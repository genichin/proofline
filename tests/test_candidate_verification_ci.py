from __future__ import annotations

import ast
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/candidate-verification.yml"
WINDOWS_GATE = ROOT / ".github/scripts/verify-windows-candidate.ps1"
HOME_INIT_TESTS = ROOT / "tests/test_home_init.py"
WINDOWS_FIXTURE = ROOT / "tests/fixtures/valid-minimal"
LINE_INIT_TESTS = ROOT / "tests/test_line_init.py"
PLAN = ROOT / ".github/resources/candidate-clean-runner-plan-v1.json"
README = ROOT / "README.md"
STORAGE_CONTRACT = ROOT / "docs/contracts/storage-and-retention.md"
ARTIFACT_LAYOUT = ROOT / "docs/artifact-layout.md"
AGENT_CONTEXT = ROOT / "src/proofline_home/agent-context.md"
HOSTED_WHEEL_CONSUMERS = {
    ROOT / "tests/test_criteria_validation.py": (
        "test_installed_wheel_cli_accepts_committed_update_draft_lifecycle",
    ),
    ROOT / "tests/test_wheel_package.py": (
        "test_built_wheel_contains_and_reads_canonical_schema_templates",
        "test_built_wheel_operations_match_source_inventory_and_payload_bytes",
    ),
}
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
    assert "tests/test_line_init.py" in text
    assert "tests/test_artifact_validation.py" in text
    assert "tests/test_cli.py" in text
    windows_runs = "\n".join(
        step.get("run", "") for step in workflow["jobs"]["windows-python311"]["steps"]
    )
    assert 'uv run pytest -q -m "not candidate_build_only"' not in windows_runs
    assert (
        "uv run pytest -q tests/test_line_init.py "
        "tests/test_artifact_validation.py tests/test_cli.py"
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


def test_operations_bearing_home_topology_and_update_contract_is_documented() -> None:
    for path in (README, STORAGE_CONTRACT, ARTIFACT_LAYOUT, AGENT_CONTEXT):
        text = path.read_text(encoding="utf-8")
        assert "~/.proofline/" in text, path
        assert "operations" in text, path
    for path in (README, STORAGE_CONTRACT, AGENT_CONTEXT):
        text = path.read_text(encoding="utf-8")
        assert "docs/operations/*.md" in text, path
        assert "manifest" in text.lower(), path


def test_each_hosted_job_contains_repository_external_command_state_and_no_mutation_checks() -> None:
    workflow = _workflow()
    required = (
        "PYTHONDONTWRITEBYTECODE",
        "UV_CACHE_DIR",
        "UV_PROJECT_ENVIRONMENT",
        "TMPDIR",
        "TEMP",
        "TMP",
        "status --short --untracked-files=all",
        "status --short --ignored --untracked-files=all",
    )
    for job_name, job in workflow["jobs"].items():
        runs = "\n".join(step.get("run", "") for step in job["steps"])
        for marker in required:
            assert marker in runs, (job_name, marker)
        assert "RUNNER_TEMP" in runs, job_name
        tracked = [match.start() for match in re.finditer("status --short --untracked-files=all", runs)]
        ignored = [
            match.start()
            for match in re.finditer("status --short --ignored --untracked-files=all", runs)
        ]
        assert len(tracked) >= 2 and len(ignored) >= 2, job_name
        command = runs.index("uv build --wheel") if job_name == "build-candidate" else runs.index("pytest")
        assert tracked[0] < command < tracked[-1], job_name
        assert ignored[0] < command < ignored[-1], job_name

    text = WORKFLOW.read_text(encoding="utf-8")
    for command in re.findall(r"(?m)^\s*(?:uv run )?pytest\b.*$", text):
        assert "--basetemp" in command, command
        assert "-p no:cacheprovider" in command, command
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            run = step.get("run", "")
            if "python -m compileall" in run:
                assert run.index("PYTHONPYCACHEPREFIX") < run.index("python -m compileall")


def test_verified_absolute_wheel_is_exported_to_each_hosted_consumer_suite() -> None:
    workflow = _workflow()
    ubuntu = next(
        step["run"]
        for step in workflow["jobs"]["ubuntu-python311"]["steps"]
        if step.get("name") == "Verify exact wheel and source candidate"
    )
    windows_steps = workflow["jobs"]["windows-python311"]["steps"]
    checksum_index = next(
        index
        for index, step in enumerate(windows_steps)
        if step.get("name") == "Run tracked Windows candidate gate"
    )
    consumer_index = next(
        index
        for index, step in enumerate(windows_steps)
        if step.get("name") == "Verify source and installed wheel regressions"
    )
    assert checksum_index < consumer_index
    windows = windows_steps[consumer_index]["run"]

    assert ubuntu.index("sha256sum --check --strict SHA256SUMS") < ubuntu.index(
        "WHEEL=$(realpath proofline-*.whl)"
    ) < ubuntu.index('export PROOFLINE_HOSTED_CANDIDATE_WHEEL="$WHEEL"')
    assert ubuntu.index('export PROOFLINE_HOSTED_CANDIDATE_WHEEL="$WHEEL"') < ubuntu.index(
        'uv run pytest -q -m "not candidate_build_only"'
    )
    assert windows.index("candidate provenance mismatch") < windows.index(
        "$env:PROOFLINE_HOSTED_CANDIDATE_WHEEL = $wheel[0].FullName"
    ) < windows.index("uv run pytest -q tests/test_line_init.py")

    controls = (
        "PROOFLINE_HOSTED_CANDIDATE_MODE",
        "PROOFLINE_HOSTED_CANDIDATE_WHEEL_SHA256",
        "PROOFLINE_HOSTED_CANDIDATE_WHEEL",
        "PROOFLINE_INSTALLED_EXECUTABLE",
    )
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            run = step.get("run", "")
            if "pytest" not in run:
                continue
            pytest_index = run.index("pytest")
            for control in controls:
                assert control in run[:pytest_index], (step.get("name"), control)
    assert "digest=${digest,,}" in WORKFLOW.read_text(encoding="utf-8")
    assert ".ToLowerInvariant()" in WORKFLOW.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", HOSTED_WHEEL_CONSUMERS)
def test_hosted_wheel_consumers_validate_exact_file_and_bypass_local_build(
    path: Path,
) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    helper = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_hosted_candidate_wheel"
    )
    helper_source = ast.unparse(helper)
    for control in (
        "PROOFLINE_HOSTED_CANDIDATE_MODE",
        "PROOFLINE_HOSTED_CANDIDATE_WHEEL",
        "PROOFLINE_HOSTED_CANDIDATE_WHEEL_SHA256",
        "PROOFLINE_INSTALLED_EXECUTABLE",
    ):
        assert control in helper_source
    assert "!= '1'" in helper_source
    assert ".is_absolute()" in helper_source
    assert helper_source.count(".is_file()") >= 2
    assert "sha256" in helper_source
    assert "hexdigest" in helper_source

    provenance_source = helper_source
    return_statement = "return wheel"
    for required in (
        "python.exe",
        "-I",
        "distribution('proofline')",
        "direct_url.json",
        ".resolve().as_uri()",
        "returncode == 0",
    ):
        assert required in provenance_source
    assert provenance_source.index("returncode == 0") < provenance_source.index(return_statement)

    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def contains_uv_build(nodes: list[ast.stmt]) -> bool:
        return any(
            {"uv", "build"}.issubset(
                {
                    child.value
                    for child in ast.walk(node)
                    if isinstance(child, ast.Constant) and isinstance(child.value, str)
                }
            )
            for node in nodes
        )

    for function_name in HOSTED_WHEEL_CONSUMERS[path]:
        function = functions[function_name]
        source = ast.unparse(function)
        assert "_hosted_candidate_wheel()" in source
        candidate_branch = next(
            node
            for node in ast.walk(function)
            if isinstance(node, ast.If)
            and "_hosted_candidate_wheel" in source
            and "wheel is not None" in ast.unparse(node.test)
        )
        assert not contains_uv_build(candidate_branch.body)


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


def test_windows_gate_pins_operations_inventory_bytes_and_manifest_hashes() -> None:
    text = WINDOWS_GATE.read_text(encoding="utf-8")
    for marker in (
        "proofline_home/operations/",
        "operations/*.md",
        "managed_files",
        "SHA256",
        "operation path set mismatch",
        "operation bytes mismatch",
        "operation manifest mismatch",
    ):
        assert marker in text
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "verify-windows-candidate.ps1" in workflow
    assert "operations inventory/bytes/manifest SHA256" in workflow


def test_windows_gate_does_not_read_or_mutate_line_execution_status(tmp_path: Path) -> None:
    fixture_line = WINDOWS_FIXTURE / ".proofline/lines/line-0001/line-0001.md"
    fixture_line_text = fixture_line.read_text(encoding="utf-8")
    assert "execution_status: verifying" in fixture_line_text
    gate = WINDOWS_GATE.read_text(encoding="utf-8")
    assert "execution_status" not in gate
    assert "FixtureLineText" not in gate
    disable_normalization = '& $GitPath -C $Application config core.autocrlf false'
    assert disable_normalization in gate
    assert gate.index(disable_normalization) < gate.index('& $GitPath -C $Application add -A')
    project = tmp_path / "windows-gate-fixture"
    shutil.copytree(WINDOWS_FIXTURE, project)
    copied_line = project / ".proofline/lines/line-0001/line-0001.md"
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
    assert copied_line.read_text(encoding="utf-8") == fixture_line_text


def test_workflow_and_gate_preserve_governance_and_home_boundaries() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    gate = WINDOWS_GATE.read_text(encoding="utf-8")
    assert ".proofline/lines" not in workflow
    assert "git tag" not in gate.lower()
    home = HOME_INIT_TESTS.read_text(encoding="utf-8")
    assert 'monkeypatch.setenv("HOME", str(home))' in home
    assert 'monkeypatch.setenv("USERPROFILE", str(home))' in home


def test_windows_consumer_suite_keeps_platform_specific_tests_skippable() -> None:
    line_init = LINE_INIT_TESTS.read_text(encoding="utf-8")
    assert 'fcntl = pytest.importorskip("fcntl")' in line_init


def test_hosted_workflow_declares_shared_clean_runner_plan_contract() -> None:
    workflow = _workflow()
    assert PLAN.is_file()
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    contract = workflow["env"]

    assert plan["plan_id"] == "candidate-clean-runner-v1"
    assert contract == {
        "PROOFLINE_CLEAN_RUNNER_PLAN_ID": plan["plan_id"],
        "PROOFLINE_CLEAN_RUNNER_PLAN_RESOURCE": (
            ".github/resources/candidate-clean-runner-plan-v1.json"
        ),
        "PROOFLINE_CLEAN_RUNNER_STEP_ORDER": (
            "verify-wheel,verify-checksum,create-environment,"
            "install-candidate,provision-harness,contract-probe"
        ),
        "PROOFLINE_CLEAN_RUNNER_ENDPOINT": "https://pypi.org/simple",
        "PROOFLINE_CLEAN_RUNNER_VERSION_SOURCE": "uv.lock",
        "PROOFLINE_CLEAN_RUNNER_NETWORK_MODE": "online-offline",
        "PROOFLINE_CLEAN_RUNNER_PUBLICATION_PREREQUISITE": "none",
    }
    for platform in ("ubuntu-python311", "windows-python311"):
        steps = plan["platforms"][platform]["steps"]
        assert [step["step_id"] for step in steps] == contract[
            "PROOFLINE_CLEAN_RUNNER_STEP_ORDER"
        ].split(",")
        assert steps[0]["step_id"] == "verify-wheel"
        assert steps[1]["step_id"] == "verify-checksum"
        assert all(step["publication_prerequisite"] == "none" for step in steps)
        provision = next(step for step in steps if step["step_id"] == "provision-harness")
        assert provision["endpoint"] == contract["PROOFLINE_CLEAN_RUNNER_ENDPOINT"]
        assert provision["version_source"] == contract[
            "PROOFLINE_CLEAN_RUNNER_VERSION_SOURCE"
        ]
        assert provision["network_mode"] == contract["PROOFLINE_CLEAN_RUNNER_NETWORK_MODE"]

    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    assert "PROOFLINE_CLEAN_RUNNER_PACKAGED_PLAN_RESOURCE" not in workflow_text
    assert "proofline_home/skills/proofline-run-dqc/resources" not in workflow_text
