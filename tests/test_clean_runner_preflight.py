from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import textwrap
import time
import zipfile
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
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "proofline_home/skills/proofline-run-dqc/scripts/preflight_clean_runner.py",
            HELPER.read_bytes(),
        )
        archive.writestr(
            "proofline_home/skills/proofline-run-dqc/resources/candidate-clean-runner-plan-v1.json",
            PLAN.read_bytes(),
        )
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


CANONICAL_STEP_ARGV = {
    "ubuntu-python311": {
        "verify-wheel": ["proofline-clean-runner-internal", "verify-wheel", "{wheel}"],
        "verify-checksum": [
            "proofline-clean-runner-internal", "verify-checksum", "{wheel}", "{wheel_sha256}"
        ],
        "create-environment": [
            "uv", "venv", "--python", "{python_interpreter}", "{environment}"
        ],
        "install-candidate": [
            "uv", "pip", "install", "--python", "{python}",
            "--no-deps", "--no-index", "{wheel}",
        ],
        "provision-harness": [
            "uv", "pip", "install", "--python", "{python}",
            "--index-url", "https://pypi.org/simple",
            "colorama==0.4.6", "iniconfig==2.3.0", "packaging==26.2",
            "pluggy==1.6.0", "pygments==2.20.0", "pytest==9.1.1",
        ],
        "contract-probe": [
            "{python}", "-m", "pytest", "-p", "no:cacheprovider", "{contract_probe}"
        ],
    },
    "windows-python311": {
        "verify-wheel": ["proofline-clean-runner-internal", "verify-wheel", "{wheel}"],
        "verify-checksum": [
            "proofline-clean-runner-internal", "verify-checksum", "{wheel}", "{wheel_sha256}"
        ],
        "create-environment": [
            "uv", "venv", "--python", "{python_interpreter}", "{environment}"
        ],
        "install-candidate": [
            "uv", "pip", "install", "--python", "{python}",
            "--no-deps", "--no-index", "{wheel}",
        ],
        "provision-harness": [
            "uv", "pip", "install", "--python", "{python}",
            "--index-url", "https://pypi.org/simple",
            "colorama==0.4.6", "iniconfig==2.3.0", "packaging==26.2",
            "pluggy==1.6.0", "pygments==2.20.0", "pytest==9.1.1",
        ],
        "contract-probe": [
            "{python}", "-m", "pytest", "-p", "no:cacheprovider", "{contract_probe}"
        ],
    },
}


def test_canonical_plan_owns_every_exact_platform_step_argv():
    value = json.loads(PLAN.read_text(encoding="utf-8"))
    actual = {
        platform: {step["step_id"]: step["argv"] for step in record["steps"]}
        for platform, record in value["platforms"].items()
    }
    assert actual == CANONICAL_STEP_ARGV


@pytest.mark.parametrize(
    ("platform", "step_id"),
    [
        (platform, step_id)
        for platform, steps in CANONICAL_STEP_ARGV.items()
        for step_id in steps
    ],
)
def test_plan_rejects_bounded_but_wrong_argv_for_every_platform_step(
    helper, valid_case, tmp_path, platform, step_id
):
    def mutate(value):
        step = next(
            record
            for record in value["platforms"][platform]["steps"]
            if record["step_id"] == step_id
        )
        step["argv"] = ["python", "--version"]

    path = _plan_variant(tmp_path, mutate)
    _assert_code(
        helper, "clean_preflight.plan.invalid", lambda: _validate(helper, valid_case, plan=path)
    )


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


REQUIRED_HARNESS = {
    "colorama": "0.4.6",
    "iniconfig": "2.3.0",
    "packaging": "26.2",
    "pluggy": "1.6.0",
    "pygments": "2.20.0",
    "pytest": "9.1.1",
}


def _tree_bytes(root: Path) -> dict[str, bytes | None]:
    if not root.exists():
        return {}
    result: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        result[relative] = None if path.is_dir() else path.read_bytes()
    return result


