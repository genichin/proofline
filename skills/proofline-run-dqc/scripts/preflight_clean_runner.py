#!/usr/bin/env python3
"""Strict identity, provenance, plan, and disposable provisioning preflight."""

from __future__ import annotations

import argparse
import ctypes
import enum
import hashlib
import json
import os
import queue
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, NoReturn


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
    "install-candidate",
    "provision-harness",
    "contract-probe",
]
_HARNESS_REQUIREMENTS = (
    "colorama==0.4.6",
    "iniconfig==2.3.0",
    "packaging==26.2",
    "pluggy==1.6.0",
    "pygments==2.20.0",
    "pytest==9.1.1",
)
_CANONICAL_STEP_ARGV = {
    platform: {
        "verify-wheel": (
            "proofline-clean-runner-internal", "verify-wheel", "{wheel}",
        ),
        "verify-checksum": (
            "proofline-clean-runner-internal", "verify-checksum", "{wheel}",
            "{wheel_sha256}",
        ),
        "create-environment": (
            "uv", "venv", "--python", "3.11", "{environment}",
        ),
        "install-candidate": (
            "uv", "pip", "install", "--python", "{python}", "--no-deps",
            "--no-index", "{wheel}",
        ),
        "provision-harness": (
            "uv", "pip", "install", "--python", "{python}", "--index-url",
            "https://pypi.org/simple", *_HARNESS_REQUIREMENTS,
        ),
        "contract-probe": (
            "{python}", "-m", "pytest", "-p", "no:cacheprovider",
            "{contract_probe}",
        ),
    }
    for platform in PLATFORMS
}
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


class ExecutionBudget:
    """One deadline and one output allowance shared by all child commands."""

    def __init__(self, seconds: float = 30.0, output_limit: int = 64 * 1024):
        self.deadline = time.monotonic() + seconds
        self.output_limit = output_limit
        self.output_used = 0

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())


class _ExecutionFailure(enum.Enum):
    TIMEOUT = "timeout"
    OUTPUT_LIMIT = "output_limit"


_EXECUTION_FAILURES = {
    _ExecutionFailure.TIMEOUT: (
        "clean_preflight.timeout",
        "provisioning deadline exceeded",
    ),
    _ExecutionFailure.OUTPUT_LIMIT: (
        "clean_preflight.output_limit",
        "provisioning output limit exceeded",
    ),
}


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
    if not repo.is_absolute():
        _fail("clean_preflight.candidate.mismatch", "repository must be an absolute directory")
    try:
        resolved_repo = repo.resolve(strict=True)
    except OSError as exc:
        raise ValidationError(
            "clean_preflight.candidate.mismatch",
            "repository must resolve as an absolute canonical directory",
        ) from exc
    if resolved_repo != repo or not repo.is_dir():
        _fail(
            "clean_preflight.candidate.mismatch",
            "repository must be an absolute canonical directory",
        )
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


def _digest_fd(fd: int) -> str:
    digest = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    os.lseek(fd, 0, os.SEEK_SET)
    return digest.hexdigest()


def _open_pinned_wheel(
    wheel: Path, provenance: dict[str, Any]
) -> tuple[int, tuple[int, int, int], str]:
    if os.name != "posix" or not Path("/proc/self/fd").is_dir():
        _fail("clean_preflight.provision.failed", "pinned wheel fd is unsupported")
    if not wheel.is_absolute():
        _fail("clean_preflight.wheel.filename", "wheel path must be absolute")
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
    if len(matching) != 1 or matching[0] != wheel:
        _fail("clean_preflight.wheel.count", "wheel parent must contain one exact proofline wheel")
    if provenance["wheel_filename"] != wheel.name:
        _fail("clean_preflight.wheel.filename", "provenance filename does not match wheel")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(wheel, flags)
    except OSError as exc:
        raise ValidationError("clean_preflight.wheel.count", "wheel open failed") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            _fail("clean_preflight.wheel.count", "wheel must be a regular file")
        identity = (metadata.st_dev, metadata.st_ino, metadata.st_size)
        digest = _digest_fd(fd)
        if provenance["wheel_sha256"] != digest:
            _fail("clean_preflight.wheel.digest", "provenance digest does not match wheel bytes")
        return fd, identity, digest
    except BaseException:
        os.close(fd)
        raise


