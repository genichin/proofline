"""Copy-only installation lifecycle for ProofLine agent skills."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import metadata, resources
from pathlib import Path, PurePosixPath
from typing import Any, Self

import yaml

from proofline.yaml_strict import safe_load_unique

SCHEMA_VERSION = 1
STATUS_SCHEMA_VERSION = 1
OWNER = "proofline"
BLOCKED_STATES = frozenset(
    {"drifted", "missing", "conflict", "invalid-manifest", "unsupported"}
)
ALL_STATES = frozenset({"healthy", "outdated", *BLOCKED_STATES})
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
_MANIFEST_FIELDS = {
    "schema_version",
    "owner",
    "agent",
    "scope",
    "layout",
    "target_root",
    "proofline_version",
    "source_identity",
    "payload_sha256",
    "files",
    "created_containers",
}
_FILE_FIELDS = {"path", "type", "sha256"}


class AgentSkillError(RuntimeError):
    pass


@dataclass(frozen=True)
class SkillPayload:
    version: str
    source_identity: str
    files: dict[str, bytes]
    digest: str


@dataclass(frozen=True)
class AgentTarget:
    agent: str
    scope: str
    layout: str
    root: Path


@dataclass(frozen=True)
class Inspection:
    agent: str
    scope: str
    target_root: str | None
    installed_version: str | None
    status: str
    details: tuple[str, ...]
    manifest_path: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "scope": self.scope,
            "target_root": self.target_root,
            "installed_version": self.installed_version,
            "status": self.status,
            "details": list(self.details),
            "manifest_path": self.manifest_path,
        }


def state_root(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    if os.name == "nt":
        base = env.get("LOCALAPPDATA")
        if not base:
            raise AgentSkillError("LOCALAPPDATA is required for agent skill state")
        return Path(base).expanduser().absolute() / "ProofLine/State/agent-skills"
    base = env.get("XDG_STATE_HOME")
    if base:
        return Path(base).expanduser().absolute() / "proofline/agent-skills"
    home = env.get("HOME")
    if not home:
        raise AgentSkillError("HOME is required for agent skill state")
    return Path(home).expanduser().absolute() / ".local/state/proofline/agent-skills"


def _safe_identifier(value: str, label: str) -> str:
    if not _SAFE_ID.fullmatch(value) or value.casefold().split(".", 1)[0] in _WINDOWS_RESERVED:
        raise AgentSkillError(f"unsafe {label}: {value!r}")
    return value


def registration_path(agent: str, scope: str, root: Path | None = None) -> Path:
    agent = _safe_identifier(agent, "agent")
    scope = _safe_identifier(scope, "scope")
    if agent not in {"hermes", "codex"}:
        raise AgentSkillError(f"unsupported agent: {agent}")
    return (state_root() if root is None else root) / agent / f"{scope}.yaml"


def _check_case_collision(directory: Path, name: str) -> None:
    if not directory.exists():
        return
    matches = [entry.name for entry in directory.iterdir() if entry.name.casefold() == name.casefold()]
    if matches and matches != [name]:
        raise AgentSkillError(f"case-fold registration collision: {name}")


def _is_reparse(state: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(state.st_mode) or bool(getattr(state, "st_file_attributes", 0) & flag)


def _assert_plain_parents(path: Path, *, stop: Path | None = None) -> None:
    current = path
    boundary = None if stop is None else stop.absolute()
    while True:
        if current.exists() or current.is_symlink():
            state = current.stat(follow_symlinks=False)
            if _is_reparse(state) or not stat.S_ISDIR(state.st_mode):
                raise AgentSkillError(f"unsafe directory component: {current}")
        if current.parent == current or (boundary is not None and current == boundary):
            return
        current = current.parent


def _payload_digest(files: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for relative, content in sorted(files.items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0file\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def load_packaged_payload() -> SkillPayload:
    try:
        version = metadata.version("proofline")
        distribution = metadata.distribution("proofline")
        root = resources.files("skills")
    except (metadata.PackageNotFoundError, ModuleNotFoundError) as exc:
        raise AgentSkillError("installed ProofLine package identity is unavailable") from exc
    files: dict[str, bytes] = {}
    try:
        skill_directories = sorted(
            entry for entry in root.iterdir() if entry.name.startswith("proofline-")
        )
        for skill in skill_directories:
            if not skill.is_dir():
                raise AgentSkillError(f"unsafe packaged skill entry: {skill.name}")
            pending = [(skill, PurePosixPath(skill.name))]
            while pending:
                directory, prefix = pending.pop()
                for entry in sorted(directory.iterdir(), key=lambda item: item.name):
                    relative = prefix / entry.name
                    if entry.is_dir():
                        pending.append((entry, relative))
                    elif entry.is_file():
                        files[relative.as_posix()] = entry.read_bytes()
                    else:
                        raise AgentSkillError(f"unsafe packaged skill path: {relative}")
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise AgentSkillError("packaged skill resources are unavailable") from exc
    if not files or not skill_directories:
        raise AgentSkillError("packaged skill inventory is empty")
    for skill in skill_directories:
        if f"{skill.name}/SKILL.md" not in files:
            raise AgentSkillError(f"packaged skill is incomplete: {skill.name}")
    source = distribution.read_text("direct_url.json")
    provenance = "installed-distribution"
    if source:
        try:
            direct = json.loads(source)
            provenance = "archive" if "archive_info" in direct else "source" if "dir_info" in direct else provenance
        except (json.JSONDecodeError, TypeError):
            pass
    identity = f"package:{version}:{provenance}:{_payload_digest(files)}"
    return SkillPayload(version, identity, files, _payload_digest(files))


def _hermes_config_path(scope: str, environ: Mapping[str, str]) -> Path:
    executable = shutil.which("hermes", path=environ.get("PATH"))
    if executable is None:
        raise AgentSkillError("Hermes executable is unavailable")
    command = [executable]
    if scope != "default":
        command.extend(["--profile", scope])
    command.extend(["config", "path"])
    completed = subprocess.run(command, env=dict(environ), text=True, capture_output=True, check=False)
    if completed.returncode or not completed.stdout.strip() or "\n" in completed.stdout.strip():
        raise AgentSkillError(f"Hermes profile is unavailable: {scope}")
    path = Path(completed.stdout.strip()).expanduser()
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise AgentSkillError("Hermes config path is unsafe")
    return path.absolute()


def _configured_skill_root(config_path: Path) -> Path:
    try:
        value = safe_load_unique(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise AgentSkillError("Hermes config is invalid") from exc
    candidates: list[str] = []
    if isinstance(value, dict):
        for key in ("skills_root", "skills_dir"):
            if isinstance(value.get(key), str):
                candidates.append(value[key])
        skills = value.get("skills")
        if isinstance(skills, dict):
            for key in ("root", "directory", "path"):
                if isinstance(skills.get(key), str):
                    candidates.append(skills[key])
    if len(set(candidates)) > 1:
        raise AgentSkillError("Hermes skill root is ambiguous")
    root = Path(candidates[0]).expanduser() if candidates else config_path.parent / "skills"
    if not root.is_absolute():
        root = config_path.parent / root
    return root.absolute()


def resolve_target(
    agent: str, scope: str | None = None, environ: Mapping[str, str] | None = None
) -> AgentTarget:
    env = os.environ if environ is None else environ
    agent = _safe_identifier(agent, "agent")
    if agent == "codex":
        selected = "user" if scope is None else _safe_identifier(scope, "scope")
        if selected != "user":
            raise AgentSkillError(f"unsupported Codex scope: {selected}")
        if shutil.which("codex", path=env.get("PATH")) is None:
            raise AgentSkillError("Codex executable is unavailable")
        home = env.get("HOME") or env.get("USERPROFILE")
        if not home:
            raise AgentSkillError("user home is unavailable")
        root = Path(home).expanduser().absolute() / ".agents/skills"
        _assert_plain_parents(root)
        return AgentTarget(agent, selected, "flat", root)
    if agent == "hermes":
        selected = "default" if scope is None else _safe_identifier(scope, "profile")
        config = _hermes_config_path(selected, env)
        root = _configured_skill_root(config) / "proofline"
        _assert_plain_parents(root)
        return AgentTarget(agent, selected, "grouped", root)
    raise AgentSkillError(f"unsupported agent: {agent}")


def _managed_files(payload: SkillPayload, target: AgentTarget) -> dict[str, bytes]:
    # Both target roots already represent the adapter-specific placement boundary.
    return dict(payload.files)


def _manifest(payload: SkillPayload, target: AgentTarget, created: Iterable[str]) -> dict[str, Any]:
    files = _managed_files(payload, target)
    return {
        "schema_version": SCHEMA_VERSION,
        "owner": OWNER,
        "agent": target.agent,
        "scope": target.scope,
        "layout": target.layout,
        "target_root": str(target.root.absolute()),
        "proofline_version": payload.version,
        "source_identity": payload.source_identity,
        "payload_sha256": payload.digest,
        "files": [
            {"path": path, "type": "file", "sha256": hashlib.sha256(content).hexdigest()}
            for path, content in sorted(files.items())
        ],
        "created_containers": sorted(created),
    }


def _encode_manifest(value: dict[str, Any]) -> bytes:
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True).encode("utf-8")


def _load_manifest_bytes(data: bytes) -> dict[str, Any]:
    try:
        value = safe_load_unique(data.decode("utf-8"))
    except (UnicodeError, yaml.YAMLError) as exc:
        raise AgentSkillError("invalid manifest YAML") from exc
    if not isinstance(value, dict) or set(value) != _MANIFEST_FIELDS:
        raise AgentSkillError("invalid manifest fields")
    if value["schema_version"] != SCHEMA_VERSION or value["owner"] != OWNER:
        raise AgentSkillError("unsupported manifest schema or owner")
    agent = _safe_identifier(value["agent"], "agent") if isinstance(value["agent"], str) else ""
    scope = _safe_identifier(value["scope"], "scope") if isinstance(value["scope"], str) else ""
    expected_layout = {"hermes": "grouped", "codex": "flat"}.get(agent)
    if not scope or value["layout"] != expected_layout:
        raise AgentSkillError("invalid manifest installation identity")
    target = value["target_root"]
    if not isinstance(target, str) or not Path(target).is_absolute() or Path(target) != Path(target).absolute():
        raise AgentSkillError("invalid manifest target root")
    for key in ("proofline_version", "source_identity"):
        if not isinstance(value[key], str) or not value[key]:
            raise AgentSkillError(f"invalid manifest {key}")
    if not isinstance(value["payload_sha256"], str) or not _SHA256.fullmatch(value["payload_sha256"]):
        raise AgentSkillError("invalid manifest payload digest")
    records = value["files"]
    if not isinstance(records, list) or not records:
        raise AgentSkillError("invalid manifest file inventory")
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != _FILE_FIELDS:
            raise AgentSkillError("invalid manifest file record")
        relative = record["path"]
        path = PurePosixPath(relative) if isinstance(relative, str) else PurePosixPath(".")
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            raise AgentSkillError("unsafe manifest file path")
        folded = relative.casefold()
        if folded in seen or record["type"] != "file" or not isinstance(record["sha256"], str) or not _SHA256.fullmatch(record["sha256"]):
            raise AgentSkillError("invalid manifest file record")
        seen.add(folded)
    containers = value["created_containers"]
    if not isinstance(containers, list) or any(
        not isinstance(item, str) or item not in {".", *(part.split("/", 1)[0] for part in (record["path"] for record in records))}
        for item in containers
    ):
        raise AgentSkillError("invalid manifest container ownership")
    return value


def _read_manifest(path: Path) -> dict[str, Any]:
    state = path.stat(follow_symlinks=False)
    if _is_reparse(state) or not stat.S_ISREG(state.st_mode):
        raise AgentSkillError("manifest is not a regular file")
    return _load_manifest_bytes(path.read_bytes())


def _actual_inventory(
    root: Path, managed_roots: set[str] | None = None
) -> tuple[dict[str, str], list[str]]:
    files: dict[str, str] = {}
    issues: list[str] = []
    if not root.exists() and not root.is_symlink():
        return files, issues
    root_state = root.stat(follow_symlinks=False)
    if _is_reparse(root_state) or not stat.S_ISDIR(root_state.st_mode):
        return files, [f"unsupported target root: {root}"]
    for directory, names, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        if base == root and managed_roots is not None:
            names[:] = [name for name in names if name in managed_roots]
            filenames = [name for name in filenames if name in managed_roots]
        for name in list(names):
            path = base / name
            state = path.stat(follow_symlinks=False)
            if _is_reparse(state) or not stat.S_ISDIR(state.st_mode):
                issues.append(f"unsupported path: {path.relative_to(root).as_posix()}")
                names.remove(name)
        for name in filenames:
            path = base / name
            relative = path.relative_to(root).as_posix()
            state = path.stat(follow_symlinks=False)
            if _is_reparse(state) or not stat.S_ISREG(state.st_mode) or state.st_nlink != 1:
                issues.append(f"unsupported path: {relative}")
                continue
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files, issues


def inspect_manifest(path: Path, payload: SkillPayload | None = None) -> Inspection:
    agent = path.parent.name
    scope = path.stem
    try:
        manifest = _read_manifest(path)
        agent = manifest["agent"]
        scope = manifest["scope"]
    except (AgentSkillError, OSError) as exc:
        return Inspection(agent, scope, None, None, "invalid-manifest", (str(exc),), str(path))
    target = Path(manifest["target_root"])
    expected = {record["path"]: record["sha256"] for record in manifest["files"]}
    managed_roots = (
        {PurePosixPath(path).parts[0] for path in expected}
        if manifest["layout"] == "flat"
        else None
    )
    actual, unsafe = _actual_inventory(target, managed_roots)
    details = list(unsafe)
    if unsafe:
        status = "unsupported"
    elif not target.exists() or any(path not in actual for path in expected):
        missing = sorted(set(expected) - set(actual))
        details.extend(f"missing: {item}" for item in missing)
        status = "missing"
    elif set(actual) != set(expected):
        details.extend(f"unexpected: {item}" for item in sorted(set(actual) - set(expected)))
        status = "conflict"
    elif any(actual[path] != digest for path, digest in expected.items()):
        details.extend(f"hash mismatch: {path}" for path, digest in expected.items() if actual[path] != digest)
        status = "drifted"
    elif payload is not None and manifest["payload_sha256"] == payload.digest and manifest["proofline_version"] == payload.version:
        status = "healthy"
    elif payload is not None:
        status = "outdated"
    else:
        status = "healthy"
    return Inspection(agent, scope, str(target), manifest["proofline_version"], status, tuple(details), str(path))


def inspect_registry(
    *, root: Path | None = None, payload: SkillPayload | None = None, agent: str | None = None, scope: str | None = None
) -> list[Inspection]:
    registry = state_root() if root is None else root
    if not registry.exists():
        return []
    if registry.is_symlink() or not registry.is_dir():
        return [Inspection("?", "?", None, None, "invalid-manifest", ("registry root is unsafe",), str(registry))]
    paths = sorted(registry.glob("*/*.yaml"), key=lambda item: item.as_posix().casefold())
    results = [inspect_manifest(path, payload) for path in paths]
    if agent is not None:
        results = [item for item in results if item.agent == agent]
    if scope is not None:
        results = [item for item in results if item.scope == scope]
    return sorted(results, key=lambda item: (item.agent.casefold(), item.scope.casefold()))


def summarize(inspections: Iterable[Inspection]) -> dict[str, int]:
    items = list(inspections)
    return {
        "registered": len(items),
        "healthy": sum(item.status == "healthy" for item in items),
        "outdated": sum(item.status == "outdated" for item in items),
        "blocked": sum(item.status in BLOCKED_STATES for item in items),
    }


class _Lock:
    def __init__(self, root: Path) -> None:
        self.path = root / ".lock"
        self.fd: int | None = None

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _assert_plain_parents(self.path.parent)
        try:
            self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise AgentSkillError("agent skill state is locked") from exc
        os.write(self.fd, f"{os.getpid()}\n".encode())
        os.fsync(self.fd)
        return self

    def __exit__(self, *args: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _check_case_collision(path.parent, path.name)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if hasattr(os, "O_DIRECTORY"):
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        try:
            Path(temporary).unlink()
        except FileNotFoundError:
            pass


def _journal(root: Path, operation: str, path: Path) -> Path:
    journal = root / "journal.json"
    _atomic_write(journal, json.dumps({"schema_version": 1, "operation": operation, "manifest": str(path)}, sort_keys=True).encode() + b"\n")
    return journal


def interrupted_transaction(root: Path | None = None) -> bool:
    return ((state_root() if root is None else root) / "journal.json").exists()


def _require_no_journal(root: Path) -> None:
    if interrupted_transaction(root):
        raise AgentSkillError("interrupted agent skill transaction requires recovery")


def _write_stage(stage: Path, files: Mapping[str, bytes]) -> None:
    for relative, content in files.items():
        path = stage.joinpath(*PurePosixPath(relative).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    actual, issues = _actual_inventory(stage)
    expected = {path: hashlib.sha256(content).hexdigest() for path, content in files.items()}
    if issues or actual != expected:
        raise AgentSkillError("staged skill payload verification failed")


def _commit_stage(stage: Path, target: AgentTarget) -> None:
    if target.layout == "grouped":
        os.replace(stage, target.root)
        return
    target.root.mkdir(parents=True, exist_ok=True)
    for skill in sorted(entry for entry in stage.iterdir() if entry.is_dir()):
        os.replace(skill, target.root / skill.name)
    stage.rmdir()


def _remove_managed_target(target: AgentTarget, roots: Iterable[str]) -> None:
    if target.layout == "grouped":
        shutil.rmtree(target.root)
        return
    for name in sorted(set(roots)):
        shutil.rmtree(target.root / name)


def setup(agent: str, scope: str | None = None, *, adopt_existing: bool = False) -> Inspection:
    payload = load_packaged_payload()
    target = resolve_target(agent, scope)
    manifest_path = registration_path(target.agent, target.scope)
    registry = state_root()
    with _Lock(registry):
        _require_no_journal(registry)
        if manifest_path.exists() or manifest_path.is_symlink():
            current = inspect_manifest(manifest_path, payload)
            if current.status == "healthy":
                return current
            raise AgentSkillError(f"registered installation is {current.status}")
        files = _managed_files(payload, target)
        managed_roots = {PurePosixPath(path).parts[0] for path in files}
        actual, issues = _actual_inventory(
            target.root, managed_roots if target.layout == "flat" else None
        )
        expected = {path: hashlib.sha256(content).hexdigest() for path, content in files.items()}
        if issues:
            raise AgentSkillError(issues[0])
        exists = (
            target.root.exists() or target.root.is_symlink()
            if target.layout == "grouped"
            else any((target.root / name).exists() or (target.root / name).is_symlink() for name in managed_roots)
        )
        if exists:
            if not adopt_existing:
                raise AgentSkillError("unmanaged target collision; use --adopt-existing only for exact bytes")
            if actual != expected:
                raise AgentSkillError("existing target does not exactly match packaged skills")
            _atomic_write(manifest_path, _encode_manifest(_manifest(payload, target, ())))
        else:
            if adopt_existing:
                raise AgentSkillError("there is no existing target to adopt")
            stage_parent = target.root.parent if target.layout == "grouped" else target.root
            stage_parent.mkdir(parents=True, exist_ok=True)
            _assert_plain_parents(stage_parent)
            stage = Path(tempfile.mkdtemp(prefix=f".{target.root.name}.proofline-stage-", dir=stage_parent))
            journal: Path | None = None
            try:
                _write_stage(stage, files)
                journal = _journal(registry, "setup", manifest_path)
                _commit_stage(stage, target)
                created = managed_roots if target.layout == "flat" else (".",)
                _atomic_write(
                    manifest_path,
                    _encode_manifest(_manifest(payload, target, created)),
                )
                result = inspect_manifest(manifest_path, payload)
                if result.status != "healthy":
                    raise AgentSkillError("setup post-verification failed")
            except Exception:
                if not manifest_path.exists():
                    for name in managed_roots:
                        shutil.rmtree(target.root / name, ignore_errors=True)
                    if target.layout == "grouped":
                        shutil.rmtree(target.root, ignore_errors=True)
                raise
            finally:
                shutil.rmtree(stage, ignore_errors=True)
                if journal is not None and manifest_path.exists():
                    journal.unlink(missing_ok=True)
        return inspect_manifest(manifest_path, payload)


def _registered_target(agent: str, scope: str | None) -> tuple[Path, dict[str, Any], AgentTarget]:
    selected = ("user" if agent == "codex" else "default") if scope is None else scope
    path = registration_path(agent, selected)
    if not path.exists() and not path.is_symlink():
        raise AgentSkillError("installation is not registered")
    manifest = _read_manifest(path)
    target = resolve_target(agent, selected)
    if manifest["target_root"] != str(target.root) or manifest["layout"] != target.layout:
        raise AgentSkillError("registered target no longer matches the agent adapter")
    return path, manifest, target


def remove(agent: str, scope: str | None = None) -> None:
    registry = state_root()
    with _Lock(registry):
        _require_no_journal(registry)
        path, manifest, target = _registered_target(agent, scope)
        inspection = inspect_manifest(path, None)
        if inspection.status != "healthy":
            raise AgentSkillError(f"installation is {inspection.status}; refusing removal")
        journal = _journal(registry, "remove", path)
        remaining = True
        try:
            expected = {record["path"] for record in manifest["files"]}
            roots = {PurePosixPath(path).parts[0] for path in expected}
            actual, issues = _actual_inventory(
                target.root, roots if target.layout == "flat" else None
            )
            if issues or set(actual) != expected:
                raise AgentSkillError("target changed before removal")
            _remove_managed_target(target, roots)
            remaining = any((target.root / name).exists() for name in roots) if target.layout == "flat" else target.root.exists()
            if remaining:
                raise AgentSkillError("target removal verification failed")
            path.unlink()
        finally:
            if not path.exists() and not remaining:
                journal.unlink(missing_ok=True)


def unregister(agent: str, scope: str | None = None) -> bool:
    selected = ("user" if agent == "codex" else "default") if scope is None else scope
    path = registration_path(agent, selected)
    registry = state_root()
    with _Lock(registry):
        _require_no_journal(registry)
        if not path.exists() and not path.is_symlink():
            raise AgentSkillError("installation is not registered")
        was_invalid = inspect_manifest(path).status == "invalid-manifest"
        state = path.stat(follow_symlinks=False)
        if _is_reparse(state) or not stat.S_ISREG(state.st_mode):
            raise AgentSkillError("registration path is unsafe")
        path.unlink()
        return was_invalid


def repair(agent: str, scope: str | None = None) -> Inspection:
    payload = load_packaged_payload()
    registry = state_root()
    with _Lock(registry):
        _require_no_journal(registry)
        path, manifest, target = _registered_target(agent, scope)
        before = inspect_manifest(path, payload)
        if before.status not in {"drifted", "missing", "outdated"}:
            if before.status == "healthy":
                return before
            raise AgentSkillError(f"installation is {before.status}; refusing repair")
        owned = {record["path"] for record in manifest["files"]}
        roots = {PurePosixPath(path).parts[0] for path in owned}
        actual, issues = _actual_inventory(
            target.root, roots if target.layout == "flat" else None
        )
        if issues or set(actual) - owned:
            raise AgentSkillError("target contains unowned or unsafe paths")
        files = _managed_files(payload, target)
        stage_parent = target.root.parent if target.layout == "grouped" else target.root
        stage = Path(tempfile.mkdtemp(prefix=f".{target.root.name}.proofline-stage-", dir=stage_parent))
        backup = Path(tempfile.mkdtemp(prefix=f".{target.root.name}.proofline-backup-", dir=stage_parent))
        backup.rmdir()
        journal = _journal(registry, "repair", path)
        try:
            _write_stage(stage, files)
            if target.layout == "grouped":
                os.replace(target.root, backup)
            else:
                backup.mkdir()
                for name in roots:
                    source = target.root / name
                    if source.exists():
                        os.replace(source, backup / name)
            _commit_stage(stage, target)
            _atomic_write(path, _encode_manifest(_manifest(payload, target, manifest["created_containers"])))
            result = inspect_manifest(path, payload)
            if result.status != "healthy":
                raise AgentSkillError("repair post-verification failed")
            shutil.rmtree(backup)
            journal.unlink(missing_ok=True)
            return result
        except Exception:
            if backup.exists():
                _remove_managed_target(target, roots)
                if target.layout == "grouped":
                    os.replace(backup, target.root)
                else:
                    for item in backup.iterdir():
                        os.replace(item, target.root / item.name)
            raise
        finally:
            shutil.rmtree(stage, ignore_errors=True)


def sync_registered(payload: SkillPayload) -> None:
    """Converge every healthy registered target to a verified wheel payload."""
    registry = state_root()
    current_payload = load_packaged_payload()
    inspections = inspect_registry(root=registry, payload=current_payload)
    blocked = [item for item in inspections if item.status in BLOCKED_STATES]
    if blocked:
        raise AgentSkillError(f"registered installation is blocked: {blocked[0].agent}/{blocked[0].scope} ({blocked[0].status})")
    with _Lock(registry):
        _require_no_journal(registry)
        prepared: list[tuple[Path, dict[str, Any], AgentTarget, Path, Path]] = []
        committed: list[tuple[Path, dict[str, Any], AgentTarget, Path]] = []
        journal: Path | None = None
        try:
            for item in inspections:
                path = Path(item.manifest_path)
                manifest = _read_manifest(path)
                target = resolve_target(item.agent, item.scope)
                if manifest["target_root"] != str(target.root):
                    raise AgentSkillError(f"registered target changed: {item.agent}/{item.scope}")
                stage_parent = target.root.parent if target.layout == "grouped" else target.root
                stage_parent.mkdir(parents=True, exist_ok=True)
                stage = Path(tempfile.mkdtemp(prefix=f".{target.root.name}.proofline-stage-", dir=stage_parent))
                backup = Path(tempfile.mkdtemp(prefix=f".{target.root.name}.proofline-backup-", dir=stage_parent))
                backup.rmdir()
                _write_stage(stage, _managed_files(payload, target))
                prepared.append((path, manifest, target, stage, backup))
            journal = _journal(registry, "update", registry)
            for path, manifest, target, stage, backup in prepared:
                roots = {PurePosixPath(record["path"]).parts[0] for record in manifest["files"]}
                if target.layout == "grouped":
                    os.replace(target.root, backup)
                else:
                    backup.mkdir()
                    for name in roots:
                        os.replace(target.root / name, backup / name)
                _commit_stage(stage, target)
                _atomic_write(path, _encode_manifest(_manifest(payload, target, manifest["created_containers"])))
                if inspect_manifest(path, payload).status != "healthy":
                    raise AgentSkillError(f"agent skill update verification failed: {target.agent}/{target.scope}")
                committed.append((path, manifest, target, backup))
            for _, _, _, backup in committed:
                shutil.rmtree(backup)
            if journal is not None:
                journal.unlink(missing_ok=True)
        except Exception:
            for path, manifest, target, backup in reversed(committed):
                if backup.exists():
                    roots = {PurePosixPath(record["path"]).parts[0] for record in manifest["files"]}
                    _remove_managed_target(target, roots)
                    if target.layout == "grouped":
                        os.replace(backup, target.root)
                    else:
                        for item in backup.iterdir():
                            os.replace(item, target.root / item.name)
                    _atomic_write(path, _encode_manifest(manifest))
            raise
        finally:
            for _, _, _, stage, backup in prepared:
                shutil.rmtree(stage, ignore_errors=True)
                shutil.rmtree(backup, ignore_errors=True)


def status_document(inspections: list[Inspection]) -> dict[str, Any]:
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "installations": [item.as_dict() for item in inspections],
        "counts": summarize(inspections),
    }
