from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

import proofline.home_protocol as home_protocol
from proofline.home_protocol import HomeProtocolError, handle_request, read_safe_tree
from proofline.home_writer import build_home_payload


def payload(version: str = "0.7.0") -> dict[str, bytes]:
    resources = {
        "agent-context.md": b"agent\n",
        "contracts/storage.md": b"contract\n",
        "operations/official-wheel-release.md": b"release\n",
        "operations/proofline-tool-environment.md": b"environment\n",
        "templates/schema-v1/artifacts/line.md": b"template\n",
        "skills/proofline-start-line/SKILL.md": b"skill\n",
    }
    return build_home_payload(version, resources)


def request(root: Path, operation: str = "generate") -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation": operation,
        "nonce": "a" * 64,
        "target_version": "0.7.0",
        "wheel_sha256": "b" * 64,
        "wheel_path": str((root.parent / "target.whl").absolute()),
        "payload_root": str(root.absolute()),
        "manifest_version": None,
        "payload_digest": None,
        "file_count": None,
    }


def test_generate_and_verify_are_closed_and_identity_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "payload"
    root.mkdir()
    expected = payload()
    monkeypatch.setattr(home_protocol.metadata, "version", lambda name: "0.7.0")
    monkeypatch.setattr(home_protocol, "_installed_wheel_sha256", lambda path: "b" * 64)
    monkeypatch.setattr(home_protocol.home_writer, "_payload", lambda: expected)

    generated = handle_request(request(root))

    assert set(generated) == home_protocol.PROTOCOL_KEYS
    assert generated["manifest_version"] == "0.7.0"
    assert generated["file_count"] == len(expected)
    assert generated["payload_digest"] == read_safe_tree(root)[1]
    verify = {**generated, "operation": "verify", "nonce": "c" * 64}
    assert handle_request(verify) == verify


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"unknown": None}, "fields"),
        ({"operation": "remove"}, "operation"),
        ({"schema_version": 2}, "schema"),
        ({"target_version": "0.8.0"}, "version mismatch"),
    ],
)
def test_malformed_unknown_or_mismatched_request_is_rejected_before_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, object],
    match: str,
) -> None:
    root = tmp_path / "payload"
    root.mkdir()
    monkeypatch.setattr(home_protocol.metadata, "version", lambda name: "0.7.0")
    monkeypatch.setattr(home_protocol, "_installed_wheel_sha256", lambda path: "b" * 64)
    value = {**request(root), **change}

    with pytest.raises(HomeProtocolError, match=match):
        handle_request(value)

    assert not list(root.iterdir())


def test_verify_rejects_digest_and_count_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "payload"
    root.mkdir()
    expected = payload()
    monkeypatch.setattr(home_protocol.metadata, "version", lambda name: "0.7.0")
    monkeypatch.setattr(home_protocol, "_installed_wheel_sha256", lambda path: "b" * 64)
    monkeypatch.setattr(home_protocol.home_writer, "_payload", lambda: expected)
    generated = handle_request(request(root))

    for field, value in [("payload_digest", "0" * 64), ("file_count", 999)]:
        verify = {
            **generated,
            "operation": "verify",
            "nonce": "d" * 64,
            field: value,
        }
        with pytest.raises(HomeProtocolError, match="identity mismatch"):
            handle_request(verify)


def test_request_rejects_installed_wheel_digest_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "payload"
    root.mkdir()
    monkeypatch.setattr(home_protocol.metadata, "version", lambda name: "0.7.0")
    monkeypatch.setattr(home_protocol, "_installed_wheel_sha256", lambda path: "c" * 64)

    with pytest.raises(HomeProtocolError, match="wheel digest mismatch"):
        handle_request(request(root))

    assert not list(root.iterdir())


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_safe_tree_digest_rejects_symlink_and_special_paths(
    tmp_path: Path, kind: str
) -> None:
    root = tmp_path / "payload"
    root.mkdir()
    (root / "manifest.yaml").write_text(yaml.safe_dump({"proofline_version": "0.7.0"}))
    unsafe = root / "unsafe"
    if kind == "symlink":
        unsafe.symlink_to(root / "manifest.yaml")
    else:
        os.mkfifo(unsafe)

    with pytest.raises(HomeProtocolError, match="symlink|special"):
        read_safe_tree(root)
