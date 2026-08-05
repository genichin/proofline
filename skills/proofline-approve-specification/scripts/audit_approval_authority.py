#!/usr/bin/env python3
"""Read-only exact-evidence authority audit for specification approval."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Any, NoReturn

import yaml


COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
OID_RE = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
GIT_READ_TIMEOUT_SECONDS = 5
GIT_READ_OUTPUT_LIMIT = 8 * 1024 * 1024
LINE_RE = re.compile(r"line-[0-9]{4}\Z")
REVIEW_SCHEMA = "proofline.independent-review/v1"
USER_SCHEMA = "proofline.user-approval/v1"
REVIEW_KEYS = {
    "schema",
    "target_commit",
    "target_tree",
    "result",
    "reviewer_actor_id",
    "mutation_performed",
}
USER_KEYS = {
    "schema",
    "target_commit",
    "target_tree",
    "decision",
    "user_actor_id",
    "actor_role",
    "review_evidence_sha256",
}


class GateError(RuntimeError):
    def __init__(self, scenario: str, detail: str) -> None:
        super().__init__(detail)
        self.scenario = scenario


def fail(scenario: str, detail: str) -> NoReturn:
    raise GateError(scenario, detail)


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    command = ("git", "-C", str(repo), *args)
    environment = os.environ.copy()
    environment.update({
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    })
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
    except OSError as exc:
        raise RuntimeError("git command failed to start") from exc

    output = [bytearray(), bytearray()]
    total = 0
    lock = threading.Lock()
    overflow = threading.Event()

    def drain(pipe: Any, destination: bytearray) -> None:
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
                    else:
                        destination.extend(chunk)
                        total += len(chunk)
        finally:
            pipe.close()

    threads = [
        threading.Thread(target=drain, args=(process.stdout, output[0]), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, output[1]), daemon=True),
    ]
    for thread in threads:
        thread.start()

    deadline = time.monotonic() + GIT_READ_TIMEOUT_SECONDS
    reason: str | None = None
    while process.poll() is None:
        if overflow.wait(0.01):
            reason = "git command output exceeds limit"
            break
        if time.monotonic() >= deadline:
            reason = "git command timed out"
            break
    if reason is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            process.kill()
    process.wait()
    for thread in threads:
        thread.join()
    if reason is not None or overflow.is_set():
        raise RuntimeError(reason or "git command output exceeds limit")
    return subprocess.CompletedProcess(command, process.returncode, bytes(output[0]), bytes(output[1]))


def decode_git(raw: bytes) -> str:
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise RuntimeError("git command output is not UTF-8") from None


def git(repo: Path, *args: str, scenario: str = "GIT_OBJECT") -> str:
    try:
        result = run_git(repo, *args)
        stdout = decode_git(result.stdout)
        stderr = decode_git(result.stderr)
    except RuntimeError as exc:
        fail(scenario, str(exc))
    if result.returncode != 0:
        detail = stderr.strip() or stdout.strip() or "git command failed"
        fail(scenario, detail)
    return stdout


def exact_actor(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        fail("ACTOR_ID", f"{label} must be a nonempty trimmed operational actor ID")
    return value


def exact_sha(value: str, label: str) -> str:
    if COMMIT_RE.fullmatch(value) is None:
        fail("GIT_OBJECT", f"{label} must be a full lowercase SHA-1")
    return value


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            fail("EVIDENCE_FORMAT", f"duplicate JSON key: {key}")
        value[key] = item
    return value


def load_envelope(path: Path, expected_keys: set[str], label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        fail("EVIDENCE_MISSING", f"{label} evidence file is missing")
    except OSError as exc:
        fail("EVIDENCE_FORMAT", f"cannot read {label} evidence: {exc}")
    try:
        value = json.loads(raw, object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail("EVIDENCE_FORMAT", f"{label} evidence is not strict JSON: {exc}")
    if not isinstance(value, dict) or set(value) != expected_keys:
        fail("EVIDENCE_FORMAT", f"{label} evidence keys must exactly match the v1 envelope")
    return value, raw


def require_string_fields(envelope: dict[str, Any], fields: set[str], label: str) -> None:
    for field in fields:
        if not isinstance(envelope[field], str):
            fail("EVIDENCE_FORMAT", f"{label}.{field} must be a JSON string")


def ensure_external_evidence(repo: Path, path: Path, label: str) -> Path:
    resolved = path.resolve()
    canonical = repo / ".proofline"
    if resolved == canonical or canonical in resolved.parents:
        fail("EVIDENCE_LOCATION", f"{label} evidence must be outside canonical .proofline")
    return resolved


def verify_commit_and_tree(repo: Path, commit: str, tree: str, label: str) -> None:
    exact_sha(commit, f"{label} commit")
    exact_sha(tree, f"{label} tree")
    resolved = git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}").strip()
    if resolved != commit:
        fail("GIT_OBJECT", f"{label} commit does not resolve exactly")
    actual_tree = git(repo, "rev-parse", f"{commit}^{{tree}}").strip()
    if actual_tree != tree:
        fail("TARGET_BINDING", f"{label} tree does not match commit")


def artifact_at(repo: Path, commit: str, path: str) -> str:
    try:
        entry = run_git(repo, "ls-tree", "-z", "--full-tree", commit, "--", path)
    except RuntimeError:
        fail("TRANSITION_PATH", f"required artifact read failed: {path}")
    if entry.returncode != 0:
        fail("TRANSITION_PATH", f"required artifact read failed: {path}")
    records = entry.stdout.split(b"\0")
    if len(records) != 2 or records[1] or not records[0]:
        fail("TRANSITION_PATH", f"required artifact is missing or malformed: {path}")
    fields = records[0].split(b"\t")
    metadata = fields[0].split(b" ") if len(fields) == 2 else []
    try:
        actual_path = fields[1].decode("utf-8")
    except (IndexError, UnicodeDecodeError):
        fail("TRANSITION_PATH", f"required artifact is missing or malformed: {path}")
    if (
        len(metadata) != 3 or metadata[0] not in {b"100644", b"100755"}
        or metadata[1] != b"blob" or OID_RE.fullmatch(metadata[2]) is None
        or actual_path != path
    ):
        if actual_path == path and len(metadata) == 3:
            fail("TRANSITION_PATH", f"canonical artifact must be a regular blob: {path}")
        fail("TRANSITION_PATH", f"required artifact is missing or malformed: {path}")
    try:
        oid = metadata[2].decode("ascii", errors="strict")
        blob = run_git(repo, "cat-file", "blob", oid)
    except (UnicodeDecodeError, RuntimeError):
        fail("TRANSITION_PATH", f"required artifact read failed: {path}")
    if blob.returncode != 0:
        fail("TRANSITION_PATH", f"required artifact read failed: {path}")
    try:
        return blob.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        fail("TRANSITION_PATH", f"required artifact is not UTF-8: {path}")


def status_only(before: str, after: str, field: str, old: str, new: str) -> bool:
    pattern = re.compile(
        rf"(?m)^(?P<prefix>{re.escape(field)}:\s*)[\"']?{re.escape(old)}[\"']?\s*$"
    )
    changed, count = pattern.subn(rf"\g<prefix>{new}", before, count=1)
    return count == 1 and changed == after


class UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys at every depth."""


