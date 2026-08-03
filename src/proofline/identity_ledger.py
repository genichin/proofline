"""Canonical Line allocation ledger codec and Git-backed invariants."""

from __future__ import annotations

import json
import re
import stat
import subprocess
from collections.abc import Hashable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

AUTHORITY_REF = "refs/heads/main"
LEDGER_PATH = ".proofline/line-identities.json"
LINE_ID_RE = re.compile(r"line-\d{4}")
LINE_PATH_RE = re.compile(r"^\.proofline/lines/(line-\d{4})(?:/|$)")


@dataclass(frozen=True)
class IdentityLedger:
    allocated_line_ids: tuple[str, ...]


@dataclass(frozen=True)
class IdentityLedgerError(Exception):
    code: str
    path: str = LEDGER_PATH
    message: str = "canonical allocation ledger가 올바르지 않습니다."

    def __str__(self) -> str:
        return f"{self.path}: {self.code}: {self.message}"


class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(
        self, node: yaml.nodes.MappingNode, deep: bool = False
    ) -> dict[Hashable, Any]:
        self.flatten_mapping(node)
        mapping: dict[Hashable, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, Hashable):
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable key",
                    key_node.start_mark,
                )
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found a duplicate key",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def encode_ledger(ids: set[str] | tuple[str, ...] | list[str]) -> bytes:
    ordered = sorted(ids)
    if len(ordered) != len(set(ordered)) or any(
        LINE_ID_RE.fullmatch(item) is None for item in ordered
    ):
        raise IdentityLedgerError("ledger.malformed")
    value = {
        "schema_version": 1,
        "authority_ref": AUTHORITY_REF,
        "allocated_line_ids": ordered,
    }
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def decode_ledger(data: bytes) -> IdentityLedger:
    try:
        text = data.decode("utf-8")
        value = json.loads(text)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise IdentityLedgerError("ledger.malformed") from exc
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "authority_ref",
        "allocated_line_ids",
    }:
        raise IdentityLedgerError("ledger.malformed")
    ids = value.get("allocated_line_ids")
    if (
        value.get("schema_version") != 1
        or value.get("authority_ref") != AUTHORITY_REF
        or not isinstance(ids, list)
        or not all(
            isinstance(item, str) and LINE_ID_RE.fullmatch(item) for item in ids
        )
        or ids != sorted(set(ids))
    ):
        raise IdentityLedgerError("ledger.malformed")
    ledger = IdentityLedger(tuple(ids))
    if encode_ledger(ledger.allocated_line_ids) != data:
        raise IdentityLedgerError("ledger.malformed")
    return ledger


def _git(project_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", *args], cwd=project_root, capture_output=True, check=False
        )
    except OSError as exc:
        raise IdentityLedgerError(
            "ledger.history.unavailable",
            message="Git 명령을 실행할 수 없습니다.",
        ) from exc


def _history_unavailable(message: str) -> IdentityLedgerError:
    return IdentityLedgerError("ledger.history.unavailable", message=message)


def _decode_git_output(result: subprocess.CompletedProcess[bytes], message: str) -> str:
    try:
        return result.stdout.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise _history_unavailable(message) from exc


def _authority_ref_absent(project_root: Path) -> bool:
    result = _git(project_root, "show-ref", "--verify", "--quiet", AUTHORITY_REF)
    if result.returncode == 0:
        return False
    if result.returncode == 1:
        return True
    raise _history_unavailable("canonical main ref를 확인할 수 없습니다.")


def require_allocation_authority(project_root: Path) -> None:
    symbolic_result = _git(project_root, "symbolic-ref", "-q", "HEAD")
    if symbolic_result.returncode == 1:
        raise IdentityLedgerError(
            "ledger.authority.required",
            message="allocation mutation은 attached refs/heads/main에서만 허용됩니다.",
        )
    if symbolic_result.returncode != 0:
        raise _history_unavailable("현재 Git branch를 확인할 수 없습니다.")
    symbolic = _decode_git_output(
        symbolic_result, "현재 Git branch를 확인할 수 없습니다."
    ).strip()
    if symbolic != AUTHORITY_REF:
        raise IdentityLedgerError(
            "ledger.authority.required",
            message="allocation mutation은 attached refs/heads/main에서만 허용됩니다.",
        )
    if _authority_ref_absent(project_root):
        raise IdentityLedgerError(
            "ledger.authority.required",
            message="allocation mutation은 resolvable refs/heads/main에서만 허용됩니다.",
        )
    authority = _git(
        project_root, "rev-parse", "--verify", "--quiet", f"{AUTHORITY_REF}^{{commit}}"
    )
    if authority.returncode != 0:
        raise _history_unavailable("canonical main commit을 확인할 수 없습니다.")
    commit = _decode_git_output(
        authority, "canonical main commit을 확인할 수 없습니다."
    ).strip()
    if re.fullmatch(r"[0-9a-fA-F]{40,64}", commit) is None:
        raise _history_unavailable("canonical main commit 결과가 올바르지 않습니다.")


