from pathlib import Path

import pytest

from proofline import validator
from proofline.validator import validate_project


def test_valid_project_config_has_no_errors(tmp_path: Path) -> None:
    (tmp_path / ".proofline" / "lines").mkdir(parents=True)
    (tmp_path / ".proofline" / "criteria").mkdir()
    (tmp_path / "proofline.yaml").write_text(
        "schema_version: 1\nartifact_root: .proofline\n",
        encoding="utf-8",
    )
    (tmp_path / ".proofline/identities.json").write_text(
        '{\n  "schema_version": 1,\n  "next_line_number": 1,\n  "next_ac_number": 1\n}\n'
    )

    assert validate_project(tmp_path) == []


def test_missing_project_config_reports_the_file(tmp_path: Path) -> None:
    errors = validate_project(tmp_path)

    assert [(error.path, error.code) for error in errors] == [
        ("proofline.yaml", "config.missing")
    ]


def test_unknown_project_config_field_is_rejected(tmp_path: Path) -> None:
    (tmp_path / ".proofline" / "lines").mkdir(parents=True)
    (tmp_path / ".proofline" / "criteria").mkdir()
    (tmp_path / "proofline.yaml").write_text(
        "schema_version: 1\nartifact_root: .proofline\nextra: true\n",
        encoding="utf-8",
    )
    (tmp_path / ".proofline/identities.json").write_text(
        '{\n  "schema_version": 1,\n  "next_line_number": 1,\n  "next_ac_number": 1\n}\n'
    )

    errors = validate_project(tmp_path)

    assert [(error.path, error.code) for error in errors] == [
        ("proofline.yaml", "config.unknown-field")
    ]


def test_wrong_project_config_values_are_rejected(tmp_path: Path) -> None:
    (tmp_path / ".proofline" / "lines").mkdir(parents=True)
    (tmp_path / ".proofline" / "criteria").mkdir()
    (tmp_path / "proofline.yaml").write_text(
        "schema_version: 2\nartifact_root: artifacts\n",
        encoding="utf-8",
    )
    (tmp_path / ".proofline/identities.json").write_text(
        '{\n  "schema_version": 1,\n  "next_line_number": 1,\n  "next_ac_number": 1\n}\n'
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


def test_non_utf8_project_config_is_reported(tmp_path: Path) -> None:
    (tmp_path / "proofline.yaml").write_bytes(b"\xff")

    errors = validate_project(tmp_path)

    assert [(error.path, error.code) for error in errors] == [
        ("proofline.yaml", "config.read")
    ]


def test_config_replaced_by_symlink_during_open_is_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "proofline.yaml"
    config.write_text("schema_version: 1\nartifact_root: .proofline\n")
    outside = tmp_path / "outside.yaml"
    outside.write_text("schema_version: 1\nartifact_root: .proofline\n")
    real = validator.read_regular_beneath
    raced = False

    def replace_before_open(root: Path, relative: str):
        nonlocal raced
        if root / relative == config and not raced:
            raced = True
            config.unlink()
            config.symlink_to(outside)
        return real(root, relative)

    monkeypatch.setattr(validator, "read_regular_beneath", replace_before_open)
    errors = validate_project(tmp_path)
    assert ("proofline.yaml", "config.read") in [
        (error.path, error.code) for error in errors
    ]
    assert outside.read_text() == "schema_version: 1\nartifact_root: .proofline\n"
