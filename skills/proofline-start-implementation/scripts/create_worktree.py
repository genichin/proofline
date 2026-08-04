#!/usr/bin/env python3
"""Create a ProofLine implementation linked worktree after fail-closed preflight."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import threading
import time


LINE_RE = re.compile(r"line-[0-9]{4}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
VALIDATE_TIMEOUT_SECONDS = 10
VALIDATE_OUTPUT_LIMIT = 256 * 1024
_REAPER_LOCK = threading.Lock()
_REAPER_REGISTRY: dict[int, subprocess.Popen[bytes]] = {}


class WorkflowError(RuntimeError):
    """Expected fail-closed workflow error."""


def _reap_validate_process(key: int, process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait()
    finally:
        with _REAPER_LOCK:
            if _REAPER_REGISTRY.get(key) is process:
                del _REAPER_REGISTRY[key]


def _transfer_validate_reap(process: subprocess.Popen[bytes]) -> None:
    key = id(process)
    with _REAPER_LOCK:
        if key in _REAPER_REGISTRY:
            return
        _REAPER_REGISTRY[key] = process
        threading.Thread(
            target=_reap_validate_process,
            args=(key, process),
            name="proofline-validate-reaper",
            daemon=True,
        ).start()


def _cleanup_validate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=0.1)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=0.1)
        except (OSError, subprocess.TimeoutExpired):
            _transfer_validate_reap(process)


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ("git", "-C", str(repo), *args),
        text=True,
        capture_output=True,
        check=False,
    )
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
    result = git(repo, "show", f"{commit}:{relative_path}")
    return result.stdout


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


def validate_transitional_history(repo: Path, line_path: str) -> None:
    """Allow only the documented fieldless P→B validation gap."""
    if not (repo / "proofline.yaml").is_file():
        return
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.update(
        {
            "LC_ALL": "C",
            "LANG": "C",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
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

    process: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None
    streams: dict[object, bytearray] = {}
    output_buffers: dict[object, bytearray] = {}
    deadline = time.monotonic() + VALIDATE_TIMEOUT_SECONDS
    try:
        process = subprocess.Popen(
            (str(executable), "validate"), cwd=repo, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
        )
        if process.stdout is None or process.stderr is None:
            raise WorkflowError("ProofLine validate executable failed to start")
        selector = selectors.DefaultSelector()
        for stream in (process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
            streams[stream] = bytearray()
            output_buffers[stream] = streams[stream]
        total = 0
        while streams:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired((str(executable), "validate"), VALIDATE_TIMEOUT_SECONDS)
            for key, _ in selector.select(timeout=min(0.05, remaining)):
                stream = key.fileobj
                chunk = os.read(stream.fileno(), 65536)
                if not chunk:
                    selector.unregister(stream)
                    streams.pop(stream, None)
                    continue
                total += len(chunk)
                if total > VALIDATE_OUTPUT_LIMIT:
                    raise WorkflowError("ProofLine validate produced excessive output")
                streams[stream].extend(chunk)
        returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
        stdout = bytes(output_buffers.get(process.stdout, b""))
        stderr = bytes(output_buffers.get(process.stderr, b""))
    except subprocess.TimeoutExpired as exc:
        raise WorkflowError("ProofLine validate executable timed out") from exc
    except FileNotFoundError as exc:
        raise WorkflowError("ProofLine validate executable is unavailable") from exc
    except OSError as exc:
        raise WorkflowError("ProofLine validate executable failed to start") from exc
    finally:
        if selector is not None:
            selector.close()
        if process is not None:
            _cleanup_validate_process(process)
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

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
    assert_artifact(
        repo,
        approval_commit,
        f"{line_dir}/req-{number}.md",
        expected_id=f"req-{number}",
        state_key="status",
        expected_state="approved",
    )
    assert_artifact(
        repo,
        approval_commit,
        f"{line_dir}/{line_id}.md",
        expected_id=line_id,
        state_key="execution_status",
        expected_state="not_started",
    )
    validate_transitional_history(repo, f"{line_dir}/{line_id}.md")

    relative_path = Path(".worktrees") / line_id
    worktree = (repo / relative_path).resolve()
    ignored = git(repo, "check-ignore", "-q", "--", str(relative_path), check=False)
    if ignored.returncode != 0:
        raise WorkflowError(".worktrees/line-NNNN must be ignored by Git")
    if worktree.exists():
        raise WorkflowError("worktree path collision")
    if git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0:
        raise WorkflowError("implementation branch collision")
    paths, branches = registered_worktrees(repo)
    if str(worktree) in paths:
        raise WorkflowError("worktree path registration collision")
    if branch in branches:
        raise WorkflowError("worktree branch registration collision")

    git(repo, "worktree", "add", str(relative_path), "-b", branch, approval_commit)

    if git(worktree, "rev-parse", "HEAD").stdout.strip() != approval_commit:
        raise WorkflowError("created worktree HEAD does not match approval commit")
    if git(worktree, "branch", "--show-current").stdout.strip() != branch:
        raise WorkflowError("created worktree branch does not match")
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
