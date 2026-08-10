from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/proofline-approve-specification/SKILL.md"
SCRIPT = ROOT / "skills/proofline-approve-specification/scripts/audit_transition.py"
CONTRACT = ROOT / "docs/contracts/requirements-and-criteria.md"


def test_approval_skill_has_single_user_authorized_workflow() -> None:
    text = SKILL.read_text(encoding="utf-8")
    _, frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)

    assert metadata["name"] == "proofline-approve-specification"
    assert metadata["description"].startswith("Use when ")
    assert metadata["version"] == "3.3.0"
    for required in (
        "사용자만",
        "명시적인 approval",
        "exact 내용을",
        "criteria.satisfy",
        "status는 변경하지 않는다",
        "proofline validate",
        "current canonical",
        "validation 결과",
        "historical active binding",
        "Normative Design Documents",
        "exact project-relative path",
        "exact document contents",
        "별도 lifecycle status",
        "관련 REQ와 영향받는 AC를 `draft`",
    ):
        assert required in body
    for removed in (
        "audit_transition.py",
        "transition: recorded",
        "transition: not recorded",
        "direct approval",
        "status-only approval",
        "Exact approval commit",
    ):
        assert removed not in body


def test_contract_defines_current_canonical_approval_baseline_only() -> None:
    text = CONTRACT.read_text(encoding="utf-8")

    for required in (
        "사용자만 REQ와 대상 AC의 의미를 승인",
        "Current canonical `approved` REQ",
        "사용자의 명시적 approval 뒤",
        "current canonical tree를 검증",
        "approval transition validation 입력이 아니다",
        "historical active binding 검증은 별도 lifecycle 규칙",
        "Normative Design Documents",
        "exact project-relative path",
        "exact document contents",
        "별도 lifecycle status",
    ):
        assert required in text
    for removed in (
        "권장 감사 경로",
        "direct approval",
        "필수 chronology는 아니다",
    ):
        assert removed not in text


def test_transition_audit_helper_is_removed() -> None:
    assert not SCRIPT.exists()


@pytest.mark.parametrize(
    ("scenario", "required"),
    (
        (
            "referenced-documents",
            (
                "모든 path가 repository 안의 existing regular Markdown file인지 확인",
                "exact contents를 누락 없이 제시",
                "모든 referenced design-document exact 내용",
            ),
        ),
        (
            "no-referenced-documents",
            ("목록이 없는 REQ도 유효", "design-document 부재만으로 approval을 거부하지 않는다"),
        ),
        (
            "invalid-referenced-path",
            ("Symlink", "repository escape", "missing path", "unexpected file type", "approval blocker"),
        ),
    ),
)
def test_approval_skill_keeps_design_document_branches_explicit(
    scenario: str,
    required: tuple[str, ...],
) -> None:
    body = SKILL.read_text(encoding="utf-8").split("---", 2)[2]

    assert scenario
    for phrase in required:
        assert phrase in body
