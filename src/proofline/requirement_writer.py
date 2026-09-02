"""Atomic Requirement and acceptance-criterion scaffold writer."""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

from .identity_allocator import IdentityAllocatorError, advanced, read_allocator
from .line_writer import (
    LineInitError,
    _acquire_repository_lock,
    _read_template,
    _release_repository_lock,
    _replace_allocator,
    _require_git_root,
)
from .project_writer import _commit_path_at
from .transaction import (
    DirectoryPin,
    FileSnapshot,
    open_child_directory,
    open_directory,
    read_regular,
    remove_owned_file,
    remove_owned_tree,
    verify_directory,
    verify_regular,
)
from .validator import validate_project
from .yaml_strict import safe_load_unique

LINE_RE = re.compile(r"line-((?!0000)\d{4})")
AC_RE = re.compile(r"ac-(?!0000)\d{4}")
KEYS = ("create", "update", "retire", "satisfy")


@dataclass
class RequirementInitError(Exception):
    code: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.code}: {self.message}"


@dataclass(frozen=True)
class RequirementInitResult:
    line_id: str
    ac_ids: tuple[str, ...]
    paths: tuple[str, ...]
    dry_run: bool


def _error(exc: LineInitError | IdentityAllocatorError) -> RequirementInitError:
    return RequirementInitError(exc.code, exc.path, exc.message)


def _read_manifest(data: bytes, display: str) -> dict[str, list[str]]:
    try:
        value = safe_load_unique(data.decode("utf-8"))
    except (UnicodeError, yaml.YAMLError) as exc:
        raise RequirementInitError("manifest.malformed", display, "manifest는 UTF-8 YAML 또는 JSON이어야 합니다.") from exc
    if not isinstance(value, dict) or set(value) != set(KEYS):
        raise RequirementInitError("manifest.schema", display, "top-level key는 create/update/retire/satisfy exact set이어야 합니다.")
    result: dict[str, list[str]] = {}
    for key in KEYS:
        items = value[key]
        if not isinstance(items, list):
            raise RequirementInitError("manifest.schema", display, f"{key}는 list여야 합니다.")
        if key == "create":
            valid = all(type(item) is str and item.strip() == item and item and "\n" not in item and "\r" not in item and not any(ord(char) < 32 or ord(char) == 127 for char in item) for item in items)
        else:
            valid = all(type(item) is str and AC_RE.fullmatch(item) for item in items)
        if not valid or len(items) != len(set(items)):
            raise RequirementInitError("manifest.entry", display, f"{key} entry가 올바르지 않거나 중복되었습니다.")
        result[key] = items
    if len(result["create"]) != len(set(result["create"])):
        raise RequirementInitError("manifest.entry", display, "create title은 중복될 수 없습니다.")
    memberships = [item for key in KEYS[1:] for item in result[key]]
    if len(memberships) != len(set(memberships)):
        raise RequirementInitError("manifest.overlap", display, "AC ID는 둘 이상의 admission list에 속할 수 없습니다.")
    if not result["create"] and not memberships:
        raise RequirementInitError("manifest.empty", display, "admission 합집합은 비어 있을 수 없습니다.")
    return result


def _frontmatter(
    path: Path, *, require_h1: bool = True
) -> tuple[dict[str, object], str, FileSnapshot]:
    try:
        snapshot = read_regular(path)
        text = snapshot.data.decode("utf-8")
        lines = text.splitlines()
        closing = lines.index("---", 1)
        value = safe_load_unique("\n".join(lines[1:closing]))
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        raise RequirementInitError("artifact.malformed", path.as_posix(), "artifact를 해석할 수 없습니다.") from exc
    if not isinstance(value, dict):
        raise RequirementInitError("artifact.malformed", path.as_posix(), "frontmatter가 mapping이 아닙니다.")
    h1 = [line[2:].strip() for line in lines[closing + 1:] if line.startswith("# ")]
    if require_h1 and len(h1) != 1:
        raise RequirementInitError("artifact.headings", path.as_posix(), "exact H1이 필요합니다.")
    return value, h1[0] if h1 else "", snapshot


def _require_active_ac(root: Path, ac_id: str) -> tuple[Path, FileSnapshot]:
    path = root / ".proofline/criteria" / f"{ac_id}.md"
    try:
        state = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise RequirementInitError("criteria.target.missing", path.relative_to(root).as_posix(), "대상 AC가 없습니다.") from exc
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISREG(state.st_mode):
        raise RequirementInitError("criteria.target.type", path.relative_to(root).as_posix(), "대상 AC는 canonical regular file이어야 합니다.")
    frontmatter, _, snapshot = _frontmatter(path)
    if frontmatter.get("id") != ac_id or frontmatter.get("status") != "active":
        raise RequirementInitError("criteria.target.inactive", path.relative_to(root).as_posix(), "대상 AC는 active여야 합니다.")
    return path, snapshot


