#!/usr/bin/env python3
"""Read-only pre-admission check for a ProofLine main-first candidate."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import yaml


SHA = re.compile(r"^[0-9a-f]{40}$")
OID = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
LINE = re.compile(r"^line-(\d{4})$")
REF = re.compile(r"^refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*$")
MS_ID = re.compile(r"^ms-(\d{4})-(\d{3})$")
IQC_ID = re.compile(r"^iqc-(\d{4})-(\d{3})$")
TIMEOUT_SECONDS = 5
OUTPUT_LIMIT = 8 * 1024 * 1024


class PreflightError(RuntimeError):
    pass


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(
    loader: yaml.SafeLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict[object, object]:
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark, f"duplicate key: {key}", key_node.start_mark
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping
)


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    command = ("git", *args)
    try:
        process = subprocess.Popen(
            command,
            cwd=repo,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except OSError as exc:
        raise PreflightError("git read failed to start") from exc
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
                    remaining = OUTPUT_LIMIT - captured
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
    deadline = time.monotonic() + TIMEOUT_SECONDS
    failure: PreflightError | None = None
    try:
        while not all(event.is_set() for event in done):
            if overflow.is_set():
                failure = PreflightError("git read produced excessive output")
                break
            if read_error.is_set():
                failure = PreflightError("git read pipe failed")
                break
            if time.monotonic() >= deadline:
                failure = PreflightError("git read timed out")
                break
            time.sleep(0.005)
        if failure is None and overflow.is_set():
            failure = PreflightError("git read produced excessive output")
        if failure is None and read_error.is_set():
            failure = PreflightError("git read pipe failed")
        if failure is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = PreflightError("git read timed out")
            else:
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    failure = PreflightError("git read timed out")
        if failure is None and overflow.is_set():
            failure = PreflightError("git read produced excessive output")
        if failure is None and read_error.is_set():
            failure = PreflightError("git read pipe failed")
    finally:
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=0.2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
                process.wait()
        else:
            process.wait()
        for reader in readers:
            reader.join()
        process.stdout.close()
        process.stderr.close()
    if failure is not None:
        raise failure
    return subprocess.CompletedProcess(
        command, process.returncode, bytes(buffers[0]), bytes(buffers[1])
    )


def git(repo: Path, *args: str) -> str:
    result = _run_git(repo, *args)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise PreflightError(f"git read failed: {detail or args[0]}")
    try:
        return result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise PreflightError("git read returned non-UTF-8 output") from exc


def exact_commit(repo: Path, value: str, label: str) -> str:
    if SHA.fullmatch(value) is None:
        raise PreflightError(f"{label} must be a lowercase full commit SHA")
    resolved = git(repo, "rev-parse", "--verify", f"{value}^{{commit}}")
    if resolved != value:
        raise PreflightError(f"{label} does not resolve exactly")
    return resolved


def parse_manifest(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if len(lines) != 6 or lines[0] != "---" or lines[-1] != "---":
        raise PreflightError("integration manifest must be frontmatter-only")
    values: dict[str, str] = {}
    for line in lines[1:-1]:
        match = re.fullmatch(r'([a-z_]+): "?([^"\n]+)"?', line)
        if match is None or match.group(1) in values:
            raise PreflightError("integration manifest schema is invalid")
        values[match.group(1)] = match.group(2)
    if set(values) != {"id", "line_id", "main_parent", "line_head"}:
        raise PreflightError("integration manifest schema is invalid")
    return values


def frontmatter(text: str, label: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---\n"):
        raise PreflightError(f"{label} frontmatter is invalid")
    closing = text.find("\n---\n", 4)
    if closing >= 0:
        body = text[closing + 5 :]
    elif text.endswith("\n---"):
        closing = len(text) - 4
        body = ""
    else:
        raise PreflightError(f"{label} frontmatter is invalid")
    try:
        value = yaml.load(text[4:closing], Loader=_UniqueLoader)
    except (yaml.YAMLError, UnicodeError) as exc:
        raise PreflightError(f"{label} frontmatter is invalid") from exc
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise PreflightError(f"{label} frontmatter is invalid")
    return value, body


def tree_paths(repo: Path, commit: str, prefix: str) -> list[str]:
    raw = git(repo, "ls-tree", "-rz", "--name-only", commit, "--", prefix)
    paths = [path for path in raw.split("\0") if path]
    for path in paths:
        parts = Path(path).parts
        if path.startswith("/") or "\\" in path or ".." in parts or not path.startswith(prefix):
            raise PreflightError("quality head contains an unsafe artifact path")
    return sorted(paths)


def object_text(repo: Path, commit: str, path: str, paths: set[str]) -> str | None:
    if path not in paths:
        return None
    try:
        entry = _run_git(repo, "ls-tree", "-z", "--full-tree", commit, "--", path)
    except PreflightError as exc:
        raise PreflightError(f"canonical artifact read failed: {path}") from exc
    if entry.returncode != 0:
        raise PreflightError(f"canonical artifact read failed: {path}")
    records = entry.stdout.split(b"\0")
    if len(records) != 2 or records[1] or not records[0]:
        raise PreflightError(f"canonical artifact tree entry is missing or malformed: {path}")
    fields = records[0].split(b"\t")
    metadata = fields[0].split(b" ") if len(fields) == 2 else []
    try:
        actual_path = fields[1].decode("utf-8")
    except (IndexError, UnicodeDecodeError) as exc:
        raise PreflightError(f"canonical artifact tree entry is missing or malformed: {path}") from exc
    if (
        len(metadata) != 3 or metadata[0] not in {b"100644", b"100755"}
        or metadata[1] != b"blob" or OID.fullmatch(metadata[2]) is None or actual_path != path
    ):
        if actual_path == path and len(metadata) == 3:
            raise PreflightError(f"canonical artifact must be a regular blob: {path}")
        raise PreflightError(f"canonical artifact tree entry is missing or malformed: {path}")
    try:
        blob = _run_git(repo, "cat-file", "blob", metadata[2].decode("ascii"))
    except PreflightError as exc:
        raise PreflightError(f"canonical artifact read failed: {path}") from exc
    if blob.returncode != 0:
        raise PreflightError(f"canonical artifact read failed: {path}")
    try:
        return blob.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PreflightError(f"canonical artifact is not UTF-8: {path}") from exc


def revision_without_status(
    state: dict[str, object], body: str, field: str
) -> tuple[dict[str, object], str]:
    normalized = dict(state)
    normalized.pop(field, None)
    return normalized, body


def changed_paths(repo: Path, commit: str) -> list[str]:
    return [
        path
        for path in git(
            repo, "diff", "--name-only", "--no-renames", f"{commit}^1", commit
        ).splitlines()
        if path
    ]


def first_parent_chain(repo: Path, commit: str) -> tuple[list[str], dict[str, int]]:
    if git(repo, "rev-parse", "--is-shallow-repository") != "false":
        raise PreflightError("quality head first-parent history is unavailable")
    commits = git(repo, "rev-list", "--first-parent", "--reverse", commit).splitlines()
    if not commits or commits[-1] != commit:
        raise PreflightError("quality head first-parent history is unavailable")
    return commits, {sha: index for index, sha in enumerate(commits)}


def _schema(state: dict[str, object], required: set[str], label: str) -> None:
    if set(state) != required:
        raise PreflightError(f"{label} frontmatter schema is invalid")


def _state_at(
    repo: Path, commit: str, path: str, label: str
) -> tuple[dict[str, object], str] | None:
    paths = set(tree_paths(repo, commit, path))
    text = object_text(repo, commit, path, paths)
    return None if text is None else frontmatter(text, label)


def validate_quality_head(repo: Path, line_id: str, q: str) -> None:
    number = LINE.fullmatch(line_id).group(1)  # type: ignore[union-attr]
    line_directory = f".proofline/lines/{line_id}"
    line_path = f"{line_directory}/{line_id}.md"
    micro_directory = f"{line_directory}/micro-specs/"
    q_paths = set(tree_paths(repo, q, ".proofline/lines/"))
    line_text = object_text(repo, q, line_path, q_paths)
    if line_text is None:
        raise PreflightError("quality head target Line is missing")
    line_state, line_body = frontmatter(line_text, "quality head Line")
    _schema(
        line_state,
        {"id", "execution_status", "implementation_history"},
        "quality head Line",
    )
    if line_state.get("id") != line_id:
        raise PreflightError("quality head target Line identity mismatch")
    if line_state.get("execution_status") != "verifying":
        raise PreflightError("quality head Line status must be verifying")
    if line_state.get("implementation_history") != "first_parent" or line_body.strip():
        raise PreflightError("quality head target Line contract is invalid")

    quality_changes = changed_paths(repo, q)
    changed_lines = [
        path
        for path in quality_changes
        if re.fullmatch(r"\.proofline/lines/line-\d{4}/line-\d{4}\.md", path)
    ]
    if any(path != line_path for path in changed_lines):
        raise PreflightError("quality head contains multiple Lines")
    allowed_ms = re.compile(rf"^{re.escape(micro_directory)}ms-{number}-\d{{3}}\.md$")
    allowed_iqc = re.compile(rf"^{re.escape(micro_directory)}iqc-{number}-\d{{3}}\.md$")
    if any(
        path != line_path and allowed_ms.fullmatch(path) is None and allowed_iqc.fullmatch(path) is None
        for path in quality_changes
    ):
        raise PreflightError("quality head transition contains unrelated paths")
    if line_path not in quality_changes:
        raise PreflightError("quality head is not the exact first-parent quality transition")

    parent = exact_commit(repo, git(repo, "rev-parse", f"{q}^1"), "quality parent")
    parent_line = _state_at(repo, parent, line_path, "quality parent Line")
    if parent_line is None:
        raise PreflightError("quality head is not the exact first-parent quality transition")
    parent_line_state, parent_line_body = parent_line
    if (
        parent_line_state.get("execution_status") != "in_progress"
        or revision_without_status(parent_line_state, parent_line_body, "execution_status")
        != revision_without_status(line_state, line_body, "execution_status")
    ):
        raise PreflightError("quality head is not the exact first-parent quality transition")

    commits, positions = first_parent_chain(repo, q)
    ms_paths = sorted(path for path in q_paths if path.startswith(micro_directory) and "/ms-" in path)
    canonical_ms_paths = [path for path in ms_paths if allowed_ms.fullmatch(path)]
    if ms_paths != canonical_ms_paths:
        raise PreflightError("quality head IQC coverage/binding invalid: noncanonical Micro-SPEC path")
    if not canonical_ms_paths:
        raise PreflightError("quality head IQC coverage/binding invalid: no target Micro-SPEC")

    required_iqc_paths: set[str] = set()
    canonical_iqc_paths: set[str] = set()
    for ms_path in canonical_ms_paths:
        ms_id = Path(ms_path).stem
        suffix = MS_ID.fullmatch(ms_id)
        assert suffix is not None
        current = _state_at(repo, q, ms_path, f"Micro-SPEC {ms_id}")
        assert current is not None
        ms_state, ms_body = current
        _schema(
            ms_state,
            {"id", "parent_req", "criteria", "spec_status", "implementation_status"},
            f"Micro-SPEC {ms_id}",
        )
        if (
            ms_state.get("id") != ms_id
            or ms_state.get("parent_req") != f"req-{number}"
            or not isinstance(ms_state.get("criteria"), list)
            or not ms_state["criteria"]
            or any(re.fullmatch(r"ac-\d{4}", item) is None for item in ms_state["criteria"] if isinstance(item, str))
            or any(not isinstance(item, str) for item in ms_state["criteria"])
            or len(set(ms_state["criteria"])) != len(ms_state["criteria"])
        ):
            raise PreflightError(
                f"quality head IQC coverage/binding invalid: Micro-SPEC {ms_id} identity mismatch"
            )
        iqc_id = ms_id.replace("ms-", "iqc-", 1)
        iqc_path = f"{micro_directory}{iqc_id}.md"
        canonical_iqc_paths.add(iqc_path)
        if ms_state.get("spec_status") == "withdrawn":
            retained_iqc_text = object_text(repo, q, iqc_path, q_paths)
            if retained_iqc_text is not None:
                retained_iqc_state, _ = frontmatter(retained_iqc_text, f"IQC {iqc_id}")
                if (
                    retained_iqc_state.get("id") != iqc_id
                    or retained_iqc_state.get("micro_spec") != ms_id
                ):
                    raise PreflightError(
                        f"quality head IQC coverage/binding invalid: IQC {iqc_id} identity mismatch"
                    )
            continue
        if ms_state.get("spec_status") != "approved" or ms_state.get("implementation_status") != "implemented":
            raise PreflightError(
                f"quality head IQC coverage/binding invalid: Micro-SPEC {ms_id} is not approved and implemented"
            )
        required_iqc_paths.add(iqc_path)
        iqc_text = object_text(repo, q, iqc_path, q_paths)
        if iqc_text is None:
            raise PreflightError(
                f"quality head IQC coverage/binding invalid: missing IQC for {ms_id}"
            )
        iqc_state, _ = frontmatter(iqc_text, f"IQC {iqc_id}")
        _schema(
            iqc_state,
            {"id", "micro_spec", "micro_spec_commit", "implementation_commit", "result"},
            f"IQC {iqc_id}",
        )
        if iqc_state.get("id") != iqc_id or iqc_state.get("micro_spec") != ms_id:
            raise PreflightError(
                f"quality head IQC coverage/binding invalid: IQC {iqc_id} identity mismatch"
            )
        if iqc_state.get("result") != "passed":
            raise PreflightError(
                f"quality head IQC coverage/binding invalid: IQC {iqc_id} is not passed"
            )

        states = [_state_at(repo, commit, ms_path, f"Micro-SPEC {ms_id}") for commit in commits]
        latest_start: int | None = None
        implemented_transition: int | None = None
        previous_status: object = None
        for index, state in enumerate(states):
            status = state[0].get("implementation_status") if state is not None else None
            if status == "in_progress" and previous_status != "in_progress":
                latest_start = index
            if status == "implemented" and previous_status == "in_progress":
                implemented_transition = index
            previous_status = status
        iqc_boundaries: list[int] = []
        previous_iqc: str | None = None
        for index, commit in enumerate(commits):
            commit_paths = set(tree_paths(repo, commit, iqc_path))
            content = object_text(repo, commit, iqc_path, commit_paths)
            if content == iqc_text and previous_iqc != iqc_text:
                iqc_boundaries.append(index)
            previous_iqc = content
        specification_value = iqc_state.get("micro_spec_commit")
        implementation_value = iqc_state.get("implementation_commit")
        specification = positions.get(specification_value) if isinstance(specification_value, str) else None
        implementation = positions.get(implementation_value) if isinstance(implementation_value, str) else None
        specification_state = (
            _state_at(repo, specification_value, ms_path, f"Micro-SPEC {ms_id}")
            if isinstance(specification_value, str) and SHA.fullmatch(specification_value)
            else None
        )
        valid_specification = (
            specification_state is not None
            and specification_state[0].get("spec_status") == "approved"
            and revision_without_status(specification_state[0], specification_state[1], "implementation_status")
            == revision_without_status(ms_state, ms_body, "implementation_status")
        )
        if (
            latest_start is None
            or implemented_transition is None
            or len(iqc_boundaries) != 1
            or iqc_boundaries[0] != implemented_transition
            or not valid_specification
            or specification is None
            or implementation is None
            or not (specification < latest_start < implementation < implemented_transition <= positions[q])
            or all(path.startswith(".proofline/") for path in changed_paths(repo, implementation_value))
        ):
            raise PreflightError(
                f"quality head IQC coverage/binding invalid: IQC {iqc_id} implementation binding is stale or invalid"
            )

    if not any(allowed_iqc.fullmatch(path) for path in quality_changes):
        raise PreflightError("quality head is not the exact first-parent quality transition")
    extra_iqcs = sorted(
        path for path in q_paths if path.startswith(micro_directory) and "/iqc-" in path and path not in canonical_iqc_paths
    )
    if extra_iqcs:
        raise PreflightError("quality head IQC coverage/binding invalid: unmatched IQC artifact")


def preflight(
    repo: Path,
    line_id: str,
    main_ref: str,
    line_ref: str,
    main_parent: str,
    line_head: str,
    candidate: str,
) -> None:
    match = LINE.fullmatch(line_id)
    if match is None:
        raise PreflightError("line-id must match line-NNNN")
    if REF.fullmatch(main_ref) is None or REF.fullmatch(line_ref) is None:
        raise PreflightError("main-ref and line-ref must be full local branch refs")
    if main_ref == line_ref:
        raise PreflightError("main and Line refs collide")
    root = Path(git(repo, "rev-parse", "--show-toplevel")).resolve()
    if root != repo.resolve():
        raise PreflightError("repo must be the exact candidate worktree root")
    if git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise PreflightError("candidate worktree is not clean")

    m = exact_commit(repo, main_parent, "main-parent M")
    q = exact_commit(repo, line_head, "line-head Q")
    v = exact_commit(repo, candidate, "candidate V")
    if git(repo, "rev-parse", "--verify", main_ref) != m:
        raise PreflightError("stale main ref: current ref does not equal M")
    if git(repo, "rev-parse", "--verify", line_ref) != q:
        raise PreflightError("stale line ref: current ref does not equal Q")
    if git(repo, "rev-parse", "HEAD") != v:
        raise PreflightError("candidate worktree HEAD does not equal V")

    parents = git(repo, "rev-list", "--parents", "-n", "1", v).split()
    if parents != [v, m, q]:
        raise PreflightError("candidate V must have exactly ordered parents M then Q")

    validate_quality_head(repo, line_id, q)

    number = match.group(1)
    manifest_path = f".proofline/lines/{line_id}/integration-{number}.md"
    try:
        manifest_paths = set(tree_paths(repo, v, manifest_path))
        manifest_text = object_text(repo, v, manifest_path, manifest_paths)
        if manifest_text is None:
            raise PreflightError("canonical integration manifest is missing")
        manifest = parse_manifest(manifest_text)
    except PreflightError as exc:
        raise PreflightError(f"manifest unavailable or invalid: {exc}") from exc
    expected = {
        "id": f"integration-{number}",
        "line_id": line_id,
        "main_parent": m,
        "line_head": q,
    }
    if manifest != expected:
        raise PreflightError("manifest binding mismatch")

    introduced = git(
        repo, "diff-tree", "--no-commit-id", "--name-only", "--diff-filter=A", "-r", m, v
    ).splitlines()
    integration_paths = sorted(
        path for path in introduced if Path(path).name.startswith("integration-") and path.endswith(".md")
    )
    if integration_paths != [manifest_path]:
        raise PreflightError("candidate must introduce exactly one canonical integration manifest")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repo", required=True, type=Path)
    result.add_argument("--line-id", required=True)
    result.add_argument("--main-ref", required=True)
    result.add_argument("--line-ref", required=True)
    result.add_argument("--main-parent", required=True)
    result.add_argument("--line-head", required=True)
    result.add_argument("--candidate", required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        preflight(
            args.repo,
            args.line_id,
            args.main_ref,
            args.line_ref,
            args.main_parent,
            args.line_head,
            args.candidate,
        )
    except PreflightError as exc:
        print(f"pre-admission: failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"pre-admission: passed M={args.main_parent} Q={args.line_head} V={args.candidate}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
