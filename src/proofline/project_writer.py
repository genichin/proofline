"""Fail-closed ProofLine project scaffold writer."""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from .project_schema import CONFIG_BYTES, RESOURCE_NAMES, SCAFFOLD_PATHS
from .validator import validate_project

_TEMPLATE_PACKAGE = "proofline_schema_v1_templates"
_SOURCE_PROJECT = Path(__file__).resolve().parents[2] / "templates/schema-v1/project"
AT_FDCWD = -100
RENAME_NOREPLACE = 1


@dataclass(frozen=True)
class ProjectInitError(Exception):
    code: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.code}: {self.message}"


@dataclass(frozen=True)
class ProjectInitResult:
    paths: tuple[str, str, str]
    dry_run: bool
    status: str


def _run_git(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=project_root,
        text=True,
        capture_output=True,
        check=False,
    )


def _require_git_root(project_root: Path) -> None:
    result = _run_git(project_root, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise ProjectInitError("git.repository.required", ".", "Git 저장소가 아닙니다.")
    try:
        actual = Path(result.stdout.strip()).resolve(strict=True)
        requested = project_root.resolve(strict=True)
    except OSError as exc:
        raise ProjectInitError("git.root.unavailable", ".", str(exc)) from exc
    if actual != requested:
        raise ProjectInitError(
            "git.root.mismatch", ".", "현재 directory가 Git 저장소 root가 아닙니다."
        )


def _resource_bytes(relative: str) -> bytes:
    try:
        return files(_TEMPLATE_PACKAGE).joinpath("project", relative).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError) as packaged_error:
        source_checkout = _SOURCE_PROJECT.is_dir() and (
            _SOURCE_PROJECT.parents[2] / "pyproject.toml"
        ).is_file()
        if not source_checkout:
            raise ProjectInitError(
                "resource.missing",
                f"proofline_schema_v1_templates/project/{relative}",
                "installed package project scaffold resource가 없습니다.",
            ) from packaged_error
        try:
            return (_SOURCE_PROJECT / relative).read_bytes()
        except OSError as exc:
            raise ProjectInitError(
                "resource.missing",
                f"templates/schema-v1/project/{relative}",
                "project scaffold resource를 읽을 수 없습니다.",
            ) from exc
    except OSError as exc:
        raise ProjectInitError(
            "resource.missing",
            f"templates/schema-v1/project/{relative}",
            "project scaffold resource를 읽을 수 없습니다.",
        ) from exc


def _payload() -> dict[str, bytes]:
    try:
        payload = dict(zip(RESOURCE_NAMES, map(_resource_bytes, RESOURCE_NAMES), strict=True))
    except ProjectInitError:
        raise
    except OSError as exc:
        raise ProjectInitError(
            "resource.missing", "templates/schema-v1/project", str(exc)
        ) from exc
    if payload["proofline.yaml"] != CONFIG_BYTES:
        raise ProjectInitError(
            "resource.malformed",
            "templates/schema-v1/project/proofline.yaml",
            "proofline.yaml resource bytes가 schema-v1 contract와 다릅니다.",
        )
    for name in ("lines.gitkeep", "criteria.gitkeep"):
        if payload[name] != b"":
            raise ProjectInitError(
                "resource.malformed",
                f"templates/schema-v1/project/{name}",
                ".gitkeep resource는 zero-byte여야 합니다.",
            )
    return payload


def _path_state(path: Path) -> os.stat_result | None:
    try:
        return path.stat(follow_symlinks=False)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise ProjectInitError(
            "project.scaffold.unavailable",
            path.name,
            f"target path를 검사할 수 없습니다: {exc}",
        ) from exc


