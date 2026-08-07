from pathlib import Path
import shutil

import pytest

from proofline.validator import validate_project


FIXTURE = Path(__file__).parent / "fixtures" / "valid-minimal"


@pytest.mark.parametrize(
    ("source_path", "old", "new"),
    [
        (".proofline/lines/line-0001/req-0001.md", "discovery: dcy-0001", "discovery: dcy-9999"),
        (".proofline/lines/line-0001/req-0001.md", "    - ac-0001", "    - ac-9999"),
    ],
)
def test_missing_direct_reference_is_rejected(
    tmp_path: Path, source_path: str, old: str, new: str
) -> None:
    project = tmp_path / "project"
    shutil.copytree(FIXTURE, project)
    source = project / source_path
    source.write_text(
        source.read_text(encoding="utf-8").replace(old, new, 1),
        encoding="utf-8",
    )

    errors = validate_project(project)

    assert (source_path, "reference.missing") in [
        (error.path, error.code) for error in errors
    ]


def test_reference_must_use_a_canonical_id(tmp_path: Path) -> None:
    project = tmp_path / "project"
    shutil.copytree(FIXTURE, project)
    source_path = ".proofline/lines/line-0001/req-0001.md"
    source = project / source_path
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "discovery: dcy-0001", "discovery: ../../outside"
        ),
        encoding="utf-8",
    )

    errors = validate_project(project)

    assert (source_path, "reference.invalid") in [
        (error.path, error.code) for error in errors
    ]
