from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .yaml_strict import safe_load_unique


LINE_PATH = re.compile(r"^\.proofline/lines/line-(\d{4})/line-\1\.md$")
MS_PATH = re.compile(
    r"^\.proofline/lines/line-(\d{4})/micro-specs/ms-\1-\d{3}\.md$"
)
IQC_PATH = re.compile(
    r"^\.proofline/lines/line-(\d{4})/micro-specs/iqc-\1-\d{3}\.md$"
)
INTEGRATION_PATH = re.compile(
    r"^\.proofline/lines/line-(\d{4})/integration-\1\.md$"
)
GIT_TIMEOUT_SECONDS = 5
GIT_OUTPUT_LIMIT = 8 * 1024 * 1024
GIT_SESSION_COMMAND_LIMIT = 20_000
GIT_SESSION_OUTPUT_LIMIT = 256 * 1024 * 1024
GIT_SESSION_CACHE_LIMIT = 128 * 1024 * 1024
GIT_SESSION_DEADLINE_SECONDS = 120
PROCESS_CLEANUP_GRACE_SECONDS = 0.1
_REAPER_LOCK = threading.Lock()
_REAPER_REGISTRY: dict[int, subprocess.Popen[bytes]] = {}


@dataclass(frozen=True, order=True)
class HistoryError:
    path: str
    code: str
    message: str


class HistoryUnavailable(Exception):
    pass


@dataclass
class _GitSession:
    root: Path
    cache: dict[tuple[str, ...], bytes] = field(default_factory=dict)
    commands: int = 0
    output_bytes: int = 0
    cache_bytes: int = 0
    started: float = field(default_factory=time.monotonic)


def _cleanup_process(
    process: subprocess.Popen[bytes], *, deadline: float, grace: float = 0.1
) -> None:
    """Best-effort bounded child cleanup used on every exceptional path."""
    del deadline  # Cleanup has an independent finite budget after command expiry.
    grace = min(max(grace, 0.001), PROCESS_CLEANUP_GRACE_SECONDS)
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except (OSError, AttributeError):
        pass
    try:
        process.wait(timeout=grace)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    if process.poll() is not None:
        return
    try:
        process.kill()
    except (OSError, AttributeError):
        pass
    try:
        process.wait(timeout=grace)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        _transfer_reap_ownership(process)


