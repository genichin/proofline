"""Atomic ProofLine Line bootstrap writer."""

from __future__ import annotations

import errno
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

try:
    import fcntl
except ModuleNotFoundError:  # pragma: no cover - POSIX writer contract
    fcntl = None  # type: ignore[assignment]

from .identity_allocator import (
    ALLOCATOR_PATH,
    IdentityAllocatorError,
    advanced,
    read_allocator,
)
from .project_writer import _commit_path_at
from .transaction import (
    DirectoryPin,
    FileSnapshot,
    TreeSnapshot,
    exchange_owned_file,
    identity,
    open_child_directory,
    open_directory,
    read_regular_at,
    read_regular,
    remove_owned_tree,
    snapshot_tree,
    verify_directory,
    verify_regular,
)
from .validator import validate_project

TEMPLATE_PACKAGE = "proofline_schema_v1_templates"


@dataclass
class LineInitError(Exception):
    code: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.code}: {self.message}"


@dataclass(frozen=True)
class LineInitResult:
    line_id: str
    paths: tuple[str, str]
    dry_run: bool


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)


def _require_git_root(root: Path) -> None:
    try:
        result = _run_git(root, "rev-parse", "--show-toplevel")
    except (OSError, UnicodeError) as exc:
        raise LineInitError("git.repository.unavailable", ".", "Git 저장소를 확인할 수 없습니다.") from exc
    if result.returncode != 0:
        code = "git.repository.unavailable" if os.path.lexists(root / ".git") else "git.repository.required"
        raise LineInitError(code, ".", "Git 저장소를 확인할 수 없습니다.")
    try:
        actual = Path(result.stdout.strip()).resolve()
        expected = root.resolve()
    except (OSError, RuntimeError) as exc:
        raise LineInitError("git.repository.unavailable", ".", "Git 저장소 경로를 확인할 수 없습니다.") from exc
    if actual != expected:
        raise LineInitError("git.root.mismatch", ".", "현재 directory가 Git 저장소 root가 아닙니다.")


def _acquire_repository_lock(root: Path) -> int:
    if fcntl is None:
        raise LineInitError("line.commit.unsupported", ".git", "POSIX repository lock이 필요합니다.")
    result = _run_git(root, "rev-parse", "--git-common-dir")
    if result.returncode != 0:
        raise LineInitError("line.lock.unavailable", ".git", "repository common directory를 확인할 수 없습니다.")
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
        raise LineInitError("line.lock.unavailable", ".git", str(exc)) from exc


def _release_repository_lock(descriptor: int) -> None:
    assert fcntl is not None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _read_template(name: str) -> str:
    relative = f"templates/schema-v1/artifacts/{name}"
    source = Path(__file__).resolve().parents[2] / relative
    try:
        if source.is_file():
            return source.read_text(encoding="utf-8")
        return files(TEMPLATE_PACKAGE).joinpath("artifacts", name).read_text(encoding="utf-8")
    except (OSError, ModuleNotFoundError) as exc:
        raise LineInitError("template.missing", relative, "template resource를 읽을 수 없습니다.") from exc


def _render(line_id: str, title: str) -> tuple[str, str]:
    suffix = line_id.removeprefix("line-")
    values = {"{{LINE_ID}}": line_id, "{{DISCOVERY_ID}}": f"dcy-{suffix}", "{{TITLE}}": title}
    rendered = []
    for name in ("line.md", "discovery.md"):
        text = _read_template(name)
        for token, value in values.items():
            text = text.replace(token, value)
        unresolved = [item for item in re.findall(r"\{\{[^{}\n]+\}\}", text) if not item.startswith(("{{TODO:", "{{UNKNOWN:", "{{NEEDS_EVIDENCE:"))]
        if unresolved:
            raise LineInitError("template.variable.unresolved", name, ", ".join(unresolved))
        rendered.append(text)
    return rendered[0], rendered[1]


def _require_title(title: str) -> str:
    title = title.strip()
    if not title:
        raise LineInitError("line.title.empty", "--title", "제목은 비어 있을 수 없습니다.")
    if "\n" in title or "\r" in title or any(ord(char) < 32 or ord(char) == 127 for char in title):
        raise LineInitError("line.title.invalid", "--title", "제목은 control character가 없는 한 줄이어야 합니다.")
    return title


