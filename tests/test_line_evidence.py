from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from proofline.validator import validate_project

CONFIG = "schema_version: 1\nartifact_root: .proofline\n"


def make_project(root: Path) -> Path:
    line = root / ".proofline/lines/line-0001"
    line.mkdir(parents=True)
    (root / ".proofline/criteria").mkdir()
    (root / "proofline.yaml").write_text(CONFIG, encoding="utf-8")
    (root / ".proofline/identities.json").write_text(
        '{\n  "schema_version": 1,\n  "next_line_number": 2,\n  "next_ac_number": 1\n}\n',
        encoding="utf-8",
    )
    (line / "line-0001.md").write_text("---\nid: line-0001\n---\n", encoding="utf-8")
    return root


def errors(root: Path) -> list[tuple[str, str]]:
    return [(error.path, error.code) for error in validate_project(root)]


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


def test_line_evidence_markdown_is_valid_and_noncanonical(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    evidence = project / ".proofline/lines/line-0001/evidence"
    evidence.mkdir()
    (evidence / "ac-9999.md").write_text(
        "---\nid: ac-9999\nstatus: active\n---\n\n"
        "# approved delivered released PASS\n\n{{TODO}}\n",
        encoding="utf-8",
    )
    before = snapshot(project)

    assert validate_project(project) == []
    assert snapshot(project) == before


def test_line_root_arbitrary_markdown_remains_invalid(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    path = project / ".proofline/lines/line-0001/progress.md"
    path.write_text("progress\n", encoding="utf-8")

    assert (path.relative_to(project).as_posix(), "artifact.path") in errors(project)


@pytest.mark.parametrize("entry", ("nested", "note.txt"))
def test_evidence_rejects_nested_directory_and_non_markdown(
    tmp_path: Path, entry: str
) -> None:
    project = make_project(tmp_path)
    evidence = project / ".proofline/lines/line-0001/evidence"
    evidence.mkdir()
    target = evidence / entry
    if entry == "nested":
        target.mkdir()
    else:
        target.write_text("not markdown", encoding="utf-8")

    assert (
        target.relative_to(project).as_posix(),
        "topology.support.unsupported",
    ) in errors(project)


def test_evidence_rejects_symlink_without_reading_target(tmp_path: Path) -> None:
    project = make_project(tmp_path / "project")
    evidence = project / ".proofline/lines/line-0001/evidence"
    evidence.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("sentinel", encoding="utf-8")
    target = evidence / "linked.md"
    target.symlink_to(outside)

    assert (
        target.relative_to(project).as_posix(),
        "topology.support.unsupported",
    ) in errors(project)
    assert outside.read_text(encoding="utf-8") == "sentinel"


def test_evidence_rejects_directory_symlink_without_traversal(tmp_path: Path) -> None:
    project = make_project(tmp_path / "project")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "claim.md").write_text("approved\n", encoding="utf-8")
    evidence = project / ".proofline/lines/line-0001/evidence"
    evidence.symlink_to(outside, target_is_directory=True)

    assert (
        evidence.relative_to(project).as_posix(),
        "topology.support.unsupported",
    ) in errors(project)
    assert all("claim.md" not in path for path, _ in errors(project))


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes require POSIX")
def test_evidence_rejects_non_regular_file(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    evidence = project / ".proofline/lines/line-0001/evidence"
    evidence.mkdir()
    target = evidence / "stream.md"
    os.mkfifo(target)

    assert (
        target.relative_to(project).as_posix(),
        "topology.support.unsupported",
    ) in errors(project)


def test_evidence_rejects_invalid_utf8(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    evidence = project / ".proofline/lines/line-0001/evidence"
    evidence.mkdir()
    target = evidence / "invalid.md"
    target.write_bytes(b"\xff\xfe")

    assert (
        target.relative_to(project).as_posix(),
        "evidence.read",
    ) in errors(project)


def test_evidence_does_not_hide_canonical_artifact_error(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    line = project / ".proofline/lines/line-0001"
    evidence = line / "evidence"
    evidence.mkdir()
    (evidence / "claim.md").write_text("approved\n", encoding="utf-8")
    (line / "line-0001.md").write_text("---\nid: line-9999\n---\n", encoding="utf-8")

    assert (
        ".proofline/lines/line-0001/line-0001.md",
        "artifact.id",
    ) in errors(project)


@pytest.fixture(scope="module")
def installed_proofline(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("installed-evidence")
    wheel_dir = root / "dist"
    environment = root / "venv"
    wheel_dir.mkdir()
    built = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(wheel_dir)],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    wheel = next(wheel_dir.glob("proofline-*.whl"))
    subprocess.run(
        ["uv", "venv", "--python", sys.executable, str(environment)],
        text=True,
        capture_output=True,
        check=True,
    )
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(wheel)],
        text=True,
        capture_output=True,
        check=True,
    )
    return environment / ("Scripts/proofline.exe" if os.name == "nt" else "bin/proofline")


def run_installed(executable: Path, project: Path) -> tuple[int, str]:
    completed = subprocess.run(
        [str(executable), "validate"],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode, completed.stderr


def test_installed_wheel_cli_matches_source_evidence_matrix(
    tmp_path: Path, installed_proofline: Path
) -> None:
    valid = make_project(tmp_path / "valid")
    valid_evidence = valid / ".proofline/lines/line-0001/evidence"
    valid_evidence.mkdir()
    (valid_evidence / "claim.md").write_text("approved PASS\n", encoding="utf-8")
    assert validate_project(valid) == []
    assert run_installed(installed_proofline, valid) == (0, "")

    invalid_utf8 = make_project(tmp_path / "invalid-utf8")
    invalid_path = invalid_utf8 / ".proofline/lines/line-0001/evidence/invalid.md"
    invalid_path.parent.mkdir()
    invalid_path.write_bytes(b"\xff\xfe")
    expected = (invalid_path.relative_to(invalid_utf8).as_posix(), "evidence.read")
    assert expected in errors(invalid_utf8)
    code, stderr = run_installed(installed_proofline, invalid_utf8)
    assert code == 1
    assert f"{expected[0]}: {expected[1]}:" in stderr

    linked = make_project(tmp_path / "linked")
    outside = tmp_path / "outside-installed"
    outside.mkdir()
    linked_path = linked / ".proofline/lines/line-0001/evidence"
    linked_path.symlink_to(outside, target_is_directory=True)
    expected = (linked_path.relative_to(linked).as_posix(), "topology.support.unsupported")
    assert expected in errors(linked)
    code, stderr = run_installed(installed_proofline, linked)
    assert code == 1
    assert f"{expected[0]}: {expected[1]}:" in stderr

    if hasattr(os, "mkfifo"):
        special = make_project(tmp_path / "special")
        special_path = special / ".proofline/lines/line-0001/evidence/stream.md"
        special_path.parent.mkdir()
        os.mkfifo(special_path)
        expected = (
            special_path.relative_to(special).as_posix(),
            "topology.support.unsupported",
        )
        assert expected in errors(special)
        code, stderr = run_installed(installed_proofline, special)
        assert code == 1
        assert f"{expected[0]}: {expected[1]}:" in stderr