def _reap_once(key: int, process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait()
    finally:
        with _REAPER_LOCK:
            if _REAPER_REGISTRY.get(key) is process:
                del _REAPER_REGISTRY[key]


def _transfer_reap_ownership(process: subprocess.Popen[bytes]) -> None:
    key = id(process)
    with _REAPER_LOCK:
        if key in _REAPER_REGISTRY:
            return
        _REAPER_REGISTRY[key] = process
        reaper = threading.Thread(
            target=_reap_once,
            args=(key, process),
            name="proofline-child-reaper",
            daemon=True,
        )
        reaper.start()


def _capture_process(
    process: subprocess.Popen[bytes], *, deadline: float, output_limit: int
) -> tuple[int, bytes, bytes]:
    """Portable bounded capture for POSIX and Windows anonymous pipes."""
    if process.stdout is None or process.stderr is None:
        _cleanup_process(process, deadline=deadline)
        raise HistoryUnavailable
    buffers = [bytearray(), bytearray()]
    finished = [threading.Event(), threading.Event()]
    overflow = threading.Event()
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
                    remaining = output_limit - captured
                    if len(chunk) > remaining:
                        accepted = max(0, remaining)
                        buffers[index].extend(chunk[:accepted])
                        captured += accepted
                        overflow.set()
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
            if overflow.is_set() or read_error.is_set() or time.monotonic() >= deadline:
                raise HistoryUnavailable
            time.sleep(0.005)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HistoryUnavailable
        returncode = process.wait(timeout=remaining)
        if overflow.is_set() or read_error.is_set():
            raise HistoryUnavailable
        return returncode, bytes(buffers[0]), bytes(buffers[1])
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise HistoryUnavailable from error
    finally:
        _cleanup_process(process, deadline=deadline)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        for reader in readers:
            reader.join(timeout=PROCESS_CLEANUP_GRACE_SECONDS)


def _git(session: _GitSession, *arguments: str) -> bytes:
    command_key = tuple(arguments)
    if time.monotonic() - session.started >= GIT_SESSION_DEADLINE_SECONDS:
        raise HistoryUnavailable
    if command_key in session.cache:
        return session.cache[command_key]
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    if session.commands >= GIT_SESSION_COMMAND_LIMIT:
        raise HistoryUnavailable
    session.commands += 1
    process: subprocess.Popen[bytes] | None = None
    command_deadline = min(
        time.monotonic() + GIT_TIMEOUT_SECONDS,
        session.started + GIT_SESSION_DEADLINE_SECONDS,
    )
    session_deadline = session.started + GIT_SESSION_DEADLINE_SECONDS
    try:
        process = subprocess.Popen(
            ("git", *arguments),
            cwd=session.root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment,
            stdin=subprocess.DEVNULL,
        )
        returncode, stdout, stderr = _capture_process(
            process,
            deadline=min(command_deadline, session_deadline),
            output_limit=min(
                GIT_OUTPUT_LIMIT,
                GIT_SESSION_OUTPUT_LIMIT - session.output_bytes,
            ),
        )
        session.output_bytes += len(stdout) + len(stderr)
        if session.output_bytes > GIT_SESSION_OUTPUT_LIMIT:
            raise HistoryUnavailable
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise HistoryUnavailable from error
    except HistoryUnavailable:
        raise
    if returncode != 0:
        raise HistoryUnavailable
    if session.cache_bytes + len(stdout) > GIT_SESSION_CACHE_LIMIT:
        raise HistoryUnavailable
    session.cache[command_key] = stdout
    session.cache_bytes += len(stdout)
    return stdout


def _frontmatter(content: bytes) -> dict[str, object]:
    try:
        text = content.decode("utf-8")
        lines = text.splitlines()
        if not lines or lines[0] != "---" or "---" not in lines[1:]:
            raise HistoryUnavailable
        closing = lines.index("---", 1)
        value = safe_load_unique("\n".join(lines[1:closing]))
    except (UnicodeError, yaml.YAMLError) as error:
        raise HistoryUnavailable from error
    if not isinstance(value, dict):
        raise HistoryUnavailable
    return value


def _yaml_has_duplicate_mapping(payload: bytes) -> bool:
    """Detect duplicate mapping keys without treating unrelated old garbage as YAML."""
    try:
        lines = payload.decode("utf-8").splitlines()
        if not lines or lines[0] != "---" or "---" not in lines[1:]:
            return False
        closing = lines.index("---", 1)
        document = yaml.compose("\n".join(lines[1:closing]))
    except (UnicodeError, yaml.YAMLError):
        return False

    def visit(node: yaml.Node) -> bool:
        if isinstance(node, yaml.MappingNode):
            keys = [key.value for key, _ in node.value if isinstance(key, yaml.ScalarNode)]
            if len(keys) != len(set(keys)):
                return True
            return any(visit(key) or visit(value) for key, value in node.value)
        if isinstance(node, yaml.SequenceNode):
            return any(visit(item) for item in node.value)
        return False

    return visit(document) if document is not None else False


def _spec_revision_bytes(content: bytes | None) -> bytes | None:
    """Return Micro-SPEC bytes with lifecycle-only status normalized away."""
    if content is None:
        return None
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HistoryUnavailable from error
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return content
    closing = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.rstrip("\r\n") == "---"
        ),
        None,
    )
    if closing is None:
        return content
    yaml_text = "".join(lines[1:closing])
    try:
        safe_load_unique(yaml_text)
        document = yaml.compose(yaml_text)
    except yaml.YAMLError as error:
        raise HistoryUnavailable from error
    if not isinstance(document, yaml.MappingNode):
        return content
    matches = [
        (key, value)
        for key, value in document.value
        if isinstance(key, yaml.ScalarNode)
        and key.value == "implementation_status"
    ]
    if not matches:
        return content
    if len(matches) != 1:
        raise HistoryUnavailable
    key, value = matches[0]
    if not isinstance(value, yaml.ScalarNode) or value.value not in {
        "not_started",
        "in_progress",
        "implemented",
    }:
        raise HistoryUnavailable
    key_line = yaml_text.count("\n", 0, key.start_mark.index)
    value_line = yaml_text.count("\n", 0, value.end_mark.index)
    if key.start_mark.column != 0 or key_line != value_line:
        raise HistoryUnavailable
    line_start = yaml_text.rfind("\n", 0, key.start_mark.index) + 1
    line_end = yaml_text.find("\n", value.end_mark.index)
    if line_end < 0:
        line_end = len(yaml_text)
    else:
        line_end += 1
    if yaml_text[line_start:key.start_mark.index].strip():
        raise HistoryUnavailable
    prefix_bytes = "".join(lines[:1]).encode("utf-8")
    before = ("".join(lines[1:closing])[:line_start]).encode("utf-8")
    removed_start = len(prefix_bytes) + len(before)
    removed_end = len(prefix_bytes) + len(yaml_text[:line_end].encode("utf-8"))
    return content[:removed_start] + content[removed_end:]


def _tree_paths(session: _GitSession, commit: str) -> set[str]:
    output = _git(
        session, "ls-tree", "-r", "--name-only", commit, "--", ".proofline/lines"
    )
    try:
        return set(output.decode("utf-8").splitlines())
    except UnicodeError as error:
        raise HistoryUnavailable from error


def _file(session: _GitSession, commit: str, path: str, paths: set[str]) -> bytes | None:
    if path not in paths:
        return None
    return _git(session, "show", f"{commit}:{path}")


def _current_artifacts(root: Path) -> tuple[dict[str, dict[str, object]], list[str]]:
    artifacts: dict[str, dict[str, object]] = {}
    malformed: list[str] = []
    try:
        paths = sorted((root / ".proofline/lines").rglob("*.md"))
    except OSError:
        return artifacts, [".proofline/lines"]
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if not (
            LINE_PATH.fullmatch(relative)
            or MS_PATH.fullmatch(relative)
            or IQC_PATH.fullmatch(relative)
            or INTEGRATION_PATH.fullmatch(relative)
        ):
            continue
        try:
            artifacts[relative] = _frontmatter(path.read_bytes())
        except (OSError, HistoryUnavailable):
            malformed.append(relative)
    return artifacts, malformed


def _unavailable(path: str) -> HistoryError:
    return HistoryError(
        path,
        "history.unavailable",
        "exact first-parent Git history를 입증할 수 없습니다.",
    )


def _line_error(path: str, code: str, message: str) -> HistoryError:
    return HistoryError(path, code, message)


