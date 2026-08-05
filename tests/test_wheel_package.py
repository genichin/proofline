from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from proofline.identity_ledger import decode_ledger, encode_ledger
from proofline.line_writer import _render

ROOT = Path(__file__).resolve().parents[1]


def test_source_checkout_line_init_fresh_and_legacy_e2e(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    command = [
        sys.executable,
        "-c",
        "from proofline.cli import main; raise SystemExit(main())",
    ]

    def create_project(name: str, *, legacy: bool) -> Path:
        project = tmp_path / name
        (project / ".proofline/lines").mkdir(parents=True)
        (project / ".proofline/criteria").mkdir()
        (project / "proofline.yaml").write_bytes(
            b"schema_version: 1\nartifact_root: .proofline\n"
        )
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project, check=True)
        if legacy:
            (project / ".proofline/line-identities.json").write_bytes(encode_ledger(set()))
        subprocess.run(["git", "add", "-A"], cwd=project, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=proofline@example.invalid",
                "-c",
                "user.name=ProofLine Test",
                "commit",
                "-qm",
                "project baseline",
            ],
            cwd=project,
            check=True,
        )
        return project

    for name, legacy in (("fresh-source", False), ("legacy-source", True)):
        project = create_project(name, legacy=legacy)
        before = {
            path.relative_to(project): path.read_bytes()
            for path in project.rglob("*")
            if path.is_file()
        }
        dry = subprocess.run(
            [*command, "line", "init", "line-0007", "--title", "Source", "--dry-run"],
            cwd=project,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert dry.returncode == 0, dry.stderr
        assert {
            path.relative_to(project): path.read_bytes()
            for path in project.rglob("*")
            if path.is_file()
        } == before
        actual = subprocess.run(
            [*command, "line", "init", "line-0007", "--title", "Source"],
            cwd=project,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert actual.returncode == 0, actual.stderr
        ledger = project / ".proofline/line-identities.json"
        assert decode_ledger(ledger.read_bytes()).allocated_line_ids == ("line-0007",)
        validated = subprocess.run(
            [*command, "validate"],
            cwd=project,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert validated.returncode == 1, validated.stderr
        assert "history.unavailable" in validated.stderr
        assert not list(project.glob(".line-*"))
        assert not list(project.glob(".proofline-ledger-*"))


@pytest.mark.parametrize("committed", [False, True], ids=["uncommitted", "committed"])
def test_source_checkout_rejects_ledger_only_delta_without_mutation(
    tmp_path: Path, committed: bool
) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    home = tmp_path / "home"
    home.mkdir()
    env["HOME"] = str(home)
    external = tmp_path / "external.txt"
    external.write_bytes(b"external sentinel\n")
    command = [
        sys.executable,
        "-c",
        "from proofline.cli import main; raise SystemExit(main())",
    ]
    project = tmp_path / "project"
    (project / ".proofline/lines").mkdir(parents=True)
    (project / ".proofline/criteria").mkdir()
    (project / "proofline.yaml").write_bytes(
        b"schema_version: 1\nartifact_root: .proofline\n"
    )
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project, check=True)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=proofline@example.invalid",
            "-c",
            "user.name=ProofLine Test",
            "commit",
            "-qm",
            "project baseline",
        ],
        cwd=project,
        check=True,
    )
    initialized = subprocess.run(
        [*command, "line", "init", "line-0007", "--title", "Source baseline"],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert initialized.returncode == 0, initialized.stderr
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=proofline@example.invalid",
            "-c",
            "user.name=ProofLine Test",
            "commit",
            "-qm",
            "valid allocation",
        ],
        cwd=project,
        check=True,
    )
    ledger = project / ".proofline/line-identities.json"
    ledger.write_bytes(encode_ledger({"line-0007", "line-0008"}))
    if committed:
        subprocess.run(["git", "add", str(ledger)], cwd=project, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=proofline@example.invalid",
                "-c",
                "user.name=ProofLine Test",
                "commit",
                "-qm",
                "ledger-only delta",
            ],
            cwd=project,
            check=True,
        )

    canonical_before = {
        path.relative_to(project): path.read_bytes()
        for path in (project / ".proofline").rglob("*")
        if path.is_file()
    }
    git_before = subprocess.run(
        [
            "git",
            "status",
            "--porcelain=v1",
            "--branch",
            "--untracked-files=all",
        ],
        cwd=project,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    refs_before = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname):%(objectname)"],
        cwd=project,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    remotes_before = subprocess.run(
        ["git", "remote", "-v"],
        cwd=project,
        text=True,
        capture_output=True,
        check=True,
    ).stdout

    rejected = subprocess.run(
        [*command, "validate"],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert rejected.returncode == 1
    assert "ledger.orphan" in rejected.stderr
    assert not (project / ".proofline/lines/line-0008").exists()
    assert {
        path.relative_to(project): path.read_bytes()
        for path in (project / ".proofline").rglob("*")
        if path.is_file()
    } == canonical_before
    assert external.read_bytes() == b"external sentinel\n"
    assert not list(project.glob(".line-*"))
    assert not list(project.glob(".proofline-ledger-*"))
    assert subprocess.run(
        [
            "git",
            "status",
            "--porcelain=v1",
            "--branch",
            "--untracked-files=all",
        ],
        cwd=project,
        text=True,
        capture_output=True,
        check=True,
    ).stdout == git_before
    assert subprocess.run(
        ["git", "for-each-ref", "--format=%(refname):%(objectname)"],
        cwd=project,
        text=True,
        capture_output=True,
        check=True,
    ).stdout == refs_before
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project,
        text=True,
        capture_output=True,
        check=True,
    ).stdout == head_before
    assert subprocess.run(
        ["git", "remote", "-v"],
        cwd=project,
        text=True,
        capture_output=True,
        check=True,
    ).stdout == remotes_before


