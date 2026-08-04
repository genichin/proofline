from __future__ import annotations

import ctypes
import errno
import hashlib
from importlib import metadata, resources
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
from typing import Any
import zipfile

import yaml


_RESOURCE_GROUPS = ("contracts", "templates", "skills")
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_STABLE_VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
_SOURCE_GROUPS = {
    "contracts": _SOURCE_ROOT / "docs" / "contracts",
    "templates": _SOURCE_ROOT / "templates",
    "skills": _SOURCE_ROOT / "skills",
}
_PATHS = (
    "~/.proofline/manifest.yaml",
    "~/.proofline/contracts/",
    "~/.proofline/templates/",
    "~/.proofline/skills/",
    "~/.proofline/agent-context.md",
)


class HomeInitError(Exception):
    pass


class HomeInitResult:
    def __init__(self, status: str, *, dry_run: bool = False) -> None:
        self.status = status
        self.paths = _PATHS
        self.dry_run = dry_run


def _distribution_version() -> str:
    try:
        return metadata.version("proofline")
    except metadata.PackageNotFoundError:
        return "0+unknown"


def _included(parts: tuple[str, ...]) -> bool:
    return (
        bool(parts)
        and "__pycache__" not in parts
        and not parts[-1].endswith((".pyc", "~"))
        and not parts[-1].startswith(".")
    )


def _source_files(root: Path, *, skills: bool = False) -> dict[str, bytes]:
    if root.is_symlink() or not root.is_dir():
        raise HomeInitError(f"resource conflict: {root}")
    payload: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise HomeInitError(f"resource symlink: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if skills and (not relative.parts or not relative.parts[0].startswith("proofline-")):
            continue
        if _included(relative.parts):
            payload[relative.as_posix()] = path.read_bytes()
    return payload


def _package_files(root: Any) -> dict[str, bytes]:
    payload: dict[str, bytes] = {}

    def visit(node: Any, parts: tuple[str, ...]) -> None:
        if node.is_dir():
            for child in sorted(node.iterdir(), key=lambda item: item.name):
                visit(child, (*parts, child.name))
        elif node.is_file() and _included(parts):
            payload["/".join(parts)] = node.read_bytes()

    visit(root, ())
    return payload


def _payload() -> dict[str, bytes]:
    try:
        package = resources.files("proofline_home")
        payload = {"agent-context.md": package.joinpath("agent-context.md").read_bytes()}
        for group in _RESOURCE_GROUPS:
            packaged = package.joinpath(group)
            files = _package_files(packaged) if packaged.is_dir() else _source_files(
                _SOURCE_GROUPS[group], skills=group == "skills"
            )
            if not files:
                raise HomeInitError(f"empty resource group: {group}")
            for relative, content in files.items():
                candidate = PurePosixPath(relative)
                if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
                    raise HomeInitError(f"unsafe resource path: {relative}")
                payload[f"{group}/{relative}"] = content
    except HomeInitError:
        raise
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        raise HomeInitError(f"resource error: {exc}") from exc

    return build_home_payload(_distribution_version(), payload)


def build_home_payload(version: str, resources_payload: dict[str, bytes]) -> dict[str, bytes]:
    if not resources_payload:
        raise HomeInitError("empty home resource payload")
    allowed_roots = {"contracts", "templates", "skills"}
    groups: set[str] = set()
    clean: dict[str, bytes] = {}
    for relative, content in resources_payload.items():
        candidate = PurePosixPath(relative)
        if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
            raise HomeInitError(f"unsafe resource path: {relative}")
        if relative == "manifest.yaml" or not isinstance(content, bytes):
            raise HomeInitError(f"invalid resource: {relative}")
        if relative != "agent-context.md":
            if candidate.parts[0] not in allowed_roots or len(candidate.parts) < 2:
                raise HomeInitError(f"unexpected resource path: {relative}")
            groups.add(candidate.parts[0])
        clean[relative] = content
    if "agent-context.md" not in clean or groups != allowed_roots:
        raise HomeInitError("incomplete home resource payload")
    records = [
        {"path": path, "sha256": hashlib.sha256(content).hexdigest()}
        for path, content in sorted(clean.items())
    ]
    manifest = {
        "schema_version": 1,
        "proofline_version": version,
        "source": {"type": "packaged-resource"},
        "managed_files": records,
    }
    clean["manifest.yaml"] = yaml.safe_dump(
        manifest, sort_keys=False, allow_unicode=True
    ).encode("utf-8")
    return clean


def payload_from_wheel(wheel: Path, version: str) -> dict[str, bytes]:
    resources_payload: dict[str, bytes] = {}
    prefix = "proofline_home/"
    try:
        with zipfile.ZipFile(wheel) as archive:
            seen: set[str] = set()
            for info in archive.infolist():
                if info.filename in seen:
                    raise HomeInitError(f"duplicate wheel entry: {info.filename}")
                seen.add(info.filename)
                if not info.filename.startswith(prefix) or info.is_dir():
                    continue
                relative = info.filename[len(prefix):]
                if relative == "__init__.py":
                    continue
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise HomeInitError(f"wheel resource symlink: {relative}")
                resources_payload[relative] = archive.read(info)
    except (OSError, zipfile.BadZipFile) as exc:
        raise HomeInitError(f"invalid wheel: {exc}") from exc
    return build_home_payload(version, resources_payload)

def _expected_directories(payload: dict[str, bytes]) -> set[str]:
    result: set[str] = set()
    for relative in payload:
        parent = PurePosixPath(relative).parent
        while parent != PurePosixPath("."):
            result.add(parent.as_posix())
            parent = parent.parent
    return result


def _verify_existing(target: Path, payload: dict[str, bytes]) -> None:
    if target.is_symlink():
        raise HomeInitError("symlink conflict at ~/.proofline")
    if not target.is_dir():
        raise HomeInitError("conflict at ~/.proofline: expected directory")
    files: dict[str, bytes] = {}
    directories: set[str] = set()
    for path in sorted(target.rglob("*")):
        relative = path.relative_to(target).as_posix()
        if path.is_symlink():
            raise HomeInitError(f"symlink conflict at ~/.proofline/{relative}")
        if path.is_dir():
            directories.add(relative)
        elif path.is_file():
            files[relative] = path.read_bytes()
        else:
            raise HomeInitError(f"conflict at ~/.proofline/{relative}")
    if set(files) != set(payload) or directories != _expected_directories(payload):
        raise HomeInitError("conflict at ~/.proofline: managed entry set differs")
    for relative, expected in payload.items():
        if files[relative] != expected:
            raise HomeInitError(f"conflict at ~/.proofline/{relative}: bytes differ")


def _write_stage(stage: Path, payload: dict[str, bytes]) -> None:
    for relative in sorted(_expected_directories(payload), key=lambda value: (value.count("/"), value)):
        (stage / relative).mkdir()
    for relative, content in sorted(payload.items()):
        (stage / relative).write_bytes(content)


def _platform_name() -> str:
    return sys.platform


def _renameat2_function():
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    return renameat2


def _linux_commit_directory(stage: Path, target: Path) -> None:
    renameat2 = _renameat2_function()
    result = renameat2(
        -100,
        os.fsencode(stage),
        -100,
        os.fsencode(target),
        1,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), target)