def _history(
    session: _GitSession, head: str = "HEAD"
) -> tuple[list[str], list[set[str]]]:
    shallow = _git(session, "rev-parse", "--is-shallow-repository").strip()
    if shallow != b"false":
        raise HistoryUnavailable
    output = _git(session, "rev-list", "--first-parent", "--reverse", head)
    try:
        commits = output.decode("ascii").splitlines()
    except UnicodeError as error:
        raise HistoryUnavailable from error
    if not commits:
        raise HistoryUnavailable
    return commits, [_tree_paths(session, commit) for commit in commits]


def _is_line_quality_head(
    session: _GitSession, line_path: str, line_head: str
) -> bool:
    line_head_paths = _tree_paths(session, line_head)
    try:
        line_state = _frontmatter(_file(session, line_head, line_path, line_head_paths) or b"")
        line_head_changes = _git(
            session, "diff", "--name-only", "--no-renames", f"{line_head}^1", line_head
        ).decode("utf-8").splitlines()
    except (HistoryUnavailable, UnicodeError):
        return False
    line_directory = line_path.rsplit("/", 1)[0]
    quality_paths = [
        path
        for path in line_head_changes
        if IQC_PATH.fullmatch(path) and path.startswith(f"{line_directory}/micro-specs/")
    ]
    return (
        line_state.get("execution_status") == "verifying"
        and line_path in line_head_changes
        and bool(quality_paths)
    )


def _integration_spine(
    session: _GitSession,
    line_path: str,
    main_commits: list[str],
    main_trees: list[set[str]],
) -> tuple[list[str], list[set[str]], list[HistoryError]]:
    """Select and validate a main-first integration candidate for one Line."""
    line_match = LINE_PATH.fullmatch(line_path)
    assert line_match is not None
    line_number = line_match.group(1)
    manifest_path = (
        f".proofline/lines/line-{line_number}/integration-{line_number}.md"
    )
    candidates: list[tuple[int, str, str, str]] = []
    for index, commit in enumerate(main_commits):
        parent_line = _git(session, "rev-list", "--parents", "-n", "1", commit)
        try:
            values = parent_line.decode("ascii").split()
        except UnicodeError as error:
            raise HistoryUnavailable from error
        if len(values) < 3:
            continue
        main_parent, line_head = values[1], values[2]
        commit_paths = main_trees[index]
        introduces_manifest = (
            manifest_path in commit_paths
            and manifest_path not in _tree_paths(session, main_parent)
        )
        if line_path in _tree_paths(session, line_head) and (
            introduces_manifest or _is_line_quality_head(session, line_path, line_head)
        ):
            candidates.append((index, commit, main_parent, line_head))
    if not candidates:
        return main_commits, main_trees, []

    candidate_index, candidate, main_parent, line_head = candidates[-1]
    candidate_paths = main_trees[candidate_index]
    parent_line = _git(session, "rev-list", "--parents", "-n", "1", candidate)
    try:
        parent_values = parent_line.decode("ascii").split()
    except UnicodeError as error:
        raise HistoryUnavailable from error
    if len(parent_values) != 3:
        return main_commits, main_trees, [
            _line_error(
                manifest_path,
                "history.integration.parent",
                "integration candidate는 main과 단일 Line head의 exactly two parents여야 합니다.",
            )
        ]

    main_parent_paths = _tree_paths(session, main_parent)
    introduced_manifests = sorted(
        path
        for path in candidate_paths - main_parent_paths
        if Path(path).name.startswith("integration-") and path.endswith(".md")
    )
    if introduced_manifests != [manifest_path]:
        return main_commits, main_trees, [
            _line_error(
                manifest_path,
                "history.integration.manifest",
                "integration candidate는 target Line의 canonical manifest 하나만 새로 포함해야 합니다.",
            )
        ]
    manifest_bytes = _file(session, candidate, manifest_path, candidate_paths)
    if manifest_bytes is None:
        return main_commits, main_trees, [
            _line_error(
                manifest_path,
                "history.integration.manifest",
                "main-first integration candidate에는 Line integration manifest가 필요합니다.",
            )
        ]
    try:
        manifest = _frontmatter(manifest_bytes)
    except HistoryUnavailable:
        return main_commits, main_trees, [_unavailable(manifest_path)]
    if (
        manifest.get("id") != f"integration-{line_number}"
        or manifest.get("line_id") != f"line-{line_number}"
        or manifest.get("main_parent") != main_parent
        or manifest.get("line_head") != line_head
    ):
        return main_commits, main_trees, [
            _line_error(
                manifest_path,
                "history.integration.parent",
                "integration manifest는 path/Line identity와 candidate parent를 exact하게 bind해야 합니다.",
            )
        ]

    if not _is_line_quality_head(session, line_path, line_head):
        return main_commits, main_trees, [
            _line_error(
                manifest_path,
                "history.integration.parent",
                "designated second parent는 Line verifying transition과 IQC를 포함한 exact Q여야 합니다.",
            )
        ]

    try:
        merge_tree_output = _git(session, "merge-tree", "--write-tree", main_parent, line_head)
    except HistoryUnavailable:
        return main_commits, main_trees, [
            _line_error(
                manifest_path,
                "history.integration.tree",
                "conflict 없는 deterministic merge result를 입증할 수 없습니다.",
            )
        ]
    try:
        expected_tree = merge_tree_output.decode("ascii").splitlines()[0]
        changed = _git(
            session,
            "diff",
            "--name-only",
            "--no-renames",
            expected_tree,
            candidate,
        ).decode("utf-8").splitlines()
    except (UnicodeError, IndexError) as error:
        raise HistoryUnavailable from error
    if changed != [manifest_path]:
        return main_commits, main_trees, [
            _line_error(
                manifest_path,
                "history.integration.tree",
                "integration candidate tree에는 merge result와 manifest 외 변경이 없어야 합니다.",
            )
        ]

    line_commits, line_trees = _history(session, line_head)
    later_commits = main_commits[candidate_index:]
    later_trees = main_trees[candidate_index:]
    dqc_errors = _validate_integration_dqc(
        session,
        line_number,
        candidate,
        later_commits,
        later_trees,
    )
    return line_commits + later_commits, line_trees + later_trees, dqc_errors


