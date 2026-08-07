"""Fail-closed ProofLine project scaffold and allocator migration writer."""

from __future__ import annotations

import ctypes
import errno
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

try:
    import fcntl
except ModuleNotFoundError:  # pragma: no cover - POSIX writer contract
    fcntl = None  # type: ignore[assignment]

from .identity_allocator import (
    ALLOCATOR_PATH,
    LEGACY_PATH,
    IdentityAllocator,
    encode_allocator,
    migrated_allocator,
)
from .project_schema import CONFIG_BYTES, RESOURCE_NAMES, SCAFFOLD_PATHS
from .validator import validate_project
from .transaction import (
    DirectoryPin,
    FileSnapshot,
    TreeSnapshot,
    commit_no_replace,
    identity,
    open_child_directory,
    open_directory,
    read_regular,
    remove_owned_file,
    remove_owned_tree,
    snapshot_tree,
    verify_directory,
)

_TEMPLATE_PACKAGE = "proofline_schema_v1_templates"
_SOURCE_PROJECT = Path(__file__).resolve().parents[2] / "templates/schema-v1/project"
AT_FDCWD = -100
RENAME_NOREPLACE = 1


@dataclass
class ProjectInitError(Exception):
    code: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.code}: {self.message}"


@dataclass(frozen=True)
class ProjectInitResult:
    paths: tuple[str, ...]
    dry_run: bool
    status: str


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)
    except (OSError, UnicodeError) as exc:
        raise ProjectInitError("git.repository.unavailable", ".", "Git 저장소를 확인할 수 없습니다.") from exc