def construct_unique_mapping(
    loader: UniqueKeySafeLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                "found an unhashable mapping key", key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                f"found duplicate key {key!r}", key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_unique_mapping
)


def criteria_sets(req: str) -> dict[str, list[str]]:
    lines = req.splitlines()
    if not lines or lines[0] != "---":
        fail("TRANSITION_CONTENT", "REQ must start with an exact YAML frontmatter boundary")
    try:
        boundary = lines.index("---", 1)
    except ValueError:
        fail("TRANSITION_CONTENT", "REQ YAML frontmatter closing boundary is missing")
    frontmatter_text = "\n".join(lines[1:boundary]) + "\n"
    try:
        frontmatter = yaml.load(frontmatter_text, Loader=UniqueKeySafeLoader)
    except yaml.YAMLError:
        fail("TRANSITION_CONTENT", "REQ frontmatter must be strict duplicate-key-free YAML")
    if not isinstance(frontmatter, dict):
        fail("TRANSITION_CONTENT", "REQ frontmatter must be a YAML mapping")
    criteria = frontmatter.get("criteria")
    keys = {"create", "update", "retire", "satisfy"}
    if not isinstance(criteria, dict) or set(criteria) != keys:
        fail(
            "TRANSITION_CONTENT",
            "REQ.criteria must contain exactly create, update, retire, and satisfy",
        )
    result: dict[str, list[str]] = {}
    seen: set[str] = set()
    for key in ("create", "update", "retire", "satisfy"):
        values = criteria[key]
        if not isinstance(values, list):
            fail("TRANSITION_CONTENT", f"REQ.criteria.{key} must be a YAML list")
        if any(not isinstance(value, str) or re.fullmatch(r"ac-[0-9]{4}", value) is None
               for value in values):
            fail(
                "TRANSITION_CONTENT",
                f"REQ.criteria.{key} entries must be canonical ac-NNNN IDs",
            )
        for value in values:
            if value in seen:
                fail("TRANSITION_CONTENT", "REQ.criteria admission lists must not repeat IDs")
            seen.add(value)
        result[key] = values
    if not seen:
        fail("TRANSITION_CONTENT", "REQ.criteria must admit at least one ID")
    return result