def _validate_integration_dqc(
    session: _GitSession,
    line_number: str,
    candidate: str,
    commits: list[str],
    trees: list[set[str]],
) -> list[HistoryError]:
    """Bind post-integration DQC PASS and delivery on the main first parent."""
    line_path = f".proofline/lines/line-{line_number}/line-{line_number}.md"
    dqc_path = f".proofline/lines/line-{line_number}/dqc-{line_number}.md"
    dqc_states: list[dict[str, object] | None] = []
    line_states: list[dict[str, object] | None] = []
    for commit, paths in zip(commits, trees, strict=True):
        dqc_content = _file(session, commit, dqc_path, paths)
        line_content = _file(session, commit, line_path, paths)
        try:
            dqc_states.append(_frontmatter(dqc_content) if dqc_content is not None else None)
            line_states.append(_frontmatter(line_content) if line_content is not None else None)
        except HistoryUnavailable:
            return [_unavailable(dqc_path)]

    delivery = next(
        (
            index
            for index, state in enumerate(line_states[1:], start=1)
            if state is not None and state.get("execution_status") == "delivered"
        ),
        None,
    )
    present = [index for index, state in enumerate(dqc_states) if state is not None]
    if not present:
        if delivery is None:
            return []
        return [
            _line_error(
                dqc_path,
                "history.integration.dqc",
                "delivery 전 exact integration candidate를 bind한 DQC PASS가 필요합니다.",
            )
        ]

    for state in (dqc_states[index] for index in present):
        assert state is not None
        if (
            state.get("id") != f"dqc-{line_number}"
            or state.get("line") != f"line-{line_number}"
            or state.get("candidate_commit") != candidate
        ):
            return [
                _line_error(
                    dqc_path,
                    "history.integration.dqc",
                    "DQC는 containing integration candidate와 Line identity를 exact하게 bind해야 합니다.",
                )
            ]
    passed: list[int] = []
    previous_result: object = None
    for index, state in enumerate(dqc_states):
        result = state.get("result") if state is not None else None
        if result == "passed" and previous_result != "passed":
            passed.append(index)
        previous_result = result
    if delivery is not None and (len(passed) != 1 or not (0 < passed[0] < delivery)):
        return [
            _line_error(
                dqc_path,
                "history.integration.dqc",
                "main first-parent에는 V → DQC PASS → delivery 순서가 필요합니다.",
            )
        ]
    return []


def _repository_activation(
    session: _GitSession, commits: list[str], trees: list[set[str]]
) -> int | None:
    for index, (commit, paths) in enumerate(zip(commits, trees, strict=True)):
        for path in sorted(candidate for candidate in paths if LINE_PATH.fullmatch(candidate)):
            try:
                line = _frontmatter(_file(session, commit, path, paths) or b"")
            except HistoryUnavailable:
                continue
            if line.get("implementation_history") == "first_parent":
                return index
    return None


def _line_states(
    session: _GitSession,
    path: str,
    commits: list[str],
    trees: list[set[str]],
) -> list[dict[str, object] | None]:
    states: list[dict[str, object] | None] = []
    for commit, paths in zip(commits, trees, strict=True):
        content = _file(session, commit, path, paths)
        if content is None:
            states.append(None)
            continue
        try:
            states.append(_frontmatter(content))
        except HistoryUnavailable:
            # Non-canonical historical garbage predates the policy. Duplicate
            # mappings are classified separately before this state walk.
            states.append(None)
    return states


def _first_policy(states: list[dict[str, object] | None]) -> int | None:
    return next(
        (
            index
            for index, state in enumerate(states)
            if state is not None and state.get("implementation_history") == "first_parent"
        ),
        None,
    )