def _require_git_root(root: Path) -> None:
    result = _run_git(root, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise ProjectInitError("git.repository.required", ".", "Git 저장소가 아닙니다.")
    try:
        if Path(result.stdout.strip()).resolve(strict=True) != root.resolve(strict=True):
            raise ProjectInitError("git.root.mismatch", ".", "현재 directory가 Git 저장소 root가 아닙니다.")
    except OSError as exc:
        raise ProjectInitError("git.root.unavailable", ".", str(exc)) from exc


def _resource_bytes(relative: str) -> bytes:
    source = _SOURCE_PROJECT / relative
    try:
        if source.is_file():
            return source.read_bytes()
        return files(_TEMPLATE_PACKAGE).joinpath("project", relative).read_bytes()
    except (OSError, ModuleNotFoundError) as exc:
        raise ProjectInitError("resource.missing", f"templates/schema-v1/project/{relative}", "project resource가 없습니다.") from exc


def _payload() -> dict[str, bytes]:
    payload = dict(zip(RESOURCE_NAMES, map(_resource_bytes, RESOURCE_NAMES), strict=True))
    expected_allocator = encode_allocator(IdentityAllocator(1, 1))
    if payload["proofline.yaml"] != CONFIG_BYTES or payload["identities.json"] != expected_allocator:
        raise ProjectInitError("resource.malformed", "templates/schema-v1/project", "project resource bytes가 schema와 다릅니다.")
    if payload["lines.gitkeep"] or payload["criteria.gitkeep"]:
        raise ProjectInitError("resource.malformed", "templates/schema-v1/project", ".gitkeep은 zero-byte여야 합니다.")
    return payload


def _commit_path_at(
    source: Path,
    target_dir_fd: int,
    target_name: str,
    expected: FileSnapshot | TreeSnapshot | None = None,
) -> tuple[int, int]:
    return commit_no_replace(source, target_dir_fd, target_name, expected)


def _commit_path(source: Path, target: Path) -> None:
    _commit_path_at(source, AT_FDCWD, os.fspath(target))


def _require_commit_capability(root: Path) -> None:
    if getattr(ctypes.CDLL(None), "renameat2", None) is None:
        raise ProjectInitError("project.commit.unsupported", ".", "atomic no-replace commit을 지원하지 않습니다.")
    if not os.access(root, os.W_OK | os.X_OK):
        raise ProjectInitError("project.permission.denied", ".", "project root에 쓸 수 없습니다.")


def _state(path: Path) -> os.stat_result | None:
    try:
        return path.stat(follow_symlinks=False)
    except (FileNotFoundError, NotADirectoryError):
        return None


def _regular_exact(path: Path, expected: bytes, root: Path) -> None:
    state = _state(path)
    relative = path.relative_to(root).as_posix()
    try:
        actual = read_regular(path) if state is not None else None
    except OSError:
        actual = None
    if state is None or stat.S_ISLNK(state.st_mode) or not stat.S_ISREG(state.st_mode) or actual is None or actual.data != expected:
        raise ProjectInitError("project.scaffold.conflict", relative, "scaffold file type 또는 bytes가 다릅니다.")


def _existing_state(root: Path, payload: dict[str, bytes]) -> str:
    config = root / "proofline.yaml"
    artifact = root / ".proofline"
    config_state, artifact_state = _state(config), _state(artifact)
    if config_state is None and artifact_state is None:
        return "fresh"
    for path, state in ((config, config_state), (artifact, artifact_state)):
        if state is not None and stat.S_ISLNK(state.st_mode):
            raise ProjectInitError("project.scaffold.symlink", path.relative_to(root).as_posix(), "scaffold symlink는 허용하지 않습니다.")
    _regular_exact(config, payload["proofline.yaml"], root)
    if artifact_state is None or not stat.S_ISDIR(artifact_state.st_mode):
        raise ProjectInitError("project.scaffold.conflict", ".proofline", "artifact root는 directory여야 합니다.")
    for name in ("lines", "criteria"):
        state = _state(artifact / name)
        if state is None or stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
            raise ProjectInitError("project.scaffold.conflict", f".proofline/{name}", "canonical directory가 올바르지 않습니다.")
    legacy_state = _state(root / LEGACY_PATH)
    if legacy_state is not None and (stat.S_ISLNK(legacy_state.st_mode) or not stat.S_ISREG(legacy_state.st_mode)):
        code = "project.scaffold.symlink" if stat.S_ISLNK(legacy_state.st_mode) else "project.scaffold.conflict"
        raise ProjectInitError(code, LEGACY_PATH, "legacy ledger는 opaque regular file이어야 합니다.")
    return "exact" if _state(root / ALLOCATOR_PATH) is not None else "migration"


def _write_allocator_no_replace(artifact: DirectoryPin, data: bytes) -> tuple[int, int]:
    verify_directory(artifact)
    root = artifact.path.parent
    descriptor, raw = tempfile.mkstemp(prefix=".identities-", dir=root / ".proofline")
    stage = Path(raw)
    stage_identity: tuple[int, int] | None = None
    committed = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        staged = read_regular(stage)
        stage_identity = staged.identity
        verify_directory(artifact)
        _commit_path_at(stage, artifact.descriptor, "identities.json", staged)
        committed = True
        return stage_identity
    finally:
        if not committed and stage_identity is not None:
            remove_owned_file(artifact.descriptor, stage.name, stage_identity, data)


def _acquire_repository_lock(root: Path) -> int:
    if fcntl is None:
        raise ProjectInitError("project.commit.unsupported", ".git", "POSIX repository lock이 필요합니다.")
    result = _run_git(root, "rev-parse", "--git-common-dir")
    if result.returncode != 0:
        raise ProjectInitError("project.lock.unavailable", ".git", "repository common directory를 확인할 수 없습니다.")
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = root / common
    descriptor: int | None = None
    try:
        descriptor = os.open(common, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise ProjectInitError("project.lock.unavailable", ".git", "repository lock을 얻을 수 없습니다.") from exc


def _release_repository_lock(descriptor: int) -> None:
    assert fcntl is not None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _pin_existing(root: Path) -> tuple[DirectoryPin, DirectoryPin, DirectoryPin, DirectoryPin]:
    pins: list[DirectoryPin] = []
    try:
        pins.append(open_directory(root))
        pins.append(open_child_directory(pins[0], ".proofline", root / ".proofline"))
        pins.append(open_child_directory(pins[1], "lines", root / ".proofline/lines"))
        pins.append(open_child_directory(pins[1], "criteria", root / ".proofline/criteria"))
    except Exception:
        for pin in reversed(pins):
            pin.close()
        raise
    return tuple(pins)  # type: ignore[return-value]


def _migration_candidate(root: Path, allocator: bytes) -> None:
    from .line_writer import LineInitError, _copy_candidate_tree

    with tempfile.TemporaryDirectory(prefix="proofline-project-candidate-") as raw:
        candidate = Path(raw)
        (candidate / ".proofline/lines").mkdir(parents=True)
        (candidate / ".proofline/criteria").mkdir()
        try:
            (candidate / "proofline.yaml").write_bytes(read_regular(root / "proofline.yaml").data)
            _copy_candidate_tree(root, candidate, ".proofline/lines")
            _copy_candidate_tree(root, candidate, ".proofline/criteria")
        except (OSError, LineInitError) as exc:
            raise ProjectInitError("project.migration.source", ".proofline", "migration source를 읽을 수 없습니다.") from exc
        (candidate / ALLOCATOR_PATH).write_bytes(allocator)
        errors = validate_project(candidate)
        if errors:
            first = errors[0]
            raise ProjectInitError("project.migration.invalid", first.path, f"{first.code}: {first.message}")


def _initialize_project(project_root: Path, *, dry_run: bool = False) -> ProjectInitResult:
    root = project_root.absolute()
    _require_git_root(root)
    payload = _payload()
    _require_commit_capability(root)
    project_pin = open_directory(root)
    try:
        state = _existing_state(root, payload)
        verify_directory(project_pin)
    except Exception:
        project_pin.close()
        raise
    if state == "exact":
        try:
            errors = validate_project(root)
            verify_directory(project_pin)
            if errors:
                first = errors[0]
                raise ProjectInitError("project.scaffold.invalid", first.path, f"{first.code}: {first.message}")
            return ProjectInitResult(SCAFFOLD_PATHS, dry_run, "already-initialized")
        finally:
            project_pin.close()
    if state == "migration":
        pins = _pin_existing(root)
        try:
            data = encode_allocator(migrated_allocator(root))
            _migration_candidate(root, data)
            for pin in pins:
                verify_directory(pin)
            verify_directory(project_pin)
        except Exception:
            for pin in reversed(pins):
                pin.close()
            project_pin.close()
            raise
        if dry_run:
            for pin in reversed(pins):
                pin.close()
            project_pin.close()
            return ProjectInitResult((ALLOCATOR_PATH,), True, "planned")
        committed_identity: tuple[int, int] | None = None
        try:
            committed_identity = _write_allocator_no_replace(pins[1], data)
            errors = validate_project(root)
            if errors:
                first = errors[0]
                raise ProjectInitError("project.migration.invalid", first.path, f"{first.code}: {first.message}")
            committed = read_regular(root / ALLOCATOR_PATH)
            if committed.identity != committed_identity or committed.data != data:
                raise OSError("committed allocator changed during validation")
        except Exception as primary:
            secondary: str | None = None
            if committed_identity is not None:
                try:
                    for pin in pins:
                        verify_directory(pin)
                    remove_owned_file(pins[1].descriptor, "identities.json", committed_identity, data)
                except OSError as exc:
                    secondary = f"project.migration.rollback.failed: {exc}"
            if secondary:
                if isinstance(primary, ProjectInitError):
                    raise ProjectInitError(primary.code, primary.path, f"{primary.message}; secondary: {secondary}") from primary
                raise ProjectInitError("project.migration.failed", ALLOCATOR_PATH, f"primary={primary}; secondary={secondary}") from primary
            raise
        finally:
            for pin in reversed(pins):
                pin.close()
            project_pin.close()
        return ProjectInitResult((ALLOCATOR_PATH,), False, "migrated")

    stage = Path(
        tempfile.mkdtemp(
            prefix="proofline-project-" if dry_run else ".proofline-project-",
            dir=None if dry_run else root,
        )
    )
    stage_in_root = not dry_run
    config_committed = artifact_committed = False
    config_identity: tuple[int, int] | None = None
    artifact_identity: tuple[int, int] | None = None
    root_pin = project_pin
    stage_identity: tuple[int, int] | None = None
    artifact_files: dict[str, tuple[tuple[int, int], bytes]] = {}
    artifact_directories: dict[str, tuple[int, int]] = {}
    stage_removed = False
    try:
        stage_identity = identity(stage.stat(follow_symlinks=False))
        (stage / ".proofline/lines").mkdir(parents=True)
        (stage / ".proofline/criteria").mkdir()
        artifact_identity = identity((stage / ".proofline").stat(follow_symlinks=False))
        for relative in ("lines", "criteria"):
            artifact_directories[relative] = identity(
                (stage / ".proofline" / relative).stat(follow_symlinks=False)
            )
        (stage / "proofline.yaml").write_bytes(payload["proofline.yaml"])
        config_snapshot = read_regular(stage / "proofline.yaml")
        config_identity = config_snapshot.identity
        (stage / ".proofline/identities.json").write_bytes(payload["identities.json"])
        allocator_snapshot = read_regular(stage / ".proofline/identities.json")
        artifact_files["identities.json"] = (
            allocator_snapshot.identity,
            allocator_snapshot.data,
        )
        (stage / ".proofline/lines/.gitkeep").write_bytes(payload["lines.gitkeep"])
        lines_marker = read_regular(stage / ".proofline/lines/.gitkeep")
        artifact_files["lines/.gitkeep"] = (lines_marker.identity, lines_marker.data)
        (stage / ".proofline/criteria/.gitkeep").write_bytes(payload["criteria.gitkeep"])
        criteria_marker = read_regular(stage / ".proofline/criteria/.gitkeep")
        artifact_files["criteria/.gitkeep"] = (
            criteria_marker.identity,
            criteria_marker.data,
        )
        errors = validate_project(stage)
        if errors:
            first = errors[0]
            raise ProjectInitError("project.prepare.invalid", first.path, f"{first.code}: {first.message}")
        verify_directory(root_pin)
        if dry_run:
            shutil.rmtree(stage)
            stage_removed = True
            root_pin.close()
            return ProjectInitResult(SCAFFOLD_PATHS, True, "planned")
        _commit_path_at(
            stage / "proofline.yaml", root_pin.descriptor, "proofline.yaml", config_snapshot
        )
        config_committed = True
        verify_directory(root_pin)
        artifact_snapshot = TreeSnapshot(
            artifact_identity, artifact_files, artifact_directories
        )
        _commit_path_at(
            stage / ".proofline", root_pin.descriptor, ".proofline", artifact_snapshot
        )
        artifact_committed = True
        verify_directory(root_pin)
        remove_owned_tree(root_pin.descriptor, stage.name, stage_identity, {})
        stage_removed = True
        errors = validate_project(root)
        if errors:
            first = errors[0]
            raise ProjectInitError("project.transaction.invalid", first.path, f"{first.code}: {first.message}")
        verify_directory(root_pin)
        if read_regular(root / "proofline.yaml") != config_snapshot:
            raise OSError("committed config changed during validation")
        if snapshot_tree(root / ".proofline") != artifact_snapshot:
            raise OSError("committed artifact tree changed during validation")
    except Exception as primary:
        rollback_errors: list[str] = []
        if artifact_committed:
            artifact = root / ".proofline"
            try:
                assert artifact_identity is not None
                verify_directory(root_pin)
                remove_owned_tree(root_pin.descriptor, ".proofline", artifact_identity, artifact_files, artifact_directories)
            except OSError as exc:
                rollback_errors.append(f"project.rollback.ownership: {exc}")
        if config_committed:
            config = root / "proofline.yaml"
            try:
                assert config_identity is not None
                remove_owned_file(root_pin.descriptor, "proofline.yaml", config_identity, payload["proofline.yaml"])
            except OSError as exc:
                rollback_errors.append(f"project.rollback.ownership: {exc}")
        if not stage_removed:
            try:
                assert stage_identity is not None
                stage_files: dict[str, tuple[tuple[int, int], bytes]] = {}
                stage_directories: dict[str, tuple[int, int]] = {}
                if not config_committed and config_identity is not None:
                    stage_files["proofline.yaml"] = (config_identity, payload["proofline.yaml"])
                if not artifact_committed and artifact_identity is not None:
                    stage_files.update(
                        {
                            f".proofline/{name}": value
                            for name, value in artifact_files.items()
                        }
                    )
                    stage_directories[".proofline"] = artifact_identity
                    stage_directories.update(
                        {
                            f".proofline/{name}": value
                            for name, value in artifact_directories.items()
                        }
                    )
                if stage_in_root:
                    remove_owned_tree(
                        root_pin.descriptor,
                        stage.name,
                        stage_identity,
                        stage_files,
                        stage_directories,
                    )
                else:
                    shutil.rmtree(stage)
            except OSError as exc:
                rollback_errors.append(f"project.cleanup.ownership: {exc}")
        root_pin.close()
        if rollback_errors:
            raise ProjectInitError("project.transaction.failed", ".", f"primary={primary}; secondary={' | '.join(rollback_errors)}") from primary
        if isinstance(primary, ProjectInitError):
            raise
        raise ProjectInitError("project.transaction.failed", ".", f"transaction 처리 중 오류가 발생했습니다: {primary}") from primary
    root_pin.close()
    return ProjectInitResult(SCAFFOLD_PATHS, False, "created")


def initialize_project(project_root: Path, *, dry_run: bool = False) -> ProjectInitResult:
    root = project_root.absolute()
    _require_git_root(root)
    lock = _acquire_repository_lock(root)
    try:
        try:
            return _initialize_project(root, dry_run=dry_run)
        except ProjectInitError:
            raise
        except OSError as exc:
            raise ProjectInitError("project.operation.failed", ".", "project 작업 중 filesystem 오류가 발생했습니다.") from exc
    finally:
        active = sys.exception()
        try:
            _release_repository_lock(lock)
        except OSError as exc:
            if isinstance(active, ProjectInitError):
                active.message += "; secondary: project.lock.release.failed"
            elif active is None:
                raise ProjectInitError("project.lock.release.failed", ".git", "repository lock을 해제할 수 없습니다.") from exc
