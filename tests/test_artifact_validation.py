from pathlib import Path
import shutil

from proofline.validator import validate_project


FIXTURE = Path(__file__).parent / "fixtures" / "valid-minimal"


def copy_valid_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    shutil.copytree(FIXTURE, project)
    return project


def test_valid_minimal_artifacts_have_no_errors(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)

    assert validate_project(project) == []


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


def test_line_artifact_body_is_rejected(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    artifact = project / ".proofline/lines/line-0001/line-0001.md"
    artifact.write_text(artifact.read_text(encoding="utf-8") + "\n# 본문\n", encoding="utf-8")

    assert_error(project, artifact, "artifact.headings")


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


def assert_error(project: Path, artifact: Path, code: str) -> None:
    relative = artifact.relative_to(project).as_posix()
    assert (relative, code) in [
        (error.path, error.code) for error in validate_project(project)
    ]
