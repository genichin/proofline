from __future__ import annotations

import ctypes
import errno
import hashlib
from importlib import metadata, resources
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from typing import Any

import yaml


_RESOURCE_GROUPS = ("contracts", "templates", "skills")
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
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

    records = [
        {"path": path, "sha256": hashlib.sha256(content).hexdigest()}
        for path, content in sorted(payload.items())
    ]
    manifest = {
        "schema_version": 1,
        "proofline_version": _distribution_version(),
        "managed_files": records,
    }
    payload["manifest.yaml"] = yaml.safe_dump(
        manifest, sort_keys=False, allow_unicode=True
    ).encode("utf-8")
    return payload


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


def _commit_directory(stage: Path, target: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
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
    if dry_run:
        return HomeInitResult("would-create", dry_run=True)

    stage = Path(tempfile.mkdtemp(prefix=".proofline-init-", dir=home))
    try:
        _write_stage(stage, payload)
        _verify_existing(stage, payload)
        _commit_directory(stage, target)
    except Exception as exc:
        if stage.exists() and not stage.is_symlink():
            shutil.rmtree(stage)
        if isinstance(exc, HomeInitError):
            raise
        raise HomeInitError(f"init failed: {exc}") from exc
    return HomeInitResult("created")
