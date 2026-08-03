from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from proofline.line_writer import _render

ROOT = Path(__file__).resolve().parents[1]


def test_built_sdist_contains_project_schema_resources(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    build = subprocess.run(
        ["uv", "build", "--refresh", "--sdist", "--out-dir", str(dist)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    sdist = next(dist.glob("proofline-*.tar.gz"))
    with tarfile.open(sdist, "r:gz") as archive:
        names = archive.getnames()
    for relative in (
        "templates/schema-v1/project/proofline.yaml",
        "templates/schema-v1/project/lines.gitkeep",
        "templates/schema-v1/project/criteria.gitkeep",
    ):
        assert any(name.endswith(relative) for name in names)


def test_built_wheel_contains_and_reads_canonical_schema_templates(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    build = subprocess.run(
        ["uv", "build", "--refresh", "--wheel", "--out-dir", str(dist)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    wheel = next(dist.glob("proofline-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert "proofline_schema_v1_templates/artifacts/line.md" in names
        assert "proofline_schema_v1_templates/artifacts/discovery.md" in names
        assert "proofline_schema_v1_templates/artifacts/dqc.md" in names
        assert "proofline_schema_v1_templates/project/proofline.yaml" in names
        assert "proofline_schema_v1_templates/project/lines.gitkeep" in names
        assert "proofline_schema_v1_templates/project/criteria.gitkeep" in names
        assert "proofline_home/contracts/storage-and-retention.md" in names
        assert "proofline_home/templates/schema-v1/artifacts/line.md" in names
        assert "proofline_home/skills/proofline-start-line/SKILL.md" in names
        assert "proofline_home/agent-context.md" in names
        unpacked = tmp_path / "wheel"
        archive.extractall(unpacked)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(unpacked)
    check = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from proofline.line_writer import _read_template; "
                "assert '{{LINE_ID}}' in _read_template('line.md'); "
                "assert '{{DISCOVERY_ID}}' in _read_template('discovery.md'); "
                "assert 'Mandatory Line-Level Checks' in _read_template('dqc.md')"
            ),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert check.returncode == 0, check.stderr

    venv = tmp_path / "venv"
    create_venv = subprocess.run(
        ["uv", "venv", "--python", sys.executable, str(venv)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert create_venv.returncode == 0, create_venv.stderr
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    proofline = venv / ("Scripts/proofline.exe" if os.name == "nt" else "bin/proofline")
    install = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--refresh",
            "--python",
            str(python),
            str(wheel),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert install.returncode == 0, install.stderr
    provenance = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            (
                "from importlib.metadata import version; "
                "from pathlib import Path; import proofline; "
                "p=Path(proofline.__file__).resolve(); "
                "assert 'site-packages' in p.parts; "
                "assert version('proofline') == '0.4.1'"
            ),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert provenance.returncode == 0, provenance.stderr
    installed_version = subprocess.run(
        [str(proofline), "--version"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert installed_version.returncode == 0, installed_version.stderr
    assert installed_version.stdout == "proofline 0.4.1\n"

    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    marker = project / ".proofline-marker"
    marker.write_text("canonical\n", encoding="utf-8")
    init_env = os.environ.copy()
    init_env["HOME"] = str(isolated_home)
    initialized = subprocess.run(
        [str(proofline), "init"],
        cwd=project,
        env=init_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert initialized.returncode == 0, initialized.stderr
    assert (isolated_home / ".proofline/manifest.yaml").is_file()
    assert (isolated_home / ".proofline/contracts").is_dir()
    assert (isolated_home / ".proofline/templates").is_dir()
    assert (isolated_home / ".proofline/skills").is_dir()
    assert marker.read_text(encoding="utf-8") == "canonical\n"

    project_home = tmp_path / "project-home"
    (project_home / ".proofline").mkdir(parents=True)
    (project_home / ".proofline/manifest.yaml").write_text(
        "schema_version: 999\n", encoding="utf-8"
    )
    home_before = {
        path.relative_to(project_home): path.read_bytes()
        for path in project_home.rglob("*")
        if path.is_file()
    }
    e2e_project = tmp_path / "checkout-outside-project"
    e2e_project.mkdir()
    git_init = subprocess.run(
        ["git", "init", "-q", "-b", "main"], cwd=e2e_project, capture_output=True, text=True, check=False
    )
    assert git_init.returncode == 0, git_init.stderr
    git_metadata_before = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname):%(objectname)"],
        cwd=e2e_project,
        text=True,
        capture_output=True,
        check=False,
    ).stdout
    project_env = os.environ.copy()
    project_env["HOME"] = str(project_home)
    project_env.pop("PYTHONPATH", None)

    dry_run = subprocess.run(
        [str(proofline), "project", "init", "--dry-run"],
        cwd=e2e_project,
        env=project_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert dry_run.returncode == 0, dry_run.stderr
    assert not (e2e_project / "proofline.yaml").exists()

    project_init = subprocess.run(
        [str(proofline), "project", "init"],
        cwd=e2e_project,
        env=project_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert project_init.returncode == 0, project_init.stderr  # ac-0020
    assert (e2e_project / "proofline.yaml").read_bytes() == (
        b"schema_version: 1\nartifact_root: .proofline\n"
    )
    assert (e2e_project / ".proofline/lines/.gitkeep").read_bytes() == b""
    assert (e2e_project / ".proofline/criteria/.gitkeep").read_bytes() == b""

    no_op = subprocess.run(
        [str(proofline), "project", "init"],
        cwd=e2e_project,
        env=project_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert no_op.returncode == 0, no_op.stderr
    assert "already-initialized: proofline.yaml" in no_op.stdout

    validate = subprocess.run(
        [str(proofline), "validate"],
        cwd=e2e_project,
        env=project_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert validate.returncode == 0, validate.stderr  # ac-0001
    assert subprocess.run(
        ["git", "for-each-ref", "--format=%(refname):%(objectname)"],
        cwd=e2e_project,
        text=True,
        capture_output=True,
        check=False,
    ).stdout == git_metadata_before

    subprocess.run(
        ["git", "add", "-A"], cwd=e2e_project, capture_output=True, check=True
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=proofline@example.invalid",
            "-c",
            "user.name=ProofLine Test",
            "commit",
            "-qm",
            "initialize project",
        ],
        cwd=e2e_project,
        capture_output=True,
        check=True,
    )
    git_metadata_before = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname):%(objectname)"],
        cwd=e2e_project,
        text=True,
        capture_output=True,
        check=False,
    ).stdout

    line_dry_run = subprocess.run(
        [
            str(proofline),
            "line",
            "init",
            "line-0001",
            "--title",
            "첫 Line",
            "--dry-run",
        ],
        cwd=e2e_project,
        env=project_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert line_dry_run.returncode == 0, line_dry_run.stderr
    assert ".proofline/lines/line-0001/line-0001.md" in line_dry_run.stdout
    assert ".proofline/lines/line-0001/dcy-0001.md" in line_dry_run.stdout
    assert not (e2e_project / ".proofline/lines/line-0001").exists()

    line_init = subprocess.run(
        [str(proofline), "line", "init", "line-0001", "--title", "첫 Line"],
        cwd=e2e_project,
        env=project_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert line_init.returncode == 0, line_init.stderr  # ac-0004
    expected_line, expected_discovery = _render("line-0001", "첫 Line")
    assert (e2e_project / ".proofline/lines/line-0001/line-0001.md").read_bytes() == (
        expected_line.encode("utf-8")
    )
    assert (e2e_project / ".proofline/lines/line-0001/dcy-0001.md").read_bytes() == (
        expected_discovery.encode("utf-8")
    )

    collision = subprocess.run(
        [str(proofline), "line", "init", "line-0001", "--title", "충돌"],
        cwd=e2e_project,
        env=project_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert collision.returncode != 0
    assert "line.path.exists" in collision.stderr
    assert not list(e2e_project.glob(".line-0001-*"))

    failure_script = """
import contextlib
import io
import sys
from pathlib import Path
from proofline.cli import main
original = Path.write_text
def fail_discovery(path, data, **kwargs):
    if path.name == 'dcy-0002.md' and path.parent.name.startswith('.line-0002-'):
        raise PermissionError('injected installed-wheel write failure')
    return original(path, data, **kwargs)
Path.write_text = fail_discovery
stderr = io.StringIO()
with contextlib.redirect_stderr(stderr):
    result = main(['line', 'init', 'line-0002', '--title', '실패 정리'])
assert result == 1, result
diagnostic = stderr.getvalue()
assert 'line.write.failed' in diagnostic, diagnostic
assert '.proofline/lines/line-0002/line-0002.md' in diagnostic, diagnostic
assert not Path('.proofline/lines/line-0002').exists()
assert not list(Path('.').glob('.line-0002-*'))
print(diagnostic, end='', file=sys.stderr)
"""
    failure_cleanup = subprocess.run(
        [str(python), "-I", "-c", failure_script],
        cwd=e2e_project,
        env=project_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert failure_cleanup.returncode == 0, failure_cleanup.stderr
    assert "line.write.failed" in failure_cleanup.stderr
    assert ".proofline/lines/line-0002/line-0002.md" in failure_cleanup.stderr

    post_line_validate = subprocess.run(
        [str(proofline), "validate"],
        cwd=e2e_project,
        env=project_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert post_line_validate.returncode == 0, post_line_validate.stderr
    assert {
        path.relative_to(project_home): path.read_bytes()
        for path in project_home.rglob("*")
        if path.is_file()
    } == home_before
    assert subprocess.run(
        ["git", "for-each-ref", "--format=%(refname):%(objectname)"],
        cwd=e2e_project,
        text=True,
        capture_output=True,
        check=False,
    ).stdout == git_metadata_before

    absent_home = tmp_path / "absent-home"
    absent_home.mkdir()
    absent_project = tmp_path / "absent-home-project"
    absent_project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=absent_project, check=True)
    absent_env = os.environ.copy()
    absent_env["HOME"] = str(absent_home)
    absent_env.pop("PYTHONPATH", None)
    absent_init = subprocess.run(
        [str(proofline), "project", "init"],
        cwd=absent_project,
        env=absent_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert absent_init.returncode == 0, absent_init.stderr
    assert not (absent_home / ".proofline").exists()
