from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from proofline.updater import (
    UpdateError,
    decide_update,
    detect_provenance,
    is_uv_tool_process,
    parse_checksum,
    parse_release,
    parse_version,
)


def release_payload(version: str = "0.2.0") -> dict[str, object]:
    wheel = f"proofline-{version}-py3-none-any.whl"
    base = f"https://github.com/genichin/proofline/releases/download/v{version}"
    return {
        "tag_name": f"v{version}",
        "draft": False,
        "prerelease": False,
        "assets": [
            {"name": wheel, "browser_download_url": f"{base}/{wheel}"},
            {"name": "SHA256SUMS", "browser_download_url": f"{base}/SHA256SUMS"},
        ],
    }


def test_parse_version_is_strict_stable_semver() -> None:
    assert parse_version("0.2.0") == (0, 2, 0)
    for value in ["v0.2.0", "0.2", "0.2.0rc1", "01.2.3"]:
        with pytest.raises(UpdateError):
            parse_version(value)


def test_parse_release_requires_exact_stable_assets_and_urls() -> None:
    release = parse_release(release_payload(), "0.2.0")
    assert release.version == "0.2.0"
    assert release.wheel_name == "proofline-0.2.0-py3-none-any.whl"

    invalid = release_payload()
    invalid["prerelease"] = True
    with pytest.raises(UpdateError, match="stable"):
        parse_release(invalid, "0.2.0")

    invalid = release_payload()
    invalid["assets"][0]["browser_download_url"] = "https://example.com/bad.whl"  # type: ignore[index]
    with pytest.raises(UpdateError, match="URL"):
        parse_release(invalid, "0.2.0")


def test_checksum_contract_accepts_only_expected_wheel() -> None:
    data = b"wheel bytes"
    digest = hashlib.sha256(data).hexdigest()
    assert parse_checksum(f"{digest}  proofline-0.2.0-py3-none-any.whl\n", "proofline-0.2.0-py3-none-any.whl") == digest
    with pytest.raises(UpdateError):
        parse_checksum(f"{digest}  other.whl\n", "proofline-0.2.0-py3-none-any.whl")
    with pytest.raises(UpdateError):
        parse_checksum(f"{digest}  proofline-0.2.0-py3-none-any.whl\nextra\n", "proofline-0.2.0-py3-none-any.whl")


def test_decision_preserves_source_without_explicit_adoption() -> None:
    result = decide_update("0.1.0", "0.2.0", "source", check=False, adopt=False)
    assert result.status == "adoption-required"
    assert result.exit_code == 1
    assert not result.mutate


def test_decision_handles_check_current_update_and_downgrade() -> None:
    assert decide_update("0.1.0", "0.2.0", "archive", check=True, adopt=False).status == "update-available"
    assert decide_update("0.2.0", "0.2.0", "archive", check=False, adopt=False).status == "already-current"
    assert decide_update("0.2.0", "0.2.0", "source", check=False, adopt=True).mutate
    with pytest.raises(UpdateError, match="downgrade"):
        decide_update("0.2.0", "0.1.0", "archive", check=False, adopt=False)


def test_detect_provenance_distinguishes_source_archive_and_unknown() -> None:
    class Dist:
        def __init__(self, value: dict[str, object] | None) -> None:
            self.value = value

        def read_text(self, name: str) -> str | None:
            assert name == "direct_url.json"
            return None if self.value is None else json.dumps(self.value)

    assert detect_provenance(Dist({"url": "file:///src", "dir_info": {}})) == "source"  # type: ignore[arg-type]
    assert detect_provenance(Dist({"url": "file:///x.whl", "archive_info": {}})) == "archive"  # type: ignore[arg-type]
    assert detect_provenance(Dist(None)) == "unknown"  # type: ignore[arg-type]


def test_uv_tool_ownership_uses_virtualenv_prefix_not_resolved_python(tmp_path: Path) -> None:
    tool_dir = tmp_path / "tools"
    prefix = tool_dir / "proofline"
    assert is_uv_tool_process(tool_dir, prefix=prefix)
    assert not is_uv_tool_process(tool_dir, prefix=tmp_path / "application-venv")