def _validate_line_policy(
    path: str,
    current: dict[str, object],
    states: list[dict[str, object] | None],
    activation: int | None,
    *,
    current_bytes: bytes | None = None,
    head_bytes: bytes | None = None,
) -> tuple[list[HistoryError], int | None]:
    baseline = _first_policy(states)
    policy = current.get("implementation_history")
    if baseline is not None:
        changed = bool(current) and policy != "first_parent"
        changed = changed or any(
            state is None or state.get("implementation_history") != "first_parent"
            for state in states[baseline:]
        )
        if changed:
            return [
                _line_error(
                    path,
                    "history.line.policy.changed",
                    "도입된 implementation history policy를 제거하거나 변경할 수 없습니다.",
                )
            ], baseline
        if current_bytes is None or head_bytes is None or current_bytes != head_bytes:
            return [
                _line_error(
                    path,
                    "history.line.current.unpersisted",
                    "현재 policy-bearing Line bytes가 exact candidate HEAD에 persisted되어 있지 않습니다.",
                )
            ], baseline
        return [], baseline

    if policy == "first_parent":
        return [_unavailable(path)], None
    terminal_statuses = {"delivered", "cancelled"}
    if current.get("execution_status") not in terminal_statuses:
        return [
            _line_error(
                path,
                "history.line.policy.missing",
                "non-terminal Line에는 implementation_history: first_parent가 필요합니다.",
            )
        ], None
    if current_bytes is None or head_bytes is None or current_bytes != head_bytes:
        code = (
            "history.line.legacy.invalid"
            if current.get("implementation_history") is None
            else "history.line.terminal.unpersisted"
        )
        return [
            _line_error(
                path,
                code,
                "현재 terminal Line bytes가 HEAD에 persisted되어 있지 않습니다.",
            )
        ], None
    terminal = next(
        (
            index
            for index, state in enumerate(states)
            if state is not None
            and state.get("execution_status") == current.get("execution_status")
            and (
                index == 0
                or states[index - 1] is None
                or states[index - 1].get("execution_status")
                != current.get("execution_status")
            )
        ),
        None,
    )
    if terminal is not None:
        later = [
            index
            for index, state in enumerate(states)
            if index > terminal
            and state is not None
            and state.get("execution_status") == current.get("execution_status")
            and (
                states[index - 1] is None
                or states[index - 1].get("execution_status")
                != current.get("execution_status")
            )
        ]
        if later:
            terminal = later[-1]
    if terminal is None or (activation is not None and terminal >= activation):
        return [
            _line_error(
                path,
                "history.line.legacy.invalid",
                "fieldless terminal Line의 legacy terminal ordering을 exact first-parent history로 입증할 수 없습니다.",
            )
        ], None
    return [], None


def _latest_transition(
    states: list[dict[str, object] | None],
    before: int,
    value: str,
    *,
    field: str = "implementation_status",
) -> int | None:
    result = None
    previous = None
    for index, state in enumerate(states[:before]):
        current = state.get(field) if state is not None else None
        if current == value and previous != value:
            result = index
        previous = current
    return result


def _resolved_commit(value: object, positions: dict[str, int]) -> int | None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        return None
    return positions.get(value)


def _quality_boundary(
    session: _GitSession,
    iqc_path: str,
    iqc_bytes: bytes,
    commits: list[str],
    trees: list[set[str]],
) -> int | None:
    boundaries: list[int] = []
    previous: bytes | None = None
    for index, (commit, paths) in enumerate(zip(commits, trees, strict=True)):
        content = _file(session, commit, iqc_path, paths)
        if content == iqc_bytes and previous != iqc_bytes:
            boundaries.append(index)
        previous = content
    return boundaries[0] if len(boundaries) == 1 else None


def _governance_only_commit(session: _GitSession, commit: str) -> bool:
    # Always compare against the first parent.  This makes merge candidates
    # deterministic and rejects root/empty commits closedly.
    output = _git(session, "diff", "--name-only", "--no-renames", f"{commit}^1", commit)
    try:
        paths = [path for path in output.decode("utf-8").splitlines() if path]
    except UnicodeError as error:
        raise HistoryUnavailable from error
    # Empty commits are not implementation either; keep them fail-closed under
    # the same binding diagnostic as lifecycle-only commits.
    return not paths or all(path.startswith(".proofline/") for path in paths)


def _iqc_path_for(path: str) -> str:
    filename = path.rsplit("/", 1)[-1]
    return path.rsplit("/", 1)[0] + "/" + filename.replace("ms-", "iqc-", 1)


def _historical_approved_revision(
    session: _GitSession,
    path: str,
    states: list[dict[str, object] | None],
    commits: list[str],
    trees: list[set[str]],
    before: int,
) -> int | None:
    approved: list[int] = []
    previous_revision: bytes | None = None
    previous_spec_status: object = None
    for index, (state, commit, paths) in enumerate(
        zip(states, commits, trees, strict=True)
    ):
        if index > before:
            break
        content = _file(session, commit, path, paths)
        revision = _spec_revision_bytes(content)
        if (
            state is not None
            and state.get("spec_status") == "approved"
            and (revision != previous_revision or previous_spec_status != "approved")
        ):
            approved.append(index)
        previous_revision = revision
        previous_spec_status = state.get("spec_status") if state is not None else None
    return approved[-1] if approved else None


