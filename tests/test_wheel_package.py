from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from proofline import agent_skills, updater
from proofline.agent_skills import AgentTarget

ROOT = Path(__file__).resolve().parents[1]


def _hosted_candidate_wheel() -> Path | None:
    if os.environ.get("PROOFLINE_HOSTED_CANDIDATE_MODE") != "1":
        return None
    provided = os.environ.get("PROOFLINE_HOSTED_CANDIDATE_WHEEL")
    expected = os.environ.get("PROOFLINE_HOSTED_CANDIDATE_WHEEL_SHA256")
    installed = os.environ.get("PROOFLINE_INSTALLED_EXECUTABLE")
    assert provided and expected and installed
    wheel = Path(provided)
    executable = Path(installed)
    assert wheel.is_absolute() and wheel.is_file()
    assert executable.is_absolute() and executable.is_file()
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == expected
    python = executable.parent / ("python.exe" if os.name == "nt" else "python")
    provenance = subprocess.run(
        (
            str(python),
            "-I",
            "-c",
            "from importlib.metadata import distribution; print(distribution('proofline').read_text('direct_url.json'))",
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    assert provenance.returncode == 0
    assert json.loads(provenance.stdout)["url"] == wheel.resolve().as_uri()
    return wheel


def _wheel(tmp_path: Path) -> Path:
    wheel = _hosted_candidate_wheel()
    if wheel is not None:
        return wheel
    dist = tmp_path / "dist"
    built = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    return next(dist.glob("proofline-*.whl"))


def test_built_wheel_contains_exact_skill_and_contract_resources(
    tmp_path: Path,
) -> None:
    wheel = _wheel(tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert "proofline_schema_v1_templates/project/identities.json" in names
        assert "skills/proofline-start-requirement/SKILL.md" in names
        assert "skills/proofline-maintain-design-docs/SKILL.md" in names
        assert "skills/proofline-review-specification/SKILL.md" in names
        assert (
            "skills/proofline-maintain-design-docs/templates/interface-contract.md"
            in names
        )
        assert "skills/proofline-maintain-design-docs/templates/data-model.md" in names
        assert "skills/proofline-maintain-design-docs/templates/runtime-flow.md" in names
        assert (
            "skills/proofline-create-worktree/scripts/inspect_worktree_readiness.py"
            in names
        )
        assert "proofline_resources/contracts/storage-and-retention.md" in names
        assert not any("proofline_home" in name for name in names)
        assert not any(name.endswith("audit_transition.py") for name in names)
        for source in (ROOT / "skills").rglob("*"):
            if (
                source.is_file()
                and source.name != "__init__.py"
                and "__pycache__" not in source.parts
            ):
                assert (
                    archive.read(
                        f"skills/{source.relative_to(ROOT / 'skills').as_posix()}"
                    )
                    == source.read_bytes()
                )


def test_built_wheel_changed_skill_payload_reaches_registered_agent_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel = _wheel(tmp_path)
    payload = updater.skill_payload_from_wheel(wheel, "0.9.1")
    relative_paths = (
        "proofline-review-specification/SKILL.md",
        "proofline-maintain-design-docs/SKILL.md",
        "proofline-maintain-design-docs/templates/interface-contract.md",
        "proofline-maintain-design-docs/templates/data-model.md",
        "proofline-maintain-design-docs/templates/runtime-flow.md",
    )
    source_root = ROOT / "skills"

    for agent, scope, layout in (
        ("hermes", "default", "grouped"),
        ("codex", "user", "flat"),
    ):
        target = AgentTarget(agent, scope, layout, tmp_path / f"{agent}-skills")
        registry = tmp_path / f"{agent}-registry"
        monkeypatch.setattr(
            agent_skills,
            "state_root",
            lambda environ=None, root=registry: root,
        )
        monkeypatch.setattr(
            agent_skills,
            "resolve_target",
            lambda selected, requested_scope=None, environ=None, value=target: value,
        )
        monkeypatch.setattr(
            agent_skills,
            "load_packaged_payload",
            lambda value=payload: value,
        )

        assert agent_skills.setup(agent, scope).status == "healthy"
        for relative in relative_paths:
            assert (target.root / relative).read_bytes() == (
                source_root / relative
            ).read_bytes()


def test_built_wheel_skill_inventory_is_v080_parser_compatible(
    tmp_path: Path,
) -> None:
    wheel = _wheel(tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        skill_files = [
            name
            for name in archive.namelist()
            if name.startswith("skills/") and not name.endswith("/")
        ]

    assert "skills/__init__.py" not in skill_files
    assert skill_files
    assert all(
        Path(name.removeprefix("skills/")).parts[0].startswith("proofline-")
        for name in skill_files
    )


def test_installed_wheel_has_no_init_and_reports_local_status(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path)
    environment = tmp_path / "venv"
    subprocess.run(
        ["uv", "venv", "--python", sys.executable, str(environment)],
        check=True,
        capture_output=True,
    )
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable = environment / (
        "Scripts/proofline.exe" if os.name == "nt" else "bin/proofline"
    )
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(wheel)],
        check=True,
        capture_output=True,
    )
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "XDG_STATE_HOME": str(tmp_path / "state")}
    status = subprocess.run(
        [str(executable), "status", "--json"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert status.returncode == 0, status.stderr
    assert set(json.loads(status.stdout)) == {
        "schema_version",
        "package",
        "project",
        "agent_skills",
    }
    retired = subprocess.run(
        [str(executable), "init"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert retired.returncode == 2
    assert not (home / ".proofline").exists()


@pytest.mark.candidate_build_only
def test_built_sdist_contains_agent_skill_resources(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    built = subprocess.run(
        ["uv", "build", "--sdist", "--out-dir", str(dist)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    with tarfile.open(next(dist.glob("proofline-*.tar.gz")), "r:gz") as archive:
        names = archive.getnames()
    assert any(
        name.endswith("skills/proofline-start-requirement/SKILL.md") for name in names
    )
    assert not any("proofline_home" in name for name in names)
