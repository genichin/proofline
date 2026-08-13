from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/proofline-review-specification/SKILL.md"


def skill_body() -> str:
    assert SKILL.is_file(), "proofline-review-specification skill이 아직 없다"
    text = SKILL.read_text(encoding="utf-8")
    _, frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == "proofline-review-specification"
    assert metadata["description"].startswith("Use when ")
    assert metadata["version"] == "1.0.0"
    return body


def test_review_skill_defines_read_only_preapproval_boundary() -> None:
    body = skill_body()

    for required in (
        "confirmed Discovery",
        "draft REQ",
        "create",
        "update",
        "retire",
        "satisfy",
        "Normative Design Documents",
        "일부 AC나 문서를 빠뜨린 채",
        "Mutation performed: false",
        "사용자 승인을 대신하지 않는다",
        "문서와 Git 상태를 변경하지 않는다",
        "proofline-approve-specification",
    ):
        assert required in body


@pytest.mark.parametrize(
    ("scenario", "required"),
    (
        (
            "discovery-traceability",
            ("문제, 근거, 범위와 제외 범위", "범위를 넘어가지 않는지"),
        ),
        (
            "criterion-verification",
            ("하나의 지속적인 요구사항", "관찰 가능한", "Criterion 전체", "변경 없음"),
        ),
        (
            "criteria-relationship",
            ("누락, 중복, 모순", "기존 active AC", "고립", "범위 초과"),
        ),
        (
            "referenced-documents",
            ("existing regular Markdown file", "symlink", "repository escape", "BLOCK"),
        ),
        (
            "no-referenced-documents",
            ("목록이 없는 REQ도", "정상적으로 검토"),
        ),
        (
            "blocker-contract",
            ("판단 근거", "구체적인 문구나 동작", "최소 수정", "non-blocking"),
        ),
        (
            "candidate-drift",
            ("내용이 바뀌면", "전체 문서 집합을 다시", "이전 PASS를 재사용하지 않는다"),
        ),
    ),
)
def test_review_skill_keeps_approved_scenarios_explicit(
    scenario: str,
    required: tuple[str, ...],
) -> None:
    body = skill_body()

    assert scenario
    for phrase in required:
        assert phrase in body


def test_review_skill_has_stable_report_contract() -> None:
    body = skill_body()

    for required in (
        "Verdict: PASS",
        "Verdict: BLOCK",
        "검토한 문서의 경로와 식별정보",
        "요구사항 반영표",
        "승인 차단 문제",
        "승인 차단이 아닌 참고 사항",
        "[REDACTED]",
    ):
        assert required in body
