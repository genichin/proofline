from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers.line_0020_scenario_runner import (
    ScenarioRegistryError,
    execute_cross_artifact_registry,
    load_registry,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tests/scenarios/line_0020_specification_registry.json"
EXPECTED_IDS = {
    "coverage.dormant-partial.pass",
    "coverage.active-exact.pass",
    "coverage.active-missing.fail",
    "coverage.active-duplicate.fail",
    "coverage.active-direct-noncanonical.fail",
    "handoff.exact-a-full-target.pass",
    "handoff.missing-target.fail",
    "handoff.path-id-target.fail",
    "handoff.draft-target.fail",
    "handoff.clean-exact-h-retry.pass",
    "handoff.tracked-dirty-retry.fail",
    "handoff.untracked-dirty-retry.fail",
    "approval.normal.pass",
    "approval.bootstrap.pass",
    "approval.self-approval.fail",
    "approval.missing-user.fail",
    "approval.denied-user.fail",
    "approval.reviewer-mutation.fail",
    "approval.stale-target-and-digest.fail",
    "approval.body-changing.fail",
    "approval.concurrent-path.fail",
    "approval.recorder-only.fail",
    "approval.stale-digest.fail",
    "approval.cross-admission-duplicate.fail",
    "approval.empty-targets.fail",
    "chronology.line-0020-bootstrap.pass",
    "chronology.bootstrap-create-body-change.fail",
    "chronology.bootstrap-update-body-change.fail",
    "chronology.bootstrap-retire-body-change.fail",
    "chronology.bootstrap-satisfy-body-change.fail",
    "chronology.future-a-h-s0-s-p.pass",
    "chronology.missing-s.fail",
    "chronology.non-direct-s.fail",
    "chronology.body-changing-s.fail",
    "chronology.stale-s.fail",
    "chronology.p-before-s.fail",
    "integration.main-first-two-parent-manifest-tree.pass",
    "integration.reversed-parents.fail",
    "integration.octopus.fail",
    "integration.wrong-binding.fail",
    "integration.merge-only-product-change.fail",
    "dqc.exact-v-pass-delivery-and-later-commit.pass",
    "dqc.pass-then-failed-delivery.fail",
    "dqc.pass-then-blocked-delivery.fail",
}


def test_registry_has_fixed_approved_scenario_id_set() -> None:
    registry = load_registry(REGISTRY)

    assert registry.schema_version == 1
    assert {scenario.scenario_id for scenario in registry.scenarios} == EXPECTED_IDS
    assert len(registry.scenarios) == len(EXPECTED_IDS) == 44


def test_registry_rejects_duplicate_scenario_ids(tmp_path: Path) -> None:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    payload["scenarios"].append(payload["scenarios"][0])
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ScenarioRegistryError, match="duplicate scenario_id"):
        load_registry(duplicate)


@pytest.mark.candidate_build_only
def test_source_wheel_and_packaged_scripts_have_exact_registry_parity(
    tmp_path: Path,
) -> None:
    evidence = execute_cross_artifact_registry(ROOT, REGISTRY, tmp_path)

    assert evidence.source.ids == evidence.wheel.ids == EXPECTED_IDS
    assert evidence.source.results == evidence.wheel.results
    assert evidence.source.results == evidence.expected_results
    assert Path(evidence.source.module_path).is_relative_to(ROOT / "src")
    assert "site-packages" in Path(evidence.wheel.module_path).parts
    assert not Path(evidence.wheel.module_path).is_relative_to(ROOT)
    assert evidence.packaged_scripts_byte_equal
    assert evidence.packaged_script_ids == {
        scenario_id
        for scenario_id in EXPECTED_IDS
        if scenario_id.startswith(("handoff.", "approval."))
    }
    assert evidence.all_no_mutation_checks_passed
