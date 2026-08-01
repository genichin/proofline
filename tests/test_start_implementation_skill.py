from pathlib import Path
import subprocess
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/proofline-start-implementation/SKILL.md"
CONTRACT = ROOT / "docs/contracts/line-delivery.md"
GITIGNORE = ROOT / ".gitignore"
SCRIPT = ROOT / "skills/proofline-start-implementation/scripts/create_worktree.py"


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )


def make_approved_repo(tmp_path: Path, *, req_status: str = "approved") -> tuple[Path, str]:
    repo = tmp_path / "project"
    line_dir = repo / ".proofline/lines/line-0007"
    line_dir.mkdir(parents=True)
    (repo / ".gitignore").write_text("/.worktrees/\n")
    (line_dir / "line-0007.md").write_text(
        '---\nid: "line-0007"\nexecution_status: not_started\n---\n'
    )
    (line_dir / "dcy-0007.md").write_text(
        '---\nid: "dcy-0007"\nstatus: confirmed\n---\n\n# Discovery\n'
    )
    (line_dir / "req-0007.md").write_text(
        f'---\nid: "req-0007"\nstatus: {req_status}\n'
        'discovery: "dcy-0007"\ncriteria:\n  create: []\n  update: []\n  retire: []\n'
        '---\n\n# Requirement\n'
    )
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "proofline@example.invalid")
    git(repo, "config", "user.name", "ProofLine Test")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Approve specification")
    approval = git(repo, "rev-parse", "HEAD").stdout.strip()
    return repo, approval


def run_script(repo: Path, approval: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(repo),
            "--line-id",
            "line-0007",
            "--branch",
            "line/line-0007-implementation",
            "--approval-commit",
            approval,
        ),
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )


def test_start_implementation_skill_has_valid_frontmatter() -> None:
    text = SKILL.read_text()
    assert text.startswith("---\n")
    _, frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == "proofline-start-implementation"
    assert metadata["description"].startswith("Use when ")
    assert metadata["version"] == "1.0.0"
    assert "## When to Use" in body
    assert body.strip()


def test_start_implementation_skill_requires_fail_closed_preflight() -> None:
    text = SKILL.read_text()
    workflow = text.index("## Workflow")
    preflight = text.index("### 1. Preflight", workflow)
    create = text.index("### 2. Worktree 생성", workflow)
    verify = text.index("### 3. 생성 후 검증", workflow)
    handoff = text.index("### 4. Implementation handoff", workflow)
    assert preflight < create < verify < handoff

    for required in [
        "git status --porcelain",
        "exact REQ approval commit",
        "path 충돌",
        "branch 충돌",
        "worktree registration",
        "no-mutation",
    ]:
        assert required in text


def test_start_implementation_skill_preserves_workspace_and_authority() -> None:
    text = SKILL.read_text()
    for required in [
        ".worktrees/line-NNNN/",
        "git worktree add",
        "main checkout",
        "공용 `proofline`",
        "ProofLine 전용 `.venv`를 생성하지 않는다",
        "fast-forward",
        "DQC",
    ]:
        assert required in text
    assert "ProofLine CLI가 Git branch" in text
    assert "자동으로 강제 삭제하지 않는다" in text


def test_line_delivery_contract_defines_linked_worktree_boundary() -> None:
    text = CONTRACT.read_text()
    for required in [
        ".worktrees/line-NNNN/",
        "exact REQ approval commit",
        "linked worktree",
        "공용 `proofline`",
        "ProofLine 전용 `.venv`",
        "main checkout",
    ]:
        assert required in text


def test_repository_ignores_worktree_container() -> None:
    patterns = {
        line.strip()
        for line in GITIGNORE.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "/.worktrees/" in patterns


def test_worktree_script_accepts_direct_approval_without_draft_parent(tmp_path: Path) -> None:
    repo, approval = make_approved_repo(tmp_path)
    assert len(git(repo, "rev-list", "--parents", "-n", "1", approval).stdout.split()) == 1

    result = run_script(repo, approval)

    assert result.returncode == 0, result.stderr
    worktree = repo / ".worktrees/line-0007"
    assert git(worktree, "rev-parse", "HEAD").stdout.strip() == approval
    assert git(worktree, "branch", "--show-current").stdout.strip() == (
        "line/line-0007-implementation"
    )
    assert git(repo, "branch", "--show-current").stdout.strip() == "main"
    assert git(repo, "status", "--porcelain").stdout == ""
    assert not (worktree / ".venv").exists()


def test_worktree_script_dirty_main_fails_without_mutation(tmp_path: Path) -> None:
    repo, approval = make_approved_repo(tmp_path)
    (repo / "dirty.txt").write_text("uncommitted")
    refs_before = git(repo, "show-ref").stdout
    worktrees_before = git(repo, "worktree", "list", "--porcelain").stdout

    result = run_script(repo, approval)

    assert result.returncode != 0
    assert "main working tree is not clean" in result.stderr
    assert git(repo, "show-ref").stdout == refs_before
    assert git(repo, "worktree", "list", "--porcelain").stdout == worktrees_before
    assert not (repo / ".worktrees/line-0007").exists()


def test_worktree_script_rejects_unapproved_req_without_mutation(tmp_path: Path) -> None:
    repo, approval = make_approved_repo(tmp_path, req_status="draft")

    result = run_script(repo, approval)

    assert result.returncode != 0
    assert "REQ.status must be approved" in result.stderr
    assert not (repo / ".worktrees/line-0007").exists()
    branch_check = subprocess.run(
        (
            "git",
            "show-ref",
            "--verify",
            "refs/heads/line/line-0007-implementation",
        ),
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert branch_check.returncode != 0
