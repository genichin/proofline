#!/usr/bin/env python3
"""Read-only pre-admission check for a ProofLine main-first candidate."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


SHA = re.compile(r"^[0-9a-f]{40}$")
LINE = re.compile(r"^line-(\d{4})$")
REF = re.compile(r"^refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*$")
TIMEOUT_SECONDS = 5
OUTPUT_LIMIT = 8 * 1024 * 1024


class PreflightError(RuntimeError):
    pass


def git(repo: Path, *args: str) -> str:
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
            ("git", *args),
            cwd=repo,
            env=environment,
            text=False,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreflightError(f"git read failed: {exc}") from exc
    if len(result.stdout) + len(result.stderr) > OUTPUT_LIMIT:
        raise PreflightError("git read produced excessive output")
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

    number = match.group(1)
    manifest_path = f".proofline/lines/{line_id}/integration-{number}.md"
    try:
        manifest = parse_manifest(git(repo, "show", f"{v}:{manifest_path}"))
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
