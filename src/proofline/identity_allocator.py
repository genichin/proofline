"""Canonical monotonic Line and AC identity allocator."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from .transaction import read_regular

ALLOCATOR_PATH = ".proofline/identities.json"
LEGACY_PATH = ".proofline/line-identities.json"
MAX_ALLOCATED_NUMBER = 9999
EXHAUSTED_SENTINEL = 10000


@dataclass(frozen=True)
class IdentityAllocator:
    next_line_number: int
    next_ac_number: int


@dataclass(frozen=True)
class AllocatorSnapshot:
    allocator: IdentityAllocator
    data: bytes
    identity: tuple[int, int]


@dataclass
class IdentityAllocatorError(Exception):
    code: str
    path: str = ALLOCATOR_PATH
    message: str = "canonical identity allocator가 올바르지 않습니다."

    def __str__(self) -> str:
        return f"{self.path}: {self.code}: {self.message}"


def encode_allocator(allocator: IdentityAllocator) -> bytes:
    for value in (allocator.next_line_number, allocator.next_ac_number):
        if type(value) is not int or not 1 <= value <= EXHAUSTED_SENTINEL:
            raise IdentityAllocatorError("allocator.counter.range")
    value = {
        "schema_version": 1,
        "next_line_number": allocator.next_line_number,
        "next_ac_number": allocator.next_ac_number,
    }
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def decode_allocator(data: bytes) -> IdentityAllocator:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise IdentityAllocatorError("allocator.malformed") from exc
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "next_line_number", "next_ac_number"
    } or value.get("schema_version") != 1:
        raise IdentityAllocatorError("allocator.malformed")
    allocator = IdentityAllocator(
        value.get("next_line_number"), value.get("next_ac_number")
    )
    try:
        canonical = encode_allocator(allocator)
    except IdentityAllocatorError as exc:
        raise exc
    if canonical != data:
        raise IdentityAllocatorError("allocator.noncanonical")
    return allocator


def _max_number(root: Path, parent: str, pattern: str) -> int:
    directory = root / parent
    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        raise IdentityAllocatorError("allocator.scan.failed", parent) from exc
    expression = re.compile(pattern)
    numbers = [int(match.group(1)) for entry in entries if (match := expression.fullmatch(entry.name))]
    return max(numbers, default=0)


def current_maxima(root: Path) -> tuple[int, int]:
    return (
        _max_number(root, ".proofline/lines", r"line-((?!0000)\d{4})"),
        _max_number(root, ".proofline/criteria", r"ac-((?!0000)\d{4})\.md"),
    )


def migrated_allocator(root: Path) -> IdentityAllocator:
    line_max, ac_max = current_maxima(root)
    return IdentityAllocator(min(line_max + 1, EXHAUSTED_SENTINEL), min(ac_max + 1, EXHAUSTED_SENTINEL))


def read_allocator(root: Path) -> AllocatorSnapshot:
    path = root / ALLOCATOR_PATH
    try:
        state = path.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise IdentityAllocatorError("allocator.missing") from exc
    except OSError as exc:
        raise IdentityAllocatorError("allocator.type") from exc
    if stat.S_ISLNK(state.st_mode):
        raise IdentityAllocatorError("allocator.symlink")
    if not stat.S_ISREG(state.st_mode):
        raise IdentityAllocatorError("allocator.type")
    try:
        snapshot = read_regular(path)
    except OSError as exc:
        raise IdentityAllocatorError("allocator.type") from exc
    if snapshot.identity != (state.st_dev, state.st_ino):
        raise IdentityAllocatorError("allocator.concurrent.changed")
    return AllocatorSnapshot(decode_allocator(snapshot.data), snapshot.data, snapshot.identity)


def validate_allocator(root: Path) -> list[IdentityAllocatorError]:
    legacy = root / LEGACY_PATH
    try:
        legacy_state = legacy.stat(follow_symlinks=False)
    except FileNotFoundError:
        legacy_state = None
    except OSError:
        return [IdentityAllocatorError("legacy.type", LEGACY_PATH)]
    if legacy_state is not None:
        if stat.S_ISLNK(legacy_state.st_mode):
            return [IdentityAllocatorError("legacy.symlink", LEGACY_PATH)]
        if not stat.S_ISREG(legacy_state.st_mode):
            return [IdentityAllocatorError("legacy.type", LEGACY_PATH)]
    try:
        snapshot = read_allocator(root)
    except IdentityAllocatorError as exc:
        return [exc]
    try:
        line_max, ac_max = current_maxima(root)
    except IdentityAllocatorError as exc:
        return [exc]
    errors: list[IdentityAllocatorError] = []
    if snapshot.allocator.next_line_number <= line_max:
        errors.append(IdentityAllocatorError("allocator.line.regressed"))
    if snapshot.allocator.next_ac_number <= ac_max:
        errors.append(IdentityAllocatorError("allocator.ac.regressed"))
    return errors


def advanced(snapshot: AllocatorSnapshot, *, lines: int = 0, acs: int = 0) -> bytes:
    line = snapshot.allocator.next_line_number
    ac = snapshot.allocator.next_ac_number
    if lines and (line > MAX_ALLOCATED_NUMBER or line + lines > EXHAUSTED_SENTINEL):
        raise IdentityAllocatorError("allocator.line.exhausted")
    if acs and (ac > MAX_ALLOCATED_NUMBER or ac + acs > EXHAUSTED_SENTINEL):
        raise IdentityAllocatorError("allocator.ac.exhausted")
    return encode_allocator(IdentityAllocator(line + lines, ac + acs))