def _wheel_path_matches(
    wheel: Path, identity: tuple[int, int, int], digest: str
) -> bool:
    try:
        metadata = wheel.lstat()
        if wheel.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            return False
        current = (metadata.st_dev, metadata.st_ino, metadata.st_size)
        if current != identity:
            return False
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        check_fd = os.open(wheel, flags)
        try:
            return _digest_fd(check_fd) == digest
        finally:
            os.close(check_fd)
    except OSError:
        return False


def _repository_source_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _verify_workflow_parity(root: Path, plan: dict[str, Any]) -> None:
    code = "clean_preflight.parity.workflow"
    order = ",".join(STEP_ORDER)
    workflow_markers = (
        f"PROOFLINE_CLEAN_RUNNER_PLAN_ID: {plan['plan_id']}",
        "PROOFLINE_CLEAN_RUNNER_PLAN_RESOURCE: skills/proofline-run-dqc/resources/candidate-clean-runner-plan-v1.json",
        "PROOFLINE_CLEAN_RUNNER_PACKAGED_PLAN_RESOURCE: proofline_home/skills/proofline-run-dqc/resources/candidate-clean-runner-plan-v1.json",
        f"PROOFLINE_CLEAN_RUNNER_STEP_ORDER: {order}",
        "PROOFLINE_CLEAN_RUNNER_ENDPOINT: https://pypi.org/simple",
        "PROOFLINE_CLEAN_RUNNER_VERSION_SOURCE: uv.lock",
        "PROOFLINE_CLEAN_RUNNER_NETWORK_MODE: online-offline",
        "PROOFLINE_CLEAN_RUNNER_PUBLICATION_PREREQUISITE: 'none'",
    )
    windows_markers = (
        f'$CleanRunnerPlanId = "{plan["plan_id"]}"',
        '$CleanRunnerPlanResource = "skills/proofline-run-dqc/resources/candidate-clean-runner-plan-v1.json"',
        '$CleanRunnerPlanOutcome = "contract_only"',
        '$CleanRunnerEndpoint = "https://pypi.org/simple"',
        '$CleanRunnerVersionSource = "uv.lock"',
        '$CleanRunnerNetworkMode = "online-offline"',
        '$CleanRunnerPublicationPrerequisite = "none"',
        *tuple(f'    "{step}"' for step in STEP_ORDER),
    )
    try:
        workflow = (root / ".github/workflows/candidate-verification.yml").read_text("utf-8")
        windows = (root / ".github/scripts/verify-windows-candidate.ps1").read_text("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValidationError(code, "workflow parity surfaces are unavailable") from exc
    if any(workflow.count(marker) != 1 for marker in workflow_markers) or any(
        windows.count(marker) != 1 for marker in windows_markers
    ):
        _fail(code, "workflow or Windows contract drifted from canonical plan")


def _verify_packaged_parity(fd: int, root: Path, _plan_path: Path) -> None:
    code = "clean_preflight.parity.packaged"
    members = {
        "proofline_home/skills/proofline-run-dqc/scripts/preflight_clean_runner.py": root
        / "skills/proofline-run-dqc/scripts/preflight_clean_runner.py",
        "proofline_home/skills/proofline-run-dqc/resources/candidate-clean-runner-plan-v1.json": root
        / "skills/proofline-run-dqc/resources/candidate-clean-runner-plan-v1.json",
    }
    try:
        with os.fdopen(os.dup(fd), "rb") as stream, zipfile.ZipFile(stream) as archive:
            for member, source in members.items():
                if archive.read(member) != source.read_bytes():
                    _fail(code, "packaged helper or plan bytes differ from source")
    except ValidationError:
        raise
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ValidationError(code, "packaged helper or plan is unavailable") from exc


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
            if tuple(argv) != _CANONICAL_STEP_ARGV[platform_name].get(step["step_id"]):
                _fail(code, "step argv differs from canonical platform template")
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


def _derive_offline_provision_argv(
    validated_online_argv: tuple[str, ...],
) -> tuple[str, ...]:
    expected = _CANONICAL_STEP_ARGV["ubuntu-python311"]["provision-harness"]
    if validated_online_argv != expected:
        _fail(
            "clean_preflight.plan.invalid",
            "offline provision argv must derive from the canonical online template",
        )
    marker = ("--index-url", "https://pypi.org/simple")
    index = validated_online_argv.index(marker[0])
    if validated_online_argv[index : index + len(marker)] != marker:
        _fail("clean_preflight.plan.invalid", "canonical online endpoint marker drifted")
    return (
        *validated_online_argv[:index],
        "--offline",
        "--no-index",
        "--find-links",
        "{wheelhouse}",
        *validated_online_argv[index + len(marker) :],
    )


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


def _resolve_uv_executable(value: str) -> str:
    candidate: Path | None = None
    requested = Path(value)
    if requested.is_absolute():
        candidate = requested
    elif requested.parent != Path("."):
        _fail("clean_preflight.provision.failed", "uv executable must not be relative")
    else:
        for entry in os.environ.get("PATH", "").split(os.pathsep):
            directory = Path(entry)
            if not directory.is_absolute():
                _fail("clean_preflight.provision.failed", "PATH entries must be absolute")
            possible = directory / value
            if possible.exists() or possible.is_symlink():
                candidate = possible
                break
    if candidate is None:
        _fail("clean_preflight.provision.failed", "uv executable was not found")
    assert candidate is not None
    try:
        metadata = candidate.lstat()
    except OSError as exc:
        raise ValidationError(
            "clean_preflight.provision.failed", "uv executable identity is unavailable"
        ) from exc
    if (
        candidate.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or not os.access(candidate, os.X_OK)
    ):
        _fail("clean_preflight.provision.failed", "uv must be one regular executable")
    canonical = candidate.resolve(strict=True)
    if not canonical.is_absolute() or canonical != candidate:
        _fail("clean_preflight.provision.failed", "uv executable is not canonical")
    return str(canonical)


def _linux_child_subreaper_state() -> int | None:
    if not sys.platform.startswith("linux"):
        return None
    try:
        prctl = ctypes.CDLL(None, use_errno=True).prctl
    except (AttributeError, OSError) as exc:
        raise OSError("Linux prctl is unavailable") from exc
    state = ctypes.c_int()
    if prctl(37, ctypes.byref(state), 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return state.value


def _set_linux_child_subreaper_state(value: int) -> None:
    if not sys.platform.startswith("linux"):
        return
    try:
        prctl = ctypes.CDLL(None, use_errno=True).prctl
    except (AttributeError, OSError) as exc:
        raise OSError("Linux prctl is unavailable") from exc
    if prctl(36, value, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        for sig, pause in ((signal.SIGTERM, 0.2), (signal.SIGKILL, 0.2)):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                break
            end = time.monotonic() + pause
            while process.poll() is None and time.monotonic() < end:
                time.sleep(0.01)
    elif process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            process.kill()
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=0.2)
    if os.name == "posix":
        while True:
            try:
                descendant, _ = os.waitpid(-process.pid, os.WNOHANG)
            except ChildProcessError:
                break
            if descendant == 0:
                break


def _run_provision_with_subreaper(
    argv: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    budget: ExecutionBudget,
    pass_fds: tuple[int, ...] = (),
) -> None:
    try:
        if pass_fds and os.name != "posix":
            _fail("clean_preflight.provision.failed", "pinned wheel fd is unsupported")
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=(os.name == "posix"),
            pass_fds=pass_fds,
        )
    except OSError as exc:
        raise ValidationError(
            "clean_preflight.provision.failed", "provisioning command could not complete"
        ) from exc
    assert process.stdout is not None and process.stderr is not None
    chunks: queue.Queue[tuple[str, bytes | None]] = queue.Queue(maxsize=4)
    stop_readers = threading.Event()

    def publish(item: tuple[str, bytes | None]) -> bool:
        while not stop_readers.is_set():
            try:
                chunks.put(item, timeout=0.02)
                return True
            except queue.Full:
                continue
        return False

    def read_stream(name: str, stream: Any) -> None:
        try:
            while not stop_readers.is_set():
                chunk = stream.read(4096)
                if not publish((name, chunk or None)) or not chunk:
                    return
        except OSError:
            publish((name, None))

    readers = [
        threading.Thread(target=read_stream, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=read_stream, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    stderr = bytearray()
    closed: set[str] = set()
    failure: _ExecutionFailure | None = None
    contained = False
    drain_deadline: float | None = None
    try:
        while len(closed) != 2:
            if failure is None and budget.remaining() <= 0:
                failure = _ExecutionFailure.TIMEOUT
            if (failure is not None or process.poll() is not None) and not contained:
                # The direct child may exit while descendants still own its pipes.
                # Contain the whole group first, then consume every queued/final byte.
                _stop_process_group(process)
                contained = True
                drain_deadline = time.monotonic() + 1.0
            if drain_deadline is not None and time.monotonic() >= drain_deadline:
                break
            try:
                wait = 0.05
                if drain_deadline is not None:
                    wait = min(wait, max(0.0, drain_deadline - time.monotonic()))
                else:
                    wait = min(wait, budget.remaining())
                name, chunk = chunks.get(timeout=wait)
            except queue.Empty:
                continue
            if chunk is None:
                closed.add(name)
                continue
            budget.output_used += len(chunk)
            if name == "stderr" and len(stderr) < 1024:
                stderr.extend(chunk[: 1024 - len(stderr)])
            if failure is None and budget.output_used > budget.output_limit:
                failure = _ExecutionFailure.OUTPUT_LIMIT
        returncode = process.poll()
        if not contained:
            _stop_process_group(process)
            returncode = process.returncode
        if failure is not None:
            code, detail = _EXECUTION_FAILURES[failure]
            _fail(code, detail)
        if returncode != 0:
            if b"clean_preflight.network.forbidden" in stderr:
                _fail("clean_preflight.network.forbidden", "offline subprocess attempted network access")
            _fail("clean_preflight.provision.failed", "provisioning command returned nonzero")
    finally:
        if process.poll() is None:
            _stop_process_group(process)
        stop_readers.set()
        process.stdout.close()
        process.stderr.close()
        for reader in readers:
            reader.join(timeout=0.2)


def _run_provision(
    argv: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    budget: ExecutionBudget,
    pass_fds: tuple[int, ...] = (),
) -> None:
    try:
        previous_subreaper = _linux_child_subreaper_state()
        if previous_subreaper == 0:
            _set_linux_child_subreaper_state(1)
    except OSError as exc:
        raise ValidationError(
            "clean_preflight.provision.failed", "child subreaper setup failed"
        ) from exc
    try:
        _run_provision_with_subreaper(
            argv,
            cwd=cwd,
            environment=environment,
            budget=budget,
            pass_fds=pass_fds,
        )
    finally:
        if previous_subreaper is not None:
            try:
                _set_linux_child_subreaper_state(previous_subreaper)
            except OSError as exc:
                raise ValidationError(
                    "clean_preflight.provision.failed", "child subreaper restore failed"
                ) from exc


def _provision_clean_environment(
    *,
    wheel_fd: int,
    wheel_filename: str,
    wheel_sha256: str,
    plan: dict[str, Any],
    network_mode: str,
    wheelhouse: Path | None,
    uv_executable: str,
    budget: ExecutionBudget,
) -> None:
    dependencies = [
        (record["name"], record["version"])
        for record in plan["harness_dependencies"]
    ]
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
        candidate_dir = root / "candidate"
        for directory in (cache, home, work, temp, candidate_dir):
            directory.mkdir()
        pinned_wheel = candidate_dir / wheel_filename
        pinned_wheel.symlink_to(f"/proc/self/fd/{wheel_fd}")
        contract_probe = work / "contract_probe.py"
        contract_probe.write_text(
            "import importlib.metadata\n\n"
            "def test_candidate_is_installed():\n"
            "    assert importlib.metadata.version('proofline')\n",
            encoding="utf-8",
        )
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
        substitutions: dict[str, str] = {
            "{wheel}": str(pinned_wheel),
            "{wheel_sha256}": wheel_sha256,
            "{environment}": str(environment_path),
            "{python}": str(python),
            "{contract_probe}": str(contract_probe),
        }
        if exact_wheelhouse is not None:
            substitutions["{wheelhouse}"] = str(exact_wheelhouse)
        steps = plan["platforms"]["ubuntu-python311"]["steps"]
        for step in steps:
            step_id: str = step["step_id"]
            template: tuple[str, ...] = tuple(step["argv"])
            if template != _CANONICAL_STEP_ARGV["ubuntu-python311"].get(step_id):
                _fail(
                    "clean_preflight.plan.invalid",
                    "execution step argv differs from validated canonical template",
                )
            if step_id == "provision-harness" and network_mode == "offline":
                template = _derive_offline_provision_argv(template)
            argv = [substitutions.get(token, token) for token in template]
            if argv[0] == "uv":
                argv[0] = uv_executable
            if step_id == "verify-wheel":
                if argv[:2] != ["proofline-clean-runner-internal", "verify-wheel"]:
                    _fail("clean_preflight.plan.invalid", "verify-wheel handler argv drifted")
                if pinned_wheel.name != wheel_filename or not pinned_wheel.is_symlink():
                    _fail("clean_preflight.wheel.filename", "pinned wheel name drifted")
                continue
            if step_id == "verify-checksum":
                if argv[:2] != ["proofline-clean-runner-internal", "verify-checksum"]:
                    _fail("clean_preflight.plan.invalid", "verify-checksum handler argv drifted")
                if _digest_fd(wheel_fd) != wheel_sha256:
                    _fail("clean_preflight.wheel.digest", "pinned wheel bytes drifted")
                continue
            if step_id == "install-candidate":
                _run_provision(
                    argv,
                    cwd=work,
                    environment=environment,
                    budget=budget,
                    pass_fds=(wheel_fd,),
                )
                continue
            _run_provision(argv, cwd=work, environment=environment, budget=budget)


def _path_snapshot(path: Path) -> tuple[tuple[str, int, bytes], ...]:
    records: list[tuple[str, int, bytes]] = []
    candidates = [path]
    if path.is_dir() and not path.is_symlink():
        candidates.extend(sorted(path.rglob("*")))
    for candidate in candidates:
        try:
            metadata = candidate.lstat()
        except OSError:
            records.append((str(candidate), -1, b""))
            continue
        payload = b""
        if candidate.is_symlink():
            payload = os.readlink(candidate).encode("utf-8", errors="surrogateescape")
        elif candidate.is_file():
            try:
                payload = candidate.read_bytes()
            except OSError:
                payload = b"<unreadable>"
        records.append((str(candidate), metadata.st_mode, payload))
    return tuple(records)


def _observable_snapshot(paths: list[Path]) -> tuple[tuple[str, tuple[tuple[str, int, bytes], ...]], ...]:
    unique = sorted({str(path): path for path in paths}.items())
    return tuple((name, _path_snapshot(path)) for name, path in unique)


def _required_git_output(repo: Path, *args: str) -> str:
    completed = _run_git(repo, *args)
    if completed.returncode != 0:
        _fail("clean_preflight.candidate.mismatch", "repository snapshot failed")
    return completed.stdout


def _git_repository_snapshot(repo: Path) -> tuple[str, ...]:
    git_dir = _required_git_output(repo, "rev-parse", "--absolute-git-dir").strip()
    common_dir = _required_git_output(
        repo, "rev-parse", "--path-format=absolute", "--git-common-dir"
    ).strip()
    index_path = Path(
        _required_git_output(
            repo, "rev-parse", "--path-format=absolute", "--git-path", "index"
        ).strip()
    )
    try:
        index_digest = hashlib.sha256(index_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValidationError(
            "clean_preflight.candidate.mismatch", "repository index snapshot failed"
        ) from exc
    symbolic = _run_git(repo, "symbolic-ref", "-q", "HEAD")
    if symbolic.returncode not in {0, 1}:
        _fail("clean_preflight.candidate.mismatch", "symbolic HEAD snapshot failed")
    refs = _required_git_output(
        repo, "for-each-ref", "--format=%(refname)%00%(objectname)"
    )
    status = _required_git_output(
        repo,
        "status",
        "--porcelain=v2",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    objects = _required_git_output(
        repo,
        "cat-file",
        "--batch-all-objects",
        "--batch-check=%(objectname) %(objecttype) %(objectsize)",
    )
    return (
        git_dir,
        common_dir,
        _required_git_output(repo, "rev-parse", "--verify", "HEAD").strip(),
        symbolic.stdout.strip(),
        hashlib.sha256(refs.encode()).hexdigest(),
        str(index_path),
        index_digest,
        hashlib.sha256(status.encode()).hexdigest(),
        hashlib.sha256(objects.encode()).hexdigest(),
    )


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
    provenance = _load_provenance(provenance_path)
    exact_candidate = _candidate_identity(repo, candidate, provenance["candidate_commit"])
    plan = _strict_json(plan_path, "clean_preflight.plan.invalid")
    plan_id = _validate_plan(plan)
    if network_mode not in {"online", "offline"}:
        _fail("clean_preflight.plan.invalid", "network mode is invalid")
    dependencies = [
        (record["name"], record["version"])
        for record in plan["harness_dependencies"]
    ]
    if network_mode == "offline":
        _offline_inventory(wheelhouse, dependencies)
    exact_uv = _resolve_uv_executable(uv_executable)
    wheel_fd, wheel_identity, wheel_sha256 = _open_pinned_wheel(wheel, provenance)
    identity = {
        "candidate_commit": exact_candidate,
        "wheel_filename": wheel.name,
        "wheel_sha256": wheel_sha256,
        "plan_id": plan_id,
    }
    try:
        source_root = _repository_source_root()
        _verify_workflow_parity(source_root, plan)
        _verify_packaged_parity(wheel_fd, source_root, plan_path)
        protected = [repo, wheel, provenance_path, plan_path]
        if wheelhouse is not None:
            protected.append(wheelhouse)
        before = _observable_snapshot(protected)
        git_before = _git_repository_snapshot(repo)
        primary: ValidationError | None = None
        try:
            _provision_clean_environment(
                wheel_fd=wheel_fd,
                wheel_filename=wheel.name,
                wheel_sha256=wheel_sha256,
                plan=plan,
                network_mode=network_mode,
                wheelhouse=wheelhouse,
                uv_executable=exact_uv,
                budget=ExecutionBudget(),
            )
        except ValidationError as exc:
            primary = exc
        mutated = _observable_snapshot(protected) != before
        try:
            mutated = mutated or _git_repository_snapshot(repo) != git_before
        except ValidationError:
            mutated = True
        mutated = mutated or not _wheel_path_matches(
            wheel, wheel_identity, wheel_sha256
        )
        mutated = mutated or _digest_fd(wheel_fd) != wheel_sha256
        if primary is not None:
            raise primary
        if mutated:
            _fail("clean_preflight.mutation", "protected observable state changed")
        return {**identity, "network_mode": network_mode}
    finally:
        os.close(wheel_fd)


class _OutcomeParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValidationError("clean_preflight.input.invalid", message)


def _parser() -> argparse.ArgumentParser:
    parser = _OutcomeParser(description=__doc__)
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


def _emit_outcome(value: dict[str, Any]) -> None:
    print(json.dumps(value, separators=(",", ":"), sort_keys=True))


def _bounded_stderr(detail: str) -> None:
    single_line = " ".join(detail.splitlines())
    payload = single_line.encode("utf-8", errors="replace")[:1023]
    print(payload.decode("utf-8", errors="ignore"), file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args: argparse.Namespace | None = None
    try:
        args = _parser().parse_args(argv)
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
        _emit_outcome(
            {
                "schema_version": 1,
                "outcome": "fail",
                "diagnostic_code": exc.code,
                "candidate_commit": "",
                "wheel_filename": "",
                "wheel_sha256": "",
                "network_mode": "" if args is None else args.network_mode,
                "plan_id": "",
            }
        )
        _bounded_stderr(exc.detail)
        return 1
    _emit_outcome(
        {
            "schema_version": 1,
            "outcome": "pass",
            "diagnostic_code": "clean_preflight.pass",
            **identity,
            "network_mode": args.network_mode,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