def _render_ac(ac_id: str, title: str) -> bytes:
    return _read_template("acceptance-criterion.md").replace("{{AC_ID}}", ac_id).replace("{{TITLE}}", title).encode("utf-8")


def _yaml_list(key: str, values: list[str]) -> str:
    if not values:
        return f"  {key}: []"
    return f"  {key}:\n" + "\n".join(f'    - "{value}"' for value in values)


def _render_req(suffix: str, title: str, criteria: dict[str, list[str]]) -> bytes:
    text = _read_template("requirement.md")
    values = {"{{REQ_ID}}": f"req-{suffix}", "{{DISCOVERY_ID}}": f"dcy-{suffix}", "{{TITLE}}": title}
    for token, value in values.items():
        text = text.replace(token, value)
    for key in KEYS:
        text = text.replace(f"  {key}: []", _yaml_list(key, criteria[key]))
    return text.encode("utf-8")


def _validate_candidate(root: Path, files_to_add: dict[str, bytes], allocator: bytes) -> None:
    with tempfile.TemporaryDirectory(prefix="proofline-requirement-candidate-") as raw:
        candidate = Path(raw)
        (candidate / ".proofline/lines").mkdir(parents=True)
        (candidate / ".proofline/criteria").mkdir()
        try:
            (candidate / "proofline.yaml").write_bytes(read_regular(root / "proofline.yaml").data)
        except OSError as exc:
            raise RequirementInitError("candidate.source.unreadable", "proofline.yaml", "project config를 읽을 수 없습니다.") from exc
        for base in (".proofline/lines", ".proofline/criteria"):
            try:
                from .line_writer import _copy_candidate_tree

                _copy_candidate_tree(root, candidate, base)
            except LineInitError as exc:
                raise _error(exc) from exc
        (candidate / ".proofline/identities.json").write_bytes(allocator)
        baseline_errors = Counter(
            error
            for error in validate_project(candidate)
            if error.code == "reference.inactive"
        )
        for relative, data in files_to_add.items():
            target = candidate / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        errors = list((Counter(validate_project(candidate)) - baseline_errors).elements())
        if errors:
            first = errors[0]
            raise RequirementInitError("candidate.invalid", first.path, f"{first.code}: {first.message}")


def _pin_requirement_directories(
    root: Path, line_id: str
) -> tuple[DirectoryPin, DirectoryPin, DirectoryPin, DirectoryPin, DirectoryPin]:
    pins: list[DirectoryPin] = []
    try:
        pins.append(open_directory(root))
        pins.append(open_child_directory(pins[0], ".proofline", root / ".proofline"))
        pins.append(open_child_directory(pins[1], "lines", root / ".proofline/lines"))
        pins.append(open_child_directory(pins[1], "criteria", root / ".proofline/criteria"))
        pins.append(open_child_directory(pins[2], line_id, root / ".proofline/lines" / line_id))
    except Exception:
        for pin in reversed(pins):
            pin.close()
        raise
    return tuple(pins)  # type: ignore[return-value]