@pytest.fixture
def fake_uv(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    inherited_bin = tmp_path / "inherited-bin"
    inherited_bin.mkdir()
    interpreter = inherited_bin / "python3.11"
    interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    interpreter.chmod(interpreter.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", os.pathsep.join((str(inherited_bin), os.defpath)))
    executable = tmp_path / "fake-uv"
    log = tmp_path / "fake-uv.jsonl"
    executable.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import os
            import pathlib
            import sys

            executable = pathlib.Path(sys.argv[0])
            log = executable.with_suffix(".jsonl")
            record = {
                "argv": sys.argv[1:],
                "cwd": os.getcwd(),
                "env": {
                    key: os.environ.get(key)
                    for key in (
                        "HOME", "USERPROFILE", "UV_CACHE_DIR", "VIRTUAL_ENV",
                        "UV_PROJECT_ENVIRONMENT", "PYTHONPATH", "PYTHONNOUSERSITE",
                        "UV_NO_CONFIG", "UV_OFFLINE", "HTTPS_PROXY"
                    )
                },
            }
            with log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, sort_keys=True) + "\\n")
            if sys.argv[1:2] == ["venv"]:
                environment = pathlib.Path(sys.argv[-1])
                python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                python.parent.mkdir(parents=True, exist_ok=True)
                python.write_text("#!/usr/bin/env python3\\n", encoding="utf-8")
                python.chmod(0o700)
            elif "--offline" in sys.argv and (executable.parent / "network-attempt").exists():
                print("clean_preflight.network.forbidden", file=sys.stderr)
                raise SystemExit(86)
            elif "--index-url" in sys.argv and (executable.parent / "online-failure").exists():
                print("deterministic resolver failure", file=sys.stderr)
                raise SystemExit(7)
            """
        ),
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable, log


def _wheelhouse(root: Path, *, omit: str | None = None) -> Path:
    root.mkdir()
    for name, version in REQUIRED_HARNESS.items():
        if name != omit:
            (root / f"{name}-{version}-py3-none-any.whl").write_bytes(
                f"fixture {name} {version}\n".encode()
            )
    return root


def _run_provisioning(
    helper,
    case,
    fake_uv,
    monkeypatch,
    tmp_path: Path,
    *,
    network_mode: str,
    wheelhouse: Path | None = None,
):
    executable, log = fake_uv
    ambient_home = tmp_path / "ambient-home"
    ambient_home.mkdir(exist_ok=True)
    (ambient_home / "sentinel").write_text("unchanged\n", encoding="utf-8")
    ambient_cache = tmp_path / "ambient-cache"
    ambient_cache.mkdir(exist_ok=True)
    (ambient_cache / "warm-only.whl").write_bytes(b"ambient warm cache")
    ambient_environment = tmp_path / "ambient-environment"
    ambient_environment.mkdir(exist_ok=True)
    project_before = _tree_bytes(case["repo"])
    home_before = _tree_bytes(ambient_home)
    wheel_before = case["wheel"].read_bytes()
    monkeypatch.setenv("HOME", str(ambient_home))
    monkeypatch.setenv("USERPROFILE", str(ambient_home))
    monkeypatch.setenv("UV_CACHE_DIR", str(ambient_cache))
    monkeypatch.setenv("VIRTUAL_ENV", str(ambient_environment))
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(ambient_environment))
    monkeypatch.setenv("PYTHONPATH", str(ambient_home / "user-site"))

    result = helper.run_clean_preflight(
        repo=case["repo"],
        candidate=case["candidate"],
        wheel=case["wheel"],
        provenance_path=case["provenance"],
        plan_path=PLAN,
        network_mode=network_mode,
        wheelhouse=wheelhouse,
        uv_executable=str(executable),
    )

    assert _tree_bytes(case["repo"]) == project_before
    assert _tree_bytes(ambient_home) == home_before
    assert case["wheel"].read_bytes() == wheel_before
    assert _tree_bytes(ambient_cache) == {"warm-only.whl": b"ambient warm cache"}
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return result, records, ambient_home, ambient_cache, ambient_environment


@pytest.mark.parametrize("network_mode", ["online", "offline"])
def test_disposable_provisioning_uses_exact_local_wheel_and_isolated_state(
    helper, valid_case, fake_uv, monkeypatch, tmp_path, network_mode
):
    wheelhouse = _wheelhouse(tmp_path / "wheelhouse") if network_mode == "offline" else None
    result, records, ambient_home, ambient_cache, ambient_environment = _run_provisioning(
        helper,
        valid_case,
        fake_uv,
        monkeypatch,
        tmp_path,
        network_mode=network_mode,
        wheelhouse=wheelhouse,
    )
    assert result["candidate_commit"] == valid_case["candidate"]
    assert result["network_mode"] == network_mode
    assert len(records) == 3
    create, candidate_install, harness_install = records
    interpreter = (tmp_path / "inherited-bin/python3.11").resolve()
    assert create["argv"][:3] == ["venv", "--python", str(interpreter)]
    assert Path(create["argv"][2]).is_absolute()
    assert create["argv"][2] != "3.11"
    assert Path(candidate_install["argv"][-1]).name == valid_case["wheel"].name
    assert Path(candidate_install["argv"][-1]).parent.name == "candidate"
    assert not Path(candidate_install["argv"][-1]).exists()
    assert "--no-deps" in candidate_install["argv"]
    assert "--no-index" in candidate_install["argv"]
    assert all("build" not in record["argv"] for record in records)
    assert all(str(valid_case["wheel"]) not in record["argv"] for record in (create, harness_install))
    for record in records:
        assert Path(record["cwd"]).is_absolute()
        assert not Path(record["cwd"]).exists()
        assert not Path(record["env"]["HOME"]).exists()
        assert record["env"]["HOME"] != str(ambient_home)
        assert record["env"]["USERPROFILE"] != str(ambient_home)
        assert record["env"]["UV_CACHE_DIR"] != str(ambient_cache)
        assert record["env"]["VIRTUAL_ENV"] is None
        assert record["env"]["UV_PROJECT_ENVIRONMENT"] is None
        assert record["env"]["PYTHONPATH"] is None
        assert record["env"]["PYTHONNOUSERSITE"] == "1"
        assert record["env"]["UV_NO_CONFIG"] == "1"
    requirements = {token for token in harness_install["argv"] if "==" in token}
    assert requirements == {f"{name}=={version}" for name, version in REQUIRED_HARNESS.items()}
    if network_mode == "online":
        assert "--index-url" in harness_install["argv"]
        assert harness_install["argv"][harness_install["argv"].index("--index-url") + 1] == "https://pypi.org/simple"
        assert "--offline" not in harness_install["argv"]
        assert "--no-index" not in harness_install["argv"]
    else:
        assert harness_install["env"]["UV_OFFLINE"] == "1"
        assert harness_install["env"]["HTTPS_PROXY"] == "http://127.0.0.1:9"
        assert "--offline" in harness_install["argv"]
        assert "--no-index" in harness_install["argv"]
        assert harness_install["argv"][harness_install["argv"].index("--find-links") + 1] == str(wheelhouse)
        assert "--index-url" not in harness_install["argv"]


def test_python311_resolution_rejects_relative_path_entry(helper, monkeypatch):
    monkeypatch.setenv("PATH", "relative/bin")
    _assert_code(
        helper,
        "clean_preflight.provision.failed",
        lambda: helper._resolve_python_interpreter("python3.11"),
    )


def test_python311_resolution_rejects_nonexecutable_file(
    helper, tmp_path: Path, monkeypatch
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "python3.11"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o600)
    monkeypatch.setenv("PATH", str(bin_dir))
    _assert_code(
        helper,
        "clean_preflight.provision.failed",
        lambda: helper._resolve_python_interpreter("python3.11"),
    )


def test_python311_resolution_returns_canonical_regular_executable_through_symlink(
    helper, tmp_path: Path, monkeypatch
):
    canonical_bin = tmp_path / "canonical"
    inherited_bin = tmp_path / "inherited"
    canonical_bin.mkdir()
    inherited_bin.mkdir()
    canonical = canonical_bin / "python3.11"
    canonical.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    canonical.chmod(canonical.stat().st_mode | stat.S_IXUSR)
    (inherited_bin / "python3.11").symlink_to(canonical)
    monkeypatch.setenv("PATH", str(inherited_bin))

    resolved = helper._resolve_python_interpreter("python3.11")

    assert resolved == str(canonical)
    assert Path(resolved).is_absolute()
    assert Path(resolved).is_file()
    assert not Path(resolved).is_symlink()
    assert os.access(resolved, os.X_OK)


@pytest.mark.parametrize("missing", sorted(REQUIRED_HARNESS))
def test_offline_missing_required_or_transitive_distribution_has_exact_diagnostic(
    helper, valid_case, fake_uv, monkeypatch, tmp_path, missing
):
    wheelhouse = _wheelhouse(tmp_path / "wheelhouse", omit=missing)
    executable, _ = fake_uv
    _assert_code(
        helper,
        "clean_preflight.dependency.missing_offline",
        lambda: helper.run_clean_preflight(
            repo=valid_case["repo"],
            candidate=valid_case["candidate"],
            wheel=valid_case["wheel"],
            provenance_path=valid_case["provenance"],
            plan_path=PLAN,
            network_mode="offline",
            wheelhouse=wheelhouse,
            uv_executable=str(executable),
        ),
    )


def test_ambient_warm_cache_alone_cannot_satisfy_offline(helper, valid_case, fake_uv, monkeypatch, tmp_path):
    ambient = _wheelhouse(tmp_path / "ambient-cache")
    empty = tmp_path / "empty-wheelhouse"
    empty.mkdir()
    monkeypatch.setenv("UV_CACHE_DIR", str(ambient))
    _assert_code(
        helper,
        "clean_preflight.dependency.missing_offline",
        lambda: helper.run_clean_preflight(
            repo=valid_case["repo"], candidate=valid_case["candidate"], wheel=valid_case["wheel"],
            provenance_path=valid_case["provenance"], plan_path=PLAN, network_mode="offline",
            wheelhouse=empty, uv_executable=str(fake_uv[0]),
        ),
    )


def test_online_resolver_nonzero_has_exact_diagnostic(helper, valid_case, fake_uv, monkeypatch, tmp_path):
    (fake_uv[0].parent / "online-failure").touch()
    _assert_code(
        helper,
        "clean_preflight.provision.failed",
        lambda: helper.run_clean_preflight(
            repo=valid_case["repo"], candidate=valid_case["candidate"], wheel=valid_case["wheel"],
            provenance_path=valid_case["provenance"], plan_path=PLAN, network_mode="online",
            wheelhouse=None, uv_executable=str(fake_uv[0]),
        ),
    )


def test_offline_network_attempt_has_exact_diagnostic(helper, valid_case, fake_uv, monkeypatch, tmp_path):
    wheelhouse = _wheelhouse(tmp_path / "wheelhouse")
    (fake_uv[0].parent / "network-attempt").touch()
    _assert_code(
        helper,
        "clean_preflight.network.forbidden",
        lambda: helper.run_clean_preflight(
            repo=valid_case["repo"], candidate=valid_case["candidate"], wheel=valid_case["wheel"],
            provenance_path=valid_case["provenance"], plan_path=PLAN, network_mode="offline",
            wheelhouse=wheelhouse, uv_executable=str(fake_uv[0]),
        ),
    )


OUTCOME_KEYS = {
    "schema_version", "outcome", "diagnostic_code", "candidate_commit",
    "wheel_filename", "wheel_sha256", "network_mode", "plan_id",
}


def test_argparse_interface_is_exact_and_required(helper):
    parser = helper._parser()
    options = {option for action in parser._actions for option in action.option_strings}
    assert options == {
        "-h", "--help", "--repo", "--candidate", "--wheel", "--provenance",
        "--network-mode", "--wheelhouse", "--plan",
    }
    assert {action.dest for action in parser._actions if action.required} == {
        "repo", "candidate", "wheel", "provenance", "network_mode",
    }


def test_cli_parse_failure_emits_one_strict_safe_json_outcome(helper, capsys):
    assert helper.main([]) != 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert len(lines) == 1
    outcome = json.loads(lines[0])
    assert set(outcome) == OUTCOME_KEYS
    assert outcome == {
        "schema_version": 1,
        "outcome": "fail",
        "diagnostic_code": "clean_preflight.input.invalid",
        "candidate_commit": "",
        "wheel_filename": "",
        "wheel_sha256": "",
        "network_mode": "",
        "plan_id": "",
    }
    assert len(captured.err.encode()) <= 1024


def _fixture_executable(tmp_path: Path, body: str) -> Path:
    executable = tmp_path / "fixture-child"
    executable.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body), encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _linux_child_subreaper_state(value: int | None = None) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    if value is not None:
        if libc.prctl(36, value, 0, 0, 0) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
    state = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(state), 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return state.value


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux prctl contract")
@pytest.mark.parametrize("outcome", ["success", "timeout", "output-overflow", "nonzero"])
def test_provision_restores_callers_child_subreaper_state(helper, tmp_path, outcome):
    bodies = {
        "success": "pass",
        "timeout": "import time; time.sleep(60)",
        "output-overflow": "import os; os.write(1, b'x' * 1200)",
        "nonzero": "raise SystemExit(7)",
    }
    expected_codes = {
        "timeout": "clean_preflight.timeout",
        "output-overflow": "clean_preflight.output_limit",
        "nonzero": "clean_preflight.provision.failed",
    }
    executable = _fixture_executable(tmp_path, bodies[outcome])
    original = _linux_child_subreaper_state()
    try:
        _linux_child_subreaper_state(0)
        before = _linux_child_subreaper_state()
        operation = lambda: helper._run_provision(
            [str(executable)],
            cwd=tmp_path,
            environment=helper._clean_environment(tmp_path / "cache", tmp_path, tmp_path),
            budget=helper.ExecutionBudget(
                seconds=0.1 if outcome == "timeout" else 5,
                output_limit=1024,
            ),
        )
        if outcome == "success":
            operation()
        else:
            _assert_code(helper, expected_codes[outcome], operation)
        assert _linux_child_subreaper_state() == before
    finally:
        _linux_child_subreaper_state(original)


def test_execution_timeout_kills_child_and_descendant(helper, tmp_path):
    pid_file = tmp_path / "descendant.pid"
    executable = _fixture_executable(
        tmp_path,
        """
        import pathlib, subprocess, sys, time
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding="ascii")
        time.sleep(60)
        """,
    )
    budget = helper.ExecutionBudget(seconds=0.5, output_limit=4096)
    _assert_code(
        helper,
        "clean_preflight.timeout",
        lambda: helper._run_provision(
            [str(executable), str(pid_file)], cwd=tmp_path,
            environment=helper._clean_environment(tmp_path / "cache", tmp_path, tmp_path),
            budget=budget,
        ),
    )
    descendant = int(pid_file.read_text(encoding="ascii"))
    for _ in range(50):
        if not _pid_exists(descendant):
            break
        time.sleep(0.02)
    assert not _pid_exists(descendant)


@pytest.mark.parametrize(
    "body",
    [
        "os.write(1, b'o' * 1200)",
        "os.write(2, b'e' * 1200)",
        "os.write(1, b'o' * 600); os.write(2, b'e' * 600)",
    ],
    ids=["stdout-only", "stderr-only", "shared-combined"],
)
def test_output_cap_has_exact_diagnostic_for_each_stream_shape(helper, tmp_path, body):
    executable = _fixture_executable(tmp_path, f"import os\n{body}\n")
    started = time.monotonic()
    _assert_code(
        helper,
        "clean_preflight.output_limit",
        lambda: helper._run_provision(
            [str(executable)], cwd=tmp_path,
            environment=helper._clean_environment(tmp_path / "cache", tmp_path, tmp_path),
            budget=helper.ExecutionBudget(seconds=5, output_limit=1024),
        ),
    )
    assert time.monotonic() - started < 2


def test_fast_exit_buffered_output_is_fully_drained_and_counted(helper, tmp_path):
    executable = _fixture_executable(
        tmp_path,
        """
        import os
        os.write(1, b"o" * 30000)
        os.write(2, b"e" * 30000)
        """,
    )
    budget = helper.ExecutionBudget(seconds=5, output_limit=100000)
    helper._run_provision(
        [str(executable)], cwd=tmp_path,
        environment=helper._clean_environment(tmp_path / "cache", tmp_path, tmp_path),
        budget=budget,
    )
    assert budget.output_used == 60000


def test_mutation_is_detected_after_successful_provision(helper, valid_case, fake_uv):
    executable, _ = fake_uv
    marker = valid_case["repo"] / "tracked.txt"
    source = executable.read_text(encoding="utf-8")
    executable.write_text(
        source.replace(
            "record = {",
            f"pathlib.Path({str(marker)!r}).write_text('mutated\\n', encoding='utf-8')\nrecord = {{",
        ),
        encoding="utf-8",
    )
    _assert_code(
        helper,
        "clean_preflight.mutation",
        lambda: helper.run_clean_preflight(
            repo=valid_case["repo"], candidate=valid_case["candidate"], wheel=valid_case["wheel"],
            provenance_path=valid_case["provenance"], plan_path=PLAN, network_mode="online",
            wheelhouse=None, uv_executable=str(executable),
        ),
    )


def test_nonzero_precedes_mutation_diagnostic(helper, valid_case, fake_uv):
    executable, _ = fake_uv
    marker = valid_case["repo"] / "tracked.txt"
    source = executable.read_text(encoding="utf-8")
    executable.write_text(
        source.replace(
            "record = {",
            f"pathlib.Path({str(marker)!r}).write_text('mutated\\n', encoding='utf-8')\nraise SystemExit(9)\nrecord = {{",
        ),
        encoding="utf-8",
    )
    _assert_code(
        helper,
        "clean_preflight.provision.failed",
        lambda: helper.run_clean_preflight(
            repo=valid_case["repo"], candidate=valid_case["candidate"], wheel=valid_case["wheel"],
            provenance_path=valid_case["provenance"], plan_path=PLAN, network_mode="online",
            wheelhouse=None, uv_executable=str(executable),
        ),
    )


def test_default_toolchain_is_resolved_from_inherited_real_style_path(
    valid_case, fake_uv, tmp_path
):
    bin_dir = tmp_path / "user-bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_bytes(fake_uv[0].read_bytes())
    uv.chmod(0o700)
    interpreter = bin_dir / "python3.11"
    interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    interpreter.chmod(0o700)
    completed = subprocess.run(
        [
            sys.executable, str(HELPER), "--repo", str(valid_case["repo"]),
            "--candidate", valid_case["candidate"], "--wheel", str(valid_case["wheel"]),
            "--provenance", str(valid_case["provenance"]), "--network-mode", "online",
        ],
        cwd=tmp_path,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["diagnostic_code"] == "clean_preflight.pass"
    records = [
        json.loads(line)
        for line in uv.with_suffix(".jsonl").read_text(encoding="utf-8").splitlines()
    ]
    create = records[0]["argv"]
    assert create[:3] == ["venv", "--python", str(interpreter.resolve())]
    assert Path(create[2]).is_absolute()


@pytest.mark.parametrize("kind", ["symlink", "nonexec", "relative"])
def test_uv_resolution_rejects_ambiguous_noncanonical_executables(
    helper, tmp_path, monkeypatch, kind
):
    real = tmp_path / "real-uv"
    real.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    real.chmod(0o700)
    if kind == "symlink":
        selected = tmp_path / "uv"
        selected.symlink_to(real)
    elif kind == "nonexec":
        selected = tmp_path / "uv"
        selected.write_bytes(real.read_bytes())
        selected.chmod(0o600)
    else:
        selected = Path("./uv")
    monkeypatch.setenv("PATH", str(tmp_path))
    _assert_code(
        helper, "clean_preflight.provision.failed",
        lambda: helper._resolve_uv_executable(str(selected)),
    )


def _parity_root(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    for relative in (
        ".github/workflows/candidate-verification.yml",
        ".github/scripts/verify-windows-candidate.ps1",
        "skills/proofline-run-dqc/scripts/preflight_clean_runner.py",
        "skills/proofline-run-dqc/resources/candidate-clean-runner-plan-v1.json",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    return root


def test_standalone_rejects_workflow_contract_drift(
    helper, valid_case, fake_uv, monkeypatch, tmp_path
):
    root = _parity_root(tmp_path)
    workflow = root / ".github/workflows/candidate-verification.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "PROOFLINE_CLEAN_RUNNER_STEP_ORDER: verify-wheel,verify-checksum,",
            "PROOFLINE_CLEAN_RUNNER_STEP_ORDER: verify-wheel,",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(helper, "_repository_source_root", lambda: root)
    _assert_code(
        helper, "clean_preflight.parity.workflow",
        lambda: helper.run_clean_preflight(
            repo=valid_case["repo"], candidate=valid_case["candidate"],
            wheel=valid_case["wheel"], provenance_path=valid_case["provenance"],
            plan_path=root / "skills/proofline-run-dqc/resources/candidate-clean-runner-plan-v1.json",
            network_mode="online", wheelhouse=None, uv_executable=str(fake_uv[0]),
        ),
    )


def test_standalone_rejects_packaged_helper_or_plan_drift(
    helper, valid_case, fake_uv, tmp_path
):
    replacement = valid_case["wheel"].with_name(valid_case["wheel"].name + ".new")
    with zipfile.ZipFile(replacement, "w") as archive:
        archive.writestr(
            "proofline_home/skills/proofline-run-dqc/scripts/preflight_clean_runner.py", b"drift\n"
        )
        archive.writestr(
            "proofline_home/skills/proofline-run-dqc/resources/candidate-clean-runner-plan-v1.json",
            PLAN.read_bytes(),
        )
    replacement.replace(valid_case["wheel"])
    payload = dict(valid_case["provenance_value"])
    payload["wheel_sha256"] = hashlib.sha256(valid_case["wheel"].read_bytes()).hexdigest()
    valid_case["provenance"].write_text(json.dumps(payload), encoding="utf-8")
    _assert_code(
        helper, "clean_preflight.parity.packaged",
        lambda: helper.run_clean_preflight(
            repo=valid_case["repo"], candidate=valid_case["candidate"],
            wheel=valid_case["wheel"], provenance_path=valid_case["provenance"],
            plan_path=PLAN, network_mode="online", wheelhouse=None,
            uv_executable=str(fake_uv[0]),
        ),
    )


def test_repository_root_symlink_is_rejected(helper, valid_case, tmp_path):
    alias = tmp_path / "repo-alias"
    alias.symlink_to(valid_case["repo"], target_is_directory=True)
    valid_case["repo"] = alias
    _assert_code(
        helper, "clean_preflight.candidate.mismatch", lambda: _validate(helper, valid_case)
    )


def test_repository_ancestor_symlink_is_rejected(helper, valid_case, tmp_path):
    alias_parent = tmp_path / "parent-alias"
    alias_parent.symlink_to(tmp_path, target_is_directory=True)
    valid_case["repo"] = alias_parent / valid_case["repo"].name
    assert not valid_case["repo"].is_symlink()
    _assert_code(
        helper, "clean_preflight.candidate.mismatch", lambda: _validate(helper, valid_case)
    )


def test_repository_lexical_dotdot_is_rejected(helper, valid_case, tmp_path):
    lexical_parent = tmp_path / "unused"
    lexical_parent.mkdir()
    valid_case["repo"] = lexical_parent / ".." / valid_case["repo"].name
    assert valid_case["repo"].is_absolute()
    _assert_code(
        helper, "clean_preflight.candidate.mismatch", lambda: _validate(helper, valid_case)
    )


def test_linked_worktree_common_ref_mutation_is_detected(helper, valid_case, fake_uv, tmp_path):
    linked = tmp_path / "linked"
    _git(valid_case["repo"], "worktree", "add", "-qb", "linked-test", str(linked))
    valid_case["repo"] = linked
    valid_case["candidate"] = _git(linked, "rev-parse", "HEAD")
    payload = dict(valid_case["provenance_value"])
    payload["candidate_commit"] = valid_case["candidate"]
    valid_case["provenance"].write_text(json.dumps(payload), encoding="utf-8")
    executable = fake_uv[0]
    executable.write_text(
        executable.read_text(encoding="utf-8").replace(
            "record = {",
            f"import subprocess\nsubprocess.run(['git','-C',{str(linked)!r},'update-ref','refs/heads/hidden-mutation','HEAD'], check=True)\nrecord = {{",
        ),
        encoding="utf-8",
    )
    _assert_code(
        helper, "clean_preflight.mutation",
        lambda: helper.run_clean_preflight(
            repo=linked, candidate=valid_case["candidate"], wheel=valid_case["wheel"],
            provenance_path=valid_case["provenance"], plan_path=PLAN,
            network_mode="online", wheelhouse=None, uv_executable=str(executable),
        ),
    )


def test_wheel_replacement_after_validation_fails_closed_and_uses_exact_fd_name(
    helper, valid_case, fake_uv, monkeypatch
):
    original_parity = helper._verify_packaged_parity

    def replace_after_parity(*args, **kwargs):
        result = original_parity(*args, **kwargs)
        replacement = valid_case["wheel"].with_name("replacement")
        replacement.write_bytes(b"replacement bytes")
        replacement.replace(valid_case["wheel"])
        return result

    monkeypatch.setattr(helper, "_verify_packaged_parity", replace_after_parity)
    _assert_code(
        helper, "clean_preflight.mutation",
        lambda: helper.run_clean_preflight(
            repo=valid_case["repo"], candidate=valid_case["candidate"],
            wheel=valid_case["wheel"], provenance_path=valid_case["provenance"],
            plan_path=PLAN, network_mode="online", wheelhouse=None,
            uv_executable=str(fake_uv[0]),
        ),
    )
    records = [json.loads(line) for line in fake_uv[1].read_text(encoding="utf-8").splitlines()]
    install_path = Path(next(r for r in records if "--no-deps" in r["argv"])["argv"][-1])
    assert install_path.name == valid_case["provenance_value"]["wheel_filename"]


def test_wheel_replacement_during_install_fails_closed(helper, valid_case, fake_uv):
    executable = fake_uv[0]
    executable.write_text(
        executable.read_text(encoding="utf-8").replace(
            'elif "--offline" in sys.argv',
            f"elif '--no-deps' in sys.argv:\n    replacement = pathlib.Path({str(valid_case['wheel'])!r} + '.replacement')\n    replacement.write_bytes(b'replaced during install')\n    replacement.replace(pathlib.Path({str(valid_case['wheel'])!r}))\nelif \"--offline\" in sys.argv",
        ),
        encoding="utf-8",
    )
    _assert_code(
        helper, "clean_preflight.mutation",
        lambda: helper.run_clean_preflight(
            repo=valid_case["repo"], candidate=valid_case["candidate"],
            wheel=valid_case["wheel"], provenance_path=valid_case["provenance"],
            plan_path=PLAN, network_mode="online", wheelhouse=None,
            uv_executable=str(executable),
        ),
    )
