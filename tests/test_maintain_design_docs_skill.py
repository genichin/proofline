from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills/proofline-maintain-design-docs"
SKILL = SKILL_ROOT / "SKILL.md"
TEMPLATES = {
    "interface-contract.md": (
        "Purpose",
        "Related Specification",
        "Boundary and Participants",
        "Inputs and Outputs",
        "Preconditions and Postconditions",
        "Errors and Compatibility",
    ),
    "data-model.md": (
        "Purpose",
        "Related Specification",
        "Structures",
        "Invariants",
        "Ownership and Lifetime",
        "Serialization and Compatibility",
        "Producers and Consumers",
    ),
    "runtime-flow.md": (
        "Purpose",
        "Related Specification",
        "Trigger and Participants",
        "Normal Flow",
        "State Transitions",
        "Failure Flow",
        "Side Effects and Recovery",
    ),
}


def test_maintenance_skill_defines_evidence_grounded_bounded_workflow() -> None:
    text = SKILL.read_text(encoding="utf-8")
    _, frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)

    assert metadata["name"] == "proofline-maintain-design-docs"
    assert metadata["description"].startswith("Use when ")
    assert metadata["version"] == "1.0.0"
    for required in (
        "confirmed Discovery",
        "Normative Design Documents",
        "source·test·config",
        "no-impact",
        "planned update",
        "write 전에",
        "existing owner",
        "symlink",
        "unexpected file type",
        "사용자만",
        "create/update",
        "changed·unchanged·unattempted",
        "자동 retry·rollback하지 않는다",
    ):
        assert required in body


def test_design_document_templates_have_exact_required_h2_inventory() -> None:
    for name, headings in TEMPLATES.items():
        path = SKILL_ROOT / "templates" / name
        text = path.read_text(encoding="utf-8")
        h1 = [line for line in text.splitlines() if line.startswith("# ")]
        h2 = [line.removeprefix("## ") for line in text.splitlines() if line.startswith("## ")]

        assert len(h1) == 1
        assert h2 == list(headings)
        assert "{{" in text and "}}" in text


@pytest.mark.parametrize(
    ("scenario", "required"),
    (
        ("existing-update", ("existing owner를 먼저 갱신", "독립 owner가 필요한 경우에만")),
        ("independent-create", ("새 문서를 계획", "`create` 또는 `update`", "의도된 기술 의미")),
        ("no-impact", ("`no-impact`이면", "planned/write set을 비운다", "목록을 변경하지 않는다")),
        (
            "unsafe-target",
            (
                "Existing symlink",
                "unexpected file type",
                "repository escape",
                "unrelated user document",
                "owner ambiguity",
                "no-write blocker",
            ),
        ),
        ("pre-write-cancellation", ("Write 전 cancellation", "zero project-document mutation")),
        (
            "partial-write",
            (
                "bytes 또는 digest",
                "전체 planned path 순서",
                "changed·unchanged·unattempted",
                "Specification approval을 진행하지 않는다",
                "자동 retry·rollback하지 않는다",
            ),
        ),
    ),
)
def test_maintenance_skill_keeps_each_approved_scenario_explicit(
    scenario: str,
    required: tuple[str, ...],
) -> None:
    body = SKILL.read_text(encoding="utf-8").split("---", 2)[2]

    assert scenario
    for phrase in required:
        assert phrase in body
