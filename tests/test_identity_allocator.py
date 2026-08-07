from __future__ import annotations

import os
from pathlib import Path

import pytest

from proofline.identity_allocator import (
    AllocatorSnapshot,
    IdentityAllocator,
    IdentityAllocatorError,
    decode_allocator,
    encode_allocator,
    advanced,
    migrated_allocator,
    validate_allocator,
)

EXPECTED = b'{\n  "schema_version": 1,\n  "next_line_number": 2,\n  "next_ac_number": 4\n}\n'


def project(tmp_path: Path) -> Path:
    (tmp_path / ".proofline/lines").mkdir(parents=True)
    (tmp_path / ".proofline/criteria").mkdir()
    return tmp_path


def test_codec_is_exact_canonical_json() -> None:
    allocator = IdentityAllocator(2, 4)
    assert encode_allocator(allocator) == EXPECTED
    assert decode_allocator(EXPECTED) == allocator
    for value in (
        EXPECTED.rstrip(b"\n"),
        EXPECTED.replace(b"  \"next_ac_number\"", b" \"next_ac_number\""),
        EXPECTED.replace(b"4", b"0"),
        EXPECTED.replace(b"4", b"10001"),
        EXPECTED.replace(b"\n}", b',\n  "extra": true\n}'),
        b"\xff",
    ):
        with pytest.raises(IdentityAllocatorError):
            decode_allocator(value)


@pytest.mark.parametrize("line,ac", [(1, 1), (9999, 9999), (10000, 10000)])
def test_codec_accepts_counter_domain(line: int, ac: int) -> None:
    value = IdentityAllocator(line, ac)
    assert decode_allocator(encode_allocator(value)) == value


def test_validator_requires_allocator_and_rejects_symlink_and_wrong_type(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert {error.code for error in validate_allocator(root)} == {"allocator.missing"}
    outside = tmp_path.parent / "outside.json"
    outside.write_bytes(EXPECTED)
    (root / ".proofline/identities.json").symlink_to(outside)
    assert {error.code for error in validate_allocator(root)} == {"allocator.symlink"}
    (root / ".proofline/identities.json").unlink()
    (root / ".proofline/identities.json").mkdir()
    assert {error.code for error in validate_allocator(root)} == {"allocator.type"}


def test_validator_rejects_counter_regression(tmp_path: Path) -> None:
    root = project(tmp_path)
    (root / ".proofline/lines/line-0002").mkdir()
    (root / ".proofline/criteria/ac-0004.md").write_text("opaque")
    (root / ".proofline/identities.json").write_bytes(EXPECTED)
    assert {error.code for error in validate_allocator(root)} == {
        "allocator.line.regressed", "allocator.ac.regressed"
    }


def test_migration_uses_current_paths_only_and_legacy_is_opaque(tmp_path: Path) -> None:
    root = project(tmp_path)
    (root / ".proofline/lines/line-0028").mkdir()
    (root / ".proofline/criteria/ac-0024.md").write_text("not parsed")
    legacy = root / ".proofline/line-identities.json"
    legacy.write_bytes(os.urandom(31))
    assert migrated_allocator(root) == IdentityAllocator(29, 25)
    (root / ".proofline/identities.json").write_bytes(
        encode_allocator(IdentityAllocator(29, 25))
    )
    assert validate_allocator(root) == []


def test_legacy_must_be_regular_not_symlink(tmp_path: Path) -> None:
    root = project(tmp_path)
    (root / ".proofline/identities.json").write_bytes(
        encode_allocator(IdentityAllocator(1, 1))
    )
    outside = tmp_path.parent / "legacy"
    outside.write_text("opaque")
    (root / ".proofline/line-identities.json").symlink_to(outside)
    assert {error.code for error in validate_allocator(root)} == {"legacy.symlink"}


@pytest.mark.parametrize(
    "value",
    [
        b"true\n",
        b"null\n",
        b"[]\n",
        b'{"schema_version":1,"next_line_number":true,"next_ac_number":1}\n',
        b'{"schema_version":1,"next_line_number":"1","next_ac_number":1}\n',
        b'{"schema_version":1,"next_line_number":1.0,"next_ac_number":1}\n',
        b'{"schema_version":1,"next_line_number":1}\n',
        b'{"schema_version":2,"next_line_number":1,"next_ac_number":1}\n',
    ],
)
def test_allocator_malformed_matrix_is_rejected(value: bytes) -> None:
    with pytest.raises(IdentityAllocatorError):
        decode_allocator(value)


def test_exact_exhaustion_boundary_allows_one_but_not_two() -> None:
    data = encode_allocator(IdentityAllocator(9999, 9999))
    snapshot = AllocatorSnapshot(IdentityAllocator(9999, 9999), data, (1, 2))
    assert decode_allocator(advanced(snapshot, lines=1, acs=1)) == IdentityAllocator(10000, 10000)
    with pytest.raises(IdentityAllocatorError, match="allocator.ac.exhausted"):
        advanced(snapshot, acs=2)


def test_scan_failure_is_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path)
    (root / ".proofline/identities.json").write_bytes(encode_allocator(IdentityAllocator(1, 1)))
    original = Path.iterdir

    def fail(path: Path):
        if path == root / ".proofline/lines":
            raise PermissionError("injected")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", fail)
    assert [error.code for error in validate_allocator(root)] == ["allocator.scan.failed"]
