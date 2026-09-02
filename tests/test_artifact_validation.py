import shutil
from pathlib import Path

import pytest

from proofline import validator
from proofline.validator import _validate_schema_candidate, validate_project

FIXTURE = Path(__file__).parent / "fixtures" / "valid-minimal"


def copy_valid_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    shutil.copytree(FIXTURE, project)
    return project


def test_valid_minimal_artifacts_have_no_errors(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)

    assert _validate_schema_candidate(project) == []


def test_artifact_replaced_by_symlink_during_open_is_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = copy_valid_project(tmp_path)
    artifact = project / ".proofline/lines/line-0001/line-0001.md"
    outside = tmp_path / "outside.md"
    outside.write_bytes(artifact.read_bytes())
    real = validator.read_regular_beneath
    raced = False

    def replace_before_open(root: Path, relative: str):
        nonlocal raced
        if root / relative == artifact and not raced:
            raced = True
            artifact.unlink()
            artifact.symlink_to(outside)
        return real(root, relative)

    monkeypatch.setattr(validator, "read_regular_beneath", replace_before_open)
    errors = validate_project(project)
    assert any(
        error.path == ".proofline/lines/line-0001/line-0001.md"
        and error.code == "artifact.read"
        for error in errors
    )
    assert outside.read_bytes().startswith(b"---\n")


def test_artifact_parent_replaced_by_symlink_during_open_is_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = copy_valid_project(tmp_path)
    line = project / ".proofline/lines/line-0001"
    moved = tmp_path / "moved-line"
    real = validator.read_regular_beneath
    raced = False

    def replace_parent_before_open(root: Path, relative: str):
        nonlocal raced
        if relative.endswith("line-0001/line-0001.md") and not raced:
            raced = True
            line.rename(moved)
            line.symlink_to(moved, target_is_directory=True)
        return real(root, relative)

    monkeypatch.setattr(
        validator, "read_regular_beneath", replace_parent_before_open
    )
    errors = validate_project(project)
    assert any(
        error.path == ".proofline/lines/line-0001/line-0001.md"
        and error.code == "artifact.read"
        for error in errors
    )


def test_public_project_validation_has_no_history_opt_out() -> None:
    import inspect

    assert "check_history" not in inspect.signature(validate_project).parameters


