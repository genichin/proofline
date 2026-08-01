from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from proofline.line_writer import LineInitError, initialize_line


ROOT = Path(__file__).resolve().parents[1]
PROOFLINE = shutil.which("proofline")


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    assert PROOFLINE is not None
    return subprocess.run(
        [PROOFLINE, *args], cwd=cwd, text=True, capture_output=True, check=False
    )


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=True
    )


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".proofline" / "lines").mkdir(parents=True)
    (project / ".proofline" / "criteria").mkdir()
    (project / "proofline.yaml").write_text(
        "schema_version: 1\nartifact_root: .proofline\n"
    )
    git("init", "-q", cwd=project)
    git("config", "user.email", "proofline@example.invalid", cwd=project)
    git("config", "user.name", "ProofLine Test", cwd=project)
    git("add", ".", cwd=project)
    git("commit", "-qm", "Initial project", cwd=project)
    return project


def test_line_init_creates_valid_line_and_discovery(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    result = run(
        "line", "init", "line-0007", "--title", "캐시 일관성 조사", cwd=project
    )

    assert result.returncode == 0, result.stderr
    line = project / ".proofline/lines/line-0007/line-0007.md"
    discovery = project / ".proofline/lines/line-0007/dcy-0007.md"
    assert yaml.safe_load(line.read_text().split("---", 2)[1]) == {
        "id": "line-0007",
        "execution_status": "not_started",
    }
    text = discovery.read_text()
    assert "id: \"dcy-0007\"" in text
    assert "status: draft" in text
    assert "# 캐시 일관성 조사" in text
    assert "{{LINE_ID}}" not in text
    assert "{{DISCOVERY_ID}}" not in text
    assert "{{TITLE}}" not in text
    assert run("validate", cwd=project).returncode == 0


def test_line_init_dry_run_does_not_change_tree(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    before = git("status", "--porcelain=v1", "--untracked-files=all", cwd=project).stdout

    result = run(
        "line", "init", "line-0008", "--title", "Dry run", "--dry-run", cwd=project
    )

    assert result.returncode == 0, result.stderr
    assert ".proofline/lines/line-0008/line-0008.md" in result.stdout
    assert ".proofline/lines/line-0008/dcy-0008.md" in result.stdout
    assert not (project / ".proofline/lines/line-0008").exists()
    assert git("status", "--porcelain=v1", "--untracked-files=all", cwd=project).stdout == before


@pytest.mark.parametrize("line_id", ["2", "line-2", "line-00002", "line-abcd"])
def test_line_init_rejects_invalid_id_without_writing(
    tmp_path: Path, line_id: str
) -> None:
    project = make_project(tmp_path)

    result = run("line", "init", line_id, "--title", "Invalid", cwd=project)

    assert result.returncode != 0
    assert "line.id.invalid" in result.stderr
    assert list((project / ".proofline/lines").iterdir()) == []


def test_line_init_rejects_current_path_collision_without_overwrite(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    target = project / ".proofline/lines/line-0009"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("do not overwrite")

    result = run("line", "init", "line-0009", "--title", "Collision", cwd=project)

    assert result.returncode != 0
    assert "line.path.exists" in result.stderr
    assert sentinel.read_text() == "do not overwrite"
    assert sorted(path.name for path in target.iterdir()) == ["keep.txt"]


def test_line_init_rejects_id_seen_in_git_history(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    target = project / ".proofline/lines/line-0010"
    target.mkdir()
    (target / "line-0010.md").write_text("historical")
    git("add", ".", cwd=project)
    git("commit", "-qm", "Use line id", cwd=project)
    shutil.rmtree(target)
    git("add", "-A", cwd=project)
    git("commit", "-qm", "Remove draft", cwd=project)

    result = run("line", "init", "line-0010", "--title", "Reuse", cwd=project)

    assert result.returncode != 0
    assert "line.id.reused" in result.stderr
    assert not target.exists()


@pytest.mark.skipif(os.name == "nt", reason="symlink requires platform privileges")
def test_line_init_rejects_symlink_artifact_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    actual = tmp_path / "actual-artifacts"
    (actual / "lines").mkdir(parents=True)
    (actual / "criteria").mkdir()
    (project / ".proofline").symlink_to(actual, target_is_directory=True)
    (project / "proofline.yaml").write_text(
        "schema_version: 1\nartifact_root: .proofline\n"
    )
    git("init", "-q", cwd=project)
    git("config", "user.email", "proofline@example.invalid", cwd=project)
    git("config", "user.name", "ProofLine Test", cwd=project)

    result = run("line", "init", "line-0011", "--title", "Symlink", cwd=project)

    assert result.returncode != 0
    assert "artifact_root.symlink" in result.stderr
    assert not (actual / "lines/line-0011").exists()


def test_line_init_rejects_empty_title_without_writing(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    result = run("line", "init", "line-0012", "--title", "   ", cwd=project)

    assert result.returncode != 0
    assert "line.title.empty" in result.stderr
    assert list((project / ".proofline/lines").iterdir()) == []


def test_line_init_rejects_multiline_title_without_writing(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    result = run(
        "line", "init", "line-0012", "--title", "Title\nInjected body", cwd=project
    )

    assert result.returncode != 0
    assert "line.title.invalid" in result.stderr
    assert list((project / ".proofline/lines").iterdir()) == []


def test_line_init_cleans_temporary_tree_when_atomic_rename_loses_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)

    def lose_race(source: Path, target: Path) -> None:
        raise FileExistsError(target)

    monkeypatch.setattr("proofline.line_writer.os.rename", lose_race)

    with pytest.raises(LineInitError, match="line.path.exists"):
        initialize_line(project, "line-0013", "Concurrent writer")

    assert not (project / ".proofline/lines/line-0013").exists()
    assert not list(project.glob(".line-0013-*"))
