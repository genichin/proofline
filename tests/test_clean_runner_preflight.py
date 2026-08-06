from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "skills/proofline-run-dqc/scripts/preflight_clean_runner.py"
PLAN = ROOT / "skills/proofline-run-dqc/resources/candidate-clean-runner-plan-v1.json"


def _load_helper():
    spec = importlib.util.spec_from_file_location("preflight_clean_runner", HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def helper():
    return _load_helper()


def _git(repo: Path, *args: str, input_bytes: bytes | None = None) -> str:
    environment = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
    }
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        env=environment,
    )
    return completed.stdout.decode("ascii").strip()


@pytest.fixture
def valid_case(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "ProofLine Tests")
    _git(repo, "config", "user.email", "proofline@example.invalid")
    (repo / "tracked.txt").write_text("candidate\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "candidate")
    candidate = _git(repo, "rev-parse", "HEAD")

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    wheel = artifacts / "proofline-0.6.1-py3-none-any.whl"
    wheel.write_bytes(b"exact candidate wheel\n")
    provenance = artifacts / "provenance.json"
    provenance_value = {
        "schema_version": 1,
        "candidate_commit": candidate,
        "wheel_filename": wheel.name,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
    }
    provenance.write_text(json.dumps(provenance_value), encoding="utf-8")
    return {
        "repo": repo,
        "candidate": candidate,
        "wheel": wheel,
        "provenance": provenance,
        "provenance_value": provenance_value,
    }


def _validate(helper, case, *, plan: Path = PLAN):
    return helper.validate_preflight_core(
        repo=case["repo"],
        candidate=case["candidate"],
        wheel=case["wheel"],
        provenance_path=case["provenance"],
        plan_path=plan,
    )


def _assert_code(helper, code: str, operation) -> None:
    with pytest.raises(helper.ValidationError) as caught:
        operation()
    assert caught.value.code == code


def test_valid_identity_provenance_wheel_and_plan_pass(helper, valid_case):
    result = _validate(helper, valid_case)
    assert result == {
        "candidate_commit": valid_case["candidate"],
        "wheel_filename": valid_case["wheel"].name,
        "wheel_sha256": valid_case["provenance_value"]["wheel_sha256"],
        "plan_id": "candidate-clean-runner-v1",
    }


@pytest.mark.parametrize(
    "raw",
    [
        '{"schema_version":1,"schema_version":1,"candidate_commit":"x","wheel_filename":"x","wheel_sha256":"x"}',
        '{"schema_version":1,"candidate_commit":"x","wheel_filename":"x","wheel_sha256":"x","run_id":7}',
        '{"schema_version":1,"candidate_commit":"x","wheel_filename":"x"}',
    ],
    ids=["duplicate", "unknown", "missing"],
)
def test_provenance_rejects_non_exact_schema(helper, valid_case, raw):
    valid_case["provenance"].write_text(raw, encoding="utf-8")
    _assert_code(
        helper, "clean_preflight.provenance.invalid", lambda: _validate(helper, valid_case)
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("candidate_commit", 7),
        ("wheel_filename", ["proofline.whl"]),
        ("wheel_sha256", None),
    ],
)
def test_provenance_rejects_type_coercion(helper, valid_case, field, value):
    payload = dict(valid_case["provenance_value"])
    payload[field] = value
    valid_case["provenance"].write_text(json.dumps(payload), encoding="utf-8")
    _assert_code(
        helper, "clean_preflight.provenance.invalid", lambda: _validate(helper, valid_case)
    )


@pytest.mark.parametrize("kind", ["unknown", "noncommit", "provenance", "head"])
def test_candidate_binding_is_exact(helper, valid_case, kind):
    if kind == "unknown":
        valid_case["candidate"] = "0" * len(valid_case["candidate"])
    elif kind == "noncommit":
        valid_case["candidate"] = _git(
            valid_case["repo"], "hash-object", "-w", "--stdin", input_bytes=b"blob\n"
        )
    elif kind == "provenance":
        payload = dict(valid_case["provenance_value"])
        payload["candidate_commit"] = "0" * len(valid_case["candidate"])
        valid_case["provenance"].write_text(json.dumps(payload), encoding="utf-8")
    else:
        (valid_case["repo"] / "next.txt").write_text("next\n", encoding="utf-8")
        _git(valid_case["repo"], "add", "next.txt")
        _git(valid_case["repo"], "commit", "-qm", "next")
    _assert_code(
        helper, "clean_preflight.candidate.mismatch", lambda: _validate(helper, valid_case)
    )