def _validate_historical_cycles(
    session: _GitSession,
    path: str,
    baseline: int,
    states: list[dict[str, object] | None],
    commits: list[str],
    trees: list[set[str]],
    positions: dict[str, int],
) -> list[HistoryError]:
    """Audit every persisted implementation cycle, including superseded ones."""
    effective: list[tuple[int, str]] = []
    for index in range(len(states)):
        state = states[index]
        if state is not None:
            status = state.get("implementation_status")
            if status not in {"not_started", "in_progress", "implemented"}:
                return [_line_error(path, "history.ms.transition", "implementation lifecycle status가 유효하지 않습니다.")]
            effective.append((index, status))  # type: ignore[arg-type]
    if not effective or effective[0][1] != "not_started":
        return [_line_error(path, "history.ms.transition", "구현 lifecycle은 persisted not_started에서 시작해야 합니다.")]

    starts: list[int] = []
    finishes: list[tuple[int, int]] = []
    previous = effective[0][1]
    for index, status in effective[1:]:
        if status == previous:
            continue
        allowed = {
            "not_started": {"in_progress"},
            "in_progress": {"implemented"},
            "implemented": {"in_progress"},
        }
        if status not in allowed[previous]:
            return [_line_error(path, "history.ms.transition", "허용되지 않은 implementation lifecycle transition입니다.")]
        if status == "in_progress":
            starts.append(index)
        else:
            if previous != "in_progress":
                return [_line_error(path, "history.ms.transition", "implemented cycle에는 persisted in_progress가 필요합니다.")]
            finishes.append((starts[-1], index))
        previous = status

    iqc_path = _iqc_path_for(path)
    boundaries: list[tuple[int, bytes]] = []
    seen_iqc_bytes: set[bytes] = set()
    previous_iqc: bytes | None = None
    for index, (commit, paths) in enumerate(zip(commits, trees, strict=True)):
        content = _file(session, commit, iqc_path, paths)
        if content is not None and content != previous_iqc:
            if content in seen_iqc_bytes:
                return [_line_error(path, "history.ms.order", "IQC evidence bytes를 이전 cycle에서 재사용할 수 없습니다.")]
            boundaries.append((index, content))
            seen_iqc_bytes.add(content)
        previous_iqc = content

    for cycle_number, (start, finish) in enumerate(finishes):
        next_start = next((candidate for candidate in starts if candidate > start), len(commits))
        if not _governance_only_commit(session, commits[start]):
            return [_line_error(path, "history.ms.order", "persisted in_progress transition과 implementation은 같은 commit일 수 없습니다.")]
        implementation_candidates = [
            index
            for index in range(start + 1, finish)
            if not _governance_only_commit(session, commits[index])
        ]
        if not implementation_candidates:
            code = (
                "history.ms.binding"
                if _governance_only_commit(session, commits[finish])
                else "history.ms.order"
            )
            return [_line_error(path, code, "cycle에는 실제 non-governance implementation commit이 필요합니다.")]
        quality = [(index, content) for index, content in boundaries if finish <= index < next_start]
        if len(quality) != 1:
            code = "history.ms.transition" if len(quality) > 1 else "history.ms.order"
            return [_line_error(path, code, "각 implementation cycle에는 fresh IQC Q commit이 정확히 하나 필요합니다.")]
        quality_index, iqc_bytes = quality[0]
        try:
            iqc = _frontmatter(iqc_bytes)
        except HistoryUnavailable:
            return [_unavailable(iqc_path)]
        expected_iqc_id = Path(iqc_path).stem
        expected_ms_id = Path(path).stem
        if iqc.get("id") != expected_iqc_id or iqc.get("micro_spec") != expected_ms_id:
            return [_line_error(path, "history.ms.binding", "IQC는 해당 Micro-SPEC identity를 exact하게 bind해야 합니다.")]
        implementation_value = iqc.get("implementation_commit")
        specification_value = iqc.get("micro_spec_commit")
        bound_implementation = _resolved_commit(implementation_value, positions)
        specification = _resolved_commit(specification_value, positions)
        if bound_implementation is None or specification is None:
            return [_line_error(path, "history.ms.binding", "IQC는 exact first-parent implementation과 approved Micro-SPEC revision을 bind해야 합니다.")]
        implementation = bound_implementation
        approved_revision = _historical_approved_revision(
            session, path, states, commits, trees, start
        )
        if (
            bound_implementation is None
            or bound_implementation not in implementation_candidates
            or specification != approved_revision
            or quality_index != finish
            or not (specification < start < implementation < finish == quality_index)
            or not (baseline < implementation)
        ):
            return [_line_error(path, "history.ms.order", "P < I < implemented < Q 및 approved Micro-SPEC binding 순서가 필요합니다.")]
    return []