def _preflight(project_root: Path, payload: dict[str, bytes]) -> str:
    config = project_root / "proofline.yaml"
    artifact_root = project_root / ".proofline"
    lines = artifact_root / "lines"
    criteria = artifact_root / "criteria"
    markers = (lines / ".gitkeep", criteria / ".gitkeep")
    config_state = _path_state(config)
    artifact_root_state = _path_state(artifact_root)
    primary = ((config, config_state), (artifact_root, artifact_root_state))
    for path, state in primary:
        if state is not None and stat.S_ISLNK(state.st_mode):
            raise ProjectInitError(
                "project.scaffold.symlink",
                path.relative_to(project_root).as_posix(),
                "scaffold path symlink는 허용하지 않습니다.",
            )
    if artifact_root_state is not None and not stat.S_ISDIR(artifact_root_state.st_mode):
        raise ProjectInitError(
            "project.scaffold.conflict", ".proofline", "scaffold directory type이 다릅니다."
        )
    required = (config, artifact_root, lines, criteria, *markers)
    if artifact_root_state is None:
        states = [config_state, artifact_root_state, None, None, None, None]
    else:
        lines_state = _path_state(lines)
        criteria_state = _path_state(criteria)
        for path, state in ((lines, lines_state), (criteria, criteria_state)):
            if state is not None and stat.S_ISLNK(state.st_mode):
                raise ProjectInitError(
                    "project.scaffold.symlink",
                    path.relative_to(project_root).as_posix(),
                    "scaffold path symlink는 허용하지 않습니다.",
                )
            if state is not None and not stat.S_ISDIR(state.st_mode):
                raise ProjectInitError(
                    "project.scaffold.conflict",
                    path.relative_to(project_root).as_posix(),
                    "scaffold directory type이 다릅니다.",
                )
        states = [
            config_state,
            artifact_root_state,
            lines_state,
            criteria_state,
            _path_state(markers[0]) if lines_state is not None else None,
            _path_state(markers[1]) if criteria_state is not None else None,
        ]
    if all(state is None for state in states):
        return "fresh"
    for path, state in zip(required, states, strict=True):
        relative = path.relative_to(project_root).as_posix()
        if state is not None and stat.S_ISLNK(state.st_mode):
            raise ProjectInitError(
                "project.scaffold.symlink", relative, "scaffold path symlink는 허용하지 않습니다."
            )
    if any(state is None for state in states):
        raise ProjectInitError(
            "project.scaffold.conflict", ".", "partial project scaffold가 존재합니다."
        )
    state_by_path = dict(zip(required, states, strict=True))
    directory_paths = (artifact_root, lines, criteria)
    if any(
        not stat.S_ISDIR(state_by_path[path].st_mode)  # type: ignore[union-attr]
        for path in directory_paths
    ):
        raise ProjectInitError(
            "project.scaffold.conflict", ".proofline", "scaffold directory type이 다릅니다."
        )
    expected_files = (
        (config, payload["proofline.yaml"]),
        (markers[0], payload["lines.gitkeep"]),
        (markers[1], payload["criteria.gitkeep"]),
    )
    for path, expected in expected_files:
        state = state_by_path[path]
        try:
            actual = path.read_bytes()
        except OSError as exc:
            raise ProjectInitError(
                "project.scaffold.unavailable",
                path.relative_to(project_root).as_posix(),
                f"scaffold file을 읽을 수 없습니다: {exc}",
            ) from exc
        if state is None or not stat.S_ISREG(state.st_mode) or actual != expected:
            raise ProjectInitError(
                "project.scaffold.conflict",
                path.relative_to(project_root).as_posix(),
                "scaffold file type 또는 bytes가 다릅니다.",
            )
    return "exact"


def _commit_path_at(source: Path, target_dir_fd: int, target_name: str) -> None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError) as exc:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable") from exc
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        AT_FDCWD,
        os.fsencode(source),
        target_dir_fd,
        os.fsencode(target_name),
        RENAME_NOREPLACE,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), target_name)


def _commit_path(source: Path, target: Path) -> None:
    _commit_path_at(source, AT_FDCWD, os.fspath(target))