def _replace_allocator(
    root: Path,
    expected_identity: tuple[int, int],
    data: bytes,
    expected_data: bytes | None = None,
) -> tuple[int, int]:
    path = root / ALLOCATOR_PATH
    try:
        current = read_regular(path)
    except OSError as exc:
        raise LineInitError("allocator.concurrent.changed", ALLOCATOR_PATH, "allocator가 preflight 이후 변경되었습니다.") from exc
    if current.identity != expected_identity or (
        expected_data is not None and current.data != expected_data
    ):
        raise LineInitError("allocator.concurrent.changed", ALLOCATOR_PATH, "allocator가 preflight 이후 변경되었습니다.")
    artifact = open_directory(root / ".proofline")
    descriptor, raw = tempfile.mkstemp(prefix=".identities-", dir=artifact.path)
    stage = Path(raw)
    staged: FileSnapshot | None = None
    committed = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        staged = read_regular(stage)
        verify_directory(artifact)
        committed_identity = exchange_owned_file(
            artifact.descriptor,
            "identities.json",
            stage.name,
            FileSnapshot(current.data, current.identity),
            staged,
        )
        from .transaction import remove_owned_file

        remove_owned_file(artifact.descriptor, stage.name, current.identity, current.data)
        committed = True
        return committed_identity
    finally:
        try:
            if not committed and staged is not None:
                from .transaction import remove_owned_file

                remove_owned_file(artifact.descriptor, stage.name, staged.identity, staged.data)
        finally:
            artifact.close()


def _copy_candidate_tree(root: Path, candidate: Path, base: str) -> None:
    source_root = root / base
    source_pin = open_directory(source_root)

    def copy(source_fd: int, source: Path, target: Path) -> None:
        try:
            entries = os.listdir(source_fd)
        except OSError as exc:
            raise LineInitError("candidate.source.unreadable", source.relative_to(root).as_posix(), "candidate source를 읽을 수 없습니다.") from exc
        for entry in entries:
            source_path = source / entry
            relative = source_path.relative_to(root).as_posix()
            try:
                state = os.stat(entry, dir_fd=source_fd, follow_symlinks=False)
            except OSError as exc:
                raise LineInitError("candidate.source.unreadable", relative, "candidate source를 읽을 수 없습니다.") from exc
            target_path = target / entry
            if stat.S_ISDIR(state.st_mode):
                child: int | None = None
                try:
                    child = os.open(
                        entry,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                        dir_fd=source_fd,
                    )
                    opened = os.fstat(child)
                    if identity(opened) != identity(state):
                        raise OSError(errno.ESTALE, "candidate source changed")
                    target_path.mkdir()
                    copy(child, source_path, target_path)
                    current = os.stat(entry, dir_fd=source_fd, follow_symlinks=False)
                    if identity(current) != identity(opened) or not stat.S_ISDIR(current.st_mode):
                        raise OSError(errno.ESTALE, "candidate source changed")
                except OSError as exc:
                    raise LineInitError("candidate.source.unreadable", relative, "candidate source directory를 읽을 수 없습니다.") from exc
                finally:
                    if child is not None:
                        os.close(child)
            elif stat.S_ISREG(state.st_mode):
                try:
                    snapshot = read_regular_at(source_fd, entry)
                    if snapshot.identity != identity(state):
                        raise OSError(errno.ESTALE, "candidate source changed")
                    target_path.write_bytes(snapshot.data)
                except OSError as exc:
                    raise LineInitError("candidate.source.unreadable", relative, "candidate source regular file을 읽을 수 없습니다.") from exc
            else:
                raise LineInitError("candidate.source.unsupported", relative, "candidate source에 symlink 또는 지원하지 않는 path가 있습니다.")

    try:
        copy(source_pin.descriptor, source_root, candidate / base)
        verify_directory(source_pin)
    finally:
        source_pin.close()


