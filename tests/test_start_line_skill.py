from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/proofline-start-line/SKILL.md"


def test_start_line_skill_has_valid_frontmatter_and_actionable_body() -> None:
    text = SKILL.read_text()
    assert text.startswith("---\n")
    _, frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == "proofline-start-line"
    assert metadata["description"].startswith("Use when ")
    assert len(metadata["description"]) <= 1024
    assert metadata["version"] == "1.3.0"
    assert ".proofline/identities.json" in body
    assert "next_line_number" in body
    assert body.strip()


def test_start_line_skill_requires_scaffold_before_evidence_authoring() -> None:
    text = SKILL.read_text()
    workflow = text.index("## Workflow")
    scaffold = text.index("proofline line init", workflow)
    evidence = text.index("### 3. 직접 evidence", workflow)
    authoring = text.index("## Discovery 작성", workflow)
    assert scaffold < evidence < authoring
    for heading in ["Problem", "Evidence", "Scope", "Out of Scope"]:
        assert f"`{heading}`" in text


def test_start_line_skill_describes_initial_informational_status() -> None:
    text = SKILL.read_text(encoding="utf-8")

    assert "`id`와 정보 표시용 `status: discovery`" in text
    assert "Line artifact는 stable `id`만 가진다" not in text
    assert "Line artifact는 `id`만 가진다" not in text


def test_start_line_skill_preserves_governance_authority() -> None:
    text = SKILL.read_text()
    assert "Open Question" in text
    assert "Owner" in text
    assert "Exit Condition" in text
    assert "`confirmed`로 전환하지 않는다" in text
    assert "사용자 대신" in text
    assert "live Hermes profile을 변경하지 않는다" in text


def test_start_line_skill_requires_validation_and_no_secret_evidence() -> None:
    text = SKILL.read_text()
    assert "proofline validate" in text
    assert "[REDACTED]" in text
    assert "credential" in text.casefold()
    assert "Verification Checklist" in text