def _ids_from_paths(paths: str) -> set[str]:
    ids: set[str] = set()
    for path in paths.splitlines():
        match = LINE_PATH_RE.fullmatch(path) or LINE_PATH_RE.match(path)
        if match:
            ids.add(match.group(1))
    return ids


def _working_line_ids(project_root: Path) -> set[str]:
    lines = project_root / ".proofline" / "lines"
    try:
        entries = list(lines.iterdir())
    except OSError:
        return set()
    result: set[str] = set()
    for entry in entries:
        if (
            LINE_ID_RE.fullmatch(entry.name)
            and entry.is_dir()
            and not entry.is_symlink()
            and (entry / f"{entry.name}.md").is_file()
        ):
            result.add(entry.name)
    return result


def _tree_line_ids(project_root: Path, revision: str) -> set[str]:
    result = _git(project_root, "ls-tree", "-r", "--name-only", revision, "--", ".proofline/lines")
    if result.returncode != 0:
        raise _history_unavailable("Git tree의 Line 목록을 확인할 수 없습니다.")
    paths = _decode_git_output(result, "Git tree의 Line 목록을 확인할 수 없습니다.")
    line_ids: set[str] = set()
    for path in paths.splitlines():
        match = re.fullmatch(r"\.proofline/lines/(line-\d{4})/\1\.md", path)
        if match:
            line_id = match.group(1)
            data = _tree_file(project_root, revision, path)
            if data is not None and _frontmatter_id(data, line_id):
                line_ids.add(line_id)
    return line_ids


def _history_line_ids(project_root: Path, revision: str) -> set[str]:
    result = _git(
        project_root,
        "log",
        "--first-parent",
        "--format=",
        "--name-only",
        revision,
        "--",
        ".proofline/lines",
    )
    if result.returncode != 0:
        if revision == AUTHORITY_REF and _authority_ref_absent(project_root):
            return set()
        raise _history_unavailable("canonical main Line history를 확인할 수 없습니다.")
    paths = _decode_git_output(result, "canonical main Line history를 확인할 수 없습니다.")
    return _ids_from_paths(paths)


def bootstrap_allocation_ids(project_root: Path) -> tuple[str, ...]:
    history = _history_line_ids(project_root, AUTHORITY_REF)
    return tuple(sorted(history | _working_line_ids(project_root)))


def _ledger_at(project_root: Path, revision: str) -> IdentityLedger | None:
    data = _tree_file(project_root, revision, LEDGER_PATH)
    return decode_ledger(data) if data is not None else None


def _ledger_commits(project_root: Path) -> list[str]:
    result = _git(
        project_root,
        "rev-list",
        "--first-parent",
        "--reverse",
        AUTHORITY_REF,
        "--",
        LEDGER_PATH,
    )
    if result.returncode != 0:
        if _authority_ref_absent(project_root):
            return []
        raise _history_unavailable("canonical main ledger history를 확인할 수 없습니다.")
    commits = _decode_git_output(
        result, "canonical main ledger history를 확인할 수 없습니다."
    ).split()
    if any(re.fullmatch(r"[0-9a-fA-F]{40,64}", commit) is None for commit in commits):
        raise _history_unavailable("canonical main ledger history 결과가 올바르지 않습니다.")
    return commits


def _pair_paths(line_id: str) -> tuple[str, str]:
    suffix = line_id.removeprefix("line-")
    base = f".proofline/lines/{line_id}"
    return f"{base}/{line_id}.md", f"{base}/dcy-{suffix}.md"


def _frontmatter_id(data: bytes, expected: str) -> bool:
    try:
        text = data.decode("utf-8")
        if not text.startswith("---\n") or "\n---\n" not in text:
            return False
        frontmatter = text[4 : text.index("\n---\n")]
        value = yaml.load(frontmatter, Loader=_UniqueKeyLoader)
    except (UnicodeError, yaml.YAMLError):
        return False
    return isinstance(value, dict) and type(value.get("id")) is str and value["id"] == expected