def _windows_commit_directory(stage: Path, target: Path) -> None:
    os.rename(stage, target)


def _commit_directory(stage: Path, target: Path) -> None:
    if _platform_name() == "win32":
        _windows_commit_directory(stage, target)
        return
    _linux_commit_directory(stage, target)


def _exchange_directories(left: Path, right: Path) -> None:
    renameat2 = _renameat2_function()
    result = renameat2(-100, os.fsencode(left), -100, os.fsencode(right), 2)
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), right)


def _preflight_commit(mode: str, home: Path, target: Path) -> None:
    if _platform_name() == "win32":
        if mode == "replace":
            raise HomeInitError("home update replacement is unsupported on Windows")
        if mode != "create":
            raise HomeInitError(f"invalid home update mode: {mode}")
        if home.resolve().drive.casefold() != target.parent.resolve().drive.casefold():
            raise HomeInitError("Windows fresh commit requires same-volume staging")
        if not callable(os.rename):
            raise HomeInitError("Windows fresh commit capability unavailable")
        return
    try:
        _renameat2_function()
    except OSError as exc:
        raise HomeInitError(f"home update commit capability unavailable: {exc}") from exc


def _identity(path: Path) -> tuple[int, int]:
    value = path.stat(follow_symlinks=False)
    if not stat.S_ISDIR(value.st_mode):
        raise HomeInitError(f"conflict at {path}: expected directory")
    return value.st_dev, value.st_ino


