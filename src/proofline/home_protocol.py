from __future__ import annotations

import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import yaml

from proofline import home_writer


SCHEMA_VERSION = 1
PROTOCOL_KEYS = {
    "schema_version",
    "operation",
    "nonce",
    "target_version",
    "wheel_sha256",
    "wheel_path",
    "payload_root",
    "manifest_version",
    "payload_digest",
    "file_count",
}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\Z")


class HomeProtocolError(RuntimeError):
    pass


def load_closed_json(raw: str) -> dict[str, Any]:
    def object_value(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise HomeProtocolError("duplicate JSON field")
            value[key] = item
        return value

    try:
        value = json.loads(
            raw,
            object_pairs_hook=object_value,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                HomeProtocolError(f"invalid JSON constant: {constant}")
            ),
        )
    except json.JSONDecodeError as exc:
        raise HomeProtocolError("malformed JSON") from exc
    if not isinstance(value, dict):
        raise HomeProtocolError("JSON message must be an object")
    return value


def read_safe_tree(root: Path) -> tuple[dict[str, bytes], str, int]:
    try:
        root_state = root.stat(follow_symlinks=False)
    except OSError as exc:
        raise HomeProtocolError(f"cannot access payload root: {exc}") from exc
    if not stat.S_ISDIR(root_state.st_mode):
        raise HomeProtocolError("payload root is not a directory")

    payload: dict[str, bytes] = {}

    def visit(directory: Path, parts: tuple[str, ...]) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise HomeProtocolError(f"cannot scan payload tree: {exc}") from exc
        if parts and not entries:
            raise HomeProtocolError(f"empty payload directory is forbidden: {'/'.join(parts)}")
        for entry in entries:
            if entry.name in {"", ".", ".."} or "/" in entry.name or "\x00" in entry.name:
                raise HomeProtocolError("unsafe payload path")
            child_parts = (*parts, entry.name)
            relative = "/".join(child_parts)
            try:
                value = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise HomeProtocolError(f"cannot inspect payload path: {relative}") from exc
            if stat.S_ISLNK(value.st_mode):
                raise HomeProtocolError(f"payload symlink is forbidden: {relative}")
            if stat.S_ISDIR(value.st_mode):
                visit(Path(entry.path), child_parts)
            elif stat.S_ISREG(value.st_mode):
                try:
                    payload[relative] = Path(entry.path).read_bytes()
                except OSError as exc:
                    raise HomeProtocolError(f"cannot read payload file: {relative}") from exc
            else:
                raise HomeProtocolError(f"special payload path is forbidden: {relative}")

    visit(root, ())
    if not payload:
        raise HomeProtocolError("payload tree is empty")
    digest = hashlib.sha256()
    for relative, content in sorted(payload.items()):
        encoded = relative.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return payload, digest.hexdigest(), len(payload)


def _manifest_version(payload: dict[str, bytes]) -> str:
    try:
        manifest = yaml.safe_load(payload["manifest.yaml"])
        value = manifest["proofline_version"]
    except (KeyError, TypeError, yaml.YAMLError, UnicodeDecodeError) as exc:
        raise HomeProtocolError("target HOME manifest is invalid") from exc
    if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
        raise HomeProtocolError("target HOME manifest version is invalid")
    return value