def _validate_candidate(
    root: Path, line_id: str, line_text: str, discovery_text: str, allocator: bytes
) -> None:
    with tempfile.TemporaryDirectory(prefix="proofline-line-candidate-") as raw:
        candidate = Path(raw)
        (candidate / ".proofline/lines").mkdir(parents=True)
        (candidate / ".proofline/criteria").mkdir()
        try:
            (candidate / "proofline.yaml").write_bytes(read_regular(root / "proofline.yaml").data)
        except OSError as exc:
            raise LineInitError("candidate.source.unreadable", "proofline.yaml", "project config를 읽을 수 없습니다.") from exc
        for base in (".proofline/lines", ".proofline/criteria"):
            _copy_candidate_tree(root, candidate, base)
        (candidate / ".proofline/identities.json").write_bytes(allocator)
        baseline_errors = Counter(
            error
            for error in validate_project(candidate)
            if error.code == "reference.inactive"
        )
        suffix = line_id.removeprefix("line-")
        target = candidate / ".proofline/lines" / line_id
        target.mkdir()
        (target / f"{line_id}.md").write_text(line_text, encoding="utf-8")
        (target / f"dcy-{suffix}.md").write_text(discovery_text, encoding="utf-8")
        diagnostics = list(
            (Counter(validate_project(candidate)) - baseline_errors).elements()
        )
        if diagnostics:
            first = diagnostics[0]
            raise LineInitError("candidate.invalid", first.path, f"{first.code}: {first.message}")


def _pin_line_directories(root: Path) -> tuple[DirectoryPin, DirectoryPin, DirectoryPin]:
    project = open_directory(root)
    try:
        artifact = open_child_directory(project, ".proofline", root / ".proofline")
        try:
            lines = open_child_directory(artifact, "lines", root / ".proofline/lines")
        except Exception:
            artifact.close()
            raise
    except Exception:
        project.close()
        raise
    return project, artifact, lines


def _verify_pins(*pins: DirectoryPin) -> None:
    for pin in pins:
        verify_directory(pin)


def _stage_files(stage: Path) -> dict[str, tuple[tuple[int, int], bytes]]:
    result = {}
    for child in stage.iterdir():
        snapshot = read_regular(child)
        result[child.name] = (snapshot.identity, snapshot.data)
    return result


def _cleanup_line_stage(
    root_fd: int,
    stage: Path,
    stage_identity: tuple[int, int],
    remaining: dict[str, tuple[tuple[int, int], bytes]],
) -> None:
    remove_owned_tree(root_fd, stage.name, stage_identity, remaining)