def _tree_ownership_signature(root: Path) -> tuple[tuple[str, int, int, int, int, int, int], ...]:
    paths = [root, *sorted(root.rglob("*"))]
    records: list[tuple[str, int, int, int, int, int, int]] = []
    for path in paths:
        relative = "." if path == root else path.relative_to(root).as_posix()
        value = path.stat(follow_symlinks=False)
        if stat.S_ISLNK(value.st_mode):
            raise HomeInitError(f"home update ownership signature found symlink: {relative}")
        records.append(
            (
                relative,
                value.st_dev,
                value.st_ino,
                value.st_mode,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            )
        )
    return tuple(records)


class HomeUpdateTransaction:
    def __init__(
        self,
        target: Path,
        stage: Path | None,
        mode: str,
        expected_payload: dict[str, bytes],
    ) -> None:
        self.target = target
        self.stage = stage
        self.mode = mode
        self._expected_payload = expected_payload
        self.committed = mode == "noop"
        self._target_identity: tuple[int, int] | None = None
        self._prepared_target_identity = (
            _identity(target) if mode in {"replace", "noop"} else None
        )
        self._old_target_identity: tuple[int, int] | None = None
        self._stage_identity = _identity(stage) if stage is not None else None
        self._prepared_stage_tree_signature = (
            _tree_ownership_signature(stage) if stage is not None else None
        )
        self._prepared_target_tree_signature = (
            _tree_ownership_signature(target) if mode in {"replace", "noop"} else None
        )
        self._committed_target_tree_signature: (
            tuple[tuple[str, int, int, int, int, int, int], ...] | None
        ) = None
        self._committed_old_tree_signature: (
            tuple[tuple[str, int, int, int, int, int, int], ...] | None
        ) = None
        self._finalization_started = False
        self._finalized = False

    def commit(self) -> None:
        if self.committed:
            return
        if self.stage is None:
            raise HomeInitError("home update stage is missing")
        try:
            if self._stage_identity is None or _identity(self.stage) != self._stage_identity:
                raise HomeInitError("home update staging identity changed")
            if _tree_ownership_signature(self.stage) != self._prepared_stage_tree_signature:
                raise HomeInitError("home update staging tree changed")
            if self.mode == "create":
                _commit_directory(self.stage, self.target)
                self.committed = True
                self._target_identity = _identity(self.target)
                if self._target_identity != self._stage_identity:
                    raise HomeInitError("home update target identity mismatch")
                self._committed_target_tree_signature = _tree_ownership_signature(self.target)
            elif self.mode == "replace":
                old_identity = _identity(self.target)
                if old_identity != self._prepared_target_identity:
                    raise HomeInitError("home update target identity changed")
                if _tree_ownership_signature(self.target) != self._prepared_target_tree_signature:
                    raise HomeInitError("home update target tree changed")
                _exchange_directories(self.stage, self.target)
                self.committed = True
                target_identity = _identity(self.target)
                stage_identity = _identity(self.stage)
                if target_identity != self._stage_identity or stage_identity != old_identity:
                    if target_identity == self._stage_identity:
                        _exchange_directories(self.stage, self.target)
                        self.committed = False
                    raise HomeInitError("home update concurrent target detected")
                self._old_target_identity = old_identity
                self._target_identity = target_identity
                self._committed_target_tree_signature = _tree_ownership_signature(self.target)
                self._committed_old_tree_signature = _tree_ownership_signature(self.stage)
            else:
                raise HomeInitError(f"invalid home update mode: {self.mode}")
        except HomeInitError:
            raise
        except OSError as exc:
            raise HomeInitError(f"home update commit failed: {exc}") from exc
        self.committed = True

    def rollback(self) -> None:
        if self.mode == "noop":
            return
        if self.stage is None:
            raise HomeInitError("home update stage is missing")
        if self._finalization_started:
            raise HomeInitError("home update rollback is unavailable after finalization started")
        try:
            if not self.committed:
                target_changed = False
                if self.mode == "replace":
                    target_changed = _identity(self.target) != self._prepared_target_identity
                elif self.mode == "create":
                    target_changed = self.target.exists() or self.target.is_symlink()
                if self.stage.exists() and _identity(self.stage) == self._stage_identity:
                    shutil.rmtree(self.stage)
                if target_changed:
                    raise HomeInitError("home update target changed before rollback")
                return
            if self._target_identity is None or _identity(self.target) != self._target_identity:
                raise HomeInitError("home update rollback target identity mismatch")
            if _tree_ownership_signature(self.target) != self._committed_target_tree_signature:
                raise HomeInitError("home update rollback ownership mismatch")
            if self.mode == "replace":
                if self._old_target_identity is None or _identity(self.stage) != self._old_target_identity:
                    raise HomeInitError("home update rollback stage identity mismatch")
                if _tree_ownership_signature(self.stage) != self._committed_old_tree_signature:
                    raise HomeInitError("home update rollback old-tree ownership mismatch")
                _exchange_directories(self.stage, self.target)
            elif self.mode == "create":
                try:
                    _verify_existing(self.target, self._expected_payload)
                except HomeInitError as exc:
                    raise HomeInitError("home update rollback ownership mismatch") from exc
                _commit_directory(self.target, self.stage)
            if _identity(self.stage) != self._target_identity:
                raise HomeInitError("home update rollback stage identity mismatch")
            if self.mode == "replace" and _identity(self.target) != self._old_target_identity:
                raise HomeInitError("home update rollback restored identity mismatch")
            if self.mode == "create" and (self.target.exists() or self.target.is_symlink()):
                raise HomeInitError("home update rollback target still exists")
            shutil.rmtree(self.stage)
            self.committed = False
        except HomeInitError:
            raise
        except OSError as exc:
            raise HomeInitError(f"home update rollback failed: {exc}") from exc

    def finalize(self) -> None:
        if self.mode == "noop" or self.stage is None:
            return
        if self._finalized:
            return
        if not self.committed:
            raise HomeInitError("cannot finalize uncommitted home update")
        try:
            if self._target_identity is None or _identity(self.target) != self._target_identity:
                raise HomeInitError("home update finalize target identity mismatch")
            if self.stage.exists():
                expected = self._old_target_identity if self.mode == "replace" else None
                if expected is not None and _identity(self.stage) != expected:
                    raise HomeInitError("home update finalize identity mismatch")
                if (
                    self.mode == "replace"
                    and _tree_ownership_signature(self.stage)
                    != self._committed_old_tree_signature
                ):
                    raise HomeInitError("home update finalize ownership mismatch")
                self._finalization_started = True
                shutil.rmtree(self.stage)
            self._finalized = True
        except HomeInitError:
            raise
        except OSError as exc:
            raise HomeInitError(f"home update finalize failed: {exc}") from exc


