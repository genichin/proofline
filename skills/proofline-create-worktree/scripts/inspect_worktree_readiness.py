"""Read-only advisory inspection for ProofLine linked-worktree readiness."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

LINE_ID = re.compile(r"line-[0-9]{4}\Z")
EXPECTED_STATUS = {
    "create": "active",
    "update": "active",
    "retire": "retired",
    "satisfy": "active",
}


class InspectionError(Exception):
    """A required observation could not be obtained or interpreted."""


def _run_git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ("git", "-C", str(repository), *arguments),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise InspectionError(f"cannot execute git: {exc}") from exc


def _git(repository: Path, *arguments: str) -> str:
    result = _run_git(repository, *arguments)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise InspectionError(f"git {' '.join(arguments)} failed: {detail}")
    return result.stdout


def _frontmatter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InspectionError(f"cannot read {path}: {exc}") from exc
    parts = text.split("---", 2)
    if len(parts) != 3 or parts[0].strip():
        raise InspectionError(f"invalid frontmatter: {path}")
    try:
        value = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        raise InspectionError(f"invalid YAML: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InspectionError(f"frontmatter must be an object: {path}")
    return value


def _string(metadata: dict[str, Any], key: str, path: Path) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value:
        raise InspectionError(f"invalid {key} in {path}")
    return value


def _worktrees(repository: Path) -> list[dict[str, str]]:
    output = _git(repository, "worktree", "list", "--porcelain")
    records: list[dict[str, str]] = []
    record: dict[str, str] = {}
    for line in output.splitlines():
        if not line:
            if record:
                records.append(record)
                record = {}
            continue
        key, separator, value = line.partition(" ")
        if key in record:
            raise InspectionError("cannot interpret git worktree list --porcelain")
        record[key] = value if separator else ""
    if record:
        records.append(record)
    if not records or "worktree" not in records[0]:
        raise InspectionError("git worktree list has no primary entry")
    for item in records:
        if "worktree" not in item or "HEAD" not in item:
            raise InspectionError("git worktree entry is incomplete")
    return records


def _canonical_observations(primary: Path, line_id: str) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    line_root = primary / ".proofline" / "lines" / line_id
    requirements = sorted(line_root.glob("req-*.md"))
    if len(requirements) != 1:
        raise InspectionError(f"expected exactly one REQ for {line_id}")
    requirement_path = requirements[0]
    requirement_metadata = _frontmatter(requirement_path)
    requirement_id = _string(requirement_metadata, "id", requirement_path)
    requirement_status = _string(requirement_metadata, "status", requirement_path)
    discovery_id = _string(requirement_metadata, "discovery", requirement_path)
    discovery_path = line_root / f"{discovery_id}.md"
    discovery_metadata = _frontmatter(discovery_path)
    discovery_status = _string(discovery_metadata, "status", discovery_path)

    criteria_value = requirement_metadata.get("criteria")
    if not isinstance(criteria_value, dict) or set(criteria_value) - set(EXPECTED_STATUS):
        raise InspectionError(f"invalid criteria mapping in {requirement_path}")
    criteria: list[dict[str, Any]] = []
    for operation, expected in EXPECTED_STATUS.items():
        identifiers = criteria_value.get(operation, [])
        if not isinstance(identifiers, list) or any(
            not isinstance(identifier, str) for identifier in identifiers
        ):
            raise InspectionError(f"invalid criteria.{operation} in {requirement_path}")
        for identifier in identifiers:
            criterion_path = primary / ".proofline" / "criteria" / f"{identifier}.md"
            criterion_metadata = _frontmatter(criterion_path)
            actual = _string(criterion_metadata, "status", criterion_path)
            criteria.append(
                {
                    "id": identifier,
                    "path": str(criterion_path.resolve()),
                    "operation": operation,
                    "expected_status": expected,
                    "status": actual,
                    "ready": actual == expected,
                }
            )

    discovery = {
        "id": discovery_id,
        "path": str(discovery_path.resolve()),
        "status": discovery_status,
        "confirmed": discovery_status == "confirmed",
    }
    requirement = {
        "id": requirement_id,
        "path": str(requirement_path.resolve()),
        "status": requirement_status,
        "approved": requirement_status == "approved",
    }
    return discovery, requirement, criteria


def _validate(primary: Path) -> dict[str, Any]:
    executable = shutil.which("proofline")
    if executable is None:
        raise InspectionError("proofline executable is not available")
    try:
        result = subprocess.run(
            (executable, "validate"),
            cwd=primary,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise InspectionError(f"cannot execute proofline validate: {exc}") from exc
    return {"passed": result.returncode == 0, "returncode": result.returncode}


def inspect(repository_argument: str, line_id: str) -> dict[str, Any]:
    if not LINE_ID.fullmatch(line_id):
        raise InspectionError("--line must match line-NNNN")
    repository = Path(repository_argument).expanduser().resolve()
    if not repository.is_dir():
        raise InspectionError(f"repository is not a directory: {repository}")
    top_level = Path(_git(repository, "rev-parse", "--show-toplevel").strip()).resolve()
    if top_level != repository:
        raise InspectionError(f"--repository must be the repository root: {top_level}")

    worktrees = _worktrees(repository)
    primary_record = worktrees[0]
    primary = Path(primary_record["worktree"]).resolve()
    head = primary_record["HEAD"]
    branch_ref = primary_record.get("branch")
    branch = branch_ref.removeprefix("refs/heads/") if branch_ref else None
    clean = not _git(primary, "status", "--porcelain", "--untracked-files=all")

    discovery, requirement, criteria = _canonical_observations(primary, line_id)
    validation = _validate(primary)

    target_ref = f"refs/heads/line/{line_id}-implementation"
    target_path = primary / ".worktrees" / line_id
    branch_probe = _run_git(primary, "show-ref", "--verify", "--quiet", target_ref)
    if branch_probe.returncode not in {0, 1}:
        detail = branch_probe.stderr.strip() or "cannot inspect target ref"
        raise InspectionError(detail)
    branch_available = branch_probe.returncode == 1
    path_available = not os.path.lexists(target_path)
    registration_available = all(
        Path(item["worktree"]).resolve() != target_path.resolve()
        and item.get("branch") != target_ref
        for item in worktrees
    )
    ignore_probe = _run_git(primary, "check-ignore", "-q", "--no-index", f".worktrees/{line_id}")
    if ignore_probe.returncode not in {0, 1}:
        detail = ignore_probe.stderr.strip() or "cannot inspect ignore rules"
        raise InspectionError(detail)
    ignored = ignore_probe.returncode == 0

    reasons: list[str] = []
    if not validation["passed"]:
        reasons.append("canonical-validation-failed")
    if not discovery["confirmed"]:
        reasons.append("discovery-not-confirmed")
    if not requirement["approved"]:
        reasons.append("requirement-not-approved")
    reasons.extend(
        f"criterion-status-mismatch:{criterion['id']}"
        for criterion in criteria
        if not criterion["ready"]
    )
    if branch != "main":
        reasons.append("primary-branch-not-main")
    if not clean:
        reasons.append("primary-worktree-dirty")
    if not ignored:
        reasons.append("worktrees-ignore-missing")
    if not branch_available:
        reasons.append("target-branch-unavailable")
    if not path_available:
        reasons.append("target-path-unavailable")
    if not registration_available:
        reasons.append("target-registration-unavailable")

    observations = {
        "repository_root": str(repository),
        "line_id": line_id,
        "canonical_validation": validation,
        "discovery": discovery,
        "requirement": requirement,
        "criteria": criteria,
        "primary_worktree": {
            "path": str(primary),
            "branch": branch,
            "head": head,
            "clean": clean,
        },
        "target": {
            "ref": target_ref,
            "path": str(target_path.resolve()),
            "branch_available": branch_available,
            "path_available": path_available,
            "registration_available": registration_available,
        },
        "ignore": {"pattern": "/.worktrees/", "ignored": ignored},
    }
    return {
        "advisory": True,
        "recommendation": "review" if reasons else "create",
        "observations": observations,
        "reasons": reasons,
    }


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--line", required=True)
    namespace = parser.parse_args(arguments)
    try:
        payload = inspect(namespace.repository, namespace.line)
    except InspectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    for reason in payload["reasons"]:
        print(f"advisory: {reason}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
