from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol
from urllib import request
from urllib.parse import unquote, urlparse

from proofline.agent_skills import (
    BLOCKED_STATES,
    AgentSkillError,
    SkillPayload,
    inspect_registry,
    load_packaged_payload,
    sync_registered,
)

REPOSITORY = "genichin/proofline"
API_ROOT = f"https://api.github.com/repos/{REPOSITORY}/releases"
DOWNLOAD_ROOT = f"https://github.com/{REPOSITORY}/releases/download"
_VERSION = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Release:
    version: str
    wheel_name: str
    wheel_url: str
    checksum_url: str


@dataclass(frozen=True)
class UpdateResult:
    current: str
    target: str
    provenance: str
    status: str
    exit_code: int
    mutate: bool


class DistributionLike(Protocol):
    def read_text(self, filename: str) -> str | None: ...


def parse_version(value: str) -> tuple[int, int, int]:
    if not _VERSION.fullmatch(value):
        raise UpdateError(f"invalid stable version: {value}")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def parse_release(payload: dict[str, Any], expected_version: str) -> Release:
    parse_version(expected_version)
    if payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise UpdateError("release is not stable")
    if payload.get("tag_name") != f"v{expected_version}":
        raise UpdateError("release tag does not match target version")
    wheel = f"proofline-{expected_version}-py3-none-any.whl"
    expected = {
        wheel: f"{DOWNLOAD_ROOT}/v{expected_version}/{wheel}",
        "SHA256SUMS": f"{DOWNLOAD_ROOT}/v{expected_version}/SHA256SUMS",
    }
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("release assets are missing")
    observed: dict[str, str] = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str) or not isinstance(asset.get("browser_download_url"), str):
            raise UpdateError("release asset metadata is invalid")
        observed[asset["name"]] = asset["browser_download_url"]
    if set(observed) != set(expected):
        raise UpdateError("release asset allowlist mismatch")
    if observed != expected:
        raise UpdateError("release asset URL mismatch")
    return Release(expected_version, wheel, observed[wheel], observed["SHA256SUMS"])


def parse_checksum(text: str, wheel_name: str) -> str:
    lines = [line for line in text.splitlines() if line]
    if len(lines) != 1:
        raise UpdateError("checksum file must contain exactly one entry")
    match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", lines[0])
    if not match or match.group(2) != wheel_name:
        raise UpdateError("checksum filename mismatch")
    return match.group(1)


def detect_provenance(distribution: DistributionLike) -> str:
    raw = distribution.read_text("direct_url.json")
    if not raw:
        return "unknown"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return "unknown"
    if isinstance(value, dict) and "dir_info" in value:
        return "source"
    if isinstance(value, dict) and "archive_info" in value:
        return "archive"
    return "unknown"


def decide_update(current: str, target: str, provenance: str, *, check: bool, adopt: bool) -> UpdateResult:
    current_key = parse_version(current)
    target_key = parse_version(target)
    if target_key < current_key:
        raise UpdateError(f"downgrade is not allowed: {current} -> {target}")
    if provenance == "unknown":
        raise UpdateError("installation provenance is unknown")
    if provenance == "source" and not adopt:
        return UpdateResult(current, target, provenance, "adoption-required", 1, False)
    if current_key == target_key and not (provenance == "source" and adopt):
        return UpdateResult(current, target, provenance, "already-current", 0, False)
    if check:
        return UpdateResult(current, target, provenance, "update-available", 0, False)
    return UpdateResult(current, target, provenance, "updated", 0, True)


