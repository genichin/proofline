from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/proofline-start-implementation/SKILL.md"
CONTRACT = ROOT / "docs/contracts/line-delivery.md"
GITIGNORE = ROOT / ".gitignore"


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