def _validate_micro_spec(
    session: _GitSession,
    path: str,
    current: dict[str, object],
    baseline: int,
    commits: list[str],
    trees: list[set[str]],
    positions: dict[str, int],
    *,
    current_bytes: bytes | None,
    head_bytes: bytes | None,
) -> list[HistoryError]:
    # Historical malformed states are always checked: they can hide a transition,
    # including when the path was subsequently deleted from the candidate tree.
    for commit, paths in zip(commits, trees, strict=True):
        content = _file(session, commit, path, paths)
        if content is not None:
            _frontmatter(content)

    if current_bytes is None or head_bytes is None or current_bytes != head_bytes:
        return [
            _line_error(
                path,
                "history.ms.current.unpersisted",
                "현재 Micro-SPEC bytes가 exact candidate HEAD에 persisted되어 있지 않습니다.",
            )
        ]
    try:
        _frontmatter(current_bytes)
        _frontmatter(head_bytes)
    except HistoryUnavailable:
        return [
            _line_error(
                path,
                "history.ms.current.unpersisted",
                "현재 Micro-SPEC 또는 candidate HEAD의 bytes가 유효한 canonical artifact가 아닙니다.",
            )
        ]
    states = _line_states(session, path, commits, trees)
    historical_errors = _validate_historical_cycles(
        session, path, baseline, states, commits, trees, positions
    )
    if historical_errors:
        return historical_errors
    status = current.get("implementation_status")

    if status in {"in_progress", "implemented"} and current.get("spec_status") != "approved":
        return [_line_error(path, "history.ms.order", "현재 implementation 상태의 Micro-SPEC은 approved여야 합니다.")]

    if status not in {"in_progress", "implemented"}:
        return []

    line_number = MS_PATH.fullmatch(path)
    assert line_number is not None
    iqc_path = _iqc_path_for(path)

    if status == "in_progress":
        start = _latest_transition(states, len(states), "in_progress")
        if start is None:
            return [_line_error(path, "history.ms.transition", "persisted in_progress transition이 없습니다.")]
        prior_implemented = _latest_transition(states, len(states), "implemented")
        if prior_implemented is not None and start <= prior_implemented:
            return [_line_error(path, "history.ms.transition", "현재 HEAD의 in_progress transition은 latest prior implemented transition보다 뒤여야 합니다.")]
        approved_before = any(
            state is not None and state.get("spec_status") == "approved"
            for state in states[:start]
        )
        if not approved_before:
            return [_line_error(path, "history.ms.order", "in_progress transition은 approved Micro-SPEC commit보다 뒤여야 합니다.")]
        return []

    try:
        iqc_bytes = (session.root / iqc_path).read_bytes()
    except OSError:
        iqc_bytes = None
    head_iqc_bytes = _file(session, commits[-1], iqc_path, trees[-1])
    if iqc_bytes is None or head_iqc_bytes is None or iqc_bytes != head_iqc_bytes:
        return [
            _line_error(
                iqc_path,
                "history.iqc.current.unpersisted",
                "현재 IQC bytes가 exact candidate HEAD의 IQC bytes와 일치하지 않습니다.",
            )
        ]
    try:
        iqc = _frontmatter(iqc_bytes)
        _frontmatter(head_iqc_bytes)
    except HistoryUnavailable:
        return [
            _line_error(
                iqc_path,
                "history.iqc.current.unpersisted",
                "현재 IQC 또는 candidate HEAD의 bytes가 유효한 canonical artifact가 아닙니다.",
            )
        ]
    latest_start = _latest_transition(states, len(states), "in_progress")
    quality = _quality_boundary(session, iqc_path, iqc_bytes, commits, trees)
    if quality is not None and latest_start is not None and quality <= latest_start:
        return [_line_error(path, "history.ms.order", "현재 IQC가 fresh implementation cycle을 덮지 않습니다.")]
    if quality is None:
        return [_unavailable(path)]

    implementation_value = iqc.get("implementation_commit")
    specification_value = iqc.get("micro_spec_commit")
    implementation = _resolved_commit(implementation_value, positions)
    specification = _resolved_commit(specification_value, positions)
    if (
        isinstance(implementation_value, str)
        and re.fullmatch(r"[0-9a-f]{40}", implementation_value) is not None
        and implementation is None
    ):
        return [
            _line_error(
                path,
                "history.ms.binding",
                "implementation_commit은 exact candidate first-parent chain에 있어야 합니다.",
            )
        ]
    if implementation is None or specification is None:
        return [_unavailable(path)]

    transition = _latest_transition(states, quality + 1, "implemented")
    start = _latest_transition(states, quality + 1, "in_progress")
    if start is None:
        return [
            _line_error(
                path,
                "history.ms.transition",
                "implemented cycle 앞에 별도 persisted in_progress transition이 없습니다.",
            )
        ]
    if transition is None or transition <= start or implementation > transition:
        return [
            _line_error(
                path,
                "history.ms.transition",
                "현재 IQC cycle에 대응하는 fresh in_progress → implemented transition이 없습니다.",
            )
        ]
    prior_status = None
    for state in reversed(states[:start]):
        if state is not None:
            prior_status = state.get("implementation_status")
            break
    had_prior_implemented = any(
        state is not None
        and state.get("implementation_status") == "implemented"
        for state in states[:start]
    )
    expected_predecessor = "implemented" if had_prior_implemented else "not_started"
    if prior_status != expected_predecessor:
        return [_line_error(path, "history.ms.transition", "허용되지 않은 implementation transition입니다.")]

    approved_revisions: list[int] = []
    previous_bytes: bytes | None = None
    previous_status: object = None
    for index, (state, commit, paths) in enumerate(zip(states, commits, trees, strict=True)):
        content = _file(session, commit, path, paths)
        revision_bytes = _spec_revision_bytes(content)
        changed = revision_bytes != previous_bytes
        approved_transition = (
            state is not None
            and state.get("spec_status") == "approved"
            and previous_status != "approved"
        )
        if index <= start and (changed or approved_transition) and state is not None and state.get("spec_status") == "approved":
            approved_revisions.append(index)
        previous_bytes = revision_bytes
        previous_status = state.get("spec_status") if state is not None else None
    approved_revision = approved_revisions[-1] if approved_revisions else None
    approved_at_start = approved_revision
    specification_state = states[specification]
    try:
        current_spec_bytes = _spec_revision_bytes((session.root / path).read_bytes())
    except OSError:
        return [_unavailable(path)]
    if (
        specification_state is None
        or specification_state.get("spec_status") != "approved"
        or approved_revision != specification
        or approved_at_start != specification
        or current_spec_bytes
        != _spec_revision_bytes(_file(session, commits[specification], path, trees[specification]))
    ):
        return [_line_error(path, "history.ms.order", "micro_spec_commit은 approved Micro-SPEC revision이어야 합니다.")]

    if _governance_only_commit(session, commits[implementation]):
        return [
            _line_error(
                path,
                "history.ms.binding",
                "lifecycle-only commit은 implementation_commit으로 사용할 수 없습니다.",
            )
        ]
    implementation_candidates = [
        index
        for index in range(start + 1, transition)
        if not _governance_only_commit(session, commits[index])
    ]
    if not (
        specification < start < implementation < transition <= quality
        and baseline < implementation
        and implementation in implementation_candidates
    ):
        return [
            _line_error(
                path,
                "history.ms.order",
                "micro_spec_commit < in_progress < implementation_commit < IQC candidate 및 baseline < implementation_commit 순서가 필요합니다.",
            )
        ]
    return []