@pytest.mark.candidate_build_only
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
    provided = os.environ.get("PROOFLINE_HOSTED_CANDIDATE_WHEEL")
    if provided:
        wheel = Path(provided)
        assert wheel.is_absolute() and wheel.is_file()
    else:
        dist = tmp_path / "dist"
        build = subprocess.run(
            ["uv", "build", "--refresh", "--wheel", "--out-dir", str(dist)],
            cwd=ROOT, text=True, capture_output=True, check=False,
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
        run_iqc = "proofline_home/skills/proofline-run-iqc/SKILL.md"
        assert run_iqc in names
        assert archive.read(run_iqc) == (
            ROOT / "skills/proofline-run-iqc/SKILL.md"
        ).read_bytes()
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
                "assert version('proofline') == '0.5.0'"
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
    assert installed_version.stdout == "proofline 0.5.0\n"

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
    ledger = e2e_project / ".proofline/line-identities.json"
    assert decode_ledger(ledger.read_bytes()).allocated_line_ids == ("line-0001",)
    assert not list(e2e_project.glob(".proofline-ledger-*"))

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
    subprocess.run(["git", "add", "-A"], cwd=e2e_project, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=proofline@example.invalid",
            "-c",
            "user.name=ProofLine Test",
            "commit",
            "-qm",
            "persist first Line",
        ],
        cwd=e2e_project,
        check=True,
    )
    git_metadata_before = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname):%(objectname)"],
        cwd=e2e_project,
        text=True,
        capture_output=True,
        check=False,
    ).stdout

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
    assert post_line_validate.stderr == ""
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

    good_ledger = ledger.read_bytes()
    bad_ledger = encode_ledger({"line-0001", "line-0002"})
    ledger.write_bytes(bad_ledger)
    uncommitted_ledger_only = subprocess.run(
        [str(proofline), "validate"],
        cwd=e2e_project,
        env=project_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert uncommitted_ledger_only.returncode == 1
    assert "ledger.orphan" in uncommitted_ledger_only.stderr
    assert not list(e2e_project.glob(".line-*"))
    assert not list(e2e_project.glob(".proofline-ledger-*"))
    ledger.write_bytes(good_ledger)
    subprocess.run(["git", "add", "-A"], cwd=e2e_project, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=proofline@example.invalid",
            "-c",
            "user.name=ProofLine Test",
            "commit",
            "-qm",
            "commit valid allocation",
        ],
        cwd=e2e_project,
        check=False,
    )
    ledger.write_bytes(bad_ledger)
    subprocess.run(["git", "add", str(ledger)], cwd=e2e_project, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=proofline@example.invalid",
            "-c",
            "user.name=ProofLine Test",
            "commit",
            "-qm",
            "injected ledger-only allocation",
        ],
        cwd=e2e_project,
        check=True,
    )
    committed_ledger_only = subprocess.run(
        [str(proofline), "validate"],
        cwd=e2e_project,
        env=project_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert committed_ledger_only.returncode == 1
    assert "ledger.orphan" in committed_ledger_only.stderr

    legacy_project = tmp_path / "legacy-project"
    legacy_project.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=legacy_project, check=True)
    legacy_init = subprocess.run(
        [str(proofline), "project", "init"],
        cwd=legacy_project,
        env=project_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert legacy_init.returncode == 0, legacy_init.stderr
    legacy_ledger = legacy_project / ".proofline/line-identities.json"
    legacy_ledger.write_bytes(encode_ledger(set()))
    subprocess.run(["git", "add", "-A"], cwd=legacy_project, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=proofline@example.invalid",
            "-c",
            "user.name=ProofLine Test",
            "commit",
            "-qm",
            "legacy project with adopted ledger",
        ],
        cwd=legacy_project,
        check=True,
    )
    legacy_before = {
        path.relative_to(legacy_project): path.read_bytes()
        for path in legacy_project.rglob("*")
        if path.is_file()
    }
    legacy_dry = subprocess.run(
        [str(proofline), "line", "init", "line-0005", "--title", "Legacy", "--dry-run"],
        cwd=legacy_project,
        env=project_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert legacy_dry.returncode == 0, legacy_dry.stderr
    assert {
        path.relative_to(legacy_project): path.read_bytes()
        for path in legacy_project.rglob("*")
        if path.is_file()
    } == legacy_before
    legacy_actual = subprocess.run(
        [str(proofline), "line", "init", "line-0005", "--title", "Legacy"],
        cwd=legacy_project,
        env=project_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert legacy_actual.returncode == 0, legacy_actual.stderr
    assert decode_ledger(legacy_ledger.read_bytes()).allocated_line_ids == ("line-0005",)
    legacy_validate = subprocess.run(
        [str(proofline), "validate"],
        cwd=legacy_project,
        env=project_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert legacy_validate.returncode == 1, legacy_validate.stderr
    assert "history.unavailable" in legacy_validate.stderr
    assert not list(legacy_project.glob(".line-*"))
    assert not list(legacy_project.glob(".proofline-ledger-*"))

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


def test_wheel_changed_resources_are_exact_source_bytes_and_keep_p_then_b_workflow(
    tmp_path: Path,
) -> None:
    provided = os.environ.get("PROOFLINE_HOSTED_CANDIDATE_WHEEL")
    if provided:
        wheel = Path(provided)
        assert wheel.is_absolute() and wheel.is_file()
    else:
        dist = tmp_path / "dist"
        build = subprocess.run(
            ["uv", "build", "--refresh", "--wheel", "--out-dir", str(dist)],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        assert build.returncode == 0, build.stderr
        wheel = next(dist.glob("proofline-*.whl"))
    resources = {
        "docs/contracts/line-delivery.md": "proofline_home/contracts/line-delivery.md",
        "docs/contracts/micro-spec-and-iqc.md": "proofline_home/contracts/micro-spec-and-iqc.md",
        "skills/proofline-start-implementation/SKILL.md": "proofline_home/skills/proofline-start-implementation/SKILL.md",
        "skills/proofline-start-implementation/scripts/create_worktree.py": "proofline_home/skills/proofline-start-implementation/scripts/create_worktree.py",
        "skills/proofline-run-iqc/SKILL.md": "proofline_home/skills/proofline-run-iqc/SKILL.md",
    }
    with zipfile.ZipFile(wheel) as archive:
        for source, packaged in resources.items():
            assert archive.read(packaged) == (ROOT / source).read_bytes()
        skill = archive.read(resources["skills/proofline-start-implementation/SKILL.md"]).decode()
        assert "별도 lifecycle-only `in_progress` commit `P`" in skill
        assert "그 다음 `implementation_history: first_parent`만 추가한 별도 commit `B`" in skill
        script = archive.read(resources["skills/proofline-start-implementation/scripts/create_worktree.py"])
        assert b"approval_commit" in script
