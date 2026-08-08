from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from proofline import agent_skills
from proofline.agent_skills import AgentTarget, SkillPayload


def payload(marker: bytes = b"current\n", version: str = "0.8.0") -> SkillPayload:
    files = {
        "proofline-one/SKILL.md": marker,
        "proofline-one/scripts/helper.py": b"print('ok')\n",
        "proofline-two/SKILL.md": b"second\n",
    }
    digest = agent_skills._payload_digest(files)
    return SkillPayload(version, f"package:{version}:archive:{digest}", files, digest)


@pytest.fixture
def codex_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    registry = tmp_path / "state/agent-skills"
    target = AgentTarget("codex", "user", "flat", tmp_path / "home/.agents/skills")
    current = payload()
    monkeypatch.setattr(agent_skills, "state_root", lambda environ=None: registry)
    monkeypatch.setattr(agent_skills, "resolve_target", lambda agent, scope=None, environ=None: target)
    monkeypatch.setattr(agent_skills, "load_packaged_payload", lambda: current)
    return registry, target, current


def test_flat_setup_status_repair_and_remove_preserve_unrelated_skill(
    codex_fixture,
) -> None:
    registry, target, current = codex_fixture
    unrelated = target.root / "user-skill/SKILL.md"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("user\n")

    result = agent_skills.setup("codex", "user")
    assert result.status == "healthy"
    assert unrelated.read_text() == "user\n"
    assert agent_skills.setup("codex", "user").status == "healthy"
    assert agent_skills.summarize(agent_skills.inspect_registry(payload=current)) == {
        "registered": 1,
        "healthy": 1,
        "outdated": 0,
        "blocked": 0,
    }

    managed = target.root / "proofline-one/SKILL.md"
    managed.write_text("drift\n")
    assert agent_skills.inspect_registry(payload=current)[0].status == "drifted"
    assert agent_skills.repair("codex", "user").status == "healthy"
    assert managed.read_bytes() == current.files["proofline-one/SKILL.md"]

    agent_skills.remove("codex", "user")
    assert unrelated.read_text() == "user\n"
    assert not (target.root / "proofline-one").exists()
    assert not (target.root / "proofline-two").exists()
    assert not list(registry.glob("*/*.yaml"))


def test_adopt_existing_requires_exact_payload(codex_fixture) -> None:
    _, target, current = codex_fixture
    for relative, content in current.files.items():
        path = target.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    before = {path.relative_to(target.root): path.read_bytes() for path in target.root.rglob("*") if path.is_file()}

    assert agent_skills.setup("codex", "user", adopt_existing=True).status == "healthy"
    after = {path.relative_to(target.root): path.read_bytes() for path in target.root.rglob("*") if path.is_file()}
    assert after == before


def test_adopt_and_remove_fail_closed_on_byte_drift(codex_fixture) -> None:
    _, target, current = codex_fixture
    for relative, content in current.files.items():
        path = target.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (target.root / "proofline-one/SKILL.md").write_text("changed\n")
    with pytest.raises(agent_skills.AgentSkillError, match="exactly match"):
        agent_skills.setup("codex", "user", adopt_existing=True)

    (target.root / "proofline-one/SKILL.md").write_bytes(current.files["proofline-one/SKILL.md"])
    agent_skills.setup("codex", "user", adopt_existing=True)
    (target.root / "proofline-one/SKILL.md").write_text("changed again\n")
    with pytest.raises(agent_skills.AgentSkillError, match="refusing removal"):
        agent_skills.remove("codex", "user")


def test_unregister_invalid_manifest_never_changes_target(codex_fixture) -> None:
    registry, target, _ = codex_fixture
    manifest = registry / "codex/user.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("schema_version: 999\n")
    target.root.mkdir(parents=True)
    marker = target.root / "proofline-user-data"
    marker.write_text("keep\n")

    assert agent_skills.unregister("codex", "user") is True
    assert marker.read_text() == "keep\n"
    assert not manifest.exists()


def test_status_marks_invalid_manifest_and_continues(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    bad = root / "codex/user.yaml"
    bad.parent.mkdir(parents=True)
    bad.write_text("schema_version: [broken\n")

    results = agent_skills.inspect_registry(root=root, payload=payload())
    assert [item.status for item in results] == ["invalid-manifest"]
    assert agent_skills.summarize(results)["blocked"] == 1


def test_state_root_uses_os_default_without_project_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(agent_skills.os, "name", "posix")
    assert agent_skills.state_root({"HOME": str(tmp_path)}) == tmp_path / ".local/state/proofline/agent-skills"
    assert agent_skills.state_root({"HOME": str(tmp_path), "XDG_STATE_HOME": str(tmp_path / "xdg")}) == tmp_path / "xdg/proofline/agent-skills"


def test_payload_digest_binds_paths_and_bytes() -> None:
    value = payload()
    assert len(value.digest) == 64
    assert value.digest != hashlib.sha256(b"".join(value.files.values())).hexdigest()
