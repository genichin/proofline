from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from proofline import updater
from proofline.agent_skills import Inspection, SkillPayload


class Distribution:
    def read_text(self, filename: str) -> str:
        assert filename == "direct_url.json"
        return json.dumps({"url": "file:///proofline.whl", "archive_info": {}})


def test_target_wheel_skill_payload_is_path_and_byte_bound(tmp_path: Path) -> None:
    wheel = tmp_path / "proofline-0.9.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("skills/proofline-one/SKILL.md", b"one\n")
        archive.writestr("skills/proofline-one/scripts/helper.py", b"pass\n")

    payload = updater.skill_payload_from_wheel(wheel, "0.9.0")

    assert payload.files["proofline-one/SKILL.md"] == b"one\n"
    assert payload.source_identity.startswith(
        f"wheel:0.9.0:{hashlib.sha256(wheel.read_bytes()).hexdigest()}:"
    )


def test_target_wheel_rejects_incomplete_skill(tmp_path: Path) -> None:
    wheel = tmp_path / "proofline-0.9.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("skills/proofline-one/helper.py", b"pass\n")
    with pytest.raises(updater.UpdateError, match="incomplete"):
        updater.skill_payload_from_wheel(wheel, "0.9.0")


def test_update_check_reports_registered_blocker_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = SkillPayload("0.8.0", "package:test", {"proofline/SKILL.md": b"x"}, "0" * 64)
    blocked = Inspection(
        "codex",
        "user",
        "/target",
        "0.8.0",
        "drifted",
        ("hash mismatch",),
        "/state/codex/user.yaml",
    )
    monkeypatch.setattr(updater.metadata, "version", lambda name: "0.8.0")
    monkeypatch.setattr(updater.metadata, "distribution", lambda name: Distribution())
    monkeypatch.setattr(updater, "load_packaged_payload", lambda: payload)
    monkeypatch.setattr(updater, "inspect_registry", lambda payload: [blocked])

    result = updater.run_update(check=True, version="0.8.0")

    assert result.status == "agent-skills-blocked"
    assert result.exit_code == 1
    assert result.mutate is False


def test_release_and_checksum_parsing_remain_exact() -> None:
    version = "0.9.0"
    wheel = f"proofline-{version}-py3-none-any.whl"
    base = f"https://github.com/genichin/proofline/releases/download/v{version}"
    release = updater.parse_release(
        {
            "tag_name": f"v{version}",
            "draft": False,
            "prerelease": False,
            "assets": [
                {"name": wheel, "browser_download_url": f"{base}/{wheel}"},
                {"name": "SHA256SUMS", "browser_download_url": f"{base}/SHA256SUMS"},
            ],
        },
        version,
    )
    digest = "a" * 64
    assert release.wheel_name == wheel
    assert updater.parse_checksum(f"{digest}  {wheel}\n", wheel) == digest
