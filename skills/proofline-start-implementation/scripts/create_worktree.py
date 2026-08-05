#!/usr/bin/env python3
"""Create a ProofLine implementation linked worktree after fail-closed preflight."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time


LINE_RE = re.compile(r"line-[0-9]{4}\Z")
AC_RE = re.compile(r"ac-[0-9]{4}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
OID_RE = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
GIT_READ_TIMEOUT_SECONDS = 5
GIT_READ_OUTPUT_LIMIT = 8 * 1024 * 1024
GIT_WORKTREE_TIMEOUT_SECONDS = 30
VALIDATE_TIMEOUT_SECONDS = 30
VALIDATE_OUTPUT_LIMIT = 256 * 1024
PROCESS_TERM_GRACE_SECONDS = 0.1
PROCESS_KILL_GRACE_SECONDS = 0.2
READER_JOIN_TIMEOUT_SECONDS = 0.2


class WorkflowError(RuntimeError):
    """Expected fail-closed workflow error."""


def _process_group_exists(process: subprocess.Popen[bytes]) -> bool:
    if os.name == "nt":
        return process.poll() is None
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Best-effort bounded termination followed by bounded direct-child reaping."""
    if not isinstance(getattr(process, "pid", None), int):
        try:
            process.terminate()
            process.wait(timeout=PROCESS_KILL_GRACE_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ("taskkill", "/PID", str(process.pid), "/T", "/F"),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=PROCESS_KILL_GRACE_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        term_deadline = time.monotonic() + PROCESS_TERM_GRACE_SECONDS
        while _process_group_exists(process) and time.monotonic() < term_deadline:
            time.sleep(0.005)
        if _process_group_exists(process):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    try:
        process.wait(timeout=PROCESS_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=PROCESS_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass


def _close_and_join_readers(
    process: subprocess.Popen[bytes], readers: list[threading.Thread]
) -> bool:
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except (OSError, ValueError):
                pass
    for reader in readers:
        reader.join(timeout=READER_JOIN_TIMEOUT_SECONDS)
    return all(not reader.is_alive() for reader in readers)


def _capture_validate_process(
    process: subprocess.Popen[bytes], *, deadline: float
) -> tuple[int, bytes, bytes]:
    """Drain Windows and POSIX anonymous pipes concurrently with hard bounds."""
    if process.stdout is None or process.stderr is None:
        _terminate_process_tree(process)
        raise WorkflowError("ProofLine validate executable failed to start")
    buffers = [bytearray(), bytearray()]
    finished = [threading.Event(), threading.Event()]
    excessive = threading.Event()
    read_error = threading.Event()
    buffer_lock = threading.Lock()
    captured = 0

    def drain(index: int, stream: object) -> None:
        nonlocal captured
        try:
            while True:
                chunk = stream.read(65536)  # type: ignore[attr-defined]
                if not chunk:
                    break
                with buffer_lock:
                    remaining = VALIDATE_OUTPUT_LIMIT - captured
                    if len(chunk) > remaining:
                        accepted = max(0, remaining)
                        buffers[index].extend(chunk[:accepted])
                        captured += accepted
                        excessive.set()
                        break
                    buffers[index].extend(chunk)
                    captured += len(chunk)
        except (OSError, ValueError):
            read_error.set()
        finally:
            finished[index].set()

    readers = [
        threading.Thread(target=drain, args=(0, process.stdout), daemon=True),
        threading.Thread(target=drain, args=(1, process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        while not all(event.is_set() for event in finished):
            if excessive.is_set():
                raise WorkflowError("ProofLine validate produced excessive output")
            if read_error.is_set():
                raise WorkflowError("ProofLine validate pipe read failed")
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("proofline validate", VALIDATE_TIMEOUT_SECONDS)
            time.sleep(0.005)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired("proofline validate", VALIDATE_TIMEOUT_SECONDS)
        returncode = process.wait(timeout=remaining)
        if excessive.is_set():
            raise WorkflowError("ProofLine validate produced excessive output")
        if read_error.is_set():
            raise WorkflowError("ProofLine validate pipe read failed")
        return returncode, bytes(buffers[0]), bytes(buffers[1])
    except (OSError, subprocess.TimeoutExpired, ValueError):
        _terminate_process_tree(process)
        raise
    finally:
        if process.poll() is None:
            _terminate_process_tree(process)
        if not _close_and_join_readers(process, readers):
            raise WorkflowError("ProofLine validate pipe reader failed to stop")


def _run_git(
    repo: Path,
    *args: str,
    timeout: float = GIT_READ_TIMEOUT_SECONDS,
    config_overrides: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[bytes]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "LC_ALL": "C",
        }
    )
    command = (
        "git",
        "-c", "core.fsmonitor=false",
        "-c", f"core.hooksPath={os.devnull}",
        "-c", "diff.external=",
        *config_overrides,
        "-C", str(repo),
        *args,
    )
    try:
        process = subprocess.Popen(
            command,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=os.name != "nt",
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            ),
        )
    except OSError as exc:
        raise WorkflowError("git command failed to start") from exc
    assert process.stdout is not None and process.stderr is not None
    buffers = [bytearray(), bytearray()]
    done = [threading.Event(), threading.Event()]
    overflow = threading.Event()
    read_error = threading.Event()
    lock = threading.Lock()
    captured = 0

    def drain(index: int, stream: object) -> None:
        nonlocal captured
        try:
            while True:
                chunk = stream.read(65536)  # type: ignore[attr-defined]
                if not chunk:
                    break
                with lock:
                    remaining = GIT_READ_OUTPUT_LIMIT - captured
                    accepted = min(len(chunk), max(0, remaining))
                    buffers[index].extend(chunk[:accepted])
                    captured += accepted
                    if accepted != len(chunk):
                        overflow.set()
                        break
        except (OSError, ValueError):
            read_error.set()
        finally:
            done[index].set()

    readers = [
        threading.Thread(target=drain, args=(0, process.stdout), daemon=True),
        threading.Thread(target=drain, args=(1, process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout
    failure: WorkflowError | None = None
    try:
        while not all(event.is_set() for event in done):
            if overflow.is_set():
                failure = WorkflowError("git command produced excessive output")
                break
            if read_error.is_set():
                failure = WorkflowError("git command pipe read failed")
                break
            if time.monotonic() >= deadline:
                failure = WorkflowError("git command timed out")
                break
            time.sleep(0.005)
        if failure is None and overflow.is_set():
            failure = WorkflowError("git command produced excessive output")
        if failure is None and read_error.is_set():
            failure = WorkflowError("git command pipe read failed")
        if failure is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = WorkflowError("git command timed out")
            else:
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    failure = WorkflowError("git command timed out")
    finally:
        if failure is not None or process.poll() is None or process.returncode != 0:
            _terminate_process_tree(process)
        else:
            try:
                process.wait(timeout=PROCESS_KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                _terminate_process_tree(process)
                failure = failure or WorkflowError("git command failed to reap")
        if not _close_and_join_readers(process, readers):
            failure = failure or WorkflowError("git command pipe reader failed to stop")
    if failure is not None:
        raise failure
    return subprocess.CompletedProcess(
        command, process.returncode, bytes(buffers[0]), bytes(buffers[1])
    )


def _filter_overrides(
    repo: Path, *, timeout: float = GIT_READ_TIMEOUT_SECONDS
) -> tuple[str, ...]:
    result = _run_git(
        repo,
        "config", "--local", "--includes", "--null", "--name-only",
        "--get-regexp", r"^filter\..*\.(clean|smudge|process|required)$",
        timeout=timeout,
    )
    if result.returncode == 1 and not result.stdout and not result.stderr:
        return ()
    if result.returncode != 0:
        raise WorkflowError("repository filter configuration enumeration failed")
    try:
        text = result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkflowError("repository filter configuration is not UTF-8") from exc
    if not text or not text.endswith("\0"):
        raise WorkflowError("repository filter configuration is malformed")
    keys = text[:-1].split("\0")
    drivers: set[str] = set()
    for key in keys:
        match = re.fullmatch(
            r"filter\.(.+)\.(?:clean|smudge|process|required)", key, re.IGNORECASE
        )
        if match is None:
            raise WorkflowError("repository filter configuration is malformed")
        driver = match.group(1)
        if "=" in driver or any(ord(character) < 32 or ord(character) == 127 for character in driver):
            raise WorkflowError("repository filter driver name is unsafe")
        drivers.add(driver)
    return tuple(
        argument
        for driver in sorted(drivers)
        for argument in (
            "-c", f"filter.{driver}.clean=",
            "-c", f"filter.{driver}.smudge=",
            "-c", f"filter.{driver}.process=",
            "-c", f"filter.{driver}.required=false",
        )
    )


def git(
    repo: Path, *args: str, check: bool = True, timeout: float = GIT_READ_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str]:
    neutralize = bool(args) and (
        args[0] in {"reset", "status"}
        or (len(args) > 1 and args[:2] == ("worktree", "add"))
    )
    overrides = _filter_overrides(repo, timeout=timeout) if neutralize else ()
    raw = _run_git(repo, *args, timeout=timeout, config_overrides=overrides)
    try:
        stdout = raw.stdout.decode("utf-8")
        stderr = raw.stderr.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkflowError("git command returned non-UTF-8 output") from exc
    result = subprocess.CompletedProcess(raw.args, raw.returncode, stdout, stderr)
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise WorkflowError(detail)
    return result


def _frontmatter_scalar(raw: str) -> object:
    value = raw.strip()
    if value == "[]":
        return []
    if not value or value.startswith(("&", "*", "!", "[", "{")):
        raise WorkflowError("canonical artifact frontmatter value is not a scalar")
    if value[0] in "\"'":
        quote = value[0]
        end = value.find(quote, 1)
        suffix = value[end + 1 :].strip() if end >= 0 else ""
        if end < 0 or end == 1 or (suffix and not suffix.startswith("#")):
            raise WorkflowError("canonical artifact frontmatter value is not a scalar")
        body = value[1:end]
        if quote == "\"" and "\\" in body:
            raise WorkflowError("canonical artifact frontmatter value is not a scalar")
        return body
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    if not value or any(character.isspace() for character in value) or ":" in value:
        raise WorkflowError("canonical artifact frontmatter value is not a scalar")
    return value


def _yaml_subset(lines: list[str]) -> object:
    meaningful: list[tuple[int, str]] = []
    for number, line in enumerate(lines, 1):
        if "\t" in line[: len(line) - len(line.lstrip(" \t"))]:
            raise WorkflowError(f"canonical artifact frontmatter has tab indentation at line {number}")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        meaningful.append((indent, line[indent:]))
    if not meaningful or meaningful[0][0] != 0:
        raise WorkflowError("canonical artifact frontmatter has invalid indentation")

    def block(index: int, indent: int) -> tuple[object, int]:
        if index >= len(meaningful) or meaningful[index][0] != indent:
            raise WorkflowError("canonical artifact frontmatter has invalid indentation")
        sequence = meaningful[index][1].startswith("-")
        result: object = [] if sequence else {}
        seen: set[str] = set()
        while index < len(meaningful):
            current_indent, content = meaningful[index]
            if current_indent < indent:
                break
            if current_indent != indent:
                raise WorkflowError("canonical artifact frontmatter has invalid dedent")
            if sequence:
                if not content.startswith("-") or (len(content) > 1 and content[1] not in " \t"):
                    raise WorkflowError("canonical artifact frontmatter sequence is malformed")
                raw = content[1:].strip()
                if raw:
                    value = _frontmatter_scalar(raw)
                    index += 1
                else:
                    if index + 1 >= len(meaningful) or meaningful[index + 1][0] <= indent:
                        raise WorkflowError("canonical artifact frontmatter container is empty")
                    value, index = block(index + 1, meaningful[index + 1][0])
                result.append(value)  # type: ignore[union-attr]
                continue
            match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*):(.*)", content)
            if match is None:
                raise WorkflowError("canonical artifact frontmatter is malformed")
            name, raw = match.groups()
            if name in seen:
                raise WorkflowError("canonical artifact frontmatter has duplicate keys")
            seen.add(name)
            if raw.strip():
                value = _frontmatter_scalar(raw)
                index += 1
            else:
                if index + 1 >= len(meaningful) or meaningful[index + 1][0] <= indent:
                    raise WorkflowError("canonical artifact frontmatter container is empty")
                value, index = block(index + 1, meaningful[index + 1][0])
            result[name] = value  # type: ignore[index]
        return result, index

    value, end = block(0, 0)
    if end != len(meaningful) or not isinstance(value, dict):
        raise WorkflowError("canonical artifact frontmatter is malformed")
    return value


def frontmatter_mapping(text: str) -> dict[str, object]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise WorkflowError("canonical artifact has no YAML frontmatter")
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise WorkflowError("canonical artifact frontmatter is incomplete") from exc
    values = _yaml_subset(lines[1:closing])
    if not isinstance(values, dict):
        raise WorkflowError("canonical artifact frontmatter is malformed")
    return values


def frontmatter_value(text: str, key: str) -> str:
    values = frontmatter_mapping(text)
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise WorkflowError(f"canonical artifact is missing {key}")
    return value


def artifact_at(repo: Path, commit: str, relative_path: str) -> str:
    try:
        result = _run_git(
            repo, "ls-tree", "-z", "--full-tree", commit, "--", relative_path
        )
    except WorkflowError as exc:
        raise WorkflowError(f"canonical artifact read failed: {relative_path}") from exc
    if result.returncode != 0:
        raise WorkflowError(f"canonical artifact read failed: {relative_path}")
    records = result.stdout.split(b"\0")
    if len(records) != 2 or records[1] or not records[0]:
        raise WorkflowError(f"canonical artifact tree entry is missing or malformed: {relative_path}")
    fields = records[0].split(b"\t")
    metadata = fields[0].split(b" ") if len(fields) == 2 else []
    try:
        actual_path = fields[1].decode("utf-8")
    except (IndexError, UnicodeDecodeError) as exc:
        raise WorkflowError(f"canonical artifact tree entry is missing or malformed: {relative_path}") from exc
    if (
        len(metadata) != 3
        or metadata[0] not in {b"100644", b"100755"}
        or metadata[1] != b"blob"
        or OID_RE.fullmatch(metadata[2]) is None
        or actual_path != relative_path
    ):
        if actual_path == relative_path and len(metadata) == 3:
            raise WorkflowError(f"canonical artifact must be a regular blob: {relative_path}")
        raise WorkflowError(f"canonical artifact tree entry is missing or malformed: {relative_path}")
    try:
        blob = _run_git(repo, "cat-file", "blob", metadata[2].decode("ascii"))
    except WorkflowError as exc:
        raise WorkflowError(f"canonical artifact read failed: {relative_path}") from exc
    if blob.returncode != 0:
        raise WorkflowError(f"canonical artifact read failed: {relative_path}")
    try:
        return blob.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkflowError(f"canonical artifact is not UTF-8: {relative_path}") from exc


def assert_artifact(
    repo: Path,
    commit: str,
    relative_path: str,
    *,
    expected_id: str,
    state_key: str,
    expected_state: str,
) -> None:
    text = artifact_at(repo, commit, relative_path)
    frontmatter = frontmatter_mapping(text)
    actual_id = frontmatter.get("id")
    if actual_id != expected_id:
        raise WorkflowError(f"{relative_path} id must be {expected_id}")
    actual_state = frontmatter.get(state_key)
    if actual_state != expected_state:
        label = relative_path.rsplit("/", 1)[-1].split("-", 1)[0].upper()
        raise WorkflowError(f"{label}.{state_key} must be {expected_state}")
    if state_key == "execution_status" and "implementation_history" in frontmatter:
        if frontmatter["implementation_history"] != "first_parent":
            raise WorkflowError("Line.implementation_history must be first_parent")


def assert_req_target_criteria(
    repo: Path, approval_commit: str, req_path: str
) -> None:
    req = frontmatter_mapping(artifact_at(repo, approval_commit, req_path))
    criteria = req.get("criteria")
    if not isinstance(criteria, dict):
        raise WorkflowError("REQ.criteria must be a mapping")
    required = ("create", "update", "retire")
    allowed = (*required, "satisfy")
    if any(name not in criteria for name in required) or any(
        name not in allowed for name in criteria
    ):
        raise WorkflowError("REQ.criteria must contain canonical admission lists")

    expected_states = {
        "create": "active",
        "update": "active",
        "retire": "retired",
        "satisfy": "active",
    }
    seen: set[str] = set()
    target_count = 0
    for admission in allowed:
        targets = criteria.get(admission, [])
        if not isinstance(targets, list):
            raise WorkflowError(f"REQ.criteria.{admission} must be a list")
        for target in targets:
            if not isinstance(target, str) or not AC_RE.fullmatch(target):
                raise WorkflowError(f"REQ.criteria.{admission} has an invalid AC identity")
            if target in seen:
                raise WorkflowError("REQ.criteria target ACs must be unique across admission lists")
            seen.add(target)
            target_count += 1
            relative_path = f".proofline/criteria/{target}.md"
            assert_artifact(
                repo,
                approval_commit,
                relative_path,
                expected_id=target,
                state_key="status",
                expected_state=expected_states[admission],
            )
    if target_count == 0:
        raise WorkflowError("REQ.criteria admission lists must target at least one AC")


def assert_status_only_handoff(
    repo: Path, approval_commit: str, handoff_commit: str, line_path: str
) -> None:
    parents = git(repo, "rev-list", "--parents", "-n", "1", handoff_commit).stdout.split()
    if len(parents) != 2 or parents[1] != approval_commit:
        raise WorkflowError("handoff commit must be the approval commit's status-only direct child")
    changed = [
        path
        for path in git(
            repo, "diff", "--no-ext-diff", "--name-only", "--no-renames",
            approval_commit, handoff_commit
        ).stdout.splitlines()
        if path
    ]
    if changed != [line_path]:
        raise WorkflowError("handoff commit must be the approval commit's status-only direct child")
    before = artifact_at(repo, approval_commit, line_path).splitlines(keepends=True)
    after = artifact_at(repo, handoff_commit, line_path).splitlines(keepends=True)
    if len(before) != len(after):
        raise WorkflowError("handoff commit must be the approval commit's status-only direct child")
    differences = [index for index, pair in enumerate(zip(before, after, strict=True)) if pair[0] != pair[1]]
    if len(differences) != 1:
        raise WorkflowError("handoff commit must be the approval commit's status-only direct child")
    index = differences[0]
    if before[index].replace("not_started", "in_progress", 1) != after[index]:
        raise WorkflowError("handoff commit must be the approval commit's status-only direct child")


def validate_transitional_history(repo: Path, line_path: str) -> None:
    """Allow only the documented fieldless P→B validation gap."""
    if not (repo / "proofline.yaml").is_file():
        return
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.pop("PYTHONPATH", None)
    environment.update(
        {
            "LC_ALL": "C",
            "LANG": "C",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
    )
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    if any(not entry or entry == "." or not Path(entry).is_absolute() for entry in path_entries):
        raise WorkflowError("ProofLine validate PATH must contain only nonempty absolute entries")
    executable_name = shutil.which("proofline", path=os.pathsep.join(path_entries))
    if executable_name is None:
        raise WorkflowError("ProofLine validate executable is unavailable")
    try:
        executable = Path(executable_name).resolve(strict=True)
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise WorkflowError("ProofLine validate executable is not a regular executable")
        repo_root = repo.resolve(strict=True)
        try:
            executable.relative_to(repo_root)
        except ValueError:
            pass
        else:
            raise WorkflowError("ProofLine validate executable must not be inside the repository")
    except OSError as exc:
        raise WorkflowError("ProofLine validate executable is unavailable") from exc

    deadline = time.monotonic() + VALIDATE_TIMEOUT_SECONDS
    try:
        process = subprocess.Popen(
            (str(executable), "validate"), cwd=repo, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
            start_new_session=os.name != "nt",
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            ),
        )
        returncode, stdout, stderr = _capture_validate_process(process, deadline=deadline)
    except subprocess.TimeoutExpired as exc:
        raise WorkflowError("ProofLine validate executable timed out") from exc
    except FileNotFoundError as exc:
        raise WorkflowError("ProofLine validate executable is unavailable") from exc
    except OSError as exc:
        raise WorkflowError("ProofLine validate executable failed to start") from exc
    stdout_text = stdout.decode("utf-8", errors="replace")
    stderr_text = stderr.decode("utf-8", errors="replace")
    if stdout_text:
        raise WorkflowError("ProofLine validate produced unexpected stdout")
    if returncode == 0:
        if stderr_text:
            raise WorkflowError("ProofLine validate produced unexpected diagnostics")
        return
    if returncode != 1:
        raise WorkflowError("ProofLine validate exited unexpectedly")

    diagnostic_re = re.compile(r"^([^:\n]+): ([^:\n]+): ([^\n]+)$")
    diagnostics: list[tuple[str, str]] = []
    for line in stderr_text.splitlines():
        if not line:
            raise WorkflowError("ProofLine validate produced malformed diagnostics")
        match = diagnostic_re.fullmatch(line)
        if match is None:
            raise WorkflowError("ProofLine validate produced malformed diagnostics")
        if not match.group(3).strip():
            raise WorkflowError("ProofLine validate produced diagnostics without a message")
        diagnostics.append((match.group(1), match.group(2)))
    allowed = {
        (line_path, "history.line.policy.missing"),
    }
    if diagnostics != list(allowed):
        raise WorkflowError("history preflight failed: unexpected diagnostics")


def registered_worktrees(repo: Path) -> tuple[set[str], set[str]]:
    result = git(repo, "worktree", "list", "--porcelain")
    paths: set[str] = set()
    branches: set[str] = set()
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            paths.add(str(Path(line.removeprefix("worktree ")).resolve()))
        elif line.startswith("branch refs/heads/"):
            branches.add(line.removeprefix("branch refs/heads/"))
    return paths, branches


def create_worktree(
    repo: Path,
    line_id: str,
    branch: str,
    approval_commit: str,
) -> Path:
    repo = repo.resolve(strict=True)
    root = Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if root != repo:
        raise WorkflowError("--repo must be the main repository root")
    if not LINE_RE.fullmatch(line_id):
        raise WorkflowError("line id must match line-NNNN")
    if not COMMIT_RE.fullmatch(approval_commit):
        raise WorkflowError("approval commit must be a full 40-character lowercase SHA")
    if git(repo, "branch", "--show-current").stdout.strip() != "main":
        raise WorkflowError("main checkout must be on main")
    if git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout:
        raise WorkflowError("main working tree is not clean")

    resolved_commit = git(
        repo, "rev-parse", "--verify", f"{approval_commit}^{{commit}}"
    ).stdout.strip()
    if resolved_commit != approval_commit:
        raise WorkflowError("approval commit does not resolve exactly")
    handoff_commit = git(repo, "rev-parse", "HEAD").stdout.strip()
    if git(repo, "check-ref-format", "--branch", branch, check=False).returncode != 0:
        raise WorkflowError("implementation branch name is invalid")

    number = line_id.removeprefix("line-")
    line_dir = f".proofline/lines/{line_id}"
    assert_artifact(
        repo,
        approval_commit,
        f"{line_dir}/dcy-{number}.md",
        expected_id=f"dcy-{number}",
        state_key="status",
        expected_state="confirmed",
    )
    req_path = f"{line_dir}/req-{number}.md"
    assert_artifact(
        repo,
        approval_commit,
        req_path,
        expected_id=f"req-{number}",
        state_key="status",
        expected_state="approved",
    )
    assert_req_target_criteria(repo, approval_commit, req_path)
    assert_artifact(
        repo,
        approval_commit,
        f"{line_dir}/{line_id}.md",
        expected_id=line_id,
        state_key="execution_status",
        expected_state="not_started",
    )
    line_path = f"{line_dir}/{line_id}.md"
    assert_status_only_handoff(repo, approval_commit, handoff_commit, line_path)
    assert_artifact(
        repo,
        handoff_commit,
        line_path,
        expected_id=line_id,
        state_key="execution_status",
        expected_state="in_progress",
    )
    validate_transitional_history(repo, f"{line_dir}/{line_id}.md")

    relative_path = Path(".worktrees") / line_id
    worktree = (repo / relative_path).resolve()
    ignored = git(repo, "check-ignore", "-q", "--", str(relative_path), check=False)
    if ignored.returncode != 0:
        raise WorkflowError(".worktrees/line-NNNN must be ignored by Git")
    paths, branches = registered_worktrees(repo)
    branch_exists = git(
        repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False
    ).returncode == 0
    path_registered = str(worktree) in paths
    branch_registered = branch in branches
    if worktree.exists() or branch_exists or path_registered or branch_registered:
        if not (worktree.is_dir() and branch_exists and path_registered and branch_registered):
            raise WorkflowError("partial worktree path, branch, or registration collision")
        if git(worktree, "rev-parse", "HEAD").stdout.strip() != handoff_commit:
            raise WorkflowError("existing worktree HEAD does not match handoff commit")
        if git(worktree, "branch", "--show-current").stdout.strip() != branch:
            raise WorkflowError("existing worktree branch does not match")
        if git(
            worktree, "status", "--porcelain=v1", "--untracked-files=all"
        ).stdout:
            raise WorkflowError("existing worktree is not clean")
        return worktree
    if str(worktree) in paths:
        raise WorkflowError("worktree path registration collision")
    if branch in branches:
        raise WorkflowError("worktree branch registration collision")

    git(
        repo, "worktree", "add", "--no-checkout", str(relative_path),
        "-b", branch, handoff_commit,
        timeout=GIT_WORKTREE_TIMEOUT_SECONDS,
    )

    created_root = Path(
        git(worktree, "rev-parse", "--show-toplevel").stdout.strip()
    ).resolve(strict=True)
    if created_root != worktree:
        raise WorkflowError("created worktree root does not match target path")
    created_gitdir = Path(
        git(worktree, "rev-parse", "--absolute-git-dir").stdout.strip()
    ).resolve(strict=True)
    if not created_gitdir.is_dir():
        raise WorkflowError("created worktree gitdir is invalid")
    if git(worktree, "rev-parse", "HEAD").stdout.strip() != handoff_commit:
        raise WorkflowError("created worktree HEAD does not match handoff commit")
    if git(worktree, "branch", "--show-current").stdout.strip() != branch:
        raise WorkflowError("created worktree branch does not match")

    git(
        worktree, "reset", "--hard", handoff_commit,
        timeout=GIT_WORKTREE_TIMEOUT_SECONDS,
    )

    if git(worktree, "rev-parse", "HEAD").stdout.strip() != handoff_commit:
        raise WorkflowError("created worktree HEAD does not match handoff commit")
    if git(worktree, "branch", "--show-current").stdout.strip() != branch:
        raise WorkflowError("created worktree branch does not match")
    if git(
        worktree, "status", "--porcelain=v1", "--untracked-files=all"
    ).stdout:
        raise WorkflowError("created worktree is not clean")
    if git(repo, "branch", "--show-current").stdout.strip() != "main":
        raise WorkflowError("main checkout branch changed")
    if git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout:
        raise WorkflowError("main working tree changed")
    if (worktree / ".venv").exists():
        raise WorkflowError("worktree unexpectedly contains a ProofLine .venv")
    return worktree


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--line-id", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--approval-commit", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        worktree = create_worktree(
            args.repo,
            args.line_id,
            args.branch,
            args.approval_commit,
        )
    except (OSError, WorkflowError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"created: {worktree}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
