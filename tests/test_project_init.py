from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from proofline.project_writer import ProjectInitError, initialize_project
from proofline.validator import ValidationError

PROOFLINE = shutil.which("proofline")
ROOT = Path(__file__).resolve().parents[1]
EXPECTED_CONFIG = b"schema_version: 1\nartifact_root: .proofline\n"
EXPECTED_PATHS = (
    "proofline.yaml",
    ".proofline/lines/.gitkeep",
    ".proofline/criteria/.gitkeep",
)


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=True
    )


def make_git_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    git("init", "-q", "-b", "main", cwd=root)
    return root


def run_cli(
    *args: str, cwd: Path, home: Path
) -> subprocess.CompletedProcess[str]:
    assert PROOFLINE is not None
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [PROOFLINE, *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def snapshot(root: Path) -> dict[str, tuple[str, bytes | str]]:
    result: dict[str, tuple[str, bytes | str]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        if path.is_symlink():
            result[relative] = ("symlink", os.readlink(path))
        elif path.is_dir():
            result[relative] = ("directory", "")
        elif path.is_file():
            result[relative] = ("file", path.read_bytes())
    return result


def assert_scaffold(root: Path) -> None:
    assert (root / "proofline.yaml").read_bytes() == EXPECTED_CONFIG
    assert (root / ".proofline/lines/.gitkeep").read_bytes() == b""
    assert (root / ".proofline/criteria/.gitkeep").read_bytes() == b""


def test_project_init_dry_run_and_create_are_deterministic(tmp_path: Path) -> None:
    root = make_git_root(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    before_home = snapshot(home)

    dry_run = run_cli("project", "init", "--dry-run", cwd=root, home=home)

    assert dry_run.returncode == 0, dry_run.stderr
    assert all(path in dry_run.stdout for path in EXPECTED_PATHS)
    assert snapshot(root) == {}
    assert snapshot(home) == before_home

    created = run_cli("project", "init", cwd=root, home=home)

    assert created.returncode == 0, created.stderr
    assert all(path in created.stdout for path in EXPECTED_PATHS)
    assert_scaffold(root)
    assert snapshot(home) == before_home


def test_project_init_exact_rerun_is_noop(tmp_path: Path) -> None:
    root = make_git_root(tmp_path)
    first = initialize_project(root)
    before = snapshot(root)

    second = initialize_project(root)

    assert first.status == "created"
    assert second.status == "already-initialized"
    assert second.paths == EXPECTED_PATHS
    assert snapshot(root) == before


def test_project_init_exact_dry_run_reports_noop(tmp_path: Path) -> None:
    root = make_git_root(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    initialize_project(root)

    result = run_cli("project", "init", "--dry-run", cwd=root, home=home)

    assert result.returncode == 0, result.stderr
    assert "already-initialized: proofline.yaml" in result.stdout
    assert "would create" not in result.stdout


@pytest.mark.parametrize(
    ("relative", "content"),
    [
        ("proofline.yaml", b"wrong\n"),
        (".proofline/lines/.gitkeep", b"not empty"),
        (".proofline/criteria", b"file instead of directory"),
    ],
)
def test_project_init_rejects_partial_or_mismatch_without_mutation(
    tmp_path: Path, relative: str, content: bytes
) -> None:
    root = make_git_root(tmp_path)
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    before = snapshot(root)

    with pytest.raises(ProjectInitError, match="project.scaffold.conflict"):
        initialize_project(root)

    assert snapshot(root) == before
    assert not list(root.glob(".proofline-project-*"))


def test_project_init_rejects_symlink_without_touching_target(tmp_path: Path) -> None:
    root = make_git_root(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"sentinel")
    (root / "proofline.yaml").symlink_to(outside)
    before = snapshot(root)

    with pytest.raises(ProjectInitError, match="project.scaffold.symlink"):
        initialize_project(root)

    assert outside.read_bytes() == b"sentinel"
    assert snapshot(root) == before


def test_project_init_rejects_artifact_root_symlink_before_descendant_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_git_root(tmp_path)
    outside = tmp_path / "outside-tree"
    (outside / "lines").mkdir(parents=True)
    (outside / "criteria").mkdir()
    (outside / "sentinel").write_bytes(b"external")
    (root / ".proofline").symlink_to(outside, target_is_directory=True)
    from proofline import project_writer

    real_path_state = project_writer._path_state

    def reject_descendant_lookup(path: Path) -> os.stat_result | None:
        if path != root / ".proofline" and root / ".proofline" in path.parents:
            raise AssertionError("artifact-root descendant was inspected")
        return real_path_state(path)

    monkeypatch.setattr(project_writer, "_path_state", reject_descendant_lookup)

    with pytest.raises(ProjectInitError, match="project.scaffold.symlink"):
        initialize_project(root)

    assert (outside / "sentinel").read_bytes() == b"external"


@pytest.mark.parametrize("child", ["lines", "criteria"])
def test_project_init_rejects_child_symlink_before_marker_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, child: str
) -> None:
    root = make_git_root(tmp_path)
    (root / ".proofline").mkdir()
    real_child = "criteria" if child == "lines" else "lines"
    (root / ".proofline" / real_child).mkdir()
    outside = tmp_path / f"outside-{child}"
    outside.mkdir()
    (outside / ".gitkeep").write_bytes(b"external")
    (root / ".proofline" / child).symlink_to(outside, target_is_directory=True)
    from proofline import project_writer

    real_path_state = project_writer._path_state
    marker = root / ".proofline" / child / ".gitkeep"

    def reject_marker_lookup(path: Path) -> os.stat_result | None:
        if path == marker:
            raise AssertionError("child-symlink marker was inspected")
        return real_path_state(path)

    monkeypatch.setattr(project_writer, "_path_state", reject_marker_lookup)

    with pytest.raises(ProjectInitError, match="project.scaffold.symlink"):
        initialize_project(root)

    assert (outside / ".gitkeep").read_bytes() == b"external"


def test_project_init_requires_exact_git_root(tmp_path: Path) -> None:
    root = make_git_root(tmp_path)
    nested = root / "nested"
    nested.mkdir()

    with pytest.raises(ProjectInitError, match="git.root.mismatch"):
        initialize_project(nested)

    non_git = tmp_path / "non-git"
    non_git.mkdir()
    with pytest.raises(ProjectInitError, match="git.repository.required"):
        initialize_project(non_git)


def test_project_init_rejects_malformed_packaged_resource_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_git_root(tmp_path)
    monkeypatch.setattr(
        "proofline.project_writer._resource_bytes",
        lambda relative: b"schema_version: 2\nartifact_root: .proofline\n"
        if relative == "proofline.yaml"
        else b"",
    )

    with pytest.raises(ProjectInitError, match="resource.malformed"):
        initialize_project(root)

    assert snapshot(root) == {}


def test_project_init_rejects_missing_packaged_resource_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_git_root(tmp_path)

    def missing(relative: str) -> bytes:
        raise FileNotFoundError(relative)

    monkeypatch.setattr("proofline.project_writer._resource_bytes", missing)

    with pytest.raises(ProjectInitError, match="resource.missing"):
        initialize_project(root)

    assert snapshot(root) == {}


def test_project_init_reports_staging_permission_failure_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_git_root(tmp_path)

    def denied(*args: object, **kwargs: object) -> str:
        raise PermissionError("injected permission failure")

    monkeypatch.setattr("proofline.project_writer.tempfile.mkdtemp", denied)

    with pytest.raises(ProjectInitError, match="project.prepare.failed"):
        initialize_project(root)

    assert snapshot(root) == {}


def test_project_init_rolls_back_owned_paths_after_commit_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_git_root(tmp_path)
    from proofline import project_writer

    real_commit = project_writer._commit_path
    calls = 0

    def fail_second(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected commit failure")
        real_commit(source, target)

    monkeypatch.setattr(project_writer, "_commit_path", fail_second)

    with pytest.raises(ProjectInitError, match="project.commit.failed"):
        initialize_project(root)

    assert snapshot(root) == {}
    assert not list(root.glob(".proofline-project-*"))


def test_project_init_preserves_concurrent_external_target_and_cleans_owned_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_git_root(tmp_path)
    from proofline import project_writer

    real_commit = project_writer._commit_path
    calls = 0

    def race_on_second(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            target.mkdir()
            (target / "external.txt").write_text("external", encoding="utf-8")
            raise FileExistsError(target)
        real_commit(source, target)

    monkeypatch.setattr(project_writer, "_commit_path", race_on_second)

    with pytest.raises(ProjectInitError, match="project.commit.conflict"):
        initialize_project(root)

    assert not (root / "proofline.yaml").exists()
    assert (root / ".proofline/external.txt").read_text(encoding="utf-8") == "external"
    assert not list(root.glob(".proofline-project-*"))


def test_project_init_preserves_config_replaced_by_concurrent_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_git_root(tmp_path)
    from proofline import project_writer

    real_commit = project_writer._commit_path
    calls = 0

    def replace_after_first(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            real_commit(source, target)
            target.unlink()
            target.write_bytes(b"external config\n")
            return
        target.mkdir()
        (target / "external.txt").write_text("external", encoding="utf-8")
        raise FileExistsError(target)

    monkeypatch.setattr(project_writer, "_commit_path", replace_after_first)

    with pytest.raises(ProjectInitError, match="project.rollback.ownership"):
        initialize_project(root)

    assert (root / "proofline.yaml").read_bytes() == b"external config\n"
    assert (root / ".proofline/external.txt").read_text(encoding="utf-8") == "external"
    assert not list(root.glob(".proofline-project-*"))


def test_project_init_exact_state_rejects_unsupported_support_path(tmp_path: Path) -> None:
    root = make_git_root(tmp_path)
    initialize_project(root)
    (root / ".proofline/unsupported.bin").write_bytes(b"unsupported")
    before = snapshot(root)

    with pytest.raises(ProjectInitError, match="project.scaffold.invalid"):
        initialize_project(root)

    assert snapshot(root) == before


def test_project_init_dry_run_and_actual_share_capability_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_git_root(tmp_path)
    monkeypatch.setattr("proofline.project_writer.os.access", lambda *args: False)

    for dry_run in (True, False):
        with pytest.raises(ProjectInitError, match="project.permission.denied"):
            initialize_project(root, dry_run=dry_run)

    assert snapshot(root) == {}


def test_project_init_preserves_replaced_stage_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_git_root(tmp_path)
    from proofline import project_writer

    real_commit = project_writer._commit_path
    calls = 0

    def replace_stage_on_second(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            real_commit(source, target)
            return
        stage = source.parent
        moved = root / "externally-moved-stage"
        stage.rename(moved)
        stage.mkdir()
        (stage / "external.txt").write_text("external", encoding="utf-8")
        raise FileExistsError(target)

    monkeypatch.setattr(project_writer, "_commit_path", replace_stage_on_second)

    with pytest.raises(ProjectInitError, match="project.cleanup.ownership"):
        initialize_project(root)

    replacements = list(root.glob(".proofline-project-*"))
    assert len(replacements) == 1
    assert (replacements[0] / "external.txt").read_text(encoding="utf-8") == "external"
    assert (root / "externally-moved-stage").is_dir()


def test_project_init_ignores_absent_or_mismatched_home(tmp_path: Path) -> None:
    for name, create_mismatch in (("absent", False), ("mismatch", True)):
        root = tmp_path / name
        root.mkdir()
        git("init", "-q", cwd=root)
        home = tmp_path / f"home-{name}"
        home.mkdir()
        if create_mismatch:
            (home / ".proofline").mkdir()
            (home / ".proofline/manifest.yaml").write_text("wrong: true\n")
        before = snapshot(home)

        result = run_cli("project", "init", cwd=root, home=home)

        assert result.returncode == 0, result.stderr
        assert_scaffold(root)
        assert snapshot(home) == before


def test_project_init_dry_run_and_actual_share_staged_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from proofline import project_writer

    monkeypatch.setattr(
        project_writer,
        "validate_project",
        lambda root: [ValidationError("proofline.yaml", "test.invalid", "invalid")],
    )
    for name, dry_run in (("dry", True), ("actual", False)):
        case = tmp_path / name
        case.mkdir()
        root = make_git_root(case)
        with pytest.raises(ProjectInitError, match="project.prepare.invalid"):
            initialize_project(root, dry_run=dry_run)
        assert snapshot(root) == {}
        assert not list(root.glob(".proofline-project-*"))


def test_project_init_identity_failure_cleans_new_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_git_root(tmp_path)
    from proofline import project_writer

    def fail_identity(path: Path) -> tuple[int, int]:
        raise OSError("identity failed")

    monkeypatch.setattr(project_writer, "_identity", fail_identity)

    with pytest.raises(ProjectInitError, match="project.prepare.failed"):
        initialize_project(root)

    assert snapshot(root) == {}
    assert not list(root.glob(".proofline-project-*"))


def test_project_init_finalize_cleanup_failure_rolls_back_scaffold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_git_root(tmp_path)
    from proofline import project_writer

    real_cleanup = project_writer._cleanup_stage
    calls = 0

    def fail_once(stage: Path, identity: tuple[int, int]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProjectInitError("project.cleanup.failed", stage.name, "injected")
        real_cleanup(stage, identity)

    monkeypatch.setattr(project_writer, "_cleanup_stage", fail_once)

    with pytest.raises(ProjectInitError, match="project.transaction.finalize"):
        initialize_project(root)

    assert snapshot(root) == {}
    assert not list(root.glob(".proofline-project-*"))


def test_source_checkout_outside_project_init_validate_and_line_init(
    tmp_path: Path,
) -> None:
    root = make_git_root(tmp_path)
    home = tmp_path / "source-home"
    home.mkdir()
    git_before = (
        git("symbolic-ref", "HEAD", cwd=root).stdout,
        git("config", "--local", "--list", cwd=root).stdout,
        git("for-each-ref", "--format=%(refname):%(objectname)", cwd=root).stdout,
    )

    initialized = run_cli("project", "init", cwd=root, home=home)
    validated = run_cli("validate", cwd=root, home=home)
    line = run_cli(
        "line", "init", "line-0001", "--title", "첫 Line", cwd=root, home=home
    )

    assert initialized.returncode == 0, initialized.stderr  # ac-0020
    assert validated.returncode == 0, validated.stderr  # ac-0001
    assert line.returncode == 0, line.stderr  # ac-0004
    assert (root / ".proofline/lines/line-0001/line-0001.md").is_file()
    assert (root / ".proofline/lines/line-0001/dcy-0001.md").is_file()
    assert (
        git("symbolic-ref", "HEAD", cwd=root).stdout,
        git("config", "--local", "--list", cwd=root).stdout,
        git("for-each-ref", "--format=%(refname):%(objectname)", cwd=root).stdout,
    ) == git_before
