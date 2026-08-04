from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml

from proofline import line_writer
from proofline.identity_ledger import decode_ledger, encode_ledger
from proofline.line_writer import LineInitError, initialize_line

ROOT = Path(__file__).resolve().parents[1]


def tree_snapshot(root: Path) -> dict[str, tuple[str, bytes | str | None]]:
    snapshot: dict[str, tuple[str, bytes | str | None]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", os.readlink(path))
        elif path.is_dir():
            snapshot[relative] = ("directory", None)
        else:
            snapshot[relative] = ("file", path.read_bytes())
    return snapshot


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from proofline.cli import main; raise SystemExit(main())",
            *args,
        ],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=True
    )


def run_synchronized_writer_race(
    project: Path, tmp_path: Path, competitor_script: str
) -> tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str]]:
    ready = tmp_path / "writer-ready.fifo"
    release = tmp_path / "competitor-ready.fifo"
    os.mkfifo(ready)
    os.mkfifo(release)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    writer_script = """
import sys
from pathlib import Path
from proofline import line_writer
from proofline.line_writer import LineInitError, initialize_line
project, ready, release = map(Path, sys.argv[1:4])
original = line_writer._commit_line_path
def synchronized_commit(source, target, parent_fd):
    with ready.open('w', encoding='utf-8') as stream:
        stream.write('ready')
    with release.open('r', encoding='utf-8') as stream:
        assert stream.read() == 'release'
    original(source, target, parent_fd)
line_writer._commit_line_path = synchronized_commit
try:
    initialize_line(project, 'line-0013', 'Synchronized race')
except LineInitError as exc:
    print(f'{exc.code}|{exc.message}')
    raise SystemExit(7)
raise SystemExit(0)
"""
    writer = subprocess.Popen(
        [sys.executable, "-c", writer_script, str(project), str(ready), str(release)],
        cwd=project,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    competitor = subprocess.Popen(
        [
            sys.executable,
            "-c",
            competitor_script,
            str(project),
            str(ready),
            str(release),
        ],
        cwd=project,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    writer_stdout, writer_stderr = writer.communicate(timeout=30)
    competitor_stdout, competitor_stderr = competitor.communicate(timeout=30)
    return (
        subprocess.CompletedProcess(
            writer.args, writer.returncode, writer_stdout, writer_stderr
        ),
        subprocess.CompletedProcess(
            competitor.args,
            competitor.returncode,
            competitor_stdout,
            competitor_stderr,
        ),
    )


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".proofline" / "lines").mkdir(parents=True)
    (project / ".proofline" / "criteria").mkdir()
    (project / "proofline.yaml").write_text(
        "schema_version: 1\nartifact_root: .proofline\n"
    )
    git("init", "-q", "-b", "main", cwd=project)
    git("config", "user.email", "proofline@example.invalid", cwd=project)
    git("config", "user.name", "ProofLine Test", cwd=project)
    git("add", ".", cwd=project)
    git("commit", "-qm", "Initial project", cwd=project)
    return project


def test_line_init_wraps_git_spawn_failure_as_typed_unavailable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)

    def fail_to_spawn(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("git executable unavailable")

    monkeypatch.setattr(line_writer.subprocess, "run", fail_to_spawn)

    with pytest.raises(LineInitError) as raised:
        initialize_line(project, "line-0007", "Git unavailable")

    assert raised.value.code == "git.repository.unavailable"
    assert raised.value.path == "."
    assert not (project / ".proofline/lines/line-0007").exists()


def test_line_init_wraps_git_output_decode_failure_as_typed_unavailable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)

    def fail_to_decode(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(line_writer.subprocess, "run", fail_to_decode)

    with pytest.raises(LineInitError) as raised:
        initialize_line(project, "line-0007", "Git output unavailable")

    assert raised.value.code == "git.repository.unavailable"
    assert raised.value.path == "."
    assert not (project / ".proofline/lines/line-0007").exists()


def test_line_init_reports_unavailable_for_malformed_repository_marker(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()

    with pytest.raises(LineInitError) as raised:
        initialize_line(project, "line-0007", "Malformed repository")

    assert raised.value.code == "git.repository.unavailable"
    assert raised.value.path == "."
    assert not (project / ".proofline").exists()


def test_line_init_requires_repository_when_git_marker_is_absent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    with pytest.raises(LineInitError) as raised:
        initialize_line(project, "line-0007", "Not a repository")

    assert raised.value.code == "git.repository.required"
    assert raised.value.path == "."
    assert list(project.iterdir()) == []


def test_line_init_preserves_git_root_mismatch_error(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    child = project / "child"
    child.mkdir()

    with pytest.raises(LineInitError) as raised:
        initialize_line(child, "line-0007", "Wrong root")

    assert raised.value.code == "git.root.mismatch"
    assert raised.value.path == "."
    assert list(child.iterdir()) == []


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
        "implementation_history": "first_parent",
    }
    text = discovery.read_text()
    assert "id: \"dcy-0007\"" in text
    assert "status: draft" in text
    assert "# 캐시 일관성 조사" in text
    assert "{{LINE_ID}}" not in text
    assert "{{DISCOVERY_ID}}" not in text
    assert "{{TITLE}}" not in text
    assert run("validate", cwd=project).returncode == 0


def test_line_init_fresh_bootstrap_commits_ledger_and_matching_pair(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    result = run("line", "init", "line-0007", "--title", "Fresh allocation", cwd=project)

    assert result.returncode == 0, result.stderr
    ledger = project / ".proofline/line-identities.json"
    assert decode_ledger(ledger.read_bytes()).allocated_line_ids == ("line-0007",)
    assert (project / ".proofline/lines/line-0007/line-0007.md").is_file()
    assert (project / ".proofline/lines/line-0007/dcy-0007.md").is_file()
    assert not list(project.glob(".proofline-ledger-*"))


def test_line_init_appends_existing_ledger_without_losing_prior_allocation(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    (project / ".proofline/line-identities.json").write_bytes(encode_ledger(set()))
    git("add", ".proofline/line-identities.json", cwd=project)
    git("commit", "-qm", "Adopt allocation ledger", cwd=project)

    result = run("line", "init", "line-0007", "--title", "Append allocation", cwd=project)

    assert result.returncode == 0, result.stderr
    assert decode_ledger(
        (project / ".proofline/line-identities.json").read_bytes()
    ).allocated_line_ids == ("line-0007",)
    assert run("validate", cwd=project).returncode == 0


@pytest.mark.parametrize("legacy", [False, True], ids=["fresh", "existing-ledger"])
def test_line_init_dry_run_has_ledger_candidate_parity_without_residue(
    tmp_path: Path, legacy: bool
) -> None:
    project = make_project(tmp_path)
    ledger = project / ".proofline/line-identities.json"
    if legacy:
        ledger.write_bytes(encode_ledger(set()))
        git("add", ".proofline/line-identities.json", cwd=project)
        git("commit", "-qm", "Adopt allocation ledger", cwd=project)
    before = tree_snapshot(project)

    dry = run(
        "line", "init", "line-0007", "--title", "Parity", "--dry-run", cwd=project
    )

    assert dry.returncode == 0, dry.stderr
    assert tree_snapshot(project) == before
    assert not list(project.glob(".line-0007-*"))
    assert not list(project.glob(".proofline-ledger-*"))

    actual = run("line", "init", "line-0007", "--title", "Parity", cwd=project)
    assert actual.returncode == 0, actual.stderr
    assert decode_ledger(ledger.read_bytes()).allocated_line_ids == ("line-0007",)


@pytest.mark.parametrize("checkout", ["topic", "detached"])
def test_line_init_rejects_non_authority_checkout_before_transaction_mutation(
    tmp_path: Path, checkout: str
) -> None:
    project = make_project(tmp_path)
    if checkout == "topic":
        git("switch", "-qc", "topic", cwd=project)
    else:
        git("checkout", "--detach", "-q", cwd=project)
    before = tree_snapshot(project)

    result = run("line", "init", "line-0007", "--title", "Wrong authority", cwd=project)

    assert result.returncode == 1
    assert "ledger.authority.required" in result.stderr
    assert tree_snapshot(project) == before


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

    def lose_race(source: Path, target: Path, _parent_fd: int) -> None:
        raise FileExistsError(target)

    monkeypatch.setattr("proofline.line_writer._commit_line_path", lose_race)

    with pytest.raises(LineInitError, match="line.path.exists"):
        initialize_line(project, "line-0013", "Concurrent writer")

    assert not (project / ".proofline/lines/line-0013").exists()
    assert not list(project.glob(".line-0013-*"))


@pytest.mark.parametrize("target_kind", ["file", "symlink", "empty_dir", "nonempty_dir"])
def test_line_init_preserves_target_kind_created_by_process_at_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
) -> None:
    project = make_project(tmp_path)
    target = project / ".proofline/lines/line-0013"
    external = tmp_path / "external.txt"
    external.write_bytes(b"external")
    original_commit = line_writer._commit_line_path

    def create_target_then_commit(source: Path, destination: Path, parent_fd: int) -> None:
        script = (
            "import os,pathlib,sys; "
            "target=pathlib.Path(sys.argv[1]); kind=sys.argv[2]; external=sys.argv[3]; "
            "target.write_bytes(b'competitor') if kind=='file' else "
            "os.symlink(external, target) if kind=='symlink' else "
            "(target.mkdir(), (target/'sentinel').write_bytes(b'competitor')) if kind=='nonempty_dir' else "
            "target.mkdir()"
        )
        subprocess.run(
            [sys.executable, "-c", script, str(target), target_kind, str(external)],
            check=True,
        )
        original_commit(source, destination, parent_fd)

    monkeypatch.setattr(line_writer, "_commit_line_path", create_target_then_commit)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", f"Race {target_kind}")

    assert exc_info.value.code == "line.path.exists"
    if target_kind == "file":
        assert target.read_bytes() == b"competitor"
    elif target_kind == "symlink":
        assert target.is_symlink()
        assert os.readlink(target) == str(external)
        assert external.read_bytes() == b"external"
    elif target_kind == "nonempty_dir":
        assert (target / "sentinel").read_bytes() == b"competitor"
    else:
        assert target.is_dir()
        assert list(target.iterdir()) == []
    assert not list(project.glob(".line-0013-*"))


@pytest.mark.skipif(os.name == "nt", reason="symlink requires platform privileges")
def test_line_init_rejects_parent_replaced_after_preflight_without_external_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    lines_root = project / ".proofline/lines"
    target = lines_root / "line-0013"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"external")
    displaced_lines = tmp_path / "displaced-lines"
    original_exists = Path.exists
    target_absence_checks = 0

    def replace_parent_after_preflight(path: Path) -> bool:
        nonlocal target_absence_checks
        exists = original_exists(path)
        if path == target and not exists:
            target_absence_checks += 1
            if target_absence_checks == 2:
                lines_root.rename(displaced_lines)
                lines_root.symlink_to(outside, target_is_directory=True)
        return exists

    monkeypatch.setattr(Path, "exists", replace_parent_after_preflight)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Parent race")

    assert exc_info.value.code == "lines_root.changed"
    assert lines_root.is_symlink()
    assert displaced_lines.is_dir()
    assert sentinel.read_bytes() == b"external"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel.txt"]
    assert not list(project.glob(".line-0013-*"))


@pytest.mark.skipif(os.name == "nt", reason="symlink requires platform privileges")
def test_line_init_rolls_back_when_parent_is_replaced_at_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    lines_root = project / ".proofline/lines"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"external")
    displaced_lines = tmp_path / "displaced-lines"
    original_commit = line_writer._commit_line_path

    def replace_parent_then_commit(source: Path, destination: Path, parent_fd: int) -> None:
        subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import os,sys; "
                    "os.rename(sys.argv[1], sys.argv[2]); "
                    "os.symlink(sys.argv[3], sys.argv[1], target_is_directory=True)"
                ),
                str(lines_root),
                str(displaced_lines),
                str(outside),
            ],
            check=True,
        )
        original_commit(source, destination, parent_fd)

    monkeypatch.setattr(line_writer, "_commit_line_path", replace_parent_then_commit)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Commit race")

    assert exc_info.value.code == "lines_root.changed"
    assert lines_root.is_symlink()
    assert displaced_lines.is_dir()
    assert sentinel.read_bytes() == b"external"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel.txt"]
    assert not list(project.glob(".line-0013-*"))


