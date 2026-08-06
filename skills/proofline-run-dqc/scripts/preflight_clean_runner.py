#!/usr/bin/env python3
"""Strict identity, provenance, plan, and disposable provisioning preflight."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


PROVENANCE_KEYS = {
    "schema_version",
    "candidate_commit",
    "wheel_filename",
    "wheel_sha256",
}
PLAN_KEYS = {"schema_version", "plan_id", "harness_dependencies", "platforms"}
DEPENDENCY_KEYS = {
    "name",
    "version",
    "version_source",
    "online_endpoint",
    "offline_wheelhouse_required",
}
STEP_KEYS = {
    "step_id",
    "argv",
    "endpoint",
    "version_source",
    "network_mode",
    "publication_prerequisite",
}
PLATFORMS = {"ubuntu-python311", "windows-python311"}
STEP_ORDER = [
    "verify-wheel",
    "verify-checksum",
    "create-environment",
    "provision-harness",
    "contract-probe",
]
LOWER_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
NORMALIZED_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
IMMUTABLE_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)*(?:[a-z]+[0-9]*)?\Z")
PINNED_REQUIREMENT = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^=\s]+)\Z")
WHEEL_FILENAME = re.compile(
    r"(?P<distribution>[A-Za-z0-9_.]+)-(?P<version>[A-Za-z0-9_.!+]+)"
    r"(?:-(?P<build>[0-9][A-Za-z0-9_.]*))?"
    r"-(?P<python>[A-Za-z0-9_.]+)-(?P<abi>[A-Za-z0-9_.]+)"
    r"-(?P<platform>[A-Za-z0-9_.]+)\.whl\Z"
)


class ValidationError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _fail(code: str, detail: str) -> None:
    raise ValidationError(code, detail)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate key: {key}")
        value[key] = item
    return value


def _strict_json(path: Path, code: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValidationError(code, f"strict JSON read failed: {path}") from exc
    if not isinstance(value, dict) or any(type(key) is not str for key in value):
        _fail(code, "strict JSON root must be an object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], code: str) -> None:
    if set(value) != expected:
        _fail(code, "object key set is not exact")


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    try:
        return subprocess.run(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-C",
                str(repo),
                *args,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValidationError(
            "clean_preflight.candidate.mismatch", "repository identity read failed"
        ) from exc


def _candidate_identity(repo: Path, candidate: str, provenance_commit: str) -> str:
    if not repo.is_absolute() or not repo.is_dir():
        _fail("clean_preflight.candidate.mismatch", "repository must be an absolute directory")
    object_format = _run_git(repo, "rev-parse", "--show-object-format")
    if object_format.returncode != 0:
        _fail("clean_preflight.candidate.mismatch", "repository object format is unavailable")
    lengths = {"sha1": 40, "sha256": 64}
    length = lengths.get(object_format.stdout.strip())
    if length is None or re.fullmatch(rf"[0-9a-f]{{{length}}}", candidate) is None:
        _fail("clean_preflight.candidate.mismatch", "candidate is not a lowercase exact OID")
    if provenance_commit != candidate:
        _fail("clean_preflight.candidate.mismatch", "provenance candidate does not match")
    object_type = _run_git(repo, "cat-file", "-t", candidate)
    if object_type.returncode != 0 or object_type.stdout.strip() != "commit":
        _fail("clean_preflight.candidate.mismatch", "candidate is not an existing commit")
    head = _run_git(repo, "rev-parse", "--verify", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != candidate:
        _fail("clean_preflight.candidate.mismatch", "candidate does not match repository HEAD")
    return candidate


def _load_provenance(path: Path) -> dict[str, Any]:
    code = "clean_preflight.provenance.invalid"
    value = _strict_json(path, code)
    _exact_keys(value, PROVENANCE_KEYS, code)
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        _fail(code, "schema_version must be integer 1")
    for key in ("candidate_commit", "wheel_filename", "wheel_sha256"):
        if type(value[key]) is not str:
            _fail(code, f"{key} must be a string")
    if LOWER_SHA256.fullmatch(value["wheel_sha256"]) is None:
        _fail(code, "wheel_sha256 must be lowercase SHA-256")
    return value


def _wheel_distribution(filename: str) -> str | None:
    match = WHEEL_FILENAME.fullmatch(filename)
    if match is None:
        return None
    return re.sub(r"[-_.]+", "-", match.group("distribution")).lower()


def _validate_wheel(wheel: Path, provenance: dict[str, Any]) -> tuple[str, str]:
    if not wheel.is_absolute():
        _fail("clean_preflight.wheel.filename", "wheel path must be absolute")
    if wheel.exists() and (not wheel.is_file() or wheel.is_symlink()):
        _fail("clean_preflight.wheel.count", "wheel must be a regular file")
    if wheel.exists():
        distribution = _wheel_distribution(wheel.name)
        if distribution is None:
            _fail("clean_preflight.wheel.filename", "wheel filename is malformed")
        if distribution != "proofline":
            _fail("clean_preflight.wheel.alternate", "wheel distribution is not proofline")
    matching = [
        path
        for path in wheel.parent.iterdir()
        if path.is_file() and not path.is_symlink() and _wheel_distribution(path.name) == "proofline"
    ] if wheel.parent.is_dir() else []
    if len(matching) != 1 or not wheel.exists() or matching[0] != wheel:
        _fail("clean_preflight.wheel.count", "wheel parent must contain one exact proofline wheel")
    if provenance["wheel_filename"] != wheel.name:
        _fail("clean_preflight.wheel.filename", "provenance filename does not match wheel")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if provenance["wheel_sha256"] != digest:
        _fail("clean_preflight.wheel.digest", "provenance digest does not match wheel bytes")
    return wheel.name, digest


def _dependency_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _validate_dependency(record: Any) -> tuple[str, str]:
    code = "clean_preflight.plan.invalid"
    if not isinstance(record, dict):
        _fail(code, "dependency record must be an object")
    _exact_keys(record, DEPENDENCY_KEYS, code)
    name = record["name"]
    version = record["version"]
    if type(name) is not str or NORMALIZED_NAME.fullmatch(name) is None:
        _fail(code, "dependency name must be exact normalized text")
    if type(version) is not str:
        _fail(code, "dependency version must be a string")
    if IMMUTABLE_VERSION.fullmatch(version) is None:
        _fail("clean_preflight.version.mutable", "dependency version must be immutable")
    if (
        record["version_source"] != "uv.lock"
        or record["online_endpoint"] != "https://pypi.org/simple"
        or type(record["offline_wheelhouse_required"]) is not bool
        or record["offline_wheelhouse_required"] is not True
    ):
        _fail(code, "dependency source contract is invalid")
    return name, version


def _unbounded_token(token: str) -> bool:
    lowered = token.lower()
    return (
        any(character in token for character in "*?[];|&\n\r")
        or "sh -c" in lowered
        or "powershell -command" in lowered
        or "fallback" in lowered
        or "source build" in lowered
    )


def _validate_plan(value: dict[str, Any]) -> str:
    code = "clean_preflight.plan.invalid"
    _exact_keys(value, PLAN_KEYS, code)
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        _fail(code, "plan schema_version must be integer 1")
    if value["plan_id"] != "candidate-clean-runner-v1":
        _fail(code, "plan_id is invalid")
    dependencies = value["harness_dependencies"]
    if not isinstance(dependencies, list) or not dependencies:
        _fail(code, "harness_dependencies must be a nonempty array")
    declared: dict[str, str] = {}
    for record in dependencies:
        name, version = _validate_dependency(record)
        if name in declared:
            _fail(code, "dependency names must be unique")
        declared[name] = version
    platforms = value["platforms"]
    if not isinstance(platforms, dict) or set(platforms) != PLATFORMS:
        _fail(code, "platform set is not exact")
    for platform_name in sorted(PLATFORMS):
        platform = platforms[platform_name]
        if not isinstance(platform, dict) or set(platform) != {"steps"}:
            _fail(code, "platform schema is invalid")
        steps = platform["steps"]
        if not isinstance(steps, list) or len(steps) != len(STEP_ORDER):
            _fail(code, "platform steps are invalid")
        step_ids: list[str] = []
        for step in steps:
            if not isinstance(step, dict):
                _fail(code, "step must be an object")
            _exact_keys(step, STEP_KEYS, code)
            if type(step["step_id"]) is not str:
                _fail(code, "step_id must be a string")
            step_ids.append(step["step_id"])
            argv = step["argv"]
            if (
                not isinstance(argv, list)
                or not argv
                or any(type(token) is not str or not token for token in argv)
            ):
                _fail(code, "argv must be a nonempty string array")
            if any(_unbounded_token(token) for token in argv):
                _fail("clean_preflight.plan.unbounded", "argv contains unbounded execution")
            if step["publication_prerequisite"] != "none":
                _fail(
                    "clean_preflight.publication_prerequisite",
                    "preflight cannot require publication",
                )
            if step["network_mode"] not in {"none", "online-offline"}:
                _fail(code, "step network_mode is invalid")
            if step["endpoint"] not in {None, "https://pypi.org/simple"}:
                _fail(code, "step endpoint is invalid")
            if step["version_source"] not in {None, "uv.lock"}:
                _fail(code, "step version_source is invalid")
            for token in argv:
                requirement = PINNED_REQUIREMENT.fullmatch(token)
                if requirement is None:
                    continue
                name = _dependency_name(requirement.group(1))
                version = requirement.group(2)
                if declared.get(name) != version:
                    _fail(
                        "clean_preflight.dependency.undeclared",
                        "argv dependency is not exactly declared",
                    )
        if step_ids != STEP_ORDER:
            _fail(code, "platform step order is invalid")
    return value["plan_id"]


def validate_preflight_core(
    *, repo: Path, candidate: str, wheel: Path, provenance_path: Path, plan_path: Path
) -> dict[str, str]:
    provenance = _load_provenance(provenance_path)
    exact_candidate = _candidate_identity(repo, candidate, provenance["candidate_commit"])
    wheel_filename, wheel_sha256 = _validate_wheel(wheel, provenance)
    plan_id = _validate_plan(_strict_json(plan_path, "clean_preflight.plan.invalid"))
    return {
        "candidate_commit": exact_candidate,
        "wheel_filename": wheel_filename,
        "wheel_sha256": wheel_sha256,
        "plan_id": plan_id,
    }


def _plan_dependencies(plan_path: Path) -> list[tuple[str, str]]:
    plan = _strict_json(plan_path, "clean_preflight.plan.invalid")
    _validate_plan(plan)
    return [
        (record["name"], record["version"])
        for record in plan["harness_dependencies"]
    ]


def _offline_inventory(
    wheelhouse: Path | None, dependencies: list[tuple[str, str]]
) -> Path:
    if wheelhouse is None or not wheelhouse.is_absolute() or not wheelhouse.is_dir():
        _fail(
            "clean_preflight.dependency.missing_offline",
            "offline mode requires an absolute wheelhouse directory",
        )
    assert wheelhouse is not None
    available: set[tuple[str, str]] = set()
    for path in wheelhouse.iterdir():
        if not path.is_file() or path.is_symlink():
            continue
        match = WHEEL_FILENAME.fullmatch(path.name)
        if match is not None:
            available.add(
                (
                    _dependency_name(match.group("distribution")),
                    match.group("version").replace("_", "-"),
                )
            )
    missing = [
        f"{name}=={version}"
        for name, version in dependencies
        if (name, version) not in available
    ]
    if missing:
        _fail(
            "clean_preflight.dependency.missing_offline",
            f"offline wheelhouse is missing {missing[0]}",
        )
    return wheelhouse


def _clean_environment(cache: Path, home: Path, temp: Path) -> dict[str, str]:
    environment: dict[str, str] = {
        "PATH": os.defpath,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "TMPDIR": str(temp),
        "TEMP": str(temp),
        "TMP": str(temp),
        "UV_CACHE_DIR": str(cache),
        "UV_NO_CONFIG": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
        "LC_ALL": "C",
    }
    for key in ("SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR"):
        value = os.environ.get(key)
        if value is not None:
            environment[key] = value
    return environment


def _run_provision(argv: list[str], *, cwd: Path, environment: dict[str, str]) -> None:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValidationError(
            "clean_preflight.provision.failed", "provisioning command could not complete"
        ) from exc
    if completed.returncode != 0:
        if "clean_preflight.network.forbidden" in completed.stderr:
            _fail("clean_preflight.network.forbidden", "offline subprocess attempted network access")
        _fail("clean_preflight.provision.failed", "provisioning command returned nonzero")


def _provision_clean_environment(
    *,
    wheel: Path,
    dependencies: list[tuple[str, str]],
    network_mode: str,
    wheelhouse: Path | None,
    uv_executable: str,
) -> None:
    exact_wheelhouse = (
        _offline_inventory(wheelhouse, dependencies)
        if network_mode == "offline"
        else None
    )
    with tempfile.TemporaryDirectory(prefix="proofline-clean-runner-") as temporary:
        root = Path(temporary).resolve()
        cache = root / "uv-cache"
        environment_path = root / "environment"
        home = root / "home"
        work = root / "work"
        temp = root / "temp"
        for directory in (cache, home, work, temp):
            directory.mkdir()
        environment = _clean_environment(cache, home, temp)
        if network_mode == "offline":
            environment.update(
                {
                    "UV_OFFLINE": "1",
                    "HTTP_PROXY": "http://127.0.0.1:9",
                    "HTTPS_PROXY": "http://127.0.0.1:9",
                    "ALL_PROXY": "http://127.0.0.1:9",
                    "NO_PROXY": "",
                }
            )
        python = environment_path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        _run_provision(
            [uv_executable, "venv", "--python", "3.11", str(environment_path)],
            cwd=work,
            environment=environment,
        )
        _run_provision(
            [
                uv_executable,
                "pip",
                "install",
                "--python",
                str(python),
                "--no-deps",
                "--no-index",
                str(wheel),
            ],
            cwd=work,
            environment=environment,
        )
        requirements = [f"{name}=={version}" for name, version in dependencies]
        harness_argv = [
            uv_executable,
            "pip",
            "install",
            "--python",
            str(python),
        ]
        if network_mode == "online":
            harness_argv.extend(["--index-url", "https://pypi.org/simple"])
        else:
            assert exact_wheelhouse is not None
            harness_argv.extend(
                ["--offline", "--no-index", "--find-links", str(exact_wheelhouse)]
            )
        harness_argv.extend(requirements)
        _run_provision(harness_argv, cwd=work, environment=environment)


def run_clean_preflight(
    *,
    repo: Path,
    candidate: str,
    wheel: Path,
    provenance_path: Path,
    plan_path: Path,
    network_mode: str,
    wheelhouse: Path | None,
    uv_executable: str = "uv",
) -> dict[str, str]:
    identity = validate_preflight_core(
        repo=repo,
        candidate=candidate,
        wheel=wheel,
        provenance_path=provenance_path,
        plan_path=plan_path,
    )
    if network_mode not in {"online", "offline"}:
        _fail("clean_preflight.plan.invalid", "network mode is invalid")
    dependencies = _plan_dependencies(plan_path)
    _provision_clean_environment(
        wheel=wheel,
        dependencies=dependencies,
        network_mode=network_mode,
        wheelhouse=wheelhouse,
        uv_executable=uv_executable,
    )
    return {**identity, "network_mode": network_mode}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--network-mode", required=True, choices=("online", "offline"))
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument(
        "--plan",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "resources/candidate-clean-runner-plan-v1.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        identity = run_clean_preflight(
            repo=args.repo,
            candidate=args.candidate,
            wheel=args.wheel,
            provenance_path=args.provenance,
            plan_path=args.plan,
            network_mode=args.network_mode,
            wheelhouse=args.wheelhouse,
        )
    except ValidationError as exc:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "outcome": "fail",
                    "diagnostic_code": exc.code,
                    "candidate_commit": "",
                    "wheel_filename": "",
                    "wheel_sha256": "",
                    "network_mode": args.network_mode,
                    "plan_id": "candidate-clean-runner-v1",
                },
                separators=(",", ":"),
            )
        )
        print(exc.detail[:1024], file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "schema_version": 1,
                "outcome": "pass",
                "diagnostic_code": "clean_preflight.pass",
                **identity,
                "network_mode": args.network_mode,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
