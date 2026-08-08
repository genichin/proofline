from __future__ import annotations

import os
import hashlib
import json
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from proofline.identity_allocator import IdentityAllocator, decode_allocator

ROOT = Path(__file__).resolve().parents[1]


def _hosted_candidate_wheel() -> Path | None:
    if os.environ.get("PROOFLINE_HOSTED_CANDIDATE_MODE") != "1":
        return None
    provided = os.environ.get("PROOFLINE_HOSTED_CANDIDATE_WHEEL")
    expected = os.environ.get("PROOFLINE_HOSTED_CANDIDATE_WHEEL_SHA256")
    installed = os.environ.get("PROOFLINE_INSTALLED_EXECUTABLE")
    assert provided and expected and installed, "hosted candidate controls are incomplete"
    wheel = Path(provided)
    executable = Path(installed)
    assert wheel.is_absolute() and wheel.is_file(), "candidate wheel must be an absolute file"
    assert executable.is_absolute() and executable.is_file(), "installed executable must be an absolute file"
    assert len(expected) == 64 and expected == expected.lower() and all(
        character in "0123456789abcdef" for character in expected
    ), "candidate wheel SHA256 must be lowercase hexadecimal"
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == expected, "candidate wheel SHA256 mismatch"
    python = executable.parent / ("python.exe" if os.name == "nt" else "python")
    assert python.is_absolute() and python.is_file(), "installed executable has no candidate Python"
    provenance = subprocess.run(
        (str(python), "-I", "-c", "from importlib.metadata import distribution; print(distribution('proofline').read_text('direct_url.json'))"),
        text=True, capture_output=True, check=False,
    )
    assert provenance.returncode == 0, provenance.stderr
    direct_url = json.loads(provenance.stdout)
    assert isinstance(direct_url, dict)
    assert direct_url.get("url") == wheel.resolve().as_uri()
    return wheel


