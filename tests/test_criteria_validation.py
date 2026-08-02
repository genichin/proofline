import shutil
from pathlib import Path

import yaml

from proofline.validator import validate_project

FIXTURE = Path(__file__).parent / "fixtures" / "valid-minimal"
REQ = ".proofline/lines/line-0001/req-0001.md"
MS = ".proofline/lines/line-0001/micro-specs/ms-0001-001.md"


def copy_valid_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    shutil.copytree(FIXTURE, project)
    return project


def replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def errors_for(project: Path, path: str):
    return [error for error in validate_project(project) if error.path == path]


def test_req_accepts_optional_satisfy_with_active_target(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    req = project / REQ
    replace(req, "    - ac-0003\n  update: []", "  update: []")
    replace(req, "  retire: []", "  retire: []\n  satisfy:\n    - ac-0003")

    assert validate_project(project) == []


def test_req_rejects_unknown_criteria_key(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    req = project / REQ
    replace(req, "  retire: []", "  retire: []\n  verify: []")

    assert any(error.code == "criteria.invalid" for error in errors_for(project, REQ))


def test_req_rejects_non_list_criteria_value(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    req = project / REQ
    replace(req, "  update: []", "  update: ac-0002")

    assert any(error.code == "criteria.invalid" for error in errors_for(project, REQ))


def test_req_rejects_empty_criteria_union(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    req = project / REQ
    frontmatter, body = req.read_text(encoding="utf-8").split("---", 2)[1:]
    data = yaml.safe_load(frontmatter)
    data["criteria"] = {"create": [], "update": [], "retire": [], "satisfy": []}
    req.write_text("---\n" + yaml.safe_dump(data, sort_keys=False) + "---" + body, encoding="utf-8")

    assert any(error.code == "criteria.empty" for error in errors_for(project, REQ))


def test_req_rejects_ac_in_multiple_criteria_lists(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    req = project / REQ
    replace(req, "  retire: []", "  retire: []\n  satisfy:\n    - ac-0001")

    duplicate = [error for error in errors_for(project, REQ) if error.code == "criteria.duplicate"]
    assert duplicate
    assert "ac-0001" in duplicate[0].message


def test_req_allows_same_ac_repeated_inside_one_list(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    req = project / REQ
    replace(
        req,
        "    - ac-0001\n    - ac-0002",
        "    - ac-0001\n    - ac-0001\n    - ac-0002",
    )

    assert errors_for(project, REQ) == []


def test_satisfy_rejects_retired_ac(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    req = project / REQ
    ac = project / ".proofline/criteria/ac-0003.md"
    replace(req, "    - ac-0003\n  update: []", "  update: []")
    replace(req, "  retire: []", "  retire: []\n  satisfy:\n    - ac-0003")
    replace(ac, "status: active", "status: retired")

    status_errors = [
        error for error in errors_for(project, REQ) if error.code == "reference.inactive"
    ]
    assert status_errors
    assert "ac-0003" in status_errors[0].message


def test_micro_spec_rejects_ac_outside_parent_req_scope(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    req = project / REQ
    replace(req, "    - ac-0003\n  update: []", "  update: []")

    scope_errors = [
        error for error in errors_for(project, MS) if error.code == "criteria.out-of-scope"
    ]
    assert scope_errors
    assert "ac-0003" in scope_errors[0].message


def test_req_rejects_target_uncovered_by_micro_specs(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    ms = project / MS
    replace(ms, "  - ac-0003\n", "")

    coverage_errors = [
        error for error in errors_for(project, REQ) if error.code == "criteria.uncovered"
    ]
    assert coverage_errors
    assert "ac-0003" in coverage_errors[0].message


def test_withdrawn_micro_spec_does_not_supply_coverage(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    ms = project / MS
    replace(ms, "spec_status: approved", "spec_status: withdrawn")

    assert any(error.code == "criteria.uncovered" for error in errors_for(project, REQ))


def test_withdrawn_req_still_rejects_inactive_satisfy_target(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    req = project / REQ
    ac = project / ".proofline/criteria/ac-0003.md"
    replace(req, "status: approved", "status: withdrawn")
    replace(req, "    - ac-0003\n  update: []", "  update: []")
    replace(req, "  retire: []", "  retire: []\n  satisfy:\n    - ac-0003")
    replace(ac, "status: active", "status: retired")

    assert any(error.code == "reference.inactive" for error in errors_for(project, REQ))


def test_withdrawn_req_still_limits_non_withdrawn_micro_spec_scope(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    req = project / REQ
    replace(req, "status: approved", "status: withdrawn")
    replace(req, "    - ac-0003\n  update: []", "  update: []")

    assert any(error.code == "criteria.out-of-scope" for error in errors_for(project, MS))