def validate_implementation_history(
    root: Path, *, excluded_line_path: str | tuple[str, ...] | None = None
) -> list[HistoryError]:
    current, malformed = _current_artifacts(root)
    excluded = (
        set(excluded_line_path)
        if isinstance(excluded_line_path, tuple)
        else {excluded_line_path}
        if excluded_line_path is not None
        else set()
    )
    line_paths = sorted(
        path for path in current
        if LINE_PATH.fullmatch(path) and path not in excluded
    )
    malformed_line_paths = {
        path for path in malformed if LINE_PATH.fullmatch(path)
    }
    line_paths = sorted(set(line_paths) | malformed_line_paths)
    session = _GitSession(root)
    try:
        top_level = _git(session, "rev-parse", "--show-toplevel").decode("utf-8").strip()
        if Path(top_level).resolve() != root.resolve():
            raise HistoryUnavailable
        commits, trees = _history(session)
        line_paths = sorted(
            set(line_paths)
            | {
                path
                for tree in trees
                for path in tree
                if LINE_PATH.fullmatch(path)
            }
        )
        if not line_paths:
            return []
        activation = _repository_activation(session, commits, trees)
    except (HistoryUnavailable, UnicodeError, OSError, RuntimeError, ValueError):
        return [_unavailable(path) for path in line_paths]

    positions = {commit: index for index, commit in enumerate(commits)}
    main_commits, main_trees = commits, trees
    malformed_history_paths: set[str] = set()
    historical_artifact_paths = sorted(
        {
            path
            for tree in trees
            for path in tree
            if LINE_PATH.fullmatch(path)
            or MS_PATH.fullmatch(path)
            or IQC_PATH.fullmatch(path)
        }
    )
    try:
        for artifact_path in historical_artifact_paths:
            for commit, paths in zip(commits, trees, strict=True):
                content = _file(session, commit, artifact_path, paths)
                if content is None:
                    continue
                try:
                    _frontmatter(content)
                except HistoryUnavailable:
                    # Presence is independent from the parsed state: a
                    # malformed artifact must remain visible even after it is
                    # normalized or deleted in a later first-parent commit.
                    malformed_history_paths.add(artifact_path)
                    break
    except HistoryUnavailable:
        return [_unavailable(path) for path in line_paths]
    errors: list[HistoryError] = [
        _unavailable(path) for path in sorted(malformed_history_paths)
    ]
    for line_path in line_paths:
        try:
            commits, trees, integration_errors = _integration_spine(
                session, line_path, main_commits, main_trees
            )
            errors.extend(integration_errors)
            if integration_errors:
                continue
            positions = {commit: index for index, commit in enumerate(commits)}
            activation = _repository_activation(session, commits, trees)
            if line_path in malformed_history_paths:
                errors.append(_unavailable(line_path))
                continue
            states = _line_states(session, line_path, commits, trees)
            if (
                not any(state is not None for state in states)
                and line_path not in current
                and line_path not in malformed
            ):
                continue
            head_paths = trees[-1]
            head_bytes = _file(session, commits[-1], line_path, head_paths)
            current_path = root / line_path
            current_bytes = current_path.read_bytes() if current_path.is_file() else None
            policy_errors, baseline = _validate_line_policy(
                line_path, current.get(line_path, {}), states, activation,
                current_bytes=current_bytes, head_bytes=head_bytes,
            )
            errors.extend(policy_errors)
            if baseline is None or policy_errors:
                continue
            line_directory = line_path.rsplit("/", 1)[0]
            head_ms_paths = {
                path
                for path in trees[-1]
                if MS_PATH.fullmatch(path)
                and path.startswith(f"{line_directory}/micro-specs/")
            }
            malformed_ms_paths = {
                path
                for path in malformed
                if MS_PATH.fullmatch(path)
                and path.startswith(f"{line_directory}/micro-specs/")
            }
            historical_ms_paths = {
                path
                for tree in trees
                for path in tree
                if MS_PATH.fullmatch(path)
                and path.startswith(f"{line_directory}/micro-specs/")
            }
            for ms_path in sorted(
                head_ms_paths
                | malformed_ms_paths
                | historical_ms_paths
                | {
                    path
                    for path in current
                    if MS_PATH.fullmatch(path)
                    and path.startswith(f"{line_directory}/micro-specs/")
                }
            ):
                try:
                    if ms_path in malformed_history_paths:
                        errors.append(_unavailable(ms_path))
                        continue
                    iqc_path = _iqc_path_for(ms_path)
                    if iqc_path in malformed_history_paths:
                        errors.append(_unavailable(iqc_path))
                        continue
                    current_path = root / ms_path
                    current_ms_bytes = (
                        current_path.read_bytes() if current_path.is_file() else None
                    )
                    head_ms_bytes = _file(session, commits[-1], ms_path, trees[-1])
                    errors.extend(
                        _validate_micro_spec(
                            session,
                            ms_path,
                            current.get(ms_path, {}),
                            baseline,
                            commits,
                            trees,
                            positions,
                            current_bytes=current_ms_bytes,
                            head_bytes=head_ms_bytes,
                        )
                    )
                except HistoryUnavailable:
                    errors.append(_unavailable(ms_path))
        except HistoryUnavailable:
            errors.append(_unavailable(line_path))
    return sorted(set(errors))
