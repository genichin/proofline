#!/usr/bin/env python3
"""Report whether an approved ProofLine specification has draft transition evidence."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
from typing import Any


LINE_RE = re.compile(r"line-[0-9]{4}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
OID_RE = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
GIT_READ_TIMEOUT_SECONDS = 5
GIT_READ_OUTPUT_LIMIT = 8 * 1024 * 1024
PROCESS_CLEANUP_GRACE_SECONDS = 0.25


class AuditError(RuntimeError):
    """Invalid audit target rather than absent optional evidence."""


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    command = (
        "git",
        "-c", "core.fsmonitor=false",
        "-c", f"core.hooksPath={os.devnull}",
        "-c", "diff.external=",
        "-C", str(repo),
        *args,
    )
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update({
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "LC_ALL": "C",
    })
    popen_options: dict[str, Any] = {}
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            **popen_options,
        )
    except OSError as exc:
        raise AuditError("git command failed to start") from exc
    assert process.stdout is not None and process.stderr is not None

    output = [bytearray(), bytearray()]
    total = 0
    lock = threading.Lock()
    overflow = threading.Event()
    read_error = threading.Event()
    finished = [threading.Event(), threading.Event()]

    def drain(index: int, pipe: Any, destination: bytearray) -> None:
        nonlocal total
        try:
            while True:
                chunk = pipe.read(64 * 1024)
                if not chunk:
                    break
                with lock:
                    remaining = GIT_READ_OUTPUT_LIMIT - total
                    if len(chunk) > remaining:
                        destination.extend(chunk[:max(remaining, 0)])
                        total = GIT_READ_OUTPUT_LIMIT
                        overflow.set()
                        break
                    else:
                        destination.extend(chunk)
                        total += len(chunk)
        except (OSError, ValueError):
            read_error.set()
        finally:
            finished[index].set()

    threads = [
        threading.Thread(target=drain, args=(0, process.stdout, output[0]), daemon=True),
        threading.Thread(target=drain, args=(1, process.stderr, output[1]), daemon=True),
    ]
    for thread in threads:
        thread.start()

    deadline = time.monotonic() + GIT_READ_TIMEOUT_SECONDS
    reason: str | None = None
    while True:
        if overflow.is_set():
            reason = "git command output exceeds limit"
            break
        if read_error.is_set():
            reason = "git command output read failed"
            break
        if process.poll() is not None and all(event.is_set() for event in finished):
            break
        if time.monotonic() >= deadline:
            reason = "git command timed out"
            break
        overflow.wait(0.01)

    cleanup_failed = False
    if reason is not None:
        if os.name == "nt":
            killer: subprocess.Popen[bytes] | None = None
            try:
                killer = subprocess.Popen(
                    ("taskkill", "/PID", str(process.pid), "/T", "/F"),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                killer.wait(timeout=PROCESS_CLEANUP_GRACE_SECONDS)
            except (OSError, subprocess.TimeoutExpired, ValueError):
                if killer is not None:
                    try:
                        killer.kill()
                        killer.wait(timeout=PROCESS_CLEANUP_GRACE_SECONDS)
                    except (OSError, subprocess.TimeoutExpired, ValueError):
                        cleanup_failed = True
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError:
                cleanup_failed = True
            grace_deadline = time.monotonic() + PROCESS_CLEANUP_GRACE_SECONDS
            while time.monotonic() < grace_deadline:
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    break
                except OSError:
                    break
                time.sleep(0.01)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                cleanup_failed = True
        try:
            process.wait(timeout=PROCESS_CLEANUP_GRACE_SECONDS)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            try:
                process.kill()
                process.wait(timeout=PROCESS_CLEANUP_GRACE_SECONDS)
            except (OSError, subprocess.TimeoutExpired, ValueError):
                cleanup_failed = True

    for stream in (process.stdout, process.stderr):
        try:
            stream.close()
        except (OSError, ValueError):
            read_error.set()
    join_deadline = time.monotonic() + PROCESS_CLEANUP_GRACE_SECONDS
    for thread in threads:
        thread.join(timeout=max(0.0, join_deadline - time.monotonic()))
    if any(thread.is_alive() for thread in threads):
        cleanup_failed = True
    if reason is not None or overflow.is_set() or read_error.is_set() or cleanup_failed:
        raise AuditError(
            reason
            or ("git command output exceeds limit" if overflow.is_set() else None)
            or ("git command output read failed" if read_error.is_set() else None)
            or "git command cleanup failed"
        )
    return subprocess.CompletedProcess(command, process.returncode, bytes(output[0]), bytes(output[1]))


def decode_git(raw: bytes) -> str:
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise AuditError("git command output is not UTF-8") from exc


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = run_git(repo, *args)
    stdout = decode_git(result.stdout)
    stderr = decode_git(result.stderr)
    if result.returncode != 0:
        detail = stderr.strip() or stdout.strip() or "git command failed"
        raise AuditError(detail)
    return subprocess.CompletedProcess(result.args, result.returncode, stdout, stderr)


def artifact_at(repo: Path, commit: str, relative_path: str) -> str:
    try:
        entry = run_git(repo, "ls-tree", "-z", "--full-tree", commit, "--", relative_path)
    except AuditError as exc:
        raise AuditError(f"canonical artifact read failed: {relative_path}") from exc
    if entry.returncode != 0:
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
        oid = metadata[2].decode("ascii", errors="strict")
        blob = run_git(repo, "cat-file", "blob", oid)
    except (UnicodeDecodeError, AuditError) as exc:
        raise AuditError(f"canonical artifact read failed: {relative_path}") from exc
    if blob.returncode != 0:
        raise AuditError(f"canonical artifact read failed: {relative_path}")
    try:
        return blob.stdout.decode("utf-8", errors="strict")
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
