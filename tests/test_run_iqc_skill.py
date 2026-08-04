from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/proofline-run-iqc/SKILL.md"


def test_run_iqc_skill_has_valid_frontmatter() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == "proofline-run-iqc"
    assert metadata["description"].startswith("Use when ")
    assert metadata["version"] == "1.0.0"
    assert "## When to Use" in body
    assert body.strip()


def test_run_iqc_skill_enforces_exact_first_parent_cycle() -> None:
    text = SKILL.read_text(encoding="utf-8")
    for required in [
        "M < P < I",
        "B < I",
        "P = I",
        "I = Q",
        "second-parent-only",
        "lifecycle-only",
        "proofline validate",
        "Source와 installed artifact evidence",
    ]:
        assert required in text
    assert text.index("### 1. Exact binding 확인") < text.index(
        "### 2. Focused verification 실행"
    ) < text.index("### 3. 후속 candidate Q 기록") < text.index(
        "### 4. Candidate 검증"
    )
