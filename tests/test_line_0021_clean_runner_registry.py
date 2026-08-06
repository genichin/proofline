from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from helpers.line_0021_clean_runner import (
    APPROVED_SCENARIO_IDS,
    HELPER_MEMBER,
    PLAN_MEMBER,
    run_registry,
)

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tests/scenarios/line_0021_clean_runner_registry.json"
SOURCE_HELPER = ROOT / "skills/proofline-run-dqc/scripts/preflight_clean_runner.py"
SOURCE_PLAN = ROOT / "skills/proofline-run-dqc/resources/candidate-clean-runner-plan-v1.json"


def _hosted_candidate_wheel() -> Path | None:
    if os.environ.get("PROOFLINE_HOSTED_CANDIDATE_MODE") != "1":
        return None
    provided = os.environ.get("PROOFLINE_HOSTED_CANDIDATE_WHEEL")
    expected = os.environ.get("PROOFLINE_HOSTED_CANDIDATE_WHEEL_SHA256")
    installed = os.environ.get("PROOFLINE_INSTALLED_EXECUTABLE")
    assert provided and expected and installed, "hosted candidate controls are incomplete"
    wheel = Path(provided)
    executable = Path(installed)
    assert wheel.is_absolute() and wheel.is_file(), "candidate wheel must be an absolute file"
    assert executable.is_absolute() and executable.is_file(), "installed executable must be an absolute file"
    assert len(expected) == 64 and expected == expected.lower() and all(
        character in "0123456789abcdef" for character in expected
    ), "candidate wheel SHA256 must be lowercase hexadecimal"
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == expected, "candidate wheel SHA256 mismatch"
    python = executable.parent / ("python.exe" if os.name == "nt" else "python")
    assert python.is_absolute() and python.is_file(), (
        "installed executable has no absolute candidate environment Python"
    )
    try:
        provenance = subprocess.run(
            (
                str(python),
                "-I",
                "-c",
                "from importlib.metadata import distribution; "
                "print(distribution('proofline').read_text('direct_url.json'))",
            ),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise AssertionError("installed candidate provenance probe failed") from exc
    assert provenance.returncode == 0, provenance.stderr
    try:
        direct_url = json.loads(provenance.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError("installed candidate provenance is malformed") from exc
    assert isinstance(direct_url, dict), "installed candidate provenance must be an object"
    assert direct_url.get("url") == wheel.resolve().as_uri(), (
        "installed candidate wheel path mismatch"
    )
    return wheel


def _provided_or_fixture_wheel(tmp_path: Path) -> Path:
    wheel = _hosted_candidate_wheel()
    if wheel is not None:
        return wheel
    wheel = tmp_path / "proofline-0.6.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(HELPER_MEMBER, SOURCE_HELPER.read_bytes())
        archive.writestr(PLAN_MEMBER, SOURCE_PLAN.read_bytes())
    return wheel


def test_registry_has_exact_fixed_ids_and_cardinality() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    ids = tuple(case["id"] for case in registry["scenarios"])
    assert registry["schema_version"] == 1
    assert registry["scenario_count"] == 23
    assert len(ids) == len(set(ids)) == 23
    assert ids == APPROVED_SCENARIO_IDS
    assert len({case["axis"] for case in registry["scenarios"]}) == 23


@pytest.mark.candidate_build_only
def test_fixed_registry_has_exact_source_and_extracted_wheel_parity(tmp_path: Path) -> None:
    wheel = _provided_or_fixture_wheel(tmp_path)
    evidence = run_registry(
        root=ROOT,
        registry_path=REGISTRY,
        wheel=wheel,
        workspace=tmp_path / "registry",
    )
    assert evidence["scenario_count"] == 23
    assert evidence["source_count"] == evidence["wheel_count"] == 23
    assert evidence["packaged_helper_count"] == 23
    assert evidence["byte_parity"] is True
    assert evidence["no_unexpected_mutation"] is True
    assert evidence["wheel"] == str(wheel.resolve())
    assert len(evidence["wheel_sha256"]) == 64


def test_registry_runner_cli_reports_machine_readable_evidence(tmp_path: Path) -> None:
    wheel = _provided_or_fixture_wheel(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tests/helpers/line_0021_clean_runner.py"),
            "--root",
            str(ROOT),
            "--registry",
            str(REGISTRY),
            "--wheel",
            str(wheel),
            "--workspace",
            str(tmp_path / "cli-registry"),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    evidence = json.loads(completed.stdout)
    assert evidence["scenario_count"] == 23
    assert evidence["source_count"] == evidence["wheel_count"] == 23
