from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/proofline-tool-environment/SKILL.md"
DOC = ROOT / "docs/operations/proofline-tool-environment.md"


def test_tool_environment_skill_has_valid_frontmatter() -> None:
    text = SKILL.read_text()
    assert text.startswith("---\n")
    _, frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == "proofline-tool-environment"
    assert metadata["description"].startswith("Use when ")
    assert metadata["version"] == "1.2.0"
    assert "## When to Use" in body
    assert body.strip()


def test_tool_environment_skill_defines_shared_non_editable_bootstrap() -> None:
    text = SKILL.read_text()
    for required in [
        "proofline update --check",
        "proofline update --adopt-official",
        "v0.2.1",
        "gh release download v0.1.0",
        "sha256sum --check --strict SHA256SUMS",
        "proofline-0.1.0-py3-none-any.whl",
        "uv tool install <proofline-checkout>",
        "uv tool dir",
        "uv tool dir --bin",
        "proofline validate",
        "proofline --version",
        "non-editable",
        "Main과 모든 Line worktree",
        "application `.venv`",
    ]:
        assert required in text
    assert "uv tool install --editable" not in text
    assert "worktree마다 ProofLine 전용 `.venv`" in text


def test_tool_environment_skill_preserves_application_and_scope_boundaries() -> None:
    text = SKILL.read_text()
    for required in [
        "pyproject.toml",
        "lockfile",
        "no-mutation",
        "Issue #9",
        "update",
        "rollback",
        "[REDACTED]",
    ]:
        assert required in text
    assert "ProofLine CLI는 application dependency를 설치하지 않는다" in text


def test_tool_environment_operation_doc_matches_skill_contract() -> None:
    text = DOC.read_text()
    for required in [
        "gh release download v0.1.0",
        "sha256sum --check --strict SHA256SUMS",
        "uv tool install <proofline-checkout>",
        "~/.local/share/uv/tools/proofline/",
        "~/.local/bin/proofline",
        "공용 `proofline`",
        "ProofLine 전용 `.venv`를 생성하지 않는다",
        "Issue #9",
    ]:
        assert required in text