def changed_paths(repo: Path, target: str, approval: str) -> dict[str, str]:
    output = git(
        repo,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "-r",
        target,
        approval,
    )
    changes: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            fail("TRANSITION_PATH", "rename/copy changes are not approval transitions")
        status, path = parts
        changes[path] = status
    return changes


def verify_direct_child(repo: Path, target: str, approval: str) -> None:
    lineage = git(repo, "rev-list", "--parents", "-n", "1", approval).split()
    if lineage != [approval, target]:
        fail("TRANSITION_PARENT", "approval commit must be the direct non-merge child of target")


def verify_normal(repo: Path, line_id: str, target: str, approval: str) -> None:
    prefix = f".proofline/lines/{line_id}/micro-specs/"
    pattern = re.compile(rf"{re.escape(prefix)}ms-{line_id[5:]}-[0-9]{{3}}\.md\Z")
    changes = changed_paths(repo, target, approval)
    if not changes:
        fail("TRANSITION_PATH", "normal approval has no Micro-SPEC transition")
    for path, kind in changes.items():
        if kind != "M" or pattern.fullmatch(path) is None:
            fail("TRANSITION_PATH", f"normal approval changed unrelated path: {path}")
        before = artifact_at(repo, target, path)
        after = artifact_at(repo, approval, path)
        if not status_only(before, after, "spec_status", "draft", "approved"):
            fail("TRANSITION_CONTENT", f"normal approval is not spec_status-only: {path}")


def tree_micro_specs(repo: Path, target: str, line_id: str) -> list[str]:
    prefix = f".proofline/lines/{line_id}/micro-specs/"
    pattern = re.compile(rf"{re.escape(prefix)}ms-{line_id[5:]}-[0-9]{{3}}\.md\Z")
    paths = git(repo, "ls-tree", "-r", "--name-only", target, "--", prefix).splitlines()
    return sorted(path for path in paths if pattern.fullmatch(path))


def verify_bootstrap(repo: Path, line_id: str, target: str, approval: str) -> None:
    number = line_id[5:]
    req_path = f".proofline/lines/{line_id}/req-{number}.md"
    req_before = artifact_at(repo, target, req_path)
    groups = criteria_sets(req_before)
    expected: dict[str, tuple[str, str, str]] = {
        req_path: ("status", "draft", "approved")
    }
    for key in ("create", "update"):
        for ac_id in groups[key]:
            expected[f".proofline/criteria/{ac_id}.md"] = ("status", "draft", "active")
    for ac_id in groups["retire"]:
        expected[f".proofline/criteria/{ac_id}.md"] = ("status", "active", "retired")
    for path in tree_micro_specs(repo, target, line_id):
        expected[path] = ("spec_status", "draft", "approved")
    if len(expected) == 1:
        fail("TRANSITION_PATH", "bootstrap approval has no Micro-SPEC")

    changes = changed_paths(repo, target, approval)
    if set(changes) != set(expected):
        unexpected = sorted(set(changes) - set(expected))
        missing = sorted(set(expected) - set(changes))
        fail("TRANSITION_PATH", f"bootstrap paths differ; unexpected={unexpected} missing={missing}")
    for path, (field, old, new) in expected.items():
        if changes[path] != "M":
            fail("TRANSITION_PATH", f"bootstrap approval did not modify existing artifact: {path}")
        if not status_only(
            artifact_at(repo, target, path), artifact_at(repo, approval, path), field, old, new
        ):
            fail("TRANSITION_CONTENT", f"bootstrap approval is not lifecycle-status-only: {path}")