def _initialize_requirement(project_root: Path, line_id: str, manifest_path: Path, *, dry_run: bool = False) -> RequirementInitResult:
    match = LINE_RE.fullmatch(line_id)
    if match is None:
        raise RequirementInitError("line.id.invalid", line_id, "Line ID는 line-NNNN 형식이어야 합니다.")
    root = project_root.absolute()
    try:
        _require_git_root(root)
        lock = _acquire_repository_lock(root)
    except LineInitError as exc:
        raise _error(exc) from exc
    pins: tuple[DirectoryPin, DirectoryPin, DirectoryPin, DirectoryPin, DirectoryPin] | None = None
    try:
        try:
            pins = _pin_requirement_directories(root, line_id)
        except OSError as exc:
            raise RequirementInitError("project.path.changed", f".proofline/lines/{line_id}", "canonical parent를 pin할 수 없습니다.") from exc
        manifest_absolute = manifest_path.absolute()
        try:
            manifest_state = manifest_absolute.stat(follow_symlinks=False)
            if stat.S_ISLNK(manifest_state.st_mode):
                raise RequirementInitError(
                    "manifest.symlink",
                    os.fspath(manifest_path),
                    "manifest symlink는 허용하지 않습니다.",
                )
            if not stat.S_ISREG(manifest_state.st_mode):
                raise OSError("manifest is not regular")
            manifest_snapshot = read_regular(manifest_absolute)
        except RequirementInitError:
            raise
        except OSError as exc:
            raise RequirementInitError("manifest.type", os.fspath(manifest_path), "manifest를 읽을 수 없습니다.") from exc
        manifest = _read_manifest(manifest_snapshot.data, os.fspath(manifest_path))
        suffix = match.group(1)
        line_path = root / f".proofline/lines/{line_id}/{line_id}.md"
        discovery_path = root / f".proofline/lines/{line_id}/dcy-{suffix}.md"
        req_path = root / f".proofline/lines/{line_id}/req-{suffix}.md"
        line_frontmatter, _, line_snapshot = _frontmatter(line_path, require_h1=False)
        discovery_frontmatter, title, discovery_snapshot = _frontmatter(discovery_path)
        line_fields = {"id", "status", "execution_status", "implementation_history"}
        if line_frontmatter.get("id") != line_id or set(line_frontmatter) - line_fields:
            raise RequirementInitError(
                "line.binding.invalid",
                line_path.relative_to(root).as_posix(),
                "matching Line identity와 허용된 표시·호환 metadata가 필요합니다.",
            )
        if discovery_frontmatter.get("id") != f"dcy-{suffix}" or discovery_frontmatter.get("status") != "confirmed":
            raise RequirementInitError("discovery.unconfirmed", discovery_path.relative_to(root).as_posix(), "matching confirmed Discovery가 필요합니다.")
        if os.path.lexists(req_path):
            raise RequirementInitError("requirement.path.exists", req_path.relative_to(root).as_posix(), "REQ가 이미 존재합니다.")
        ac_snapshots = [
            _require_active_ac(root, ac_id)
            for ac_id in [item for key in KEYS[1:] for item in manifest[key]]
        ]
        errors = validate_project(root)
        if errors:
            first = errors[0]
            raise RequirementInitError("project.invalid", first.path, f"{first.code}: {first.message}")
        try:
            snapshot = read_allocator(root)
            allocator = advanced(snapshot, acs=len(manifest["create"]))
        except IdentityAllocatorError as exc:
            raise _error(exc) from exc
        ac_ids = tuple(f"ac-{number:04d}" for number in range(snapshot.allocator.next_ac_number, snapshot.allocator.next_ac_number + len(manifest["create"])))
        criteria = {key: list(value) for key, value in manifest.items() if key != "create"}
        criteria["create"] = list(ac_ids)
        criteria = {key: criteria[key] for key in KEYS}
        files_to_add = {
            f".proofline/criteria/{ac_id}.md": _render_ac(ac_id, ac_title)
            for ac_id, ac_title in zip(ac_ids, manifest["create"], strict=True)
        }
        files_to_add[req_path.relative_to(root).as_posix()] = _render_req(suffix, title, criteria)
        for relative in files_to_add:
            if os.path.lexists(root / relative):
                raise RequirementInitError("artifact.path.exists", relative, "대상 artifact가 이미 존재합니다.")
        _validate_candidate(root, files_to_add, allocator)
        try:
            for pin in pins:
                verify_directory(pin)
            verify_regular(line_path, line_snapshot)
            verify_regular(discovery_path, discovery_snapshot)
            verify_regular(manifest_absolute, manifest_snapshot)
            for path, file_snapshot in ac_snapshots:
                verify_regular(path, file_snapshot)
            current = read_allocator(root)
        except (OSError, IdentityAllocatorError) as exc:
            raise RequirementInitError("project.concurrent.changed", ".proofline", "preflight 이후 project가 변경되었습니다.") from exc
        if current.identity != snapshot.identity or current.data != snapshot.data:
            raise RequirementInitError("allocator.concurrent.changed", ".proofline/identities.json", "allocator가 preflight 이후 변경되었습니다.")
        paths = tuple(files_to_add)
        if dry_run:
            return RequirementInitResult(line_id, ac_ids, paths, True)

        stage = Path(tempfile.mkdtemp(prefix=f".req-{suffix}-", dir=root))
        committed: list[tuple[str, tuple[int, int]]] = []
        stage_state = stage.stat(follow_symlinks=False)
        stage_identity = (stage_state.st_dev, stage_state.st_ino)
        remaining: dict[str, tuple[tuple[int, int], bytes]] = {}
        allocator_changed = False
        allocator_identity: tuple[int, int] | None = None
        stage_removed = False
        try:
            def verify_inputs() -> None:
                for pin in pins:
                    verify_directory(pin)
                verify_regular(line_path, line_snapshot)
                verify_regular(discovery_path, discovery_snapshot)
                verify_regular(manifest_absolute, manifest_snapshot)
                for active_path, active_snapshot in ac_snapshots:
                    verify_regular(active_path, active_snapshot)

            for relative, data in files_to_add.items():
                staged = stage / Path(relative).name
                staged.write_bytes(data)
                staged_state = staged.stat(follow_symlinks=False)
                staged_identity = (staged_state.st_dev, staged_state.st_ino)
                remaining[staged.name] = (staged_identity, data)
                parent = root / relative
                parent_pin = pins[4] if relative.startswith(f".proofline/lines/{line_id}/") else pins[3]
                verify_inputs()
                _commit_path_at(
                    staged,
                    parent_pin.descriptor,
                    parent.name,
                    FileSnapshot(data, staged_identity),
                )
                committed.append((relative, staged_identity))
                remaining.pop(staged.name)
            remove_owned_tree(pins[0].descriptor, stage.name, stage_identity, remaining)
            stage_removed = True
            if ac_ids:
                verify_inputs()
                allocator_identity = _replace_allocator(root, snapshot.identity, allocator, snapshot.data)
                allocator_changed = True
            errors = validate_project(root)
            if errors:
                first = errors[0]
                raise RequirementInitError("transaction.invalid", first.path, f"{first.code}: {first.message}")
            verify_inputs()
            for relative, committed_identity in committed:
                if read_regular(root / relative) != FileSnapshot(
                    files_to_add[relative], committed_identity
                ):
                    raise OSError("committed artifact changed during post-validation")
            current = read_allocator(root)
            expected_allocator = allocator if ac_ids else snapshot.data
            expected_allocator_identity = allocator_identity if ac_ids else snapshot.identity
            if current.data != expected_allocator or current.identity != expected_allocator_identity:
                raise OSError("allocator changed during post-validation")
        except Exception as primary:
            details: list[str] = []
            artifact_rollback_ok = True
            for relative, identity in reversed(committed):
                try:
                    for pin in pins:
                        verify_directory(pin)
                    parent_pin = pins[4] if relative.startswith(f".proofline/lines/{line_id}/") else pins[3]
                    remove_owned_file(parent_pin.descriptor, Path(relative).name, identity, files_to_add[relative])
                except OSError as exc:
                    artifact_rollback_ok = False
                    details.append(f"artifact.rollback.failed: {relative}: {exc}")
            if not stage_removed:
                try:
                    remove_owned_tree(pins[0].descriptor, stage.name, stage_identity, remaining)
                except OSError as exc:
                    details.append(f"requirement.cleanup.failed: {exc}")
            if allocator_changed and artifact_rollback_ok:
                try:
                    current = read_allocator(root)
                    if current.data != allocator or current.identity != allocator_identity:
                        raise OSError("allocator ownership changed")
                    _replace_allocator(root, current.identity, snapshot.data, allocator)
                except Exception as exc:
                    details.append(f"allocator.rollback.failed: {exc}")
            if isinstance(primary, RequirementInitError):
                message = primary.message
                if details:
                    message += f"; secondary: {'; '.join(details)}"
                raise RequirementInitError(primary.code, primary.path, message) from primary
            message = f"transaction 처리 중 오류가 발생했습니다: {primary}"
            if details:
                message += f"; secondary: {'; '.join(details)}"
            raise RequirementInitError("requirement.transaction.failed", ".", message) from primary
        return RequirementInitResult(line_id, ac_ids, paths, False)
    finally:
        active = sys.exception()
        if pins is not None:
            for pin in reversed(pins):
                pin.close()
        try:
            _release_repository_lock(lock)
        except OSError as exc:
            if isinstance(active, RequirementInitError):
                active.message += "; secondary: requirement.lock.release.failed"
            elif active is None:
                raise RequirementInitError("requirement.lock.release.failed", ".git", "repository lock을 해제할 수 없습니다.") from exc


def initialize_requirement(
    project_root: Path,
    line_id: str,
    manifest_path: Path,
    *,
    dry_run: bool = False,
) -> RequirementInitResult:
    try:
        return _initialize_requirement(project_root, line_id, manifest_path, dry_run=dry_run)
    except RequirementInitError:
        raise
    except OSError as exc:
        raise RequirementInitError("requirement.operation.failed", ".", "Requirement 작업 중 filesystem 오류가 발생했습니다.") from exc