def _working_pair_valid(project_root: Path, line_id: str) -> bool:
    line_path, discovery_path = _pair_paths(line_id)
    try:
        line = (project_root / line_path).read_bytes()
        discovery = (project_root / discovery_path).read_bytes()
    except OSError:
        return False
    suffix = line_id.removeprefix("line-")
    return _frontmatter_id(line, line_id) and _frontmatter_id(discovery, f"dcy-{suffix}")


def _tree_file(project_root: Path, revision: str, path: str) -> bytes | None:
    revision_result = _git(
        project_root, "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"
    )
    if revision_result.returncode != 0:
        raise _history_unavailable(f"Git revision {revision!r}을 확인할 수 없습니다.")
    commit = _decode_git_output(
        revision_result, f"Git revision {revision!r}을 확인할 수 없습니다."
    ).strip()
    if re.fullmatch(r"[0-9a-fA-F]{40,64}", commit) is None:
        raise _history_unavailable(f"Git revision {revision!r} 결과가 올바르지 않습니다.")

    entry_result = _git(project_root, "ls-tree", "-z", commit, "--", path)
    if entry_result.returncode != 0:
        raise _history_unavailable(f"Git tree의 {path} 항목을 확인할 수 없습니다.")
    if not entry_result.stdout:
        return None
    entries = entry_result.stdout.rstrip(b"\0").split(b"\0")
    try:
        metadata, entry_path = entries[0].split(b"\t", 1)
        mode, object_type, object_id = metadata.split(b" ", 2)
        decoded_path = entry_path.decode("utf-8", "strict")
        decoded_id = object_id.decode("ascii", "strict")
    except (ValueError, UnicodeError) as exc:
        raise _history_unavailable(f"Git tree의 {path} 항목이 올바르지 않습니다.") from exc
    if (
        len(entries) != 1
        or decoded_path != path
        or object_type != b"blob"
        or mode not in {b"100644", b"100755"}
        or re.fullmatch(r"[0-9a-fA-F]{40,64}", decoded_id) is None
    ):
        raise _history_unavailable(f"Git tree의 {path} 항목이 올바르지 않습니다.")

    blob_result = _git(project_root, "cat-file", "blob", decoded_id)
    if blob_result.returncode != 0:
        raise _history_unavailable(f"Git tree의 {path} 내용을 읽을 수 없습니다.")
    return blob_result.stdout


def _commit_parent(project_root: Path, commit: str) -> str | None:
    result = _git(project_root, "rev-list", "--parents", "-n", "1", commit)
    if result.returncode != 0:
        raise _history_unavailable(f"commit {commit}의 parent를 확인할 수 없습니다.")
    fields = _decode_git_output(
        result, f"commit {commit}의 parent를 확인할 수 없습니다."
    ).split()
    if not fields or fields[0] != commit or any(
        re.fullmatch(r"[0-9a-fA-F]{40,64}", field) is None for field in fields
    ):
        raise _history_unavailable(f"commit {commit}의 parent 결과가 올바르지 않습니다.")
    return fields[1] if len(fields) > 1 else None


def _tree_pair_valid(project_root: Path, revision: str, line_id: str) -> bool:
    line_path, discovery_path = _pair_paths(line_id)
    line = _tree_file(project_root, revision, line_path)
    discovery = _tree_file(project_root, revision, discovery_path)
    suffix = line_id.removeprefix("line-")
    return (
        line is not None
        and discovery is not None
        and _frontmatter_id(line, line_id)
        and _frontmatter_id(discovery, f"dcy-{suffix}")
    )


def _pair_absent(project_root: Path, revision: str, line_id: str) -> bool:
    return all(_tree_file(project_root, revision, path) is None for path in _pair_paths(line_id))


def _current_ledger(project_root: Path) -> IdentityLedger | IdentityLedgerError | None:
    path = project_root / LEDGER_PATH
    try:
        state = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:
        return IdentityLedgerError("ledger.type")
    if stat.S_ISLNK(state.st_mode):
        return IdentityLedgerError("ledger.symlink")
    if not stat.S_ISREG(state.st_mode):
        return IdentityLedgerError("ledger.type")
    try:
        return decode_ledger(path.read_bytes())
    except OSError:
        return IdentityLedgerError("ledger.type")
    except IdentityLedgerError as exc:
        return exc