def test_missing_wheel_is_count_failure(helper, valid_case):
    valid_case["wheel"].unlink()
    _assert_code(helper, "clean_preflight.wheel.count", lambda: _validate(helper, valid_case))


def test_multiple_matching_wheels_are_rejected(helper, valid_case):
    (valid_case["wheel"].parent / "proofline-0.6.2-py3-none-any.whl").write_bytes(b"other")
    _assert_code(helper, "clean_preflight.wheel.count", lambda: _validate(helper, valid_case))


def test_alternate_distribution_is_rejected(helper, valid_case):
    alternate = valid_case["wheel"].with_name("other-0.6.1-py3-none-any.whl")
    valid_case["wheel"].rename(alternate)
    valid_case["wheel"] = alternate
    _assert_code(helper, "clean_preflight.wheel.alternate", lambda: _validate(helper, valid_case))


def test_malformed_wheel_filename_is_rejected(helper, valid_case):
    malformed = valid_case["wheel"].with_name("proofline-latest.whl")
    valid_case["wheel"].rename(malformed)
    valid_case["wheel"] = malformed
    _assert_code(helper, "clean_preflight.wheel.filename", lambda: _validate(helper, valid_case))


def test_provenance_filename_must_match_input_basename(helper, valid_case):
    payload = dict(valid_case["provenance_value"])
    payload["wheel_filename"] = "proofline-0.6.2-py3-none-any.whl"
    valid_case["provenance"].write_text(json.dumps(payload), encoding="utf-8")
    _assert_code(helper, "clean_preflight.wheel.filename", lambda: _validate(helper, valid_case))


def test_wheel_digest_must_match_bytes(helper, valid_case):
    valid_case["wheel"].write_bytes(b"changed bytes")
    _assert_code(helper, "clean_preflight.wheel.digest", lambda: _validate(helper, valid_case))


def _plan_variant(tmp_path: Path, mutator) -> Path:
    value = json.loads(PLAN.read_text(encoding="utf-8"))
    mutator(value)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.mark.parametrize("kind", ["missing", "unknown", "type", "duplicate"])
def test_plan_rejects_non_exact_strict_schema(helper, valid_case, tmp_path, kind):
    if kind == "duplicate":
        path = tmp_path / "plan.json"
        path.write_text(
            '{"schema_version":1,"schema_version":1,"plan_id":"candidate-clean-runner-v1",'
            '"harness_dependencies":[],"platforms":{}}',
            encoding="utf-8",
        )
    else:
        def mutate(value):
            if kind == "missing":
                del value["plan_id"]
            elif kind == "unknown":
                value["publication_result"] = "pass"
            else:
                value["schema_version"] = "1"
        path = _plan_variant(tmp_path, mutate)
    _assert_code(
        helper, "clean_preflight.plan.invalid", lambda: _validate(helper, valid_case, plan=path)
    )


@pytest.mark.parametrize("version", ["latest", ">=9", "9.*", ""])
def test_plan_rejects_mutable_dependency_versions(helper, valid_case, tmp_path, version):
    path = _plan_variant(
        tmp_path, lambda value: value["harness_dependencies"][0].__setitem__("version", version)
    )
    _assert_code(
        helper, "clean_preflight.version.mutable", lambda: _validate(helper, valid_case, plan=path)
    )


def test_plan_rejects_undeclared_dependency(helper, valid_case, tmp_path):
    def mutate(value):
        value["platforms"]["ubuntu-python311"]["steps"][2]["argv"].append("ruff==1.0.0")
    path = _plan_variant(tmp_path, mutate)
    _assert_code(
        helper, "clean_preflight.dependency.undeclared", lambda: _validate(helper, valid_case, plan=path)
    )


def test_plan_rejects_publication_prerequisite(helper, valid_case, tmp_path):
    def mutate(value):
        value["platforms"]["windows-python311"]["steps"][0][
            "publication_prerequisite"
        ] = "github-release"
    path = _plan_variant(tmp_path, mutate)
    _assert_code(
        helper,
        "clean_preflight.publication_prerequisite",
        lambda: _validate(helper, valid_case, plan=path),
    )


@pytest.mark.parametrize("token", ["sh -c 'uv pip install pytest'", "proofline-*.whl", "fallback"])
def test_plan_rejects_shell_glob_and_fallback_argv(helper, valid_case, tmp_path, token):
    def mutate(value):
        value["platforms"]["ubuntu-python311"]["steps"][3]["argv"].append(token)
    path = _plan_variant(tmp_path, mutate)
    _assert_code(
        helper, "clean_preflight.plan.unbounded", lambda: _validate(helper, valid_case, plan=path)
    )