def _get_json(url: str) -> dict[str, Any]:
    try:
        req = request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "proofline-updater"})
        with request.urlopen(req, timeout=30) as response:
            value = json.load(response)
    except Exception as exc:
        raise UpdateError(f"release lookup failed: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise UpdateError("release response is invalid")
    return value


def discover_release(version: str | None) -> Release:
    if version is None:
        payload = _get_json(f"{API_ROOT}/latest")
        tag = payload.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith("v"):
            raise UpdateError("latest release tag is invalid")
        version = tag[1:]
    else:
        parse_version(version)
        payload = _get_json(f"{API_ROOT}/tags/v{version}")
    return parse_release(payload, version)


def _download(url: str, destination: Path) -> None:
    try:
        req = request.Request(url, headers={"User-Agent": "proofline-updater"})
        with request.urlopen(req, timeout=60) as response:
            destination.write_bytes(response.read())
    except Exception as exc:
        raise UpdateError(f"asset download failed: {type(exc).__name__}") from exc


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def _uv_tool_paths(uv: str, cwd: Path) -> tuple[Path, Path]:
    tool = _run([uv, "tool", "dir"], cwd=cwd)
    bins = _run([uv, "tool", "dir", "--bin"], cwd=cwd)
    if tool.returncode or bins.returncode:
        raise UpdateError("uv tool directory lookup failed")
    return Path(tool.stdout.strip()).resolve(), Path(bins.stdout.strip()).resolve()


def _download_verified(release: Release, root: Path) -> Path:
    directory = root / release.version
    directory.mkdir()
    wheel = directory / release.wheel_name
    checksum = directory / "SHA256SUMS"
    _download(release.checksum_url, checksum)
    _download(release.wheel_url, wheel)
    expected_digest = parse_checksum(checksum.read_text(), release.wheel_name)
    actual_digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if not _SHA256.fullmatch(actual_digest) or actual_digest != expected_digest:
        raise UpdateError("wheel checksum mismatch")
    return wheel


def _source_rollback_path(distribution: DistributionLike) -> Path:
    raw = distribution.read_text("direct_url.json")
    try:
        value = json.loads(raw or "")
        url = value["url"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise UpdateError("source rollback provenance is invalid") from exc
    parsed = urlparse(url)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise UpdateError("source rollback path is not local")
    path = Path(unquote(parsed.path))
    if not path.is_dir():
        raise UpdateError("source rollback path is unavailable")
    return path


def _install(uv: str, artifact: Path, *, cwd: Path) -> None:
    installed = _run([uv, "tool", "install", "--force", "--no-config", str(artifact)], cwd=cwd)
    if installed.returncode:
        raise UpdateError("uv tool installation failed")


def _verify_install(version: str, expected_env: Path, executable: Path, *, cwd: Path) -> None:
    tool_python = expected_env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    probe = _run(
        [str(tool_python), "-I", "-c", "from importlib.metadata import version; from pathlib import Path; import proofline; print(version('proofline')); print(Path(proofline.__file__).resolve())"],
        cwd=cwd,
    )
    cli = _run([str(executable), "--version"], cwd=cwd)
    lines = probe.stdout.splitlines()
    if probe.returncode or len(lines) != 2 or lines[0] != version or "site-packages" not in Path(lines[1]).parts:
        raise UpdateError("installed package post-verification failed")
    if cli.returncode or cli.stdout.strip() != f"proofline {version}":
        raise UpdateError("installed console post-verification failed")


def is_uv_tool_process(tool_dir: Path, *, prefix: Path | None = None) -> bool:
    active_prefix = Path(sys.prefix) if prefix is None else prefix
    return active_prefix.absolute() == (tool_dir / "proofline").absolute()


def _require_supported_predecessor(current: str, target: str) -> None:
    if current == "0.7.0" and current != target:
        raise UpdateError("v0.7.0 requires the HOME-retirement exact-tag installer; self-update is unsupported")


def skill_payload_from_wheel(wheel: Path, version: str) -> SkillPayload:
    prefix = "skills/"
    files: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(wheel) as archive:
            for info in archive.infolist():
                if not info.filename.startswith(prefix) or info.is_dir():
                    continue
                relative = info.filename.removeprefix(prefix)
                path = Path(relative)
                if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
                    raise UpdateError("target wheel contains an unsafe skill path")
                if not path.parts[0].startswith("proofline-") or relative in files:
                    raise UpdateError("target wheel skill inventory is invalid")
                files[relative] = archive.read(info)
    except (OSError, zipfile.BadZipFile) as exc:
        raise UpdateError("target wheel skill payload is unavailable") from exc
    names = {Path(relative).parts[0] for relative in files}
    if not names or any(f"{name}/SKILL.md" not in files for name in names):
        raise UpdateError("target wheel skill payload is incomplete")
    digest = hashlib.sha256()
    for relative, content in sorted(files.items()):
        digest.update(relative.encode())
        digest.update(b"\0file\0")
        digest.update(hashlib.sha256(content).digest())
    payload_digest = digest.hexdigest()
    wheel_digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    return SkillPayload(version, f"wheel:{version}:{wheel_digest}:{payload_digest}", files, payload_digest)


def run_update(
    *,
    check: bool = False,
    version: str | None = None,
    adopt: bool = False,
    no_sync_agent_skills: bool = False,
) -> UpdateResult:
    current = metadata.version("proofline")
    distribution = metadata.distribution("proofline")
    provenance = detect_provenance(distribution)
    current_payload = load_packaged_payload()
    inspections = inspect_registry(payload=current_payload)
    blocked = [item for item in inspections if item.status in BLOCKED_STATES]
    if version is not None and parse_version(version) == parse_version(current):
        decision = decide_update(current, version, provenance, check=check, adopt=adopt)
        if blocked and check:
            return UpdateResult(
                current,
                version,
                provenance,
                "agent-skills-blocked",
                1,
                False,
            )
        if decision.status == "already-current" and not blocked:
            return decision
    release = discover_release(version)
    decision = decide_update(current, release.version, provenance, check=check, adopt=adopt)
    _require_supported_predecessor(current, release.version)
    if blocked and check:
        return UpdateResult(
            current,
            release.version,
            provenance,
            "agent-skills-blocked",
            1,
            False,
        )
    if blocked and not no_sync_agent_skills:
        first = blocked[0]
        raise UpdateError(
            "agent skill synchronization blocked by "
            f"{first.agent}/{first.scope}: {first.status}; "
            "use --no-sync-agent-skills for a package-only update"
        )
    if check or not decision.mutate:
        return decision
    uv = shutil.which("uv")
    if uv is None:
        raise UpdateError("uv executable is required")
    with tempfile.TemporaryDirectory(prefix="proofline-update-") as temporary:
        temp = Path(temporary)
        tool_dir, bin_dir = _uv_tool_paths(uv, temp)
        expected_env = (tool_dir / "proofline").absolute()
        if not is_uv_tool_process(tool_dir):
            raise UpdateError("current process is not owned by the ProofLine uv tool environment")
        wheel = _download_verified(release, temp)
        target_payload = skill_payload_from_wheel(wheel, release.version)
        rollback_artifact = _download_verified(discover_release(current), temp) if provenance == "archive" else _source_rollback_path(distribution)
        executable = bin_dir / ("proofline.exe" if sys.platform == "win32" else "proofline")
        try:
            _install(uv, wheel, cwd=temp)
            _verify_install(release.version, expected_env, executable, cwd=temp)
            if not no_sync_agent_skills and inspections:
                sync_registered(target_payload)
        except (UpdateError, AgentSkillError, OSError) as exc:
            try:
                _install(uv, rollback_artifact, cwd=temp)
                _verify_install(current, expected_env, executable, cwd=temp)
            except (UpdateError, OSError) as rollback_exc:
                raise UpdateError(f"update failed: {exc}; package rollback failed: {rollback_exc}") from exc
            raise UpdateError(f"update transaction failed: {exc}") from exc
    if no_sync_agent_skills and blocked:
        return UpdateResult(current, release.version, provenance, "skipped-with-issues", 0, True)
    return decision