def _require_commit_capability(project_root: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if getattr(libc, "renameat2", None) is None:
        raise ProjectInitError(
            "project.commit.unsupported", ".", "atomic no-replace commit을 지원하지 않습니다."
        )
    if not os.access(project_root, os.W_OK | os.X_OK):
        raise ProjectInitError(
            "project.permission.denied", ".", "project root에 scaffold를 생성할 수 없습니다."
        )


def _identity(path: Path) -> tuple[int, int]:
    state = path.stat(follow_symlinks=False)
    return state.st_dev, state.st_ino


def _rollback_config(
    config: Path, identity: tuple[int, int], expected: bytes
) -> None:
    try:
        state = config.stat(follow_symlinks=False)
        owned = (
            stat.S_ISREG(state.st_mode)
            and (state.st_dev, state.st_ino) == identity
            and state.st_nlink == 1
            and config.read_bytes() == expected
        )
        if not owned:
            raise ProjectInitError(
                "project.rollback.ownership", "proofline.yaml", "외부 변경 path를 보존했습니다."
            )
        config.unlink()
    except FileNotFoundError:
        return
    except ProjectInitError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ProjectInitError(
            "project.rollback.failed", "proofline.yaml", f"rollback에 실패했습니다: {exc}"
        ) from exc


def _cleanup_stage(stage: Path, identity: tuple[int, int]) -> None:
    try:
        state = stage.stat(follow_symlinks=False)
        if stat.S_ISLNK(state.st_mode) or (state.st_dev, state.st_ino) != identity:
            raise ProjectInitError(
                "project.cleanup.ownership", stage.name, "교체된 staging path를 보존했습니다."
            )
        for relative in (
            ".proofline/lines/.gitkeep",
            ".proofline/criteria/.gitkeep",
            "proofline.yaml",
        ):
            try:
                (stage / relative).unlink()
            except FileNotFoundError:
                pass
        for relative in (".proofline/lines", ".proofline/criteria", ".proofline"):
            try:
                (stage / relative).rmdir()
            except FileNotFoundError:
                pass
        stage.rmdir()
    except FileNotFoundError:
        return
    except ProjectInitError:
        raise
    except OSError as exc:
        raise ProjectInitError(
            "project.cleanup.failed", stage.name, f"staging cleanup에 실패했습니다: {exc}"
        ) from exc


def _new_stage(parent: Path | None) -> tuple[Path, tuple[int, int]]:
    stage: Path | None = None
    try:
        stage = Path(
            tempfile.mkdtemp(
                prefix=".proofline-project-",
                dir=parent,
            )
        )
        return stage, _identity(stage)
    except OSError as exc:
        cleanup_detail = ""
        if stage is not None:
            try:
                stage.rmdir()
            except OSError as cleanup_exc:
                cleanup_detail = f"; staging cleanup 실패: {cleanup_exc}"
        raise ProjectInitError(
            "project.prepare.failed",
            ".",
            f"scaffold staging에 실패했습니다: {exc}{cleanup_detail}",
        ) from exc


def _render_stage(
    stage: Path, payload: dict[str, bytes]
) -> tuple[int, tuple[int, int], tuple[int, int]]:
    owned_config_fd: int | None = None
    try:
        (stage / ".proofline/lines").mkdir(parents=True)
        (stage / ".proofline/criteria").mkdir()
        (stage / "proofline.yaml").write_bytes(payload["proofline.yaml"])
        (stage / ".proofline/lines/.gitkeep").write_bytes(payload["lines.gitkeep"])
        (stage / ".proofline/criteria/.gitkeep").write_bytes(payload["criteria.gitkeep"])
        owned_config_fd = os.open(
            stage / "proofline.yaml", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        staged_errors = validate_project(stage)
        if staged_errors:
            first = staged_errors[0]
            raise ProjectInitError(
                "project.prepare.invalid", first.path, f"{first.code}: {first.message}"
            )
        config_state = os.fstat(owned_config_fd)
        artifact_state = (stage / ".proofline").stat(follow_symlinks=False)
        return (
            owned_config_fd,
            (config_state.st_dev, config_state.st_ino),
            (artifact_state.st_dev, artifact_state.st_ino),
        )
    except Exception:
        if owned_config_fd is not None:
            os.close(owned_config_fd)
        raise


def _rollback_artifact_root(
    artifact_root: Path, identity: tuple[int, int]
) -> None:
    try:
        state = artifact_root.stat(follow_symlinks=False)
        if (
            stat.S_ISLNK(state.st_mode)
            or (state.st_dev, state.st_ino) != identity
        ):
            raise ProjectInitError(
                "project.rollback.ownership", ".proofline", "외부 변경 path를 보존했습니다."
            )
        for relative in ("lines/.gitkeep", "criteria/.gitkeep"):
            (artifact_root / relative).unlink()
        (artifact_root / "lines").rmdir()
        (artifact_root / "criteria").rmdir()
        artifact_root.rmdir()
    except ProjectInitError:
        raise
    except OSError as exc:
        raise ProjectInitError(
            "project.rollback.failed", ".proofline", f"rollback에 실패했습니다: {exc}"
        ) from exc


def initialize_project(
    project_root: Path, *, dry_run: bool = False
) -> ProjectInitResult:
    project_root = project_root.absolute()
    _require_git_root(project_root)
    payload = _payload()
    _require_commit_capability(project_root)
    state = _preflight(project_root, payload)
    if state == "exact":
        validation_errors = validate_project(project_root)
        if validation_errors:
            first = validation_errors[0]
            raise ProjectInitError(
                "project.scaffold.invalid", first.path, f"{first.code}: {first.message}"
            )
        return ProjectInitResult(SCAFFOLD_PATHS, dry_run, "already-initialized")
    if dry_run:
        stage, stage_identity = _new_stage(None)
        owned_config_fd: int | None = None
        dry_primary: ProjectInitError | None = None
        secondary: list[str] = []
        try:
            try:
                owned_config_fd, _, _ = _render_stage(stage, payload)
            except OSError as exc:
                raise ProjectInitError(
                    "project.prepare.failed", ".", f"scaffold staging에 실패했습니다: {exc}"
                ) from exc
        except ProjectInitError as exc:
            dry_primary = exc
        if owned_config_fd is not None:
            try:
                os.close(owned_config_fd)
            except OSError as exc:
                secondary.append(f"descriptor close 실패: {exc}")
        try:
            _cleanup_stage(stage, stage_identity)
        except ProjectInitError as exc:
            secondary.append(str(exc))
        if dry_primary is not None:
            if secondary:
                raise ProjectInitError(
                    "project.transaction.failed",
                    ".",
                    f"primary={dry_primary}; secondary={' | '.join(secondary)}",
                ) from dry_primary
            raise dry_primary
        if secondary:
            raise ProjectInitError(
                "project.transaction.finalize", ".", " | ".join(secondary)
            )
        return ProjectInitResult(SCAFFOLD_PATHS, True, "planned")

    stage, stage_identity = _new_stage(project_root)
    committed_config_identity: tuple[int, int] | None = None
    committed_artifact_identity: tuple[int, int] | None = None
    owned_config_fd: int | None = None
    try:
        try:
            owned_config_fd, staged_config_identity, staged_artifact_identity = (
                _render_stage(stage, payload)
            )
        except OSError as exc:
            raise ProjectInitError(
                "project.prepare.failed", ".", f"scaffold staging에 실패했습니다: {exc}"
            ) from exc
        try:
            _commit_path(stage / "proofline.yaml", project_root / "proofline.yaml")
            committed_config_identity = staged_config_identity
            _commit_path(stage / ".proofline", project_root / ".proofline")
            committed_artifact_identity = staged_artifact_identity
        except FileExistsError as exc:
            raise ProjectInitError(
                "project.commit.conflict", ".", "scaffold target이 생성 중 나타났습니다."
            ) from exc
        except OSError as exc:
            raise ProjectInitError(
                "project.commit.failed", ".", f"scaffold commit에 실패했습니다: {exc}"
            ) from exc
    except Exception as primary:
        secondary = []
        config = project_root / "proofline.yaml"
        if committed_artifact_identity is not None:
            try:
                _rollback_artifact_root(
                    project_root / ".proofline", committed_artifact_identity
                )
            except ProjectInitError as exc:
                secondary.append(str(exc))
        if committed_config_identity is not None:
            try:
                _rollback_config(config, committed_config_identity, payload["proofline.yaml"])
            except ProjectInitError as exc:
                secondary.append(str(exc))
        if owned_config_fd is not None:
            try:
                os.close(owned_config_fd)
            except OSError as exc:
                secondary.append(f"descriptor close 실패: {exc}")
        try:
            _cleanup_stage(stage, stage_identity)
        except ProjectInitError as exc:
            secondary.append(str(exc))
        if secondary:
            raise ProjectInitError(
                "project.transaction.failed",
                ".",
                f"primary={primary}; secondary={' | '.join(secondary)}",
            ) from primary
        raise
    finalize_errors: list[str] = []
    if owned_config_fd is not None:
        try:
            os.close(owned_config_fd)
        except OSError as exc:
            finalize_errors.append(f"descriptor close 실패: {exc}")
    try:
        _cleanup_stage(stage, stage_identity)
    except ProjectInitError as exc:
        finalize_errors.append(str(exc))
    if finalize_errors:
        rollback_errors: list[str] = []
        if committed_artifact_identity is not None:
            try:
                _rollback_artifact_root(
                    project_root / ".proofline", committed_artifact_identity
                )
            except ProjectInitError as exc:
                rollback_errors.append(str(exc))
        if committed_config_identity is not None:
            try:
                _rollback_config(
                    project_root / "proofline.yaml",
                    committed_config_identity,
                    payload["proofline.yaml"],
                )
            except ProjectInitError as exc:
                rollback_errors.append(str(exc))
        try:
            _cleanup_stage(stage, stage_identity)
        except ProjectInitError as exc:
            rollback_errors.append(str(exc))
        detail = " | ".join(finalize_errors + rollback_errors)
        raise ProjectInitError("project.transaction.finalize", ".", detail)
    return ProjectInitResult(SCAFFOLD_PATHS, False, "created")
