#!/usr/bin/env python3
"""Report whether an approved ProofLine specification has draft transition evidence."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys


LINE_RE = re.compile(r"line-[0-9]{4}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
OID_RE = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
GIT_READ_TIMEOUT_SECONDS = 5
GIT_READ_OUTPUT_LIMIT = 8 * 1024 * 1024


class AuditError(RuntimeError):
    """Invalid audit target rather than absent optional evidence."""


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    try:
        result = subprocess.run(
            ("git", "-C", str(repo), *args),
            text=True,
            capture_output=True,
            check=False,
            env=environment,
            timeout=GIT_READ_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AuditError("git command failed") from exc
    if (
        len(result.stdout.encode()) + len(result.stderr.encode()) > GIT_READ_OUTPUT_LIMIT
        or result.returncode != 0
    ):
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise AuditError(detail)
    return result


def artifact_at(repo: Path, commit: str, relative_path: str) -> str:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    try:
        entry = subprocess.run(
            ("git", "-C", str(repo), "ls-tree", "-z", "--full-tree", commit, "--", relative_path),
            capture_output=True,
            check=False,
            env=environment,
            timeout=GIT_READ_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AuditError(f"canonical artifact read failed: {relative_path}") from exc
    if len(entry.stdout) + len(entry.stderr) > GIT_READ_OUTPUT_LIMIT or entry.returncode != 0:
        raise AuditError(f"canonical artifact read failed: {relative_path}")
    records = entry.stdout.split(b"\0")
    if len(records) != 2 or records[1] or not records[0]:
        raise AuditError(f"canonical artifact tree entry is missing or malformed: {relative_path}")
    fields = records[0].split(b"\t")
    metadata = fields[0].split(b" ") if len(fields) == 2 else []
    try:
        actual_path = fields[1].decode("utf-8")
    except (IndexError, UnicodeDecodeError) as exc:
        raise AuditError(f"canonical artifact tree entry is missing or malformed: {relative_path}") from exc
    if (
        len(metadata) != 3
        or metadata[0] not in {b"100644", b"100755"}
        or metadata[1] != b"blob"
        or OID_RE.fullmatch(metadata[2]) is None
        or actual_path != relative_path
    ):
        if actual_path == relative_path and len(metadata) == 3:
            raise AuditError(f"canonical artifact must be a regular blob: {relative_path}")
        raise AuditError(f"canonical artifact tree entry is missing or malformed: {relative_path}")
    try:
        blob = subprocess.run(
            ("git", "-C", str(repo), "cat-file", "blob", metadata[2].decode("ascii")),
            capture_output=True,
            check=False,
            env=environment,
            timeout=GIT_READ_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AuditError(f"canonical artifact read failed: {relative_path}") from exc
    if len(blob.stdout) + len(blob.stderr) > GIT_READ_OUTPUT_LIMIT or blob.returncode != 0:
        raise AuditError(f"canonical artifact read failed: {relative_path}")
    try:
        return blob.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AuditError(f"canonical artifact is not UTF-8: {relative_path}") from exc


def frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        raise AuditError("canonical artifact has no YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise AuditError("canonical artifact frontmatter is incomplete")
    return parts[1]


def scalar(text: str, key: str) -> str:
    match = re.search(
        rf"(?m)^{re.escape(key)}:\s*[\"']?([^\"'\n]+?)[\"']?\s*$",
        frontmatter(text),
    )
    if match is None:
        raise AuditError(f"canonical artifact is missing {key}")
    return match.group(1).strip()


def created_criteria(text: str) -> list[str]:
    lines = frontmatter(text).splitlines()
    try:
        criteria_index = next(i for i, line in enumerate(lines) if line == "criteria:")
        create_index = next(
            i
            for i in range(criteria_index + 1, len(lines))
            if lines[i].strip().startswith("create:")
        )
    except StopIteration as exc:
        raise AuditError("REQ.criteria.create is missing") from exc

    create_line = lines[create_index].strip()
    if create_line == "create: []":
        return []
    if create_line != "create:":
        raise AuditError("REQ.criteria.create has invalid syntax")

    values: list[str] = []
    for line in lines[create_index + 1 :]:
        if line.startswith("  ") and not line.startswith("    "):
            break
        match = re.fullmatch(r"    - (ac-[0-9]{4})", line)
        if match is None:
            raise AuditError("REQ.criteria.create has invalid syntax")
        values.append(match.group(1))
    return values


def with_status(text: str, expected: str, replacement: str) -> str:
    pattern = re.compile(rf"(?m)^status:\s*[\"']?{re.escape(expected)}[\"']?\s*$")
    changed, count = pattern.subn(f"status: {replacement}", text, count=1)
    if count != 1:
        raise AuditError(f"artifact status must be {expected}")
    return changed


def transition_is_recorded(repo: Path, line_id: str, approval: str) -> bool:
    number = line_id.removeprefix("line-")
    req_path = f".proofline/lines/{line_id}/req-{number}.md"
    req = artifact_at(repo, approval, req_path)
    if scalar(req, "id") != f"req-{number}":
        raise AuditError("REQ.id does not match Line")
    if scalar(req, "status") != "approved":
        raise AuditError("REQ.status must be approved")

    criteria = created_criteria(req)
    current_acs: dict[str, str] = {}
    for ac_id in criteria:
        path = f".proofline/criteria/{ac_id}.md"
        text = artifact_at(repo, approval, path)
        if scalar(text, "id") != ac_id:
            raise AuditError(f"{ac_id} id does not match path")
        if scalar(text, "status") != "active":
            raise AuditError(f"{ac_id}.status must be active")
        current_acs[path] = text

    lineage = git(repo, "rev-list", "--parents", "-n", "1", approval).stdout.split()
    if len(lineage) < 2:
        return False
    parent = lineage[1]

    try:
        parent_req = artifact_at(repo, parent, req_path)
    except AuditError:
        return False
    if parent_req != with_status(req, "approved", "draft"):
        return False

    for path, current in current_acs.items():
        try:
            parent_ac = artifact_at(repo, parent, path)
        except AuditError:
            return False
        if parent_ac != with_status(current, "active", "draft"):
            return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--line-id", required=True)
    parser.add_argument("--approval-commit", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        repo = args.repo.resolve(strict=True)
        root = Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
        if root != repo:
            raise AuditError("--repo must be the repository root")
        if LINE_RE.fullmatch(args.line_id) is None:
            raise AuditError("line id must match line-NNNN")
        if COMMIT_RE.fullmatch(args.approval_commit) is None:
            raise AuditError("approval commit must be a full lowercase SHA")
        resolved = git(
            repo, "rev-parse", "--verify", f"{args.approval_commit}^{{commit}}"
        ).stdout.strip()
        if resolved != args.approval_commit:
            raise AuditError("approval commit does not resolve exactly")
        recorded = transition_is_recorded(repo, args.line_id, args.approval_commit)
    except (AuditError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    state = "recorded" if recorded else "not recorded"
    print(f"transition: {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
