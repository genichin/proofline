"""ProofLine Line bootstrap writer."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
import tempfile
import ctypes
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Callable

try:
    import fcntl
except ModuleNotFoundError:  # Windows has no POSIX advisory lock module.
    fcntl = None  # type: ignore[assignment]

from .identity_ledger import (
    AllocationCandidate,
    IdentityLedgerError,
    prepare_allocation_candidate,
)
from .project_writer import (
    ProjectInitError,
    _commit_path_at,
)
from .project_writer import (
    _require_commit_capability as _require_project_commit_capability,
)
from .validator import validate_project

LINE_ID_RE = re.compile(r"^line-(\d{4})$")
TEMPLATE_PACKAGE = "proofline_schema_v1_templates"
RENAME_NOREPLACE_FILESYSTEMS = frozenset(
    {
        "btrfs",
        "cifs",
        "ext2",
        "ext3",
        "ext4",
        "jfs",
        "minix",
        "overlay",
        "reiserfs",
        "tmpfs",
        "vfat",
        "xfs",
    }
)


@dataclass(frozen=True)
class LineInitError(Exception):
    code: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.code}: {self.message}"


@dataclass(frozen=True)
class LineInitResult:
    paths: tuple[str, str]
    dry_run: bool


@dataclass
class _TransactionState:
    line_committed: bool = False
    ledger_mutated: bool = False


@dataclass(frozen=True)
class _LockReleaseResult:
    unlocked: bool
    closed: bool
    detail: str | None = None


def _with_secondary(primary: LineInitError, detail: str | None) -> LineInitError:
    if detail is None:
        return primary
    return LineInitError(
        primary.code,
        primary.path,
        f"{primary.message}; secondary: {detail}",
    )


def _close_descriptors(descriptors: tuple[int | None, ...]) -> str | None:
    failures: list[str] = []
    for descriptor in descriptors:
        if descriptor is None:
            continue
        try:
            os.close(descriptor)
        except OSError as exc:
            failures.append(f"fd={descriptor}: {exc}")
    if failures:
        return f"line.finalize.failed: {'; '.join(failures)}"
    return None


def _run_git(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=project_root,
        text=True,
        capture_output=True,
        check=False,
    )


def _require_git_root(project_root: Path) -> None:
    try:
        result = _run_git(project_root, "rev-parse", "--show-toplevel")
    except (OSError, UnicodeError) as exc:
        raise LineInitError(
            "git.repository.unavailable", ".", "Git 저장소를 확인할 수 없습니다."
        ) from exc
    if result.returncode != 0:
        if os.path.lexists(project_root / ".git"):
            raise LineInitError(
                "git.repository.unavailable", ".", "Git 저장소를 확인할 수 없습니다."
            )
        raise LineInitError("git.repository.required", ".", "Git 저장소가 아닙니다.")
    actual = Path(result.stdout.strip()).resolve()
    if actual != project_root.resolve():
        raise LineInitError(
            "git.root.mismatch", ".", "현재 directory가 Git 저장소 root가 아닙니다."
        )


def _require_valid_project(project_root: Path) -> None:
    diagnostics = validate_project(project_root)
    if diagnostics:
        first = diagnostics[0]
        raise LineInitError(
            "project.invalid", first.path, f"{first.code}: {first.message}"
        )


def _require_safe_paths(project_root: Path, line_id: str) -> Path:
    artifact_root = project_root / ".proofline"
    if artifact_root.is_symlink():
        raise LineInitError(
            "artifact_root.symlink", ".proofline", "artifact root symlink는 허용하지 않습니다."
        )
    lines_root = artifact_root / "lines"
    if lines_root.is_symlink():
        raise LineInitError(
            "lines_root.symlink",
            ".proofline/lines",
            "Line root symlink는 허용하지 않습니다.",
        )
    if not lines_root.is_dir():
        raise LineInitError(
            "lines_root.missing", ".proofline/lines", "Line root directory가 없습니다."
        )
    target = lines_root / line_id
    if target.exists() or target.is_symlink():
        raise LineInitError(
            "line.path.exists",
            target.relative_to(project_root).as_posix(),
            "대상 Line path가 이미 존재합니다.",
        )
    return target


def _directory_identity(path: Path) -> tuple[int, int]:
    state = path.stat(follow_symlinks=False)
    return state.st_dev, state.st_ino


def _require_directory_identity(
    path: Path, expected: tuple[int, int], code: str, display_path: str
) -> None:
    try:
        state = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise LineInitError(code, display_path, f"directory identity 확인에 실패했습니다: {exc}") from exc
    if (
        stat.S_ISLNK(state.st_mode)
        or not stat.S_ISDIR(state.st_mode)
        or (state.st_dev, state.st_ino) != expected
    ):
        raise LineInitError(code, display_path, "directory identity가 변경되었습니다.")


def _open_verified_directory(
    path: Path, expected: tuple[int, int], code: str, display_path: str
) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        state = os.fstat(descriptor)
    except OSError as exc:
        primary = LineInitError(
            code, display_path, f"directory anchor 생성에 실패했습니다: {exc}"
        )
        raise _with_secondary(primary, _close_descriptors((descriptor,))) from exc
    if (state.st_dev, state.st_ino) != expected:
        primary = LineInitError(code, display_path, "directory identity가 변경되었습니다.")
        raise _with_secondary(primary, _close_descriptors((descriptor,)))
    return descriptor


def _open_verified_child(
    parent_fd: int,
    name: str,
    expected: tuple[int, int],
    code: str,
    display_path: str,
) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor: int | None = None
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
        state = os.fstat(descriptor)
    except OSError as exc:
        primary = LineInitError(
            code, display_path, f"directory anchor 생성에 실패했습니다: {exc}"
        )
        raise _with_secondary(primary, _close_descriptors((descriptor,))) from exc
    if (state.st_dev, state.st_ino) != expected:
        primary = LineInitError(code, display_path, "directory identity가 변경되었습니다.")
        raise _with_secondary(primary, _close_descriptors((descriptor,)))
    return descriptor


def _commit_line_path(source: Path, target: Path, parent_fd: int) -> None:
    _commit_path_at(source, parent_fd, target.name)


def _exchange_path_at(source: Path, target_dir_fd: int, target_name: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = libc.renameat2
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    if renameat2(
        -100, os.fsencode(source), target_dir_fd, os.fsencode(target_name), 2
    ) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), target_name)


def _read_regular_at(
    parent_fd: int, name: str
) -> tuple[tuple[int, int], bytes, str | None]:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd
        )
        state = os.fstat(descriptor)
        if not stat.S_ISREG(state.st_mode):
            raise OSError(f"{name} is not a regular file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 65536):
            chunks.append(chunk)
    except OSError as exc:
        close_detail = _close_descriptors((descriptor,))
        if close_detail:
            raise OSError(f"{exc}; secondary: {close_detail}") from exc
        raise
    close_detail = _close_descriptors((descriptor,))
    return (state.st_dev, state.st_ino), b"".join(chunks), close_detail


def _new_ledger_stage(project_root: Path, candidate: bytes) -> tuple[Path, tuple[int, int]]:
    descriptor, raw_path = tempfile.mkstemp(prefix=".proofline-ledger-", dir=project_root)
    path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(candidate)
            stream.flush()
            os.fsync(stream.fileno())
        return path, _directory_identity(path)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _commit_ledger_path(
    stage: Path,
    artifact_root_fd: int,
    plan: AllocationCandidate,
    transaction: _TransactionState,
) -> None:
    name = "line-identities.json"
    if plan.prior_bytes is None:
        try:
            _commit_path_at(stage, artifact_root_fd, name)
            transaction.ledger_mutated = True
        except FileExistsError as exc:
            raise LineInitError(
                "ledger.concurrent.changed", ".proofline/line-identities.json", "ledger가 생성 중 나타났습니다."
            ) from exc
        return
    try:
        _exchange_path_at(stage, artifact_root_fd, name)
        transaction.ledger_mutated = True
    except OSError as exc:
        raise LineInitError(
            "ledger.commit.failed", ".proofline/line-identities.json", f"ledger commit에 실패했습니다: {exc}"
        ) from exc


def _validate_displaced_ledger(
    stage: Path,
    artifact_root_fd: int,
    plan: AllocationCandidate,
    transaction: _TransactionState,
) -> None:
    assert plan.prior_bytes is not None
    name = "line-identities.json"
    try:
        displaced_identity = _directory_identity(stage)
        displaced_bytes = stage.read_bytes()
    except OSError as exc:
        raise LineInitError(
            "ledger.commit.failed", ".proofline/line-identities.json", f"ledger commit에 실패했습니다: {exc}"
        ) from exc
    if displaced_identity != plan.prior_identity or displaced_bytes != plan.prior_bytes:
        try:
            _exchange_path_at(stage, artifact_root_fd, name)
            transaction.ledger_mutated = False
        except OSError as rollback_error:
            raise LineInitError(
                "ledger.concurrent.changed",
                ".proofline/line-identities.json",
                f"외부 ledger 변경을 보존하지 못했습니다.; secondary: ledger.rollback.failed: {rollback_error}",
            ) from rollback_error
        raise LineInitError(
            "ledger.concurrent.changed",
            ".proofline/line-identities.json",
            "ledger가 preflight 이후 변경되어 외부 bytes를 보존했습니다.",
        )


def _owned_transaction_is_exact(
    target: Path,
    target_identity: tuple[int, int],
    expected: dict[str, bytes],
    artifact_root_fd: int,
    candidate_identity: tuple[int, int],
    candidate_bytes: bytes,
) -> bool:
    try:
        ledger_identity, ledger_bytes, close_detail = _read_regular_at(
            artifact_root_fd, "line-identities.json"
        )
        state = target.stat(follow_symlinks=False)
        return (
            close_detail is None
            and stat.S_ISDIR(state.st_mode)
            and (state.st_dev, state.st_ino) == target_identity
            and {path.name for path in target.iterdir()} == set(expected)
            and ledger_identity == candidate_identity
            and ledger_bytes == candidate_bytes
            and all((target / name).read_bytes() == content for name, content in expected.items())
        )
    except OSError:
        return False


def _rollback_owned_ledger(
    stage: Path,
    artifact_root_fd: int,
    plan: AllocationCandidate,
    candidate_identity: tuple[int, int],
) -> str | None:
    try:
        current_identity, current_bytes, close_detail = _read_regular_at(
            artifact_root_fd, "line-identities.json"
        )
        if current_identity != candidate_identity or current_bytes != plan.candidate_bytes:
            ownership = "ledger.rollback.ownership: 외부 변경 ledger를 보존했습니다."
            return f"{ownership}; {close_detail}" if close_detail else ownership
        if plan.prior_bytes is None:
            os.unlink("line-identities.json", dir_fd=artifact_root_fd)
        else:
            stage_identity = _directory_identity(stage)
            if stage_identity != plan.prior_identity or stage.read_bytes() != plan.prior_bytes:
                ownership = "ledger.rollback.ownership: 외부 변경 prior ledger를 보존했습니다."
                return f"{ownership}; {close_detail}" if close_detail else ownership
            _exchange_path_at(stage, artifact_root_fd, "line-identities.json")
        return close_detail
    except OSError as exc:
        return f"ledger.rollback.failed: {exc}"


def _rollback_owned_target(
    parent_fd: int,
    target_name: str,
    identity: tuple[int, int],
    expected: dict[str, bytes],
) -> str | None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        target_fd = os.open(target_name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise LineInitError(
            "line.rollback.failed", target_name, f"anchored target open에 실패했습니다: {exc}"
        ) from exc

    secondary_details: list[str] = []

    def read_artifact(name: str) -> bytes:
        artifact_fd: int | None = None
        try:
            artifact_fd = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=target_fd,
            )
            chunks: list[bytes] = []
            while chunk := os.read(artifact_fd, 65536):
                chunks.append(chunk)
            content = b"".join(chunks)
        except OSError as exc:
            primary = LineInitError(
                "line.rollback.failed",
                target_name,
                f"rollback artifact를 읽을 수 없습니다: {exc}",
            )
            raise _with_secondary(
                primary, _close_descriptors((artifact_fd,))
            ) from exc
        close_detail = _close_descriptors((artifact_fd,))
        if close_detail is not None:
            secondary_details.append(
                f"rollback artifact descriptor finalization 실패; {close_detail}"
            )
        return content

    def require_owned(*, allow_missing_artifacts: bool) -> None:
        state = os.fstat(target_fd)
        if not stat.S_ISDIR(state.st_mode) or (state.st_dev, state.st_ino) != identity:
            raise LineInitError(
                "line.rollback.ownership", target_name, "외부 변경 target을 보존했습니다."
            )
        for name, content in expected.items():
            try:
                artifact_state = os.stat(name, dir_fd=target_fd, follow_symlinks=False)
            except FileNotFoundError:
                if allow_missing_artifacts:
                    continue
                raise LineInitError(
                    "line.rollback.ownership", target_name, "외부 변경 target을 보존했습니다."
                ) from None
            if not stat.S_ISREG(artifact_state.st_mode) or read_artifact(name) != content:
                raise LineInitError(
                    "line.rollback.ownership", target_name, "외부 변경 target을 보존했습니다."
                )

    def cleanup_once(*, retry: bool) -> None:
        require_owned(allow_missing_artifacts=retry)
        for name in expected:
            try:
                os.unlink(name, dir_fd=target_fd)
            except FileNotFoundError:
                if not retry:
                    raise
        os.rmdir(target_name, dir_fd=parent_fd)

    rollback_detail: str | None = None
    try:
        try:
            cleanup_once(retry=False)
        except LineInitError:
            raise
        except OSError as first:
            try:
                cleanup_once(retry=True)
            except LineInitError:
                raise
            except OSError as second:
                raise LineInitError(
                    "line.rollback.failed",
                    target_name,
                    f"rollback 재시도에 실패했습니다: {first}; {second}",
                ) from second
            rollback_detail = f"line.rollback.failed: {first}"
    except LineInitError as primary:
        close_detail = _close_descriptors((target_fd,))
        details = [*secondary_details]
        if close_detail is not None:
            details.append(close_detail)
        if not details:
            raise
        raise _with_secondary(primary, "; ".join(details)) from primary
    except OSError as exc:
        primary = LineInitError(
            "line.rollback.failed", target_name, f"rollback에 실패했습니다: {exc}"
        )
        close_detail = _close_descriptors((target_fd,))
        details = [*secondary_details]
        if close_detail is not None:
            details.append(close_detail)
        raise _with_secondary(primary, "; ".join(details) if details else None) from exc

    close_detail = _close_descriptors((target_fd,))
    if close_detail is not None:
        secondary_details.append(f"rollback descriptor finalization 실패; {close_detail}")
    details = [detail for detail in (rollback_detail, *secondary_details) if detail]
    if not details:
        return None
    combined = "; ".join(details)
    if combined.startswith("line.rollback.failed:"):
        return combined
    return f"line.rollback.failed: {combined}"


def _new_stage(project_root: Path, line_id: str) -> tuple[Path, tuple[int, int]]:
    stage: Path | None = None
    try:
        stage = Path(tempfile.mkdtemp(prefix=f".{line_id}-", dir=project_root))
        return stage, _directory_identity(stage)
    except OSError as exc:
        cleanup_detail = ""
        if stage is not None:
            try:
                stage.rmdir()
            except OSError as cleanup_exc:
                cleanup_detail = (
                    f"; secondary: line.cleanup.failed: staging cleanup 실패: {cleanup_exc}"
                )
        raise LineInitError(
            "line.prepare.failed",
            ".",
            f"Line staging에 실패했습니다: {exc}{cleanup_detail}",
        ) from exc


def _linux_filesystem_type(path: Path) -> str:
    try:
        resolved = path.resolve(strict=True)
        mount_lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LineInitError(
            "line.commit.unsupported",
            ".",
            f"filesystem capability를 확인할 수 없습니다: {exc}",
        ) from exc
    matches: list[tuple[int, str]] = []
    for line in mount_lines:
        fields = line.split()
        try:
            separator = fields.index("-")
            mount_point = Path(
                fields[4]
                .replace("\\040", " ")
                .replace("\\011", "\t")
                .replace("\\012", "\n")
                .replace("\\134", "\\")
            )
            filesystem = fields[separator + 1]
        except (IndexError, ValueError):
            continue
        if resolved == mount_point or resolved.is_relative_to(mount_point):
            matches.append((len(mount_point.parts), filesystem))
    if not matches:
        raise LineInitError(
            "line.commit.unsupported",
            ".",
            "project filesystem mount를 식별할 수 없습니다.",
        )
    return max(matches)[1]


def _require_commit_capability(project_root: Path) -> None:
    if sys.platform != "linux":
        raise LineInitError(
            "line.commit.unsupported",
            ".",
            "atomic no-replace Line commit은 Linux에서만 지원합니다.",
        )
    required_constants = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
    missing = [name for name in required_constants if not hasattr(os, name)]
    if missing:
        raise LineInitError(
            "line.commit.unsupported",
            ".",
            f"필수 directory capability가 없습니다: {', '.join(missing)}",
        )
    release_match = re.match(r"(\d+)\.(\d+)", os.uname().release)
    if release_match is None or tuple(map(int, release_match.groups())) < (4, 9):
        raise LineInitError(
            "line.commit.unsupported",
            ".",
            "Linux kernel 4.9 이상의 renameat2 지원이 필요합니다.",
        )
    filesystem = _linux_filesystem_type(project_root)
    if filesystem not in RENAME_NOREPLACE_FILESYSTEMS:
        raise LineInitError(
            "line.commit.unsupported",
            ".",
            f"지원되지 않은 filesystem입니다: {filesystem}",
        )
    lines_root = project_root / ".proofline" / "lines"
    try:
        if project_root.stat().st_dev != lines_root.stat().st_dev:
            raise LineInitError(
                "line.commit.unsupported",
                ".proofline/lines",
                "staging과 Line target이 서로 다른 filesystem에 있습니다.",
            )
    except OSError as exc:
        raise LineInitError(
            "line.commit.unsupported",
            ".proofline/lines",
            f"filesystem identity를 확인할 수 없습니다: {exc}",
        ) from exc
    if not os.access(lines_root, os.W_OK | os.X_OK):
        raise LineInitError(
            "line.permission.denied",
            ".proofline/lines",
            "Line target directory에 write/search 권한이 없습니다.",
        )
    try:
        _require_project_commit_capability(project_root)
    except ProjectInitError as exc:
        code = (
            "line.commit.unsupported"
            if exc.code == "project.commit.unsupported"
            else "line.permission.denied"
        )
        raise LineInitError(code, exc.path, exc.message) from exc


def _cleanup_stage(
    stage: Path, identity: tuple[int, int], artifact_names: tuple[str, ...]
) -> str | None:
    def cleanup_once() -> None:
        state = stage.stat(follow_symlinks=False)
        if stat.S_ISLNK(state.st_mode) or (state.st_dev, state.st_ino) != identity:
            raise LineInitError(
                "line.cleanup.ownership", stage.name, "교체된 staging path를 보존했습니다."
            )
        for name in artifact_names:
            (stage / name).unlink(missing_ok=True)
        stage.rmdir()

    try:
        cleanup_once()
        return None
    except LineInitError:
        raise
    except OSError as first:
        try:
            cleanup_once()
        except LineInitError:
            raise
        except OSError as second:
            raise LineInitError(
                "line.cleanup.failed",
                stage.name,
                f"cleanup 재시도에 실패했습니다: {first}; {second}",
            ) from second
        return f"line.cleanup.failed: {first}"


def _read_template(name: str) -> str:
    relative = f"templates/schema-v1/artifacts/{name}"
    module_path = Path(__file__).resolve()
    source_root = module_path.parents[2]
    if module_path.parents[1].name == "src" and (source_root / "pyproject.toml").is_file():
        source_resource = source_root / relative
        try:
            return source_resource.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise LineInitError(
                "template.missing", relative, "source template을 읽을 수 없습니다."
            ) from exc
    try:
        resource = files(TEMPLATE_PACKAGE).joinpath("artifacts", name)
        return resource.read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise LineInitError(
            "template.missing", relative, "package-owned template을 읽을 수 없습니다."
        ) from exc


def _render(line_id: str, title: str) -> tuple[str, str]:
    suffix = line_id.removeprefix("line-")
    values = {
        "{{LINE_ID}}": line_id,
        "{{DISCOVERY_ID}}": f"dcy-{suffix}",
        "{{TITLE}}": title,
    }
    line_text = _read_template("line.md")
    discovery_text = _read_template("discovery.md")
    for token, value in values.items():
        line_text = line_text.replace(token, value)
        discovery_text = discovery_text.replace(token, value)
    unresolved = sorted(
        {
            match
            for text in (line_text, discovery_text)
            for match in re.findall(r"\{\{[^{}\n]+\}\}", text)
            if not match.startswith(
                ("{{TODO:", "{{NEEDS_EVIDENCE:", "{{UNKNOWN:")
            )
        }
    )
    if unresolved:
        raise LineInitError(
            "template.variable.unresolved",
            "templates/schema-v1",
            f"치환되지 않은 template variable: {', '.join(unresolved)}",
        )
    return line_text, discovery_text


def _validate_rendered(line_id: str, line_text: str, discovery_text: str) -> None:
    with tempfile.TemporaryDirectory(prefix="proofline-render-") as raw:
        root = Path(raw)
        (root / "proofline.yaml").write_text(
            "schema_version: 1\nartifact_root: .proofline\n", encoding="utf-8"
        )
        target = root / ".proofline" / "lines" / line_id
        target.mkdir(parents=True)
        (root / ".proofline" / "criteria").mkdir()
        suffix = line_id.removeprefix("line-")
        (target / f"{line_id}.md").write_text(line_text, encoding="utf-8")
        (target / f"dcy-{suffix}.md").write_text(discovery_text, encoding="utf-8")
        diagnostics = validate_project(root)
        if diagnostics:
            first = diagnostics[0]
            raise LineInitError(
                "render.invalid", first.path, f"{first.code}: {first.message}"
            )


def _acquire_repository_lock(project_root: Path) -> int:
    if fcntl is None:
        raise LineInitError(
            "line.commit.unsupported",
            ".",
            "repository lock은 POSIX fcntl을 지원하는 platform에서만 사용할 수 있습니다.",
        )
    try:
        common = _run_git(project_root, "rev-parse", "--git-common-dir")
    except (OSError, UnicodeError) as exc:
        raise LineInitError(
            "line.lock.unavailable", ".git", "repository lock 위치를 확인할 수 없습니다."
        ) from exc
    if common.returncode != 0:
        raise LineInitError(
            "line.lock.unavailable", ".git", "repository lock 위치를 확인할 수 없습니다."
        )
    common_path = Path(common.stdout.strip())
    if not common_path.is_absolute():
        common_path = project_root / common_path
    descriptor: int | None = None
    try:
        descriptor = os.open(
            common_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except BlockingIOError as exc:
        detail = _close_descriptors((descriptor,))
        primary = LineInitError(
            "line.lock.contended", ".git", "다른 ProofLine invocation이 실행 중입니다."
        )
        raise _with_secondary(primary, detail) from exc
    except OSError as exc:
        detail = _close_descriptors((descriptor,))
        primary = LineInitError(
            "line.lock.unavailable", ".git", f"repository lock을 획득할 수 없습니다: {exc}"
        )
        raise _with_secondary(primary, detail) from exc


def _release_repository_lock(descriptor: int) -> _LockReleaseResult:
    if fcntl is None:
        return _LockReleaseResult(
            unlocked=False,
            closed=False,
            detail="line.lock.release.failed: POSIX fcntl을 사용할 수 없습니다.",
        )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError as exc:
        return _LockReleaseResult(
            unlocked=False,
            closed=False,
            detail=f"line.lock.release.failed: {exc}",
        )
    close_detail = _close_descriptors((descriptor,))
    return _LockReleaseResult(
        unlocked=True,
        closed=close_detail is None,
        detail=close_detail,
    )


def _initialize_line_unlocked(
    project_root: Path,
    line_id: str,
    title: str,
    *,
    dry_run: bool = False,
    finalizer: Callable[[], _LockReleaseResult] | None = None,
) -> LineInitResult:
    project_root = project_root.absolute()
    if LINE_ID_RE.fullmatch(line_id) is None:
        raise LineInitError(
            "line.id.invalid", line_id, "Line ID는 line-NNNN 형식이어야 합니다."
        )
    title = title.strip()
    if not title:
        raise LineInitError("line.title.empty", line_id, "제목은 비어 있을 수 없습니다.")
    if any(ord(character) < 32 or ord(character) == 127 for character in title):
        raise LineInitError(
            "line.title.invalid", line_id, "제목은 control character가 없는 한 줄이어야 합니다."
        )

    _require_git_root(project_root)
    artifact_root = project_root / ".proofline"
    if artifact_root.is_symlink():
        raise LineInitError(
            "artifact_root.symlink", ".proofline", "artifact root symlink는 허용하지 않습니다."
        )
    target = _require_safe_paths(project_root, line_id)
    project_root_identity = _directory_identity(project_root)
    artifact_root_identity = _directory_identity(artifact_root)
    lines_root_identity = _directory_identity(target.parent)
    _require_valid_project(project_root)
    try:
        allocation = prepare_allocation_candidate(project_root, line_id)
    except IdentityLedgerError as exc:
        raise LineInitError(exc.code, exc.path, exc.message) from exc
    try:
        line_text, discovery_text = _render(line_id, title)
    except LineInitError:
        raise
    except UnicodeError as exc:
        raise LineInitError(
            "template.malformed",
            "templates/schema-v1/artifacts",
            f"template encoding이 올바르지 않습니다: {exc}",
        ) from exc
    except OSError as exc:
        raise LineInitError(
            "template.unavailable",
            "templates/schema-v1/artifacts",
            f"template preparation에 실패했습니다: {exc}",
        ) from exc
    try:
        _validate_rendered(line_id, line_text, discovery_text)
    except LineInitError:
        raise
    except OSError as exc:
        raise LineInitError(
            "render.unavailable", line_id, f"rendered validation에 실패했습니다: {exc}"
        ) from exc
    _require_commit_capability(project_root)

    suffix = line_id.removeprefix("line-")
    paths = (
        f".proofline/lines/{line_id}/{line_id}.md",
        f".proofline/lines/{line_id}/dcy-{suffix}.md",
    )
    if dry_run:
        return LineInitResult(paths=paths, dry_run=True)

    project_root_fd = _open_verified_directory(
        project_root, project_root_identity, "project_root.changed", "."
    )
    artifact_root_fd: int | None = None
    try:
        artifact_root_fd = _open_verified_child(
            project_root_fd,
            ".proofline",
            artifact_root_identity,
            "artifact_root.changed",
            ".proofline",
        )
        lines_root_fd = _open_verified_child(
            artifact_root_fd,
            "lines",
            lines_root_identity,
            "lines_root.changed",
            ".proofline/lines",
        )
    except LineInitError as primary:
        close_detail = _close_descriptors((artifact_root_fd, project_root_fd))
        if close_detail is None:
            raise
        raise _with_secondary(primary, close_detail) from primary
    anchor_fds = (lines_root_fd, artifact_root_fd, project_root_fd)
    try:
        temp, temp_identity = _new_stage(project_root, line_id)
    except LineInitError as primary:
        close_detail = _close_descriptors(anchor_fds)
        if close_detail is None:
            raise
        raise _with_secondary(primary, close_detail) from primary
    try:
        ledger_stage, ledger_stage_identity = _new_ledger_stage(
            project_root, allocation.candidate_bytes
        )
    except OSError as exc:
        cleanup_detail = _cleanup_stage(temp, temp_identity, ())
        close_detail = _close_descriptors(anchor_fds)
        details = [detail for detail in (cleanup_detail, close_detail) if detail]
        primary = LineInitError(
            "ledger.prepare.failed",
            ".proofline/line-identities.json",
            f"ledger staging에 실패했습니다: {exc}",
        )
        raise _with_secondary(primary, "; ".join(details) if details else None) from exc
    expected = {
        f"{line_id}.md": line_text.encode("utf-8"),
        f"dcy-{suffix}.md": discovery_text.encode("utf-8"),
    }
    transaction = _TransactionState()
    lock_release: _LockReleaseResult | None = None
    rollback_fd: int | None = None
    ledger_rollback_fd: int | None = None
    closed_fds: set[int] = set()
    try:
        try:
            (temp / f"{line_id}.md").write_text(line_text, encoding="utf-8")
            (temp / f"dcy-{suffix}.md").write_text(discovery_text, encoding="utf-8")
        except OSError as exc:
            raise LineInitError(
                "line.write.failed", paths[0], f"Line artifact write에 실패했습니다: {exc}"
            ) from exc
        if target.exists() or target.is_symlink():
            raise LineInitError(
                "line.path.exists", paths[0], "대상 Line path가 생성 중 나타났습니다."
            )
        _require_directory_identity(
            artifact_root, artifact_root_identity, "artifact_root.changed", ".proofline"
        )
        _require_directory_identity(
            target.parent, lines_root_identity, "lines_root.changed", ".proofline/lines"
        )
        try:
            rollback_fd = os.dup(lines_root_fd)
            ledger_rollback_fd = os.dup(artifact_root_fd)
        except OSError as exc:
            raise LineInitError(
                "line.finalize.failed", paths[0], f"rollback anchor 복제에 실패했습니다: {exc}"
            ) from exc
        try:
            _commit_line_path(temp, target, lines_root_fd)
            transaction.line_committed = True
        except FileExistsError as exc:
            raise LineInitError(
                "line.path.exists", paths[0], "대상 Line path가 생성 중 나타났습니다."
            ) from exc
        except OSError as exc:
            raise LineInitError(
                "line.commit.failed", paths[0], f"atomic Line commit에 실패했습니다: {exc}"
            ) from exc
        _commit_ledger_path(ledger_stage, artifact_root_fd, allocation, transaction)
        if allocation.prior_bytes is not None:
            _validate_displaced_ledger(
                ledger_stage, artifact_root_fd, allocation, transaction
            )
        _require_directory_identity(
            artifact_root, artifact_root_identity, "artifact_root.changed", ".proofline"
        )
        _require_directory_identity(
            target.parent, lines_root_identity, "lines_root.changed", ".proofline/lines"
        )
        _require_valid_project(project_root)
        for descriptor in anchor_fds:
            try:
                os.close(descriptor)
                closed_fds.add(descriptor)
            except OSError as exc:
                raise LineInitError(
                    "line.finalize.failed", paths[0], f"directory anchor close에 실패했습니다: {exc}"
                ) from exc
        if finalizer is not None:
            lock_release = finalizer()
            if lock_release.detail:
                raise LineInitError(
                    "line.finalize.failed", paths[0], lock_release.detail
                )
        for descriptor in (rollback_fd, ledger_rollback_fd):
            try:
                os.close(descriptor)
                closed_fds.add(descriptor)
            except OSError as exc:
                raise LineInitError(
                    "line.finalize.failed", paths[0], f"rollback anchor close에 실패했습니다: {exc}"
                ) from exc
        if ledger_stage.exists():
            try:
                ledger_stage.unlink()
            except OSError as exc:
                raise LineInitError(
                    "line.finalize.failed", paths[0], f"prior ledger cleanup에 실패했습니다: {exc}"
                ) from exc
    except Exception as primary:
        details: list[str] = []
        reacquired_lock_fd: int | None = None
        rollback_authorized = True
        preserve_advanced = False
        if lock_release is not None and lock_release.unlocked:
            try:
                reacquired_lock_fd = _acquire_repository_lock(project_root)
            except LineInitError as recovery_error:
                details.append(str(recovery_error))
                rollback_authorized = False
        ledger_anchor = (
            ledger_rollback_fd
            if ledger_rollback_fd is not None and ledger_rollback_fd not in closed_fds
            else artifact_root_fd if artifact_root_fd not in closed_fds else None
        )
        line_anchor = (
            rollback_fd
            if rollback_fd is not None and rollback_fd not in closed_fds
            else lines_root_fd if lines_root_fd not in closed_fds else None
        )
        recovered_fds: list[int] = []
        if rollback_authorized and transaction.ledger_mutated and ledger_anchor is None:
            try:
                ledger_anchor = _open_verified_directory(
                    artifact_root,
                    artifact_root_identity,
                    "artifact_root.changed",
                    ".proofline",
                )
                recovered_fds.append(ledger_anchor)
            except LineInitError as recovery_error:
                details.append(str(recovery_error))
        if rollback_authorized and transaction.line_committed and line_anchor is None:
            try:
                line_anchor = _open_verified_directory(
                    target.parent,
                    lines_root_identity,
                    "lines_root.changed",
                    ".proofline/lines",
                )
                recovered_fds.append(line_anchor)
            except LineInitError as recovery_error:
                details.append(str(recovery_error))
        if (
            lock_release is not None
            and lock_release.unlocked
            and reacquired_lock_fd is not None
            and transaction.ledger_mutated
            and transaction.line_committed
            and ledger_anchor is not None
        ):
            if not _owned_transaction_is_exact(
                target,
                temp_identity,
                expected,
                ledger_anchor,
                ledger_stage_identity,
                allocation.candidate_bytes,
            ):
                try:
                    _require_valid_project(project_root)
                    preserve_advanced = True
                except LineInitError as validation_error:
                    details.append(str(validation_error))
        if rollback_authorized and transaction.ledger_mutated and not preserve_advanced:
            if ledger_anchor is None:
                details.append("ledger.rollback.failed: usable artifact anchor가 없습니다.")
            else:
                detail = _rollback_owned_ledger(
                    ledger_stage,
                    ledger_anchor,
                    allocation,
                    ledger_stage_identity,
                )
                if detail:
                    details.append(detail)
        try:
            if (
                transaction.line_committed
                and line_anchor is not None
                and rollback_authorized
                and not preserve_advanced
            ):
                detail = _rollback_owned_target(
                    line_anchor, target.name, temp_identity, expected
                )
                if detail:
                    details.append(detail)
            elif (
                transaction.line_committed
                and rollback_authorized
                and not preserve_advanced
            ):
                details.append("line.rollback.failed: usable parent anchor가 없습니다.")
            elif not transaction.line_committed:
                detail = _cleanup_stage(temp, temp_identity, tuple(expected))
                if detail:
                    details.append(detail)
        except LineInitError as cleanup_error:
            details.append(str(cleanup_error))
        except OSError as cleanup_error:
            code = "line.rollback.failed" if transaction.line_committed else "line.cleanup.failed"
            details.append(f"{code}: {cleanup_error}")
        if ledger_stage.exists():
            try:
                ledger_stage.unlink()
            except OSError as cleanup_error:
                details.append(f"ledger.cleanup.failed: {cleanup_error}")
        for descriptor in (*anchor_fds, rollback_fd, ledger_rollback_fd, *recovered_fds):
            if descriptor is None or descriptor in closed_fds:
                continue
            try:
                os.close(descriptor)
                closed_fds.add(descriptor)
            except OSError as close_error:
                details.append(f"line.finalize.failed: {close_error}")
        if reacquired_lock_fd is not None:
            release = _release_repository_lock(reacquired_lock_fd)
            if release.detail:
                details.append(release.detail)
        if details and isinstance(primary, LineInitError):
            raise LineInitError(
                primary.code, primary.path, f"{primary.message}; secondary: {'; '.join(details)}"
            ) from primary
        raise
    return LineInitResult(paths=paths, dry_run=False)


def initialize_line(
    project_root: Path, line_id: str, title: str, *, dry_run: bool = False
) -> LineInitResult:
    if dry_run:
        return _initialize_line_unlocked(project_root, line_id, title, dry_run=True)
    absolute_root = project_root.absolute()
    _require_git_root(absolute_root)
    descriptor: int | None = _acquire_repository_lock(absolute_root)

    def finalize_lock() -> _LockReleaseResult:
        nonlocal descriptor
        owned_descriptor = descriptor
        assert owned_descriptor is not None
        descriptor = None
        release = _release_repository_lock(owned_descriptor)
        if not release.unlocked:
            descriptor = owned_descriptor
        return release

    try:
        result = _initialize_line_unlocked(
            project_root, line_id, title, finalizer=finalize_lock
        )
    except Exception as primary:
        detail: str | None = None
        if descriptor is not None:
            detail = _release_repository_lock(descriptor).detail
        if detail and isinstance(primary, LineInitError):
            raise _with_secondary(primary, detail) from primary
        raise
    return result