def _initialize_line(project_root: Path, title: str, *, dry_run: bool = False) -> LineInitResult:
    root = project_root.absolute()
    title = _require_title(title)
    _require_git_root(root)
    lock = _acquire_repository_lock(root)
    pins: tuple[DirectoryPin, DirectoryPin, DirectoryPin] | None = None
    try:
        try:
            pins = _pin_line_directories(root)
        except OSError as exc:
            raise LineInitError("project.path.changed", ".proofline/lines", "canonical parent를 pin할 수 없습니다.") from exc
        diagnostics = validate_project(root)
        if diagnostics:
            first = diagnostics[0]
            raise LineInitError("project.invalid", first.path, f"{first.code}: {first.message}")
        try:
            snapshot = read_allocator(root)
            candidate = advanced(snapshot, lines=1)
        except IdentityAllocatorError as exc:
            raise LineInitError(exc.code, exc.path, exc.message) from exc
        number = snapshot.allocator.next_line_number
        line_id = f"line-{number:04d}"
        suffix = f"{number:04d}"
        paths = (
            f".proofline/lines/{line_id}/{line_id}.md",
            f".proofline/lines/{line_id}/dcy-{suffix}.md",
        )
        target = root / ".proofline/lines" / line_id
        try:
            os.stat(line_id, dir_fd=pins[2].descriptor, follow_symlinks=False)
            target_exists = True
        except FileNotFoundError:
            target_exists = False
        except OSError as exc:
            raise LineInitError("line.path.unavailable", target.relative_to(root).as_posix(), "대상 Line path를 확인할 수 없습니다.") from exc
        if target_exists:
            raise LineInitError("line.path.exists", target.relative_to(root).as_posix(), "대상 Line path가 이미 존재합니다.")
        line_text, discovery_text = _render(line_id, title)
        _validate_candidate(root, line_id, line_text, discovery_text, candidate)
        try:
            _verify_pins(*pins)
            current = read_allocator(root)
        except (OSError, IdentityAllocatorError) as exc:
            raise LineInitError("project.concurrent.changed", ".proofline", "preflight 이후 project가 변경되었습니다.") from exc
        if current.identity != snapshot.identity or current.data != snapshot.data:
            raise LineInitError("allocator.concurrent.changed", ALLOCATOR_PATH, "allocator가 preflight 이후 변경되었습니다.")
        if dry_run:
            return LineInitResult(line_id, paths, True)

        stage = Path(tempfile.mkdtemp(prefix=f".{line_id}-", dir=root))
        stage_identity: tuple[int, int] | None = None
        stage_files: dict[str, tuple[tuple[int, int], bytes]] = {}
        committed = False
        allocator_changed = False
        allocator_identity: tuple[int, int] | None = None
        try:
            stage_state = stage.stat(follow_symlinks=False)
            stage_identity = (stage_state.st_dev, stage_state.st_ino)
            (stage / f"{line_id}.md").write_text(line_text, encoding="utf-8")
            line_stage = read_regular(stage / f"{line_id}.md")
            stage_files[f"{line_id}.md"] = (line_stage.identity, line_stage.data)
            (stage / f"dcy-{suffix}.md").write_text(discovery_text, encoding="utf-8")
            discovery_stage = read_regular(stage / f"dcy-{suffix}.md")
            stage_files[f"dcy-{suffix}.md"] = (discovery_stage.identity, discovery_stage.data)
            _verify_pins(*pins)
            _commit_path_at(
                stage,
                pins[2].descriptor,
                line_id,
                TreeSnapshot(stage_identity, stage_files, {}),
            )
            committed = True
            allocator_identity = _replace_allocator(root, snapshot.identity, candidate, snapshot.data)
            allocator_changed = True
            diagnostics = validate_project(root)
            if diagnostics:
                first = diagnostics[0]
                raise LineInitError("transaction.invalid", first.path, f"{first.code}: {first.message}")
            _verify_pins(*pins)
            if snapshot_tree(target) != TreeSnapshot(stage_identity, stage_files, {}):
                raise OSError("committed Line changed during validation")
            current = read_allocator(root)
            if current.identity != allocator_identity or current.data != candidate:
                raise OSError("committed allocator changed during validation")
        except Exception as primary:
            rollback_errors: list[str] = []
            artifact_rollback_ok = True
            if committed:
                try:
                    assert stage_identity is not None
                    _verify_pins(*pins)
                    remove_owned_tree(pins[2].descriptor, line_id, stage_identity, stage_files)
                except OSError as exc:
                    artifact_rollback_ok = False
                    rollback_errors.append(f"line.rollback.failed: {exc}")
            else:
                try:
                    assert stage_identity is not None
                    _cleanup_line_stage(pins[0].descriptor, stage, stage_identity, stage_files)
                except OSError as exc:
                    rollback_errors.append(f"line.cleanup.failed: {exc}")
            if allocator_changed and artifact_rollback_ok:
                try:
                    current = read_allocator(root)
                    if current.data != candidate or current.identity != allocator_identity:
                        raise OSError("allocator ownership changed")
                    _replace_allocator(root, current.identity, snapshot.data, candidate)
                except Exception as exc:  # preserve primary and report rollback failure
                    rollback_errors.append(f"allocator.rollback.failed: {exc}")
            if isinstance(primary, LineInitError):
                message = primary.message
                if rollback_errors:
                    message += f"; secondary: {'; '.join(rollback_errors)}"
                raise LineInitError(primary.code, primary.path, message) from primary
            message = f"transaction 처리 중 오류가 발생했습니다: {primary}"
            if rollback_errors:
                message += f"; secondary: {'; '.join(rollback_errors)}"
            raise LineInitError("line.transaction.failed", ".", message) from primary
        return LineInitResult(line_id, paths, False)
    finally:
        active = sys.exception()
        if pins is not None:
            for pin in reversed(pins):
                pin.close()
        try:
            _release_repository_lock(lock)
        except OSError as exc:
            if isinstance(active, LineInitError):
                active.message += "; secondary: line.lock.release.failed"
            elif active is None:
                raise LineInitError("line.lock.release.failed", ".git", "repository lock을 해제할 수 없습니다.") from exc


def initialize_line(project_root: Path, title: str, *, dry_run: bool = False) -> LineInitResult:
    try:
        return _initialize_line(project_root, title, dry_run=dry_run)
    except LineInitError:
        raise
    except OSError as exc:
        raise LineInitError("line.operation.failed", ".", "Line 작업 중 filesystem 오류가 발생했습니다.") from exc
