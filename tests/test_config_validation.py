from pathlib import Path

from proofline.validator import validate_project


def test_valid_project_config_has_no_errors(tmp_path: Path) -> None:
    (tmp_path / "proofline.yaml").write_text(
        "schema_version: 1\nartifact_root: .proofline\n",
        encoding="utf-8",
    )

    assert validate_project(tmp_path) == []


def test_missing_project_config_reports_the_file(tmp_path: Path) -> None:
    errors = validate_project(tmp_path)

    assert [(error.path, error.code) for error in errors] == [
        ("proofline.yaml", "config.missing")
    ]


def test_unknown_project_config_field_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "proofline.yaml").write_text(
        "schema_version: 1\nartifact_root: .proofline\nextra: true\n",
        encoding="utf-8",
    )

    errors = validate_project(tmp_path)

    assert [(error.path, error.code) for error in errors] == [
        ("proofline.yaml", "config.unknown-field")
    ]


def test_wrong_project_config_values_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "proofline.yaml").write_text(
        "schema_version: 2\nartifact_root: artifacts\n",
        encoding="utf-8",
    )

    errors = validate_project(tmp_path)

    assert [(error.path, error.code) for error in errors] == [
        ("proofline.yaml", "config.artifact-root"),
        ("proofline.yaml", "config.schema-version"),
    ]


def test_malformed_project_config_is_reported(tmp_path: Path) -> None:
    (tmp_path / "proofline.yaml").write_text("schema_version: [\n", encoding="utf-8")

    errors = validate_project(tmp_path)

    assert [(error.path, error.code) for error in errors] == [
        ("proofline.yaml", "config.yaml")
    ]