@pytest.mark.parametrize("failing_name", ["line-0013.md", "dcy-0013.md"])
def test_line_init_write_failure_is_typed_and_preserves_recursive_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing_name: str
) -> None:
    project = make_project(tmp_path)
    (project / "notes.bin").write_bytes(b"\x00external\xff")
    before = tree_snapshot(project)
    original_write_text = Path.write_text

    def fail_discovery_write(path: Path, data: str, **kwargs: str | None) -> int:
        if path.name == failing_name and path.parent.name.startswith(".line-0013-"):
            raise PermissionError("injected artifact write failure")
        return original_write_text(path, data, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_discovery_write)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Write failure")

    assert exc_info.value.code == "line.write.failed"
    assert tree_snapshot(project) == before
    assert not list(project.glob(".line-0013-*"))


def test_line_init_stage_identity_failure_is_typed_and_cleans_empty_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    before = tree_snapshot(project)
    original_identity = line_writer._directory_identity

    def fail_stage_identity(path: Path) -> tuple[int, int]:
        if path.name.startswith(".line-0013-"):
            raise PermissionError("injected stage identity failure")
        return original_identity(path)

    monkeypatch.setattr(line_writer, "_directory_identity", fail_stage_identity)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Identity failure")

    assert exc_info.value.code == "line.prepare.failed"
    assert tree_snapshot(project) == before
    assert not list(project.glob(".line-0013-*"))


