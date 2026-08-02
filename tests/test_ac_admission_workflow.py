from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_requirement_template_emits_optional_satisfy_list() -> None:
    template = read("templates/schema-v1/artifacts/requirement.md")

    assert "  satisfy: []" in template


def test_contract_preserves_legacy_req_and_defines_satisfy() -> None:
    contract = read("docs/contracts/requirements-and-criteria.md")

    assert "criteria.satisfy" in contract
    assert "create ∪ update ∪ retire ∪ satisfy" in contract
    assert "legacy" in contract
    assert "active" in contract


def test_micro_spec_contract_includes_satisfy_in_coverage_union() -> None:
    contract = read("docs/contracts/micro-spec-and-iqc.md")

    assert "create`, `update`, `retire`, `satisfy" in contract


def test_start_line_skill_requires_admission_classification() -> None:
    skill = read("skills/proofline-start-line/SKILL.md")

    for classification in (
        "create",
        "update",
        "retire",
        "satisfy",
        "release evidence",
        "housekeeping",
    ):
        assert classification in skill
    assert "가장 가까운 active AC" in skill
    assert "Open Question" in skill


def test_approval_and_implementation_skills_preserve_satisfy_ac() -> None:
    approval = read("skills/proofline-approve-specification/SKILL.md")
    implementation = read("skills/proofline-start-implementation/SKILL.md")

    assert "criteria.satisfy" in approval
    assert "status를 변경하지" in approval
    assert "criteria.satisfy" in implementation
    assert "active" in implementation


def test_dqc_skill_covers_satisfy_targets() -> None:
    skill = read("skills/proofline-run-dqc/SKILL.md")

    assert "create·update·retire·satisfy" in skill


def test_release_specific_text_is_review_warning_not_validator_error() -> None:
    start = read("skills/proofline-start-line/SKILL.md")
    contract = read("docs/contracts/requirements-and-criteria.md")

    assert "review warning" in start
    assert "hard error" in contract
