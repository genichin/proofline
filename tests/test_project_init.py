from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from proofline import project_writer
from proofline.identity_allocator import IdentityAllocator, decode_allocator
from proofline.project_writer import ProjectInitError, initialize_project
from proofline.validator import validate_project

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_PATHS = (
    "proofline.yaml",
    ".proofline/identities.json",
    ".proofline/lines/.gitkeep",
    ".proofline/criteria/.gitkeep",
)


def root(tmp_path: Path) -> Path:
    value = tmp_path / "project"
    value.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=value, check=True)
    return value


def snapshot(path: Path) -> dict[str, bytes | str]:
    result = {}
    for item in path.rglob("*"):
        if ".git" in item.relative_to(path).parts:
            continue
        relative = item.relative_to(path).as_posix()
        if item.is_symlink():
            result[relative] = os.readlink(item)
        elif item.is_file():
            result[relative] = item.read_bytes()
    return result


def test_fresh_init_dry_run_actual_and_exact_rerun(tmp_path: Path) -> None:
    project = root(tmp_path)
    assert initialize_project(project, dry_run=True).paths == EXPECTED_PATHS
    assert snapshot(project) == {}
    created = initialize_project(project)
    assert created.status == "created" and created.paths == EXPECTED_PATHS
    assert decode_allocator((project / ".proofline/identities.json").read_bytes()) == IdentityAllocator(1, 1)
    assert validate_project(project) == []
    before = snapshot(project)
    assert initialize_project(project).status == "already-initialized"
    assert snapshot(project) == before


def test_fresh_init_dry_run_stages_outside_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = root(tmp_path)
    real = project_writer.tempfile.mkdtemp
    parents = []

    def record_parent(*args, **kwargs):
        parents.append(kwargs.get("dir"))
        return real(*args, **kwargs)

    monkeypatch.setattr(project_writer.tempfile, "mkdtemp", record_parent)
    initialize_project(project, dry_run=True)
    assert parents == [None]
    assert snapshot(project) == {}


def test_existing_project_migration_plans_and_creates_max_plus_one(tmp_path: Path) -> None:
    project = root(tmp_path)
    (project / ".proofline/lines/line-0028").mkdir(parents=True)
    (project / ".proofline/criteria").mkdir()
    (project / "proofline.yaml").write_text("schema_version: 1\nartifact_root: .proofline\n")
    (project / ".proofline/lines/line-0028/line-0028.md").write_text(
        '---\nid: "line-0028"\n---\n'
    )
    (project / ".proofline/criteria/ac-0024.md").write_text(
        '---\nid: "ac-0024"\nstatus: active\n---\n\n# Existing\n\n## Criterion\n\nExisting behavior.\n\n## Verification\n\n- Existing check.\n'
    )
    legacy = project / ".proofline/line-identities.json"
    legacy.write_bytes(b"opaque and deliberately malformed\n")
    before = snapshot(project)
    planned = initialize_project(project, dry_run=True)
    assert planned.paths == (".proofline/identities.json",)
    assert snapshot(project) == before
    migrated = initialize_project(project)
    assert migrated.status == "migrated"
    assert decode_allocator((project / ".proofline/identities.json").read_bytes()) == IdentityAllocator(29, 25)
    assert legacy.read_bytes() == b"opaque and deliberately malformed\n"


def test_migration_allows_populated_tree_without_gitkeep(tmp_path: Path) -> None:
    project = root(tmp_path)
    (project / ".proofline/lines").mkdir(parents=True)
    (project / ".proofline/criteria").mkdir()
    (project / "proofline.yaml").write_text("schema_version: 1\nartifact_root: .proofline\n")
    assert initialize_project(project).status == "migrated"


def test_migration_rejects_legacy_symlink_without_reading_target(tmp_path: Path) -> None:
    project = root(tmp_path)
    (project / ".proofline/lines").mkdir(parents=True)
    (project / ".proofline/criteria").mkdir()
    (project / "proofline.yaml").write_text("schema_version: 1\nartifact_root: .proofline\n")
    outside = tmp_path / "outside"
    outside.write_bytes(b"sentinel")
    (project / ".proofline/line-identities.json").symlink_to(outside)
    with pytest.raises(ProjectInitError, match="project.scaffold.symlink"):
        initialize_project(project)
    assert outside.read_bytes() == b"sentinel"


def test_fresh_commit_failure_rolls_back_all_owned_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = root(tmp_path)
    real = project_writer._commit_path_at
    calls = 0
    def fail_second(source: Path, descriptor: int, target: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected")
        real(source, descriptor, target)
    monkeypatch.setattr(project_writer, "_commit_path_at", fail_second)
    with pytest.raises(ProjectInitError, match="project.transaction.failed"):
        initialize_project(project)
    assert snapshot(project) == {}


def test_fresh_stage_write_fault_cleans_exact_topology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = root(tmp_path)
    real = Path.write_bytes

    def fail_allocator(path: Path, data: bytes) -> int:
        if path.name == "identities.json" and path.parent.name == ".proofline":
            raise OSError("injected")
        return real(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_allocator)
    with pytest.raises(ProjectInitError, match="project.transaction.failed"):
        initialize_project(project)
    assert snapshot(project) == {}
    assert not list(project.glob(".proofline-project-*"))


def test_fresh_post_validation_fault_rolls_back_scaffold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = root(tmp_path)
    real = project_writer.validate_project
    calls = 0

    def fail_post(path: Path):
        nonlocal calls
        calls += 1
        if calls == 2:
            from proofline.validator import ValidationError

            return [ValidationError(".", "injected", "failure")]
        return real(path)

    monkeypatch.setattr(project_writer, "validate_project", fail_post)
    with pytest.raises(ProjectInitError, match="project.transaction.invalid"):
        initialize_project(project)
    assert snapshot(project) == {}


def test_fresh_stage_cleanup_fault_rolls_back_scaffold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = root(tmp_path)
    real = project_writer.remove_owned_tree
    failed = False

    def fail_stage_once(parent_fd, name, *args, **kwargs):
        nonlocal failed
        if not failed and name.startswith(".proofline-project-"):
            failed = True
            raise OSError("injected")
        return real(parent_fd, name, *args, **kwargs)

    monkeypatch.setattr(project_writer, "remove_owned_tree", fail_stage_once)
    with pytest.raises(ProjectInitError, match="project.transaction.failed"):
        initialize_project(project)
    assert snapshot(project) == {}


def test_project_init_requires_exact_git_root(tmp_path: Path) -> None:
    project = root(tmp_path)
    child = project / "child"
    child.mkdir()
    with pytest.raises(ProjectInitError, match="git.root.mismatch"):
        initialize_project(child)


def test_concurrent_project_migrations_share_repository_lock(tmp_path: Path) -> None:
    project = root(tmp_path)
    (project / ".proofline/lines").mkdir(parents=True)
    (project / ".proofline/criteria").mkdir()
    (project / "proofline.yaml").write_text("schema_version: 1\nartifact_root: .proofline\n")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    command = [sys.executable, "-c", "from pathlib import Path; from proofline.project_writer import initialize_project; print(initialize_project(Path.cwd()).status)"]
    processes = [
        subprocess.Popen(command, cwd=project, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(2)
    ]
    results = [process.communicate(timeout=30) for process in processes]
    assert [process.returncode for process in processes] == [0, 0], results
    assert {stdout.strip() for stdout, _ in results} == {"migrated", "already-initialized"}
    assert decode_allocator((project / ".proofline/identities.json").read_bytes()) == IdentityAllocator(1, 1)