def test_built_wheel_contains_and_reads_canonical_schema_templates(tmp_path: Path) -> None:
    wheel = _hosted_candidate_wheel()
    if wheel is not None:
        pass
    else:
        dist = tmp_path / "dist"
        built = subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(dist)],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        assert built.returncode == 0, built.stderr
        wheel = next(dist.glob("proofline-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert "proofline_schema_v1_templates/project/identities.json" in names
        assert "proofline_schema_v1_templates/artifacts/acceptance-criterion.md" in names
        assert "proofline_schema_v1_templates/artifacts/requirement.md" in names
        assert "proofline_home/skills/proofline-start-requirement/SKILL.md" in names
        assert "proofline_home/skills/proofline-approve-specification/SKILL.md" in names
        assert "proofline_home/skills/proofline-create-worktree/SKILL.md" in names
        assert (
            "proofline_home/skills/proofline-create-worktree/scripts/"
            "inspect_worktree_readiness.py"
        ) in names
        assert not any(name.endswith("audit_transition.py") for name in names)
        assert not any(name.endswith("/AGENTS.md") for name in names)
        assert archive.read("proofline_schema_v1_templates/project/identities.json") == (
            ROOT / "templates/schema-v1/project/identities.json"
        ).read_bytes()
        for source_root, packaged_root in (
            (ROOT / "docs/contracts", "proofline_home/contracts"),
            (ROOT / "docs/operations", "proofline_home/operations"),
            (ROOT / "templates", "proofline_home/templates"),
            (ROOT / "skills", "proofline_home/skills"),
        ):
            for source in source_root.rglob("*"):
                if source.is_file() and "__pycache__" not in source.parts and source.suffix != ".pyc":
                    packaged = f"{packaged_root}/{source.relative_to(source_root).as_posix()}"
                    assert archive.read(packaged) == source.read_bytes()


@pytest.mark.candidate_build_only
def test_built_sdist_contains_allocator_and_requirement_resources(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    built = subprocess.run(
        ["uv", "build", "--sdist", "--out-dir", str(dist)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    sdist = next(dist.glob("proofline-*.tar.gz"))
    with tarfile.open(sdist, "r:gz") as archive:
        names = archive.getnames()
    assert not any(name.endswith("audit_transition.py") for name in names)
    assert not any(name.endswith("/AGENTS.md") for name in names)
    for relative in (
        "templates/schema-v1/project/identities.json",
        "templates/schema-v1/artifacts/requirement.md",
        "templates/schema-v1/artifacts/acceptance-criterion.md",
        "skills/proofline-start-requirement/SKILL.md",
        "skills/proofline-create-worktree/SKILL.md",
        "skills/proofline-create-worktree/scripts/inspect_worktree_readiness.py",
    ):
        assert any(name.endswith(relative) for name in names)


def test_built_wheel_operations_match_source_inventory_and_payload_bytes(tmp_path: Path) -> None:
    wheel = _hosted_candidate_wheel()
    if wheel is not None:
        pass
    else:
        dist = tmp_path / "dist"
        built = subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(dist)],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        assert built.returncode == 0, built.stderr
        wheel = next(dist.glob("proofline-*.whl"))
    venv = tmp_path / "venv"
    assert subprocess.run(["uv", "venv", "--python", sys.executable, str(venv)], capture_output=True).returncode == 0
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    proofline = venv / ("Scripts/proofline.exe" if os.name == "nt" else "bin/proofline")
    installed = subprocess.run(["uv", "pip", "install", "--python", str(python), str(wheel)], text=True, capture_output=True)
    assert installed.returncode == 0, installed.stderr
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project, check=True)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    home = tmp_path / "home"
    home.mkdir()
    env["HOME"] = str(home)
    initialized_home = subprocess.run([str(proofline), "init"], cwd=project, env=env, text=True, capture_output=True)
    assert initialized_home.returncode == 0, initialized_home.stderr
    for relative in (
        "contracts/document-format.md",
        "templates/schema-v1/artifacts/discovery.md",
        "templates/schema-v1/artifacts/requirement.md",
        "templates/schema-v1/artifacts/acceptance-criterion.md",
        "skills/proofline-start-line/SKILL.md",
        "skills/proofline-start-requirement/SKILL.md",
        "skills/proofline-approve-specification/SKILL.md",
        "skills/proofline-create-worktree/SKILL.md",
        "skills/proofline-create-worktree/scripts/inspect_worktree_readiness.py",
    ):
        source = ROOT / (
            f"docs/{relative}" if relative.startswith("contracts/") else relative
        )
        assert (home / ".proofline" / relative).read_bytes() == source.read_bytes()
    assert not (
        home
        / ".proofline/skills/proofline-approve-specification/scripts/audit_transition.py"
    ).exists()
    assert (home / ".proofline/contracts/storage-and-retention.md").read_bytes() == (
        ROOT / "docs/contracts/storage-and-retention.md"
    ).read_bytes()
    for args in (("project", "init"), ("line", "init", "--title", "Wheel title")):
        result = subprocess.run([str(proofline), *args], cwd=project, env=env, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
    discovery = project / ".proofline/lines/line-0001/dcy-0001.md"
    discovery.write_text(discovery.read_text().replace("status: draft", "status: confirmed").replace("{{TODO:", "TODO_REMOVED:").replace("{{NEEDS_EVIDENCE:", "EVIDENCE_REMOVED:").replace("{{UNKNOWN:", "UNKNOWN_REMOVED:").replace("}}", ""))
    manifest = project / "admission.yaml"
    manifest.write_text("create: [Wheel criterion]\nupdate: []\nretire: []\nsatisfy: []\n")
    dry = subprocess.run([str(proofline), "requirement", "init", "line-0001", "--manifest", str(manifest), "--dry-run"], cwd=project, env=env, text=True, capture_output=True)
    assert dry.returncode == 0, dry.stderr
    assert not (project / ".proofline/criteria/ac-0001.md").exists()
    actual = subprocess.run([str(proofline), "requirement", "init", "line-0001", "--manifest", str(manifest)], cwd=project, env=env, text=True, capture_output=True)
    assert actual.returncode == 0, actual.stderr
    validated = subprocess.run([str(proofline), "validate"], cwd=project, env=env, text=True, capture_output=True)
    assert validated.returncode == 0, validated.stderr
    assert decode_allocator((project / ".proofline/identities.json").read_bytes()) == IdentityAllocator(2, 2)
    subprocess.run(["git", "config", "user.name", "ProofLine Test"], cwd=project, check=True)
    subprocess.run(
        ["git", "config", "user.email", "proofline@example.invalid"],
        cwd=project,
        check=True,
    )
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "managed helper fixture"], cwd=project, check=True)
    helper_env = env.copy()
    helper_env["PATH"] = str(proofline.parent) + os.pathsep + helper_env.get("PATH", "")
    helper = home / ".proofline/skills/proofline-create-worktree/scripts/inspect_worktree_readiness.py"
    inspected = subprocess.run(
        [
            str(python),
            str(helper),
            "--repository",
            str(project.resolve()),
            "--line",
            "line-0001",
        ],
        cwd=project,
        env=helper_env,
        text=True,
        capture_output=True,
    )
    assert inspected.returncode == 0, inspected.stderr
    payload = json.loads(inspected.stdout)
    assert payload["advisory"] is True
    assert payload["recommendation"] == "review"
    assert "requirement-not-approved" in payload["reasons"]
    assert "criterion-status-mismatch:ac-0001" in payload["reasons"]