def _validate_committed_history(
    project_root: Path, commits: list[str]
) -> tuple[list[IdentityLedgerError], set[str]]:
    errors: list[IdentityLedgerError] = []
    prior_ids: set[str] = set()
    for index, commit in enumerate(commits):
        try:
            ledger = _ledger_at(project_root, commit)
        except IdentityLedgerError as exc:
            if exc.code == "ledger.history.unavailable":
                raise
            errors.append(exc)
            continue
        if ledger is None:
            continue
        ids = set(ledger.allocated_line_ids)
        parent = _commit_parent(project_root, commit)
        if index == 0:
            expected = _tree_line_ids(project_root, commit)
            if parent:
                expected |= _history_line_ids(project_root, parent)
            if ids != expected:
                errors.append(IdentityLedgerError("ledger.bootstrap.incomplete"))
        else:
            if not prior_ids <= ids:
                errors.append(IdentityLedgerError("ledger.regressed"))
            if not _tree_line_ids(project_root, commit) <= ids:
                errors.append(IdentityLedgerError("ledger.stale"))
            prior_line_history = (
                _history_line_ids(project_root, parent) if parent is not None else set()
            )
            for line_id in ids - prior_ids:
                if (
                    parent is None
                    or line_id in prior_line_history
                    or not _pair_absent(project_root, parent, line_id)
                    or not _tree_pair_valid(project_root, commit, line_id)
                ):
                    errors.append(IdentityLedgerError("ledger.orphan"))
        prior_ids |= ids
    return errors, prior_ids


def _local_git_metadata_exists(project_root: Path) -> bool:
    try:
        (project_root / ".git").stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _history_unavailable("project Git metadata를 확인할 수 없습니다.") from exc
    return True


def _validate_ledger(project_root: Path) -> list[IdentityLedgerError]:
    current = _current_ledger(project_root)
    if isinstance(current, IdentityLedgerError):
        return [current]
    if current is None and not _local_git_metadata_exists(project_root):
        return []

    commits = _ledger_commits(project_root)
    committed_errors, prior_main_ids = _validate_committed_history(project_root, commits)
    if current is None:
        return [IdentityLedgerError("ledger.missing")] if commits else []

    current_ids = set(current.allocated_line_ids)
    if not commits:
        expected = set(bootstrap_allocation_ids(project_root))
        errors = [
            IdentityLedgerError("ledger.bootstrap.incomplete")
            for _ in [0]
            if not expected <= current_ids
        ]
        if current_ids - expected:
            errors.append(IdentityLedgerError("ledger.orphan"))
        return errors

    errors = list(committed_errors)
    if not prior_main_ids <= current_ids:
        errors.append(IdentityLedgerError("ledger.regressed"))

    try:
        head_ledger = _ledger_at(project_root, "HEAD")
    except IdentityLedgerError as exc:
        if not any(
            error.code == exc.code and error.path == exc.path for error in errors
        ):
            errors.append(exc)
        return errors
    head_ids = set(head_ledger.allocated_line_ids) if head_ledger else set()
    head_lines = _tree_line_ids(project_root, "HEAD")
    if not head_lines <= head_ids:
        errors.append(IdentityLedgerError("ledger.stale"))
    if not _working_line_ids(project_root) <= current_ids:
        errors.append(IdentityLedgerError("ledger.stale"))

    prior_line_history = _history_line_ids(project_root, "HEAD")
    for line_id in current_ids - head_ids:
        if (
            line_id in prior_line_history
            or not _pair_absent(project_root, "HEAD", line_id)
            or not _working_pair_valid(project_root, line_id)
        ):
            errors.append(IdentityLedgerError("ledger.orphan"))
    return errors


def validate_ledger(project_root: Path) -> list[IdentityLedgerError]:
    try:
        return _validate_ledger(project_root)
    except IdentityLedgerError as exc:
        return [exc]


def require_allocation_preflight(project_root: Path, line_id: str) -> None:
    require_allocation_authority(project_root)
    diagnostics = validate_ledger(project_root)
    if diagnostics:
        raise diagnostics[0]
    current = _current_ledger(project_root)
    if isinstance(current, IdentityLedger):
        allocated = set(current.allocated_line_ids)
    else:
        allocated = set(bootstrap_allocation_ids(project_root))
    if line_id in allocated:
        raise IdentityLedgerError(
            "line.id.reused",
            path=f".proofline/lines/{line_id}",
            message="canonical allocation ledger에 이미 예약된 Line ID입니다.",
        )