def prepare_home_update(
    target_payload: dict[str, bytes],
    *,
    current_payload: dict[str, bytes] | None,
    home: Path | None = None,
) -> HomeUpdateTransaction:
    active_home = Path.home() if home is None else home
    try:
        home_state = active_home.stat(follow_symlinks=False)
    except OSError as exc:
        raise HomeInitError(f"cannot access user home: {exc}") from exc
    if not stat.S_ISDIR(home_state.st_mode):
        raise HomeInitError("user home is not a directory")
    target = active_home / ".proofline"
    if target.exists() or target.is_symlink():
        if current_payload is None:
            raise HomeInitError("conflict at ~/.proofline: current payload is unavailable")
        _verify_existing(target, current_payload)
        try:
            _verify_existing(target, target_payload)
        except HomeInitError:
            mode = "replace"
        else:
            return HomeUpdateTransaction(target, None, "noop", target_payload)
    else:
        mode = "create"

    _preflight_commit(mode, active_home, target)
    stage = Path(tempfile.mkdtemp(prefix=".proofline-update-", dir=active_home))
    try:
        _write_stage(stage, target_payload)
        _verify_existing(stage, target_payload)
        return HomeUpdateTransaction(target, stage, mode, target_payload)
    except Exception as exc:
        if stage.exists() and not stage.is_symlink():
            shutil.rmtree(stage)
        if isinstance(exc, HomeInitError):
            raise
        raise HomeInitError(f"home update prepare failed: {exc}") from exc


def preflight_home(current_payload: dict[str, bytes], *, home: Path | None = None) -> str:
    active_home = Path.home() if home is None else home
    target = active_home / ".proofline"
    if not target.exists() and not target.is_symlink():
        return "absent"
    _verify_existing(target, current_payload)
    return "exact"


def verify_home(payload: dict[str, bytes], *, home: Path | None = None) -> None:
    active_home = Path.home() if home is None else home
    _verify_existing(active_home / ".proofline", payload)


