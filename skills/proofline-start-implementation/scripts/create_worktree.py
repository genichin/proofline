#!/usr/bin/env python3
"""Create a ProofLine implementation linked worktree after fail-closed preflight."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys


LINE_RE = re.compile(r"line-[0-9]{4}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")


class WorkflowError(RuntimeError):
    """Expected fail-closed workflow error."""


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


def frontmatter_value(text: str, key: str) -> str:
    if not text.startswith("---\n"):
        raise WorkflowError("canonical artifact has no YAML frontmatter")
    try:
        frontmatter = text.split("---", 2)[1]
    except IndexError as exc:
        raise WorkflowError("canonical artifact frontmatter is incomplete") from exc
    match = re.search(
        rf"(?m)^{re.escape(key)}:\s*[\"']?([^\"'\n]+?)[\"']?\s*$",
        frontmatter,
    )
    if match is None:
        raise WorkflowError(f"canonical artifact is missing {key}")
    return match.group(1).strip()


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
    actual_id = frontmatter_value(text, "id")
    if actual_id != expected_id:
        raise WorkflowError(f"{relative_path} id must be {expected_id}")
    actual_state = frontmatter_value(text, state_key)
    if actual_state != expected_state:
        label = relative_path.rsplit("/", 1)[-1].split("-", 1)[0].upper()
        raise WorkflowError(f"{label}.{state_key} must be {expected_state}")


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
