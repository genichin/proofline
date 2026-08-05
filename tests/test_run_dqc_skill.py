from pathlib import Path
import re

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/proofline-run-dqc/SKILL.md"
CONTRACT = ROOT / "docs/contracts/line-delivery.md"
TEMPLATE = ROOT / "templates/schema-v1/artifacts/dqc.md"

EXPECTED_REQUIRED = {
    "iqc_coverage_binding",
    "full_regression",
    "canonical_validation",
    "cross_spec_integration_scope",
    "main_fast_forward",
    "post_candidate_source_immutability",
}
EXPECTED_TRIGGERS = {
    "source_after_iqc": "rerun_affected_component_checks",
    "uncovered_integration_risk": "run_risk_specific_checks",
    "invalid_iqc_evidence": "block_until_valid_iqc",
    "explicit_line_level_requirement": "run_explicit_line_checks",
}


def load_policy() -> dict:
    text = SKILL.read_text()
    match = re.search(r"```yaml dqc-policy\n(.*?)\n```", text, re.DOTALL)
    assert match is not None
    return yaml.safe_load(match.group(1))


def conditional_actions(policy: dict, active_triggers: set[str]) -> list[str]:
    return [
        details["action"]
        for trigger, details in policy["conditional_triggers"].items()
        if trigger in active_triggers
    ]


def test_run_dqc_skill_has_valid_frontmatter() -> None:
    text = SKILL.read_text()
    assert text.startswith("---\n")
    _, frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == "proofline-run-dqc"
    assert metadata["description"].startswith("Use when ")
    assert metadata["version"] == "1.3.0"
    assert "## When to Use" in body
    assert body.strip()


def test_policy_has_only_line_level_default_checks() -> None:
    policy = load_policy()
    assert policy["policy_version"] == 1
    assert set(policy["required_line_checks"]) == EXPECTED_REQUIRED
    defaults = "\n".join(policy["required_line_checks"])
    for component_check in ["compileall", "lock", "wheel", "package", "skill_metadata"]:
        assert component_check not in defaults


def test_policy_requires_exact_github_hosted_candidate_gate() -> None:
    gate = load_policy()["hosted_candidate_gate"]
    assert gate == {
        "mandatory": True,
        "provider": "github-actions",
        "workflow_path": ".github/workflows/candidate-verification.yml",
        "candidate_branch": "candidate/{line_id}",
        "required_jobs": [
            "build-candidate",
            "ubuntu-python311",
            "windows-python311",
        ],
        "artifact_name": "proofline-candidate-{run_id}-{run_attempt}",
        "provenance_filename": "CANDIDATE_PROVENANCE.json",
        "checksum_filename": "SHA256SUMS",
        "evidence_helper": ".github/scripts/verify-candidate-evidence.py",
        "non_pass_action": "block_dqc",
        "same_v_retry": "forbidden",
    }


def test_exact_bound_passed_iqc_is_reused_without_triggers() -> None:
    policy = load_policy()
    assert conditional_actions(policy, set()) == []
    assert policy["no_trigger"]["action"] == "reuse_exact_bound_passed_iqc"
    assert set(policy["no_trigger"]["required_record"]) == {
        "exact_iqc_binding",
        "skip_rationale",
    }


@pytest.mark.parametrize(("trigger", "expected_action"), EXPECTED_TRIGGERS.items())
def test_each_conditional_trigger_requires_its_action(
    trigger: str, expected_action: str
) -> None:
    policy = load_policy()
    assert conditional_actions(policy, {trigger}) == [expected_action]
    assert policy["conditional_triggers"][trigger]["result_required_before_pass"] is True


def test_contract_separates_required_and_conditional_dqc_responsibilities() -> None:
    text = CONTRACT.read_text()
    for required in [
        "DQC 항상 필수 검사",
        "IQC evidence 재사용",
        "source-after-IQC",
        "uncovered integration risk",
        "invalid IQC evidence",
        "explicit Line-level requirement",
        "skip rationale",
        "`proofline validate`의 validation scope를 확대하지 않는다",
        "mandatory hosted candidate gate",
        "same-`V` retry",
        "DQC Checks",
        "run attempt",
    ]:
        assert required in text


def test_dqc_template_records_bindings_required_checks_and_conditional_decisions() -> None:
    text = TEMPLATE.read_text()
    for required in [
        "### Mandatory Line-Level Checks",
        "iqc_coverage_binding",
        "full_regression",
        "canonical_validation",
        "cross_spec_integration_scope",
        "main_fast_forward",
        "post_candidate_source_immutability",
        "### Conditional Component Checks",
        "source_after_iqc",
        "uncovered_integration_risk",
        "invalid_iqc_evidence",
        "explicit_line_level_requirement",
        "Exact IQC binding",
        "Skip 또는 실행 rationale",
        "### Mandatory Hosted Candidate Gate",
        "Candidate V",
        "Run ID / attempt",
        "build-candidate",
        "ubuntu-python311",
        "windows-python311",
        "Artifact ID / name / expiry",
        "Wheel filename / SHA-256",
        "CANDIDATE_PROVENANCE.json",
        "SHA256SUMS",
        "verify-candidate-evidence.py",
    ]:
        assert required in text


def test_workflow_keeps_authority_and_runtime_boundaries() -> None:
    text = SKILL.read_text()
    for required in [
        "사용자나 지정 governance authority",
        "ProofLine CLI는",
        "Git branch, commit, merge 또는 push",
        "not applicable은 실패가 아니다",
        "candidate 이후 제품 source 불변",
        "same-`V` retry",
        "DQC Checks",
        "main integration이나 release authority를 부여하지 않는다",
        "preflight_integration_candidate.py",
        "pre-integration",
        "post-integration",
        "V.parent[0]=M",
        "V.parent[1]=Q",
    ]:
        assert required in text