def test_line_init_preserves_primary_error_and_reports_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    before = tree_snapshot(project)
    original_write_text = Path.write_text
    original_unlink = Path.unlink
    cleanup_failures = 0

    def fail_discovery_write(path: Path, data: str, **kwargs: str | None) -> int:
        if path.name == "dcy-0013.md" and path.parent.name.startswith(".line-0013-"):
            raise PermissionError("injected primary write failure")
        return original_write_text(path, data, **kwargs)

    def fail_first_cleanup(path: Path, missing_ok: bool = False) -> None:
        nonlocal cleanup_failures
        if path.name == "line-0013.md" and path.parent.name.startswith(".line-0013-") and cleanup_failures == 0:
            cleanup_failures += 1
            raise PermissionError("injected cleanup failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "write_text", fail_discovery_write)
    monkeypatch.setattr(Path, "unlink", fail_first_cleanup)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Cleanup failure")

    assert exc_info.value.code == "line.write.failed"
    assert "line.cleanup.failed" in exc_info.value.message
    assert cleanup_failures == 1
    assert tree_snapshot(project) == before


def test_line_init_reports_persistent_stage_cleanup_failure_without_masking_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    original_write_text = Path.write_text
    original_unlink = Path.unlink

    def fail_discovery_write(path: Path, data: str, **kwargs: str | None) -> int:
        if path.name == "dcy-0013.md" and path.parent.name.startswith(".line-0013-"):
            raise PermissionError("injected primary write failure")
        return original_write_text(path, data, **kwargs)

    def fail_owned_stage_cleanup(path: Path, missing_ok: bool = False) -> None:
        if path.parent.name.startswith(".line-0013-"):
            raise PermissionError("injected persistent cleanup failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "write_text", fail_discovery_write)
    monkeypatch.setattr(Path, "unlink", fail_owned_stage_cleanup)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Persistent cleanup failure")

    assert exc_info.value.code == "line.write.failed"
    assert "secondary:" in exc_info.value.message
    assert "line.cleanup.failed" in exc_info.value.message
    assert len(list(project.glob(".line-0013-*"))) == 1


def test_line_init_preserves_primary_when_cleanup_inspection_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    original_write_text = Path.write_text
    original_stat = Path.stat
    primary_failed = False

    def fail_discovery_write(path: Path, data: str, **kwargs: str | None) -> int:
        nonlocal primary_failed
        if path.name == "dcy-0013.md" and path.parent.name.startswith(".line-0013-"):
            primary_failed = True
            raise PermissionError("injected primary write failure")
        return original_write_text(path, data, **kwargs)

    def fail_stage_inspection(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if primary_failed and path.name.startswith(".line-0013-"):
            raise PermissionError("injected persistent cleanup inspection failure")
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "write_text", fail_discovery_write)
    monkeypatch.setattr(Path, "stat", fail_stage_inspection)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Cleanup inspection failure")

    assert exc_info.value.code == "line.write.failed"
    assert "secondary:" in exc_info.value.message
    assert "line.cleanup.failed" in exc_info.value.message


def test_line_init_reports_stage_identity_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    original_identity = line_writer._directory_identity
    original_rmdir = Path.rmdir

    def fail_stage_identity(path: Path) -> tuple[int, int]:
        if path.name.startswith(".line-0013-"):
            raise PermissionError("injected stage identity failure")
        return original_identity(path)

    def fail_stage_rmdir(path: Path) -> None:
        if path.name.startswith(".line-0013-"):
            raise PermissionError("injected stage cleanup failure")
        original_rmdir(path)

    monkeypatch.setattr(line_writer, "_directory_identity", fail_stage_identity)
    monkeypatch.setattr(Path, "rmdir", fail_stage_rmdir)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Stage cleanup failure")

    assert exc_info.value.code == "line.prepare.failed"
    assert "line.cleanup.failed" in exc_info.value.message
    assert "staging cleanup 실패" in exc_info.value.message
    assert len(list(project.glob(".line-0013-*"))) == 1


def test_line_init_rejects_missing_commit_capability_before_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    before = tree_snapshot(project)

    def reject_capability(_project_root: Path) -> None:
        raise LineInitError(
            "line.commit.unsupported", ".", "atomic no-replace commit을 지원하지 않습니다."
        )

    def stage_must_not_start(_project_root: Path, _line_id: str) -> tuple[Path, tuple[int, int]]:
        raise AssertionError("staging started before capability preflight")

    monkeypatch.setattr(line_writer, "_require_commit_capability", reject_capability, raising=False)
    monkeypatch.setattr(line_writer, "_new_stage", stage_must_not_start)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Unsupported commit")

    assert exc_info.value.code == "line.commit.unsupported"
    assert tree_snapshot(project) == before


@pytest.mark.parametrize("dry_run", [False, True])
def test_line_init_rejects_unsupported_filesystem_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dry_run: bool,
) -> None:
    project = make_project(tmp_path)
    before = tree_snapshot(project)

    def unsupported_filesystem(_project_root: Path) -> str:
        return "unsupported-test-fs"

    def stage_must_not_start(
        _project_root: Path, _line_id: str
    ) -> tuple[Path, tuple[int, int]]:
        raise AssertionError("staging started before filesystem capability preflight")

    monkeypatch.setattr(line_writer, "_linux_filesystem_type", unsupported_filesystem)
    monkeypatch.setattr(line_writer, "_new_stage", stage_must_not_start)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(
            project,
            "line-0013",
            "Unsupported filesystem",
            dry_run=dry_run,
        )

    assert exc_info.value.code == "line.commit.unsupported"
    assert "unsupported-test-fs" in exc_info.value.message
    assert tree_snapshot(project) == before


def test_source_cli_reports_injected_write_failure_and_cleans_stage(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    before = tree_snapshot(project)
    script = """
from pathlib import Path
from proofline.cli import main
original = Path.write_text
def fail_discovery(path, data, **kwargs):
    if path.name == 'dcy-0013.md' and path.parent.name.startswith('.line-0013-'):
        raise PermissionError('injected source CLI write failure')
    return original(path, data, **kwargs)
Path.write_text = fail_discovery
raise SystemExit(main(['line', 'init', 'line-0013', '--title', 'CLI failure']))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "line.write.failed" in result.stderr
    assert ".proofline/lines/line-0013/line-0013.md" in result.stderr
    assert tree_snapshot(project) == before


def test_line_init_commit_failure_is_typed_and_preserves_recursive_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    before = tree_snapshot(project)

    def fail_commit(_source: Path, _target: Path, _parent_fd: int) -> None:
        raise OSError(5, "injected commit I/O failure")

    monkeypatch.setattr(line_writer, "_commit_line_path", fail_commit)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Commit failure")

    assert exc_info.value.code == "line.commit.failed"
    assert tree_snapshot(project) == before


@pytest.mark.skipif(os.name == "nt", reason="symlink requires platform privileges")
def test_line_init_retries_owned_rollback_and_reports_secondary_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    lines_root = project / ".proofline/lines"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"external")
    displaced_lines = tmp_path / "displaced-lines"
    original_commit = line_writer._commit_line_path
    original_unlink = os.unlink
    rollback_failures = 0
    commit_done = False

    def replace_parent_then_commit(source: Path, destination: Path, parent_fd: int) -> None:
        nonlocal commit_done
        lines_root.rename(displaced_lines)
        lines_root.symlink_to(outside, target_is_directory=True)
        original_commit(source, destination, parent_fd)
        commit_done = True

    def fail_first_rollback(
        path: str | bytes,
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal rollback_failures
        if (
            commit_done
            and path == "line-0013.md"
            and dir_fd is not None
            and rollback_failures == 0
        ):
            rollback_failures += 1
            raise PermissionError("injected rollback failure")
        original_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(line_writer, "_commit_line_path", replace_parent_then_commit)
    monkeypatch.setattr(os, "unlink", fail_first_rollback)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Rollback failure")

    assert exc_info.value.code == "lines_root.changed"
    assert "line.rollback.failed" in exc_info.value.message
    assert rollback_failures == 1
    assert sentinel.read_bytes() == b"external"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel.txt"]


@pytest.mark.skipif(os.name == "nt", reason="dirfd and symlink semantics are POSIX-specific")
def test_line_init_reports_persistent_anchored_rollback_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    lines_root = project / ".proofline/lines"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"external")
    displaced_lines = tmp_path / "displaced-lines"
    original_commit = line_writer._commit_line_path
    original_unlink = os.unlink
    commit_done = False

    def replace_parent_then_commit(source: Path, destination: Path, parent_fd: int) -> None:
        nonlocal commit_done
        lines_root.rename(displaced_lines)
        lines_root.symlink_to(outside, target_is_directory=True)
        original_commit(source, destination, parent_fd)
        commit_done = True

    def fail_owned_rollback(
        path: str | bytes,
        *,
        dir_fd: int | None = None,
    ) -> None:
        if commit_done and dir_fd is not None and path == "line-0013.md":
            raise PermissionError("injected persistent rollback failure")
        original_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(line_writer, "_commit_line_path", replace_parent_then_commit)
    monkeypatch.setattr(os, "unlink", fail_owned_rollback)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Persistent rollback failure")

    assert exc_info.value.code == "lines_root.changed"
    assert "secondary:" in exc_info.value.message
    assert "line.rollback.failed" in exc_info.value.message
    assert sentinel.read_bytes() == b"external"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel.txt"]
    assert (displaced_lines / "line-0013/line-0013.md").is_file()


@pytest.mark.skipif(os.name == "nt", reason="dirfd and symlink semantics are POSIX-specific")
def test_line_init_rolls_back_through_original_parent_after_postcommit_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    lines_root = project / ".proofline/lines"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"external")
    displaced_lines = tmp_path / "displaced-lines"
    original_commit = line_writer._commit_line_path

    def commit_then_replace_parent(source: Path, destination: Path, parent_fd: int) -> None:
        original_commit(source, destination, parent_fd)
        lines_root.rename(displaced_lines)
        lines_root.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(line_writer, "_commit_line_path", commit_then_replace_parent)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Postcommit parent race")

    assert exc_info.value.code == "lines_root.changed"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel.txt"]
    assert sorted(path.name for path in displaced_lines.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="dirfd and symlink semantics are POSIX-specific")
def test_line_init_detects_artifact_root_swap_that_preserves_lines_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    artifact_root = project / ".proofline"
    displaced_artifact_root = tmp_path / "displaced-proofline"
    original_commit = line_writer._commit_line_path

    def replace_ancestor_then_commit(
        source: Path, destination: Path, parent_fd: int
    ) -> None:
        artifact_root.rename(displaced_artifact_root)
        artifact_root.symlink_to(displaced_artifact_root, target_is_directory=True)
        original_commit(source, destination, parent_fd)

    monkeypatch.setattr(line_writer, "_commit_line_path", replace_ancestor_then_commit)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Ancestor race")

    assert exc_info.value.code == "artifact_root.changed"
    assert artifact_root.is_symlink()
    assert sorted(path.name for path in (displaced_artifact_root / "lines").iterdir()) == []


@pytest.mark.parametrize(
    ("helper_name", "expected_code"),
    [("_render", "template.unavailable"), ("_validate_rendered", "render.unavailable")],
)
def test_line_init_preparation_oserror_is_typed_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
    expected_code: str,
) -> None:
    project = make_project(tmp_path)
    before = tree_snapshot(project)

    def fail_preparation(*_args: object, **_kwargs: object) -> None:
        raise OSError(5, "injected preparation I/O failure")

    def stage_must_not_start(_project_root: Path, _line_id: str) -> tuple[Path, tuple[int, int]]:
        raise AssertionError("staging started after preparation failure")

    monkeypatch.setattr(line_writer, helper_name, fail_preparation)
    monkeypatch.setattr(line_writer, "_new_stage", stage_must_not_start)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Preparation failure")

    assert exc_info.value.code == expected_code
    assert tree_snapshot(project) == before


def test_line_init_malformed_template_is_typed_before_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    before = tree_snapshot(project)

    def fail_decode(_line_id: str, _title: str) -> tuple[str, str]:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "injected malformed template")

    def stage_must_not_start(_project_root: Path, _line_id: str) -> tuple[Path, tuple[int, int]]:
        raise AssertionError("staging started after malformed template")

    monkeypatch.setattr(line_writer, "_render", fail_decode)
    monkeypatch.setattr(line_writer, "_new_stage", stage_must_not_start)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Malformed template")

    assert exc_info.value.code == "template.malformed"
    assert tree_snapshot(project) == before


def test_line_init_rejects_arbitrary_unresolved_template_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    before = tree_snapshot(project)
    original_read_template = line_writer._read_template

    def template_with_unknown_variable(name: str) -> str:
        text = original_read_template(name)
        if name == "line.md":
            return f"{text}\n{{{{UNKNOWN_VARIABLE}}}}\n"
        return text

    monkeypatch.setattr(line_writer, "_read_template", template_with_unknown_variable)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Unresolved variable")

    assert exc_info.value.code == "template.variable.unresolved"
    assert "{{UNKNOWN_VARIABLE}}" in exc_info.value.message
    assert tree_snapshot(project) == before


def test_line_init_close_failure_rolls_back_owned_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    before = tree_snapshot(project)
    original_open_child = line_writer._open_verified_child
    original_close = os.close
    lines_fd: int | None = None
    close_failures = 0

    def record_lines_fd(
        parent_fd: int,
        name: str,
        expected: tuple[int, int],
        code: str,
        display_path: str,
    ) -> int:
        nonlocal lines_fd
        descriptor = original_open_child(parent_fd, name, expected, code, display_path)
        if name == "lines":
            lines_fd = descriptor
        return descriptor

    def fail_first_lines_close(descriptor: int) -> None:
        nonlocal close_failures
        if descriptor == lines_fd and close_failures == 0:
            close_failures += 1
            raise OSError(5, "injected descriptor close failure")
        original_close(descriptor)

    monkeypatch.setattr(line_writer, "_open_verified_child", record_lines_fd)
    monkeypatch.setattr(os, "close", fail_first_lines_close)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Close failure")

    assert exc_info.value.code == "line.finalize.failed"
    assert close_failures == 1
    assert tree_snapshot(project) == before


def test_line_init_anchor_open_primary_survives_close_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    before = tree_snapshot(project)
    original_open_child = line_writer._open_verified_child
    original_close = os.close
    artifact_fd: int | None = None
    close_attempts: list[int] = []
    failed = False

    def fail_lines_open(
        parent_fd: int,
        name: str,
        expected: tuple[int, int],
        code: str,
        display_path: str,
    ) -> int:
        nonlocal artifact_fd
        if name == "lines":
            raise LineInitError("lines_root.changed", display_path, "injected open failure")
        artifact_fd = original_open_child(parent_fd, name, expected, code, display_path)
        return artifact_fd

    def fail_artifact_close(descriptor: int) -> None:
        nonlocal failed
        if artifact_fd is not None:
            close_attempts.append(descriptor)
        if descriptor == artifact_fd and not failed:
            failed = True
            raise OSError(5, "injected anchor close failure")
        original_close(descriptor)

    monkeypatch.setattr(line_writer, "_open_verified_child", fail_lines_open)
    monkeypatch.setattr(os, "close", fail_artifact_close)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Anchor close")

    assert exc_info.value.code == "lines_root.changed"
    assert "line.finalize.failed" in exc_info.value.message
    assert artifact_fd in close_attempts
    assert len(close_attempts) == 3  # artifact, project root, repository lock
    assert tree_snapshot(project) == before
    assert artifact_fd is not None
    original_close(artifact_fd)


def test_line_init_stage_primary_survives_anchor_close_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    before = tree_snapshot(project)
    original_open_directory = line_writer._open_verified_directory
    original_open_child = line_writer._open_verified_child
    original_close = os.close
    opened: list[int] = []
    close_attempts: list[int] = []
    failed = False
    stage_failed = False

    def record_directory(*args: object, **kwargs: object) -> int:
        descriptor = original_open_directory(*args, **kwargs)  # type: ignore[arg-type]
        opened.append(descriptor)
        return descriptor

    def record_child(*args: object, **kwargs: object) -> int:
        descriptor = original_open_child(*args, **kwargs)  # type: ignore[arg-type]
        opened.append(descriptor)
        return descriptor

    def fail_stage(_root: Path, _line_id: str) -> tuple[Path, tuple[int, int]]:
        nonlocal stage_failed
        stage_failed = True
        raise LineInitError("line.prepare.failed", ".", "injected stage failure")

    def fail_first_close(descriptor: int) -> None:
        nonlocal failed
        if not stage_failed:
            original_close(descriptor)
            return
        close_attempts.append(descriptor)
        if not failed:
            failed = True
            raise OSError(5, "injected anchor close failure")
        original_close(descriptor)

    monkeypatch.setattr(line_writer, "_open_verified_directory", record_directory)
    monkeypatch.setattr(line_writer, "_open_verified_child", record_child)
    monkeypatch.setattr(line_writer, "_new_stage", fail_stage)
    monkeypatch.setattr(os, "close", fail_first_close)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Stage close")

    assert exc_info.value.code == "line.prepare.failed"
    assert "line.finalize.failed" in exc_info.value.message
    assert set(opened).issubset(close_attempts)
    assert len(set(close_attempts) - set(opened)) == 1  # repository lock
    assert tree_snapshot(project) == before
    original_close(close_attempts[0])


@pytest.mark.parametrize("close_kind", ["artifact", "target"])
def test_line_init_rollback_primary_survives_internal_close_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, close_kind: str
) -> None:
    project = make_project(tmp_path)
    lines_root = project / ".proofline/lines"
    displaced = project / "displaced-lines"
    original_commit = line_writer._commit_line_path
    original_open = os.open
    original_close = os.close
    selected_fd: int | None = None
    close_failed = False
    committed = False

    def swap_then_commit(source: Path, destination: Path, parent_fd: int) -> None:
        nonlocal committed
        original_commit(source, destination, parent_fd)
        committed = True
        lines_root.rename(displaced)
        lines_root.mkdir()

    def record_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal selected_fd
        descriptor = original_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]
        name = os.fsdecode(path) if isinstance(path, (str, bytes)) else ""
        if committed and (
            (close_kind == "target" and name == "line-0013")
            or (close_kind == "artifact" and name == "line-0013.md")
        ):
            selected_fd = descriptor
        return descriptor

    def fail_selected_close(descriptor: int) -> None:
        nonlocal close_failed
        if descriptor == selected_fd and not close_failed:
            close_failed = True
            raise OSError(5, "injected rollback close failure")
        original_close(descriptor)

    monkeypatch.setattr(line_writer, "_commit_line_path", swap_then_commit)
    monkeypatch.setattr(os, "open", record_open)
    monkeypatch.setattr(os, "close", fail_selected_close)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Rollback close")

    assert exc_info.value.code == "lines_root.changed"
    assert "line.rollback.failed" in exc_info.value.message
    assert "line.finalize.failed" in exc_info.value.message
    assert close_failed
    assert selected_fd is not None
    assert not (displaced / "line-0013").exists()
    assert list(lines_root.iterdir()) == []
    assert not list(project.glob(".line-0013-*"))
    original_close(selected_fd)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux FIFO race contract")
def test_line_init_synchronized_process_target_appearance_race(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    competitor_script = """
import sys
from pathlib import Path
project, ready, release = map(Path, sys.argv[1:4])
with ready.open('r', encoding='utf-8') as stream:
    assert stream.read() == 'ready'
target = project / '.proofline/lines/line-0013'
target.mkdir()
(target / 'competitor.txt').write_bytes(b'competitor')
with release.open('w', encoding='utf-8') as stream:
    stream.write('release')
"""

    writer, competitor = run_synchronized_writer_race(
        project, tmp_path, competitor_script
    )

    assert competitor.returncode == 0, competitor.stderr
    assert writer.returncode == 7, writer.stderr
    assert writer.stdout.startswith("line.path.exists|")
    target = project / ".proofline/lines/line-0013"
    assert (target / "competitor.txt").read_bytes() == b"competitor"
    assert not list(project.glob(".line-0013-*"))


@pytest.mark.skipif(sys.platform != "linux", reason="Linux FIFO race contract")
def test_line_init_synchronized_process_parent_replacement_race(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_bytes(b"external")
    competitor_script = """
import os
import sys
from pathlib import Path
project, ready, release = map(Path, sys.argv[1:4])
with ready.open('r', encoding='utf-8') as stream:
    assert stream.read() == 'ready'
lines = project / '.proofline/lines'
lines.rename(project / 'displaced-lines')
os.symlink(project.parent / 'outside', lines, target_is_directory=True)
with release.open('w', encoding='utf-8') as stream:
    stream.write('release')
"""

    writer, competitor = run_synchronized_writer_race(
        project, tmp_path, competitor_script
    )

    assert competitor.returncode == 0, competitor.stderr
    assert writer.returncode == 7, writer.stderr
    assert writer.stdout.startswith("lines_root.changed|")
    assert (outside / "sentinel.txt").read_bytes() == b"external"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel.txt"]
    displaced = project / "displaced-lines"
    assert not (displaced / "line-0013").exists()
    assert not list(project.glob(".line-0013-*"))


def test_line_init_rejects_repository_lock_contention_without_residue(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    before = tree_snapshot(project)
    locked = tmp_path / "lock-held.fifo"
    release = tmp_path / "lock-release.fifo"
    os.mkfifo(locked)
    os.mkfifo(release)
    holder_script = """
import fcntl
import os
import sys
from pathlib import Path
project, locked, release = map(Path, sys.argv[1:4])
descriptor = os.open(project / '.git', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
fcntl.flock(descriptor, fcntl.LOCK_EX)
with locked.open('w', encoding='utf-8') as stream:
    stream.write('locked')
with release.open('r', encoding='utf-8') as stream:
    assert stream.read() == 'release'
os.close(descriptor)
"""
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_script, str(project), str(locked), str(release)],
        cwd=project,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    with locked.open("r", encoding="utf-8") as stream:
        assert stream.read() == "locked"
    result = run("line", "init", "line-0013", "--title", "Lock contention", cwd=project)
    with release.open("w", encoding="utf-8") as stream:
        stream.write("release")
    _, holder_stderr = holder.communicate(timeout=30)

    assert holder.returncode == 0, holder_stderr
    assert result.returncode == 1
    assert "line.lock.contended" in result.stderr
    assert tree_snapshot(project) == before


def test_line_init_preserves_concurrent_existing_ledger_and_rolls_back_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    ledger = project / ".proofline/line-identities.json"
    ledger.write_bytes(encode_ledger(set()))
    git("add", ".proofline/line-identities.json", cwd=project)
    git("commit", "-qm", "Adopt ledger", cwd=project)
    external = encode_ledger({"line-0099"})
    original_commit = line_writer._commit_ledger_path

    def mutate_then_commit(*args: object, **kwargs: object) -> None:
        ledger.write_bytes(external)
        original_commit(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(line_writer, "_commit_ledger_path", mutate_then_commit)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Ledger race")

    assert exc_info.value.code == "ledger.concurrent.changed"
    assert ledger.read_bytes() == external
    assert not (project / ".proofline/lines/line-0013").exists()
    assert not list(project.glob(".line-0013-*"))
    assert not list(project.glob(".proofline-ledger-*"))


def test_line_init_post_result_external_ledger_mutation_preserves_primary_and_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    ledger = project / ".proofline/line-identities.json"
    external = encode_ledger({"line-0099"})
    original_validate = line_writer._require_valid_project
    validations = 0

    def mutate_before_post_result_validation(root: Path) -> None:
        nonlocal validations
        validations += 1
        if validations == 2:
            ledger.write_bytes(external)
        original_validate(root)

    monkeypatch.setattr(line_writer, "_require_valid_project", mutate_before_post_result_validation)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Post result race")

    assert exc_info.value.code == "project.invalid"
    assert "ledger.rollback.ownership" in exc_info.value.message
    assert ledger.read_bytes() == external
    assert not (project / ".proofline/lines/line-0013").exists()
    assert not list(project.glob(".line-0013-*"))
    assert not list(project.glob(".proofline-ledger-*"))


def test_line_init_ledger_rollback_continues_after_read_descriptor_close_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    before = tree_snapshot(project)
    original_validate = line_writer._require_valid_project
    original_open = os.open
    original_close = os.close
    validations = 0
    ledger_read_fd: int | None = None
    committed = False
    close_failed = False

    def fail_post_result_validation(root: Path) -> None:
        nonlocal validations, committed
        validations += 1
        if validations == 2:
            committed = True
            raise LineInitError("project.invalid", ".", "injected post-result failure")
        original_validate(root)

    def record_ledger_read(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal ledger_read_fd
        descriptor = original_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]
        if committed and isinstance(path, (str, bytes)) and os.fsdecode(path) == "line-identities.json":
            ledger_read_fd = descriptor
        return descriptor

    def fail_ledger_read_close(descriptor: int) -> None:
        nonlocal close_failed
        if descriptor == ledger_read_fd and not close_failed:
            close_failed = True
            raise OSError(5, "injected ledger rollback close failure")
        original_close(descriptor)

    monkeypatch.setattr(line_writer, "_require_valid_project", fail_post_result_validation)
    monkeypatch.setattr(os, "open", record_ledger_read)
    monkeypatch.setattr(os, "close", fail_ledger_read_close)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Ledger rollback close")

    assert exc_info.value.code == "project.invalid"
    assert "line.finalize.failed" in exc_info.value.message
    assert close_failed
    assert tree_snapshot(project) == before
    assert ledger_read_fd is not None
    original_close(ledger_read_fd)


def test_line_init_second_rollback_anchor_close_failure_restores_exact_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    before = tree_snapshot(project)
    original_dup = os.dup
    original_close = os.close
    rollback_fds: list[int] = []
    failed = False

    def record_rollback_anchor(descriptor: int) -> int:
        duplicate = original_dup(descriptor)
        rollback_fds.append(duplicate)
        return duplicate

    def fail_ledger_rollback_anchor_close(descriptor: int) -> None:
        nonlocal failed
        if len(rollback_fds) == 2 and descriptor == rollback_fds[1] and not failed:
            failed = True
            raise OSError(5, "injected ledger rollback anchor close failure")
        original_close(descriptor)

    monkeypatch.setattr(os, "dup", record_rollback_anchor)
    monkeypatch.setattr(os, "close", fail_ledger_rollback_anchor_close)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Rollback anchor close")

    assert exc_info.value.code == "line.finalize.failed"
    assert "injected ledger rollback anchor close failure" in exc_info.value.message
    assert failed
    assert tree_snapshot(project) == before
    assert not list(project.glob(".line-0013-*"))
    assert not list(project.glob(".proofline-ledger-*"))


def git_state_snapshot(project: Path) -> tuple[str, bytes, bytes]:
    head = git("symbolic-ref", "-q", "HEAD", cwd=project).stdout.strip()
    refs = git("show-ref", cwd=project).stdout.encode()
    remotes = git("remote", "-v", cwd=project).stdout.encode()
    return head, refs, remotes


def assert_transaction_residue_absent(project: Path) -> None:
    assert not list(project.glob(".line-*"))
    assert not list(project.glob(".proofline-ledger-*"))
    assert not list(project.glob(".proofline-transaction-*"))
    assert not list(project.glob(".git/proofline-*"))


def test_line_init_post_exchange_identity_failure_restores_exact_prior_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    ledger = project / ".proofline/line-identities.json"
    prior = encode_ledger(set())
    ledger.write_bytes(prior)
    git("add", ".proofline/line-identities.json", cwd=project)
    git("commit", "-qm", "Adopt ledger", cwd=project)
    before = tree_snapshot(project)
    git_before = git_state_snapshot(project)
    original_exchange = line_writer._exchange_path_at
    original_identity = line_writer._directory_identity
    exchange_completed = False
    failed = False

    def exchange_and_arm(source: Path, target_dir_fd: int, target_name: str) -> None:
        nonlocal exchange_completed
        original_exchange(source, target_dir_fd, target_name)
        exchange_completed = True

    def fail_displaced_identity(path: Path) -> tuple[int, int]:
        nonlocal failed
        if exchange_completed and path.name.startswith(".proofline-ledger-") and not failed:
            failed = True
            raise OSError(5, "injected displaced ledger identity failure")
        return original_identity(path)

    monkeypatch.setattr(line_writer, "_exchange_path_at", exchange_and_arm)
    monkeypatch.setattr(line_writer, "_directory_identity", fail_displaced_identity)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Post-exchange identity failure")

    assert exc_info.value.code == "ledger.commit.failed"
    assert "injected displaced ledger identity failure" in exc_info.value.message
    assert "secondary:" not in exc_info.value.message
    assert failed
    assert ledger.read_bytes() == prior
    assert decode_ledger(ledger.read_bytes()).allocated_line_ids == ()
    assert tree_snapshot(project) == before
    assert_transaction_residue_absent(project)
    assert git_state_snapshot(project) == git_before


def test_line_init_unlock_failure_rolls_back_while_repository_lock_is_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    before = tree_snapshot(project)
    git_before = git_state_snapshot(project)
    original_acquire = line_writer._acquire_repository_lock
    original_flock = fcntl.flock
    original_close = os.close
    original_rollback = line_writer._rollback_owned_ledger
    repository_fd: int | None = None
    unlock_attempts = 0
    closed: list[int] = []
    rollback_lock_retained: list[bool] = []

    def record_acquire(root: Path) -> int:
        nonlocal repository_fd
        descriptor = original_acquire(root)
        if repository_fd is None:
            repository_fd = descriptor
        return descriptor

    def fail_first_unlock(descriptor: int, operation: int) -> None:
        nonlocal unlock_attempts
        if descriptor == repository_fd and operation == fcntl.LOCK_UN:
            unlock_attempts += 1
            if unlock_attempts == 1:
                raise OSError(5, "injected repository unlock failure")
        original_flock(descriptor, operation)

    def record_close(descriptor: int) -> None:
        closed.append(descriptor)
        original_close(descriptor)

    def record_rollback(*args: object, **kwargs: object) -> str | None:
        assert repository_fd is not None
        rollback_lock_retained.append(repository_fd not in closed)
        return original_rollback(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(line_writer, "_acquire_repository_lock", record_acquire)
    monkeypatch.setattr(fcntl, "flock", fail_first_unlock)
    monkeypatch.setattr(os, "close", record_close)
    monkeypatch.setattr(line_writer, "_rollback_owned_ledger", record_rollback)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Repository unlock failure")

    assert exc_info.value.code == "line.finalize.failed"
    assert "line.lock.release.failed" in exc_info.value.message
    assert "injected repository unlock failure" in exc_info.value.message
    assert "secondary:" not in exc_info.value.message
    assert unlock_attempts == 2
    assert rollback_lock_retained == [True]
    assert repository_fd is not None and closed.count(repository_fd) == 1
    assert tree_snapshot(project) == before
    assert_transaction_residue_absent(project)
    assert git_state_snapshot(project) == git_before


def test_line_init_unlock_success_post_close_failure_reacquires_before_exact_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    before = tree_snapshot(project)
    git_before = git_state_snapshot(project)
    original_acquire = line_writer._acquire_repository_lock
    original_close = os.close
    acquired: list[int] = []
    close_failed = False

    def record_acquire(root: Path) -> int:
        descriptor = original_acquire(root)
        acquired.append(descriptor)
        return descriptor

    def fail_first_repository_close_after_real_close(descriptor: int) -> None:
        nonlocal close_failed
        if acquired and descriptor == acquired[0] and not close_failed:
            close_failed = True
            original_close(descriptor)
            raise OSError(5, "injected repository descriptor close failure")
        original_close(descriptor)

    monkeypatch.setattr(line_writer, "_acquire_repository_lock", record_acquire)
    monkeypatch.setattr(os, "close", fail_first_repository_close_after_real_close)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "Repository close failure")

    assert exc_info.value.code == "line.finalize.failed"
    assert "line.finalize.failed" in exc_info.value.message
    assert "injected repository descriptor close failure" in exc_info.value.message
    assert "line.rollback" not in exc_info.value.message
    assert close_failed
    assert len(acquired) == 2
    assert tree_snapshot(project) == before
    assert_transaction_residue_absent(project)
    assert git_state_snapshot(project) == git_before


def test_line_init_post_close_error_preserves_reused_successor_lock_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    git_before = git_state_snapshot(project)
    original_acquire = line_writer._acquire_repository_lock
    original_close = os.close
    repository_fd: int | None = None
    successor_fd: int | None = None
    target_close_attempts = 0

    def record_acquire(root: Path) -> int:
        nonlocal repository_fd
        descriptor = original_acquire(root)
        if repository_fd is None:
            repository_fd = descriptor
        return descriptor

    def close_then_reuse_and_fail(descriptor: int) -> None:
        nonlocal successor_fd, target_close_attempts
        if descriptor == repository_fd:
            target_close_attempts += 1
            if target_close_attempts == 1:
                original_close(descriptor)
                successor_fd = original_acquire(project)
                assert successor_fd == descriptor
                raise OSError(5, "injected post-close repository descriptor failure")
        original_close(descriptor)

    monkeypatch.setattr(line_writer, "_acquire_repository_lock", record_acquire)
    monkeypatch.setattr(os, "close", close_then_reuse_and_fail)

    try:
        with pytest.raises(LineInitError) as exc_info:
            initialize_line(project, "line-0013", "Post-close descriptor failure")

        assert exc_info.value.code == "line.finalize.failed"
        assert "injected post-close repository descriptor failure" in exc_info.value.message
        assert "secondary:" in exc_info.value.message
        assert "line.lock.contended" in exc_info.value.message
        assert repository_fd is not None
        assert successor_fd == repository_fd
        assert target_close_attempts == 1
        os.fstat(successor_fd)
        contender_fd = os.open(project / ".git", os.O_RDONLY | os.O_DIRECTORY)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            original_close(contender_fd)
        ledger = project / ".proofline/line-identities.json"
        assert ledger.read_bytes() == encode_ledger({"line-0013"})
        assert decode_ledger(ledger.read_bytes()).allocated_line_ids == ("line-0013",)
        target = project / ".proofline/lines/line-0013"
        assert sorted(path.name for path in target.iterdir()) == [
            "dcy-0013.md",
            "line-0013.md",
        ]
        assert 'id: "line-0013"' in (target / "line-0013.md").read_text()
        assert 'id: "dcy-0013"' in (target / "dcy-0013.md").read_text()
        assert_transaction_residue_absent(project)
        assert git_state_snapshot(project) == git_before
    finally:
        if successor_fd is not None:
            try:
                fcntl.flock(successor_fd, fcntl.LOCK_UN)
                original_close(successor_fd)
            except OSError:
                pass


def test_line_init_close_failure_preserves_successor_advanced_whole_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    git_before = git_state_snapshot(project)
    original_acquire = line_writer._acquire_repository_lock
    original_close = os.close
    acquired: list[int] = []
    close_failed = False
    successor_result = None

    def record_acquire(root: Path) -> int:
        descriptor = original_acquire(root)
        acquired.append(descriptor)
        return descriptor

    def commit_successor_then_fail_close(descriptor: int) -> None:
        nonlocal close_failed, successor_result
        if acquired and descriptor == acquired[0] and not close_failed:
            close_failed = True
            successor_result = initialize_line(project, "line-0014", "Cooperative successor")
            raise OSError(5, "injected repository descriptor close failure")
        original_close(descriptor)

    monkeypatch.setattr(line_writer, "_acquire_repository_lock", record_acquire)
    monkeypatch.setattr(os, "close", commit_successor_then_fail_close)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "First transaction")

    assert exc_info.value.code == "line.finalize.failed"
    assert "line.finalize.failed" in exc_info.value.message
    assert "injected repository descriptor close failure" in exc_info.value.message
    assert "line.rollback" not in exc_info.value.message
    assert close_failed
    assert successor_result is not None
    assert len(acquired) == 3  # first, successor, first transaction rollback re-acquire
    ledger = project / ".proofline/line-identities.json"
    assert ledger.read_bytes() == encode_ledger({"line-0013", "line-0014"})
    assert decode_ledger(ledger.read_bytes()).allocated_line_ids == (
        "line-0013",
        "line-0014",
    )
    for line_id in ("line-0013", "line-0014"):
        suffix = line_id.removeprefix("line-")
        target = project / ".proofline/lines" / line_id
        assert sorted(path.name for path in target.iterdir()) == [
            f"dcy-{suffix}.md",
            f"{line_id}.md",
        ]
        assert f'id: "{line_id}"' in (target / f"{line_id}.md").read_text()
        assert f'id: "dcy-{suffix}"' in (target / f"dcy-{suffix}.md").read_text()
    assert_transaction_residue_absent(project)
    assert git_state_snapshot(project) == git_before


def test_line_init_close_failure_without_recovery_lock_preserves_both_transactions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    git_before = git_state_snapshot(project)
    original_acquire = line_writer._acquire_repository_lock
    original_release = line_writer._release_repository_lock
    original_close = os.close
    acquired: list[int] = []
    acquisition_attempts = 0
    acquisition_guard = threading.Lock()
    successor_holds_lock = threading.Event()
    recovery_attempted = threading.Event()
    successor_errors: list[BaseException] = []
    successor_result = None
    successor_thread: threading.Thread | None = None
    close_failed = False

    def synchronized_acquire(root: Path) -> int:
        nonlocal acquisition_attempts
        with acquisition_guard:
            acquisition_attempts += 1
            attempt = acquisition_attempts
        try:
            descriptor = original_acquire(root)
            acquired.append(descriptor)
            return descriptor
        finally:
            if attempt == 3:
                recovery_attempted.set()

    def hold_successor_lock(descriptor: int) -> line_writer._LockReleaseResult:
        if len(acquired) >= 2 and descriptor == acquired[1]:
            successor_holds_lock.set()
            assert recovery_attempted.wait(timeout=30)
        return original_release(descriptor)

    def run_successor() -> None:
        nonlocal successor_result
        try:
            successor_result = initialize_line(
                project, "line-0014", "Lock-holding cooperative successor"
            )
        except BaseException as exc:
            successor_errors.append(exc)

    def commit_successor_then_fail_close(descriptor: int) -> None:
        nonlocal close_failed, successor_thread
        if acquired and descriptor == acquired[0] and not close_failed:
            close_failed = True
            successor_thread = threading.Thread(target=run_successor)
            successor_thread.start()
            assert successor_holds_lock.wait(timeout=30)
            raise OSError(5, "injected repository descriptor close failure")
        original_close(descriptor)

    monkeypatch.setattr(line_writer, "_acquire_repository_lock", synchronized_acquire)
    monkeypatch.setattr(line_writer, "_release_repository_lock", hold_successor_lock)
    monkeypatch.setattr(os, "close", commit_successor_then_fail_close)

    with pytest.raises(LineInitError) as exc_info:
        initialize_line(project, "line-0013", "First transaction")

    assert successor_thread is not None
    successor_thread.join(timeout=30)
    assert not successor_thread.is_alive()
    assert successor_errors == []
    assert successor_result is not None
    assert exc_info.value.code == "line.finalize.failed"
    assert "injected repository descriptor close failure" in exc_info.value.message
    assert "secondary:" in exc_info.value.message
    assert "line.lock.contended" in exc_info.value.message
    assert close_failed
    assert acquisition_attempts == 3
    ledger = project / ".proofline/line-identities.json"
    assert ledger.read_bytes() == encode_ledger({"line-0013", "line-0014"})
    assert decode_ledger(ledger.read_bytes()).allocated_line_ids == (
        "line-0013",
        "line-0014",
    )
    successor = project / ".proofline/lines/line-0014"
    assert successor.is_dir()
    first = project / ".proofline/lines/line-0013"
    assert first.is_dir(), "ledger A,B와 Line B만 남는 불일치 residue"
    for line_id in ("line-0013", "line-0014"):
        suffix = line_id.removeprefix("line-")
        target = project / ".proofline/lines" / line_id
        assert sorted(path.name for path in target.iterdir()) == [
            f"dcy-{suffix}.md",
            f"{line_id}.md",
        ]
    assert_transaction_residue_absent(project)
    assert git_state_snapshot(project) == git_before