def verify_evidence(
    review: dict[str, Any],
    review_raw: bytes,
    user: dict[str, Any],
    target: str,
    tree: str,
    author: str,
    recorder: str,
) -> None:
    require_string_fields(review, REVIEW_KEYS - {"mutation_performed"}, "review")
    require_string_fields(user, USER_KEYS, "user approval")
    if type(review["mutation_performed"]) is not bool:
        fail("EVIDENCE_FORMAT", "review.mutation_performed must be a JSON boolean")
    exact_sha(review["target_commit"], "review target commit")
    exact_sha(review["target_tree"], "review target tree")
    exact_sha(user["target_commit"], "user approval target commit")
    exact_sha(user["target_tree"], "user approval target tree")
    if SHA256_RE.fullmatch(user["review_evidence_sha256"]) is None:
        fail("EVIDENCE_FORMAT", "user approval review_evidence_sha256 must be lowercase hex")
    if review["schema"] != REVIEW_SCHEMA or user["schema"] != USER_SCHEMA:
        fail("EVIDENCE_FORMAT", "unsupported evidence schema")
    reviewer = exact_actor(review["reviewer_actor_id"], "reviewer")
    user_actor = exact_actor(user["user_actor_id"], "user")
    if review["target_commit"] != target or review["target_tree"] != tree:
        fail("TARGET_BINDING", "review evidence does not bind exact target commit and tree")
    if user["target_commit"] != target or user["target_tree"] != tree:
        fail("TARGET_BINDING", "user approval does not bind exact target commit and tree")
    if review["result"] != "PASS":
        fail("REVIEW_RESULT", "independent review result must be PASS")
    if review["mutation_performed"] is not False:
        fail("REVIEW_MUTATION", "independent reviewer must report mutation_performed=false")
    if user["actor_role"] != "user":
        fail("USER_ROLE", "approval actor_role must be user")
    if user["decision"] != "approved":
        fail("USER_DECISION", "user approval decision must be approved")
    digest = hashlib.sha256(review_raw).hexdigest()
    if user["review_evidence_sha256"] != digest:
        fail("REVIEW_DIGEST", "user approval review evidence digest is stale or mismatched")
    actors = [author, reviewer, user_actor, recorder]
    if len(set(actors)) != len(actors):
        fail("ACTOR_SEPARATION", "draft author, reviewer, user, and recorder must be distinct")


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--repo", required=True, type=Path)
    value.add_argument("--mode", required=True, choices=("normal", "bootstrap"))
    value.add_argument("--line-id", required=True)
    value.add_argument("--target-commit", required=True)
    value.add_argument("--target-tree", required=True)
    value.add_argument("--approval-commit", required=True)
    value.add_argument("--approval-tree", required=True)
    value.add_argument("--review-evidence", required=True, type=Path)
    value.add_argument("--user-approval-evidence", required=True, type=Path)
    value.add_argument("--draft-author-actor-id", required=True)
    value.add_argument("--governance-recorder-actor-id", required=True)
    return value


def run(args: argparse.Namespace) -> None:
    try:
        repo = args.repo.resolve(strict=True)
    except OSError as exc:
        fail("REPOSITORY", f"repository path is unavailable: {exc}")
    root = Path(git(repo, "rev-parse", "--show-toplevel", scenario="REPOSITORY").strip()).resolve()
    if root != repo:
        fail("REPOSITORY", "--repo must be the repository root")
    if LINE_RE.fullmatch(args.line_id) is None:
        fail("LINE_ID", "line id must match line-NNNN")
    for value, label in (
        (args.target_commit, "target commit"),
        (args.target_tree, "target tree"),
        (args.approval_commit, "approval commit"),
        (args.approval_tree, "approval tree"),
    ):
        exact_sha(value, label)
    author = exact_actor(args.draft_author_actor_id, "draft author")
    recorder = exact_actor(args.governance_recorder_actor_id, "governance recorder")
    if author == recorder:
        fail("ACTOR_SEPARATION", "draft author and governance recorder must be distinct")

    review_path = ensure_external_evidence(repo, args.review_evidence, "review")
    user_path = ensure_external_evidence(repo, args.user_approval_evidence, "user approval")
    review, review_raw = load_envelope(review_path, REVIEW_KEYS, "review")
    user, _ = load_envelope(user_path, USER_KEYS, "user approval")

    status_before = git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    head_before = git(repo, "rev-parse", "HEAD").strip()
    refs_before = git(repo, "show-ref")
    if status_before:
        fail("WORKTREE_STATE", "repository worktree and index must be clean")
    if head_before != args.approval_commit:
        fail("WORKTREE_STATE", "repository HEAD must equal exact approval commit")

    verify_commit_and_tree(repo, args.target_commit, args.target_tree, "target")
    verify_commit_and_tree(repo, args.approval_commit, args.approval_tree, "approval")
    verify_evidence(
        review,
        review_raw,
        user,
        args.target_commit,
        args.target_tree,
        author,
        recorder,
    )
    verify_direct_child(repo, args.target_commit, args.approval_commit)
    if args.mode == "normal":
        verify_normal(repo, args.line_id, args.target_commit, args.approval_commit)
    else:
        verify_bootstrap(repo, args.line_id, args.target_commit, args.approval_commit)

    if (
        git(repo, "rev-parse", "HEAD").strip() != head_before
        or git(repo, "status", "--porcelain=v1", "--untracked-files=all") != status_before
        or git(repo, "show-ref") != refs_before
    ):
        fail("REPOSITORY_MUTATION", "repository HEAD, status, or refs changed during audit")


def main() -> int:
    args = parser().parse_args()
    try:
        run(args)
    except GateError as exc:
        print(f"approval-authority[{exc.scenario}]: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"approval-authority[IO]: {exc}", file=sys.stderr)
        return 2
    print(
        f"approval-authority: passed mode={args.mode} "
        f"target={args.target_commit} approval={args.approval_commit}"
    )
    print(
        "authority-note: validates supplied evidence; does not cryptographically authenticate a human"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