def test_line_accepts_canonical_minimal_schema(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    artifact = project / ".proofline/lines/line-0001/line-0001.md"
    artifact.write_text('---\nid: "line-0001"\n---\n', encoding="utf-8")

    assert _validate_schema_candidate(project) == []


def test_validator_rejects_zero_line_and_ac_artifact_paths(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    zero_line = project / ".proofline/lines/line-0000"
    zero_line.mkdir()
    (zero_line / "line-0000.md").write_text(
        '---\nid: "line-0000"\n---\n', encoding="utf-8"
    )
    source_ac = project / ".proofline/criteria/ac-0001.md"
    zero_ac = project / ".proofline/criteria/ac-0000.md"
    zero_ac.write_text(
        source_ac.read_text(encoding="utf-8").replace("ac-0001", "ac-0000"),
        encoding="utf-8",
    )

    errors = validate_project(project)

    assert any(error.path == ".proofline/lines/line-0000" for error in errors)
    assert any(
        error.path == ".proofline/lines/line-0000/line-0000.md"
        and error.code == "artifact.path"
        for error in errors
    )
    assert any(
        error.path == ".proofline/criteria/ac-0000.md"
        and error.code == "artifact.path"
        for error in errors
    )


def test_line_accepts_implementation_history_without_execution_status(
    tmp_path: Path,
) -> None:
    project = copy_valid_project(tmp_path)
    artifact = project / ".proofline/lines/line-0001/line-0001.md"
    artifact.write_text(
        '---\nid: "line-0001"\nimplementation_history: first_parent\n---\n',
        encoding="utf-8",
    )

    assert _validate_schema_candidate(project) == []


def test_line_accepts_opaque_deprecated_metadata(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    artifact = project / ".proofline/lines/line-0001/line-0001.md"
    artifact.write_text(
        '---\nid: "line-0001"\nexecution_status: arbitrary-value\n'
        'implementation_history:\n  nested: [opaque, metadata]\n---\n',
        encoding="utf-8",
    )

    assert _validate_schema_candidate(project) == []


def test_line_accepts_arbitrary_informational_status(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    artifact = project / ".proofline/lines/line-0001/line-0001.md"
    artifact.write_text(
        '---\nid: "line-0001"\nstatus: any-project-label\n---\n',
        encoding="utf-8",
    )

    assert _validate_schema_candidate(project) == []


def test_line_rejects_noncanonical_field(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    artifact = project / ".proofline/lines/line-0001/line-0001.md"
    artifact.write_text(
        '---\nid: "line-0001"\nowner: team\n---\n',
        encoding="utf-8",
    )

    assert_error(project, artifact, "artifact.unknown-field")


def test_retained_legacy_canonical_paths_are_opaque(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    retained = (
        ".proofline/lines/line-0001/micro-specs/ms-0001-001.md",
        ".proofline/lines/line-0001/micro-specs/iqc-0001-001.md",
        ".proofline/lines/line-0001/dqc-0001.md",
        ".proofline/lines/line-0001/integration-0001.md",
        ".proofline/lines/line-0001/legacy-migration-0001.md",
    )
    for relative in retained:
        artifact = project / relative
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"\xffnot yaml or utf-8")

    errors = validate_project(project)

    assert not [error for error in errors if error.path in retained]


def test_unknown_artifact_frontmatter_field_is_rejected(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    artifact = project / ".proofline/criteria/ac-0001.md"
    artifact.write_text(
        artifact.read_text(encoding="utf-8").replace(
            "status: active", "status: active\nowner: team"
        ),
        encoding="utf-8",
    )

    errors = validate_project(project)

    assert (".proofline/criteria/ac-0001.md", "artifact.unknown-field") in [
        (error.path, error.code) for error in errors
    ]


def test_missing_required_frontmatter_field_is_rejected(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    artifact = project / ".proofline/criteria/ac-0001.md"
    artifact.write_text(
        artifact.read_text(encoding="utf-8").replace("status: active\n", ""),
        encoding="utf-8",
    )

    assert_error(project, artifact, "artifact.missing-field")


def test_invalid_artifact_status_is_rejected(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    artifact = project / ".proofline/criteria/ac-0001.md"
    artifact.write_text(
        artifact.read_text(encoding="utf-8").replace("status: active", "status: done"),
        encoding="utf-8",
    )

    assert_error(project, artifact, "artifact.status")


def test_artifact_id_must_match_filename(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    artifact = project / ".proofline/criteria/ac-0001.md"
    artifact.write_text(
        artifact.read_text(encoding="utf-8").replace("id: ac-0001", "id: ac-9999"),
        encoding="utf-8",
    )

    assert_error(project, artifact, "artifact.id")


def test_wrong_required_h2_is_rejected(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    artifact = project / ".proofline/criteria/ac-0001.md"
    artifact.write_text(
        artifact.read_text(encoding="utf-8").replace("## Verification", "## Checks"),
        encoding="utf-8",
    )

    assert_error(project, artifact, "artifact.headings")


def test_non_draft_artifact_cannot_contain_placeholder(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    artifact = project / ".proofline/criteria/ac-0001.md"
    artifact.write_text(
        artifact.read_text(encoding="utf-8").replace(
            "검증 가능한 조건이다.", "{{TODO: 조건을 작성한다}}"
        ),
        encoding="utf-8",
    )

    assert_error(project, artifact, "artifact.placeholder")


def test_invalid_placeholder_name_is_rejected_in_draft(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    artifact = project / ".proofline/lines/line-0001/dcy-0001.md"
    artifact.write_text(
        artifact.read_text(encoding="utf-8")
        .replace("status: confirmed", "status: draft")
        .replace("문제를 설명한다.", "{{LATER: 문제를 작성한다}}"),
        encoding="utf-8",
    )

    assert_error(project, artifact, "artifact.placeholder")


def test_line_artifact_accepts_activity_summary_and_relative_link(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    artifact = project / ".proofline/lines/line-0001/line-0001.md"
    artifact.write_text(
        artifact.read_text(encoding="utf-8")
        + "\n# Activity\n\n- 최근 활동: 구현 시작\n"
        + "- 로그: [activity-log.md](evidence/activity-log.md)\n",
        encoding="utf-8",
    )

    assert _validate_schema_candidate(project) == []


def test_malformed_artifact_frontmatter_is_reported(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    artifact = project / ".proofline/criteria/ac-0001.md"
    artifact.write_text(
        artifact.read_text(encoding="utf-8").replace("status: active", "status: ["),
        encoding="utf-8",
    )

    assert_error(project, artifact, "artifact.frontmatter")


def test_line_number_must_match_canonical_path(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    source = project / ".proofline/lines/line-0001/dcy-0001.md"
    artifact = project / ".proofline/lines/line-0002/dcy-0001.md"
    artifact.parent.mkdir(parents=True)
    source.replace(artifact)

    assert_error(project, artifact, "artifact.path")


def test_draft_artifact_accepts_governance_placeholder(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    artifact = project / ".proofline/lines/line-0001/dcy-0001.md"
    artifact.write_text(
        artifact.read_text(encoding="utf-8")
        .replace("status: confirmed", "status: draft")
        .replace("문제를 설명한다.", "{{TODO: 문제를 작성한다}}"),
        encoding="utf-8",
    )

    relative = artifact.relative_to(project).as_posix()
    assert "artifact.placeholder" not in [
        error.code for error in validate_project(project) if error.path == relative
    ]


def test_unknown_markdown_path_under_artifact_root_is_rejected(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    artifact = project / ".proofline/mystery.md"
    artifact.write_text("---\nid: mystery\n---\n", encoding="utf-8")

    assert_error(project, artifact, "artifact.path")


def test_non_utf8_artifact_is_reported(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    artifact = project / ".proofline/criteria/ac-0001.md"
    artifact.write_bytes(b"\xff")

    assert_error(project, artifact, "artifact.read")


def assert_error(project: Path, artifact: Path, code: str) -> None:
    relative = artifact.relative_to(project).as_posix()
    assert (relative, code) in [
        (error.path, error.code) for error in validate_project(project)
    ]