def _installed_wheel_sha256(expected_path: Path) -> str:
    raw = metadata.distribution("proofline").read_text("direct_url.json")
    try:
        value = json.loads(raw or "")
        archive = value["archive_info"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise HomeProtocolError("installed target archive provenance is invalid") from exc
    hashes = archive.get("hashes") if isinstance(archive, dict) else None
    observed = hashes.get("sha256") if isinstance(hashes, dict) else None
    legacy = archive.get("hash") if isinstance(archive, dict) else None
    if observed is None and isinstance(legacy, str) and legacy.startswith("sha256="):
        observed = legacy.removeprefix("sha256=")
    url = value.get("url") if isinstance(value, dict) else None
    parsed = urlparse(url) if isinstance(url, str) else None
    installed_path = (
        Path(url2pathname(unquote(parsed.path))).resolve()
        if parsed is not None
        and parsed.scheme == "file"
        and parsed.netloc in {"", "localhost"}
        else None
    )
    if installed_path != expected_path.resolve():
        raise HomeProtocolError("installed target wheel path mismatch")
    try:
        actual = hashlib.sha256(expected_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise HomeProtocolError("installed target wheel cannot be read") from exc
    if observed is not None and observed != actual:
        raise HomeProtocolError("installed target wheel provenance digest mismatch")
    return actual


def _validate_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != PROTOCOL_KEYS:
        raise HomeProtocolError("protocol request fields are invalid")
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise HomeProtocolError("unsupported protocol schema")
    if value["operation"] not in {"generate", "verify"}:
        raise HomeProtocolError("unknown protocol operation")
    if not isinstance(value["nonce"], str) or _SHA256.fullmatch(value["nonce"]) is None:
        raise HomeProtocolError("protocol nonce is invalid")
    if not isinstance(value["target_version"], str) or _VERSION.fullmatch(value["target_version"]) is None:
        raise HomeProtocolError("protocol target version is invalid")
    if not isinstance(value["wheel_sha256"], str) or _SHA256.fullmatch(value["wheel_sha256"]) is None:
        raise HomeProtocolError("protocol wheel digest is invalid")
    if not isinstance(value["wheel_path"], str) or not Path(value["wheel_path"]).is_absolute():
        raise HomeProtocolError("protocol wheel path is invalid")
    if not isinstance(value["payload_root"], str) or not Path(value["payload_root"]).is_absolute():
        raise HomeProtocolError("protocol payload root is invalid")
    expected_version = metadata.version("proofline")
    if value["target_version"] != expected_version:
        raise HomeProtocolError("installed target version mismatch")
    if value["wheel_sha256"] != _installed_wheel_sha256(Path(value["wheel_path"])):
        raise HomeProtocolError("installed target wheel digest mismatch")
    if value["operation"] == "generate":
        if any(value[key] is not None for key in ("manifest_version", "payload_digest", "file_count")):
            raise HomeProtocolError("generate request result fields must be null")
    else:
        if value["manifest_version"] != value["target_version"]:
            raise HomeProtocolError("protocol manifest version mismatch")
        if not isinstance(value["payload_digest"], str) or _SHA256.fullmatch(value["payload_digest"]) is None:
            raise HomeProtocolError("protocol payload digest is invalid")
        if type(value["file_count"]) is not int or value["file_count"] <= 0:
            raise HomeProtocolError("protocol file count is invalid")
    return value


def handle_request(value: Any) -> dict[str, Any]:
    request_value = _validate_request(value)
    root = Path(request_value["payload_root"])
    expected = home_writer._payload()
    manifest_version = _manifest_version(expected)
    if manifest_version != request_value["target_version"]:
        raise HomeProtocolError("target HOME version mismatch")

    if request_value["operation"] == "generate":
        if root.is_symlink() or not root.is_dir() or any(root.iterdir()):
            raise HomeProtocolError("generate payload root must be an empty directory")
        home_writer._write_stage(root, expected)

    payload, payload_digest, file_count = read_safe_tree(root)
    home_writer._verify_existing(root, expected)
    if request_value["operation"] == "verify":
        if payload_digest != request_value["payload_digest"] or file_count != request_value["file_count"]:
            raise HomeProtocolError("verified payload identity mismatch")
        if _manifest_version(payload) != request_value["manifest_version"]:
            raise HomeProtocolError("verified manifest version mismatch")

    return {
        **request_value,
        "manifest_version": manifest_version,
        "payload_digest": payload_digest,
        "file_count": file_count,
    }


def main() -> int:
    try:
        raw = sys.stdin.read()
        request_value = load_closed_json(raw)
        response = handle_request(request_value)
    except (HomeProtocolError, home_writer.HomeInitError, OSError) as exc:
        print(f"home protocol error: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