def _payload_from_existing_manifest(target: Path) -> dict[str, bytes]:
    manifest_path = target / "manifest.yaml"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise HomeInitError("conflict at ~/.proofline/manifest.yaml")
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = yaml.safe_load(manifest_bytes)
    except yaml.YAMLError as exc:
        raise HomeInitError("invalid ~/.proofline/manifest.yaml") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise HomeInitError("invalid ~/.proofline manifest schema")
    if set(manifest) not in (
        {"schema_version", "proofline_version", "managed_files"},
        {"schema_version", "proofline_version", "source", "managed_files"},
    ):
        raise HomeInitError("invalid ~/.proofline manifest fields")
    if not isinstance(manifest.get("proofline_version"), str):
        raise HomeInitError("invalid ~/.proofline manifest version")
    if "source" in manifest and manifest["source"] != {"type": "packaged-resource"}:
        raise HomeInitError("invalid ~/.proofline manifest source")
    records = manifest.get("managed_files")
    if not isinstance(records, list) or not records:
        raise HomeInitError("invalid ~/.proofline managed files")
    payload: dict[str, bytes] = {"manifest.yaml": manifest_bytes}
    observed: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            raise HomeInitError("invalid ~/.proofline managed record")
        relative = record.get("path")
        digest = record.get("sha256")
        if not isinstance(relative, str) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise HomeInitError("invalid ~/.proofline managed record")
        candidate = PurePosixPath(relative)
        if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
            raise HomeInitError(f"unsafe managed path: {relative}")
        if relative in observed or relative == "manifest.yaml":
            raise HomeInitError(f"duplicate managed path: {relative}")
        observed.add(relative)
        path = target / relative
        if path.is_symlink() or not path.is_file():
            raise HomeInitError(f"conflict at ~/.proofline/{relative}")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise HomeInitError(f"checksum conflict at ~/.proofline/{relative}")
        payload[relative] = content
    _verify_existing(target, payload)
    return payload


def _payload_version(payload: dict[str, bytes]) -> tuple[int, int, int]:
    try:
        value = yaml.safe_load(payload["manifest.yaml"])["proofline_version"]
    except (KeyError, TypeError, yaml.YAMLError) as exc:
        raise HomeInitError("invalid ~/.proofline manifest version") from exc
    if not isinstance(value, str) or _STABLE_VERSION.fullmatch(value) is None:
        raise HomeInitError("invalid ~/.proofline manifest version")
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def reconcile_existing_home() -> str:
    target = Path.home() / ".proofline"
    if not target.exists() and not target.is_symlink():
        return "absent"
    target_payload = _payload()
    try:
        _verify_existing(target, target_payload)
        return "already-current"
    except HomeInitError:
        pass
    current_payload = _payload_from_existing_manifest(target)
    if _payload_version(current_payload) >= _payload_version(target_payload):
        raise HomeInitError("existing harness is not an older stable version")
    transaction = prepare_home_update(target_payload, current_payload=current_payload)
    try:
        transaction.commit()
        verify_home(target_payload)
        transaction.finalize()
    except Exception as exc:
        try:
            transaction.rollback()
        except Exception as rollback_exc:
            raise HomeInitError(f"home reconciliation failed: {exc}; rollback failed: {rollback_exc}") from exc
        if isinstance(exc, HomeInitError):
            raise
        raise HomeInitError(f"home reconciliation failed: {exc}") from exc
    return "updated"


def initialize_home(*, dry_run: bool = False) -> HomeInitResult:
    home = Path.home()
    try:
        home_state = home.stat(follow_symlinks=False)
    except OSError as exc:
        raise HomeInitError(f"cannot access user home: {exc}") from exc
    if not stat.S_ISDIR(home_state.st_mode):
        raise HomeInitError("user home is not a directory")
    target = home / ".proofline"
    payload = _payload()
    if target.exists() or target.is_symlink():
        _verify_existing(target, payload)
        return HomeInitResult("already-initialized", dry_run=dry_run)
    _preflight_commit("create", home, target)
    if dry_run:
        return HomeInitResult("would-create", dry_run=True)

    transaction = prepare_home_update(payload, current_payload=None, home=home)
    try:
        transaction.commit()
        verify_home(payload, home=home)
        transaction.finalize()
    except Exception as exc:
        try:
            transaction.rollback()
        except Exception as rollback_exc:
            raise HomeInitError(f"init failed: {exc}; cleanup failed: {rollback_exc}") from exc
        if isinstance(exc, HomeInitError):
            raise
        raise HomeInitError(f"init failed: {exc}") from exc
    return HomeInitResult("created")
