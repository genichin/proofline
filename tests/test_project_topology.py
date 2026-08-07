from __future__ import annotations

import os
from pathlib import Path

import pytest

from proofline.validator import validate_project

CONFIG = "schema_version: 1\nartifact_root: .proofline\n"


def make_minimal(root: Path) -> Path:
    (root / ".proofline/lines").mkdir(parents=True)
    (root / ".proofline/criteria").mkdir()
    (root / "proofline.yaml").write_text(CONFIG, encoding="utf-8")
    (root / ".proofline/identities.json").write_text(
        '{\n  "schema_version": 1,\n  "next_line_number": 1,\n  "next_ac_number": 1\n}\n'
    )
    return root


def snapshot(root: Path) -> dict[str, tuple[str, bytes | str]]:
    result: dict[str, tuple[str, bytes | str]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = ("symlink", os.readlink(path))
        elif path.is_dir():
            result[relative] = ("directory", "")
        else:
            result[relative] = ("file", path.read_bytes())
    return result


def pairs(root: Path) -> list[tuple[str, str]]:
    return [(error.path, error.code) for error in validate_project(root)]


def test_config_only_project_reports_all_missing_topology_without_mutation(
    tmp_path: Path,
) -> None:
    (tmp_path / "proofline.yaml").write_text(CONFIG, encoding="utf-8")
    before = snapshot(tmp_path)

    errors = pairs(tmp_path)

    assert (".proofline", "topology.directory.missing") in errors
    assert (".proofline/lines", "topology.directory.missing") in errors
    assert (".proofline/criteria", "topology.directory.missing") in errors
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "relative",
    [".proofline", ".proofline/lines", ".proofline/criteria"],
)
def test_required_topology_rejects_file_instead_of_directory(
    tmp_path: Path, relative: str
) -> None:
    (tmp_path / "proofline.yaml").write_text(CONFIG, encoding="utf-8")
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("wrong type", encoding="utf-8")

    assert (relative, "topology.directory.type") in pairs(tmp_path)


@pytest.mark.parametrize(
    "relative",
    [".proofline", ".proofline/lines", ".proofline/criteria"],
)
def test_required_topology_rejects_symlink(tmp_path: Path, relative: str) -> None:
    (tmp_path / "proofline.yaml").write_text(CONFIG, encoding="utf-8")
    outside = tmp_path / "outside" / relative.replace("/", "-")
    outside.mkdir(parents=True)
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside, target_is_directory=True)

    assert (relative, "topology.directory.symlink") in pairs(tmp_path)


def test_artifact_root_symlink_is_not_traversed(tmp_path: Path) -> None:
    (tmp_path / "proofline.yaml").write_text(CONFIG, encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "mystery.md").write_text("not an artifact", encoding="utf-8")
    (tmp_path / ".proofline").symlink_to(outside, target_is_directory=True)

    errors = pairs(tmp_path)

    assert (".proofline", "topology.directory.symlink") in errors
    assert all(path != ".proofline/mystery.md" for path, _ in errors)


def test_artifact_file_symlink_is_not_read(tmp_path: Path) -> None:
    project = make_minimal(tmp_path / "project")
    outside = tmp_path / "outside.md"
    outside.write_text("not canonical", encoding="utf-8")
    artifact = project / ".proofline/criteria/ac-0001.md"
    artifact.symlink_to(outside)

    errors = pairs(project)

    assert (
        ".proofline/criteria/ac-0001.md",
        "topology.support.unsupported",
    ) in errors
    assert all(
        not (path == ".proofline/criteria/ac-0001.md" and code.startswith("artifact."))
        for path, code in errors
    )


def test_only_two_canonical_gitkeep_support_markers_are_allowed(tmp_path: Path) -> None:
    project = make_minimal(tmp_path)
    (project / ".proofline/lines/.gitkeep").write_bytes(b"")
    (project / ".proofline/criteria/.gitkeep").write_bytes(b"")

    assert validate_project(project) == []

    unsupported = project / ".proofline/.gitkeep"
    unsupported.write_bytes(b"")
    assert (".proofline/.gitkeep", "topology.support.unsupported") in pairs(project)


def test_gitkeep_marker_must_be_regular_zero_byte_file(tmp_path: Path) -> None:
    project = make_minimal(tmp_path)
    marker = project / ".proofline/lines/.gitkeep"
    marker.write_bytes(b"not empty")

    assert (
        ".proofline/lines/.gitkeep",
        "topology.support.invalid",
    ) in pairs(project)


def test_unrecognized_project_support_file_is_rejected(tmp_path: Path) -> None:
    project = make_minimal(tmp_path)
    support = project / ".proofline/criteria/README.txt"
    support.write_text("not canonical", encoding="utf-8")

    assert (
        ".proofline/criteria/README.txt",
        "topology.support.unsupported",
    ) in pairs(project)
