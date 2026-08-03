from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from proofline.identity_ledger import (
    AUTHORITY_REF,
    IdentityLedgerError,
    bootstrap_allocation_ids,
    decode_ledger,
    encode_ledger,
    require_allocation_authority,
    require_allocation_preflight,
    validate_ledger,
)
from proofline.validator import validate_project


def git(project: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=project, text=True, capture_output=True, check=check
    )


def init_repo(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".proofline/lines").mkdir(parents=True)
    (project / ".proofline/criteria").mkdir()
    (project / "proofline.yaml").write_text(
        "schema_version: 1\nartifact_root: .proofline\n", encoding="utf-8"
    )
    git(project, "init", "-q", "-b", "main")
    git(project, "config", "user.email", "proofline@example.invalid")
    git(project, "config", "user.name", "ProofLine Test")
    commit_all(project, "initial")
    return project


def commit_all(project: Path, message: str) -> None:
    git(project, "add", "-A")
    git(project, "commit", "-qm", message)


def add_pair(project: Path, line_id: str) -> None:
    suffix = line_id.removeprefix("line-")
    target = project / ".proofline/lines" / line_id
    target.mkdir(parents=True)
    (target / f"{line_id}.md").write_text(
        f'---\nid: "{line_id}"\nexecution_status: not_started\n---\n', encoding="utf-8"
    )
    (target / f"dcy-{suffix}.md").write_text(
        f'---\nid: "dcy-{suffix}"\nstatus: draft\n---\n\n# title\n\n## Problem\n\n{{{{TODO: x}}}}\n\n## Evidence\n\n{{{{NEEDS_EVIDENCE: x}}}}\n\n## Scope\n\n{{{{TODO: x}}}}\n\n## Out of Scope\n\n{{{{TODO: x}}}}\n',
        encoding="utf-8",
    )


def write_ledger(project: Path, ids: set[str] | tuple[str, ...]) -> None:
    (project / ".proofline/line-identities.json").write_bytes(encode_ledger(ids))


def codes(project: Path) -> set[str]:
    return {error.code for error in validate_ledger(project)}


def test_codec_emits_and_accepts_only_canonical_utf8_json() -> None:
    expected = (
        b'{\n'
        b'  "schema_version": 1,\n'
        b'  "authority_ref": "refs/heads/main",\n'
        b'  "allocated_line_ids": [\n'
        b'    "line-0001",\n'
        b'    "line-0010"\n'
        b'  ]\n'
        b'}\n'
    )

    assert encode_ledger({"line-0010", "line-0001"}) == expected
    assert decode_ledger(expected).allocated_line_ids == ("line-0001", "line-0010")

    malformed = [
        expected.rstrip(b"\n"),
        expected.replace(b'"schema_version": 1', b'"schema_version": 2'),
        expected.replace(AUTHORITY_REF.encode(), b"refs/heads/trunk"),
        expected.replace(b'"line-0001",\n    "line-0010"', b'"line-0010",\n    "line-0001"'),
        expected.replace(b'"line-0010"', b'"line-0001"'),
        expected.replace(b'  "allocated_line_ids"', b'  "extra": true,\n  "allocated_line_ids"'),
        b"\xff",
    ]
    for candidate in malformed:
        with pytest.raises(IdentityLedgerError) as raised:
            decode_ledger(candidate)
        assert raised.value.code == "ledger.malformed"


def test_authority_requires_attached_local_main(tmp_path: Path) -> None:
    project = init_repo(tmp_path)
    require_allocation_authority(project)

    git(project, "switch", "-qc", "topic")
    with pytest.raises(IdentityLedgerError) as branch_error:
        require_allocation_authority(project)
    assert branch_error.value.code == "ledger.authority.required"

    git(project, "checkout", "--detach", "-q")
    with pytest.raises(IdentityLedgerError) as detached_error:
        require_allocation_authority(project)
    assert detached_error.value.code == "ledger.authority.required"


def test_bootstrap_union_uses_current_and_main_first_parent_history_only(tmp_path: Path) -> None:
    project = init_repo(tmp_path)
    add_pair(project, "line-0001")
    commit_all(project, "historical main line")
    shutil.rmtree(project / ".proofline/lines/line-0001")
    commit_all(project, "remove historical main line")

    git(project, "switch", "-qc", "topic")
    add_pair(project, "line-0099")
    commit_all(project, "non authority line")
    git(project, "switch", "-q", "main")
    add_pair(project, "line-0002")

    assert bootstrap_allocation_ids(project) == ("line-0001", "line-0002")

    git(project, "tag", "topic-only", "topic")
    git(project, "branch", "-D", "topic")
    assert bootstrap_allocation_ids(project) == ("line-0001", "line-0002")


def test_first_ledger_must_equal_exact_bootstrap_union_and_omission_survives_recreation(
    tmp_path: Path,
) -> None:
    project = init_repo(tmp_path)
    add_pair(project, "line-0001")
    commit_all(project, "historical")
    shutil.rmtree(project / ".proofline/lines/line-0001")
    commit_all(project, "removed")

    write_ledger(project, set())
    commit_all(project, "bad adoption")
    add_pair(project, "line-0001")
    write_ledger(project, {"line-0001"})
    commit_all(project, "attempt recreation")

    assert "ledger.bootstrap.incomplete" in codes(project)


def test_existing_ledger_distinguishes_missing_regressed_stale_and_orphan(
    tmp_path: Path,
) -> None:
    project = init_repo(tmp_path)
    add_pair(project, "line-0001")
    write_ledger(project, {"line-0001"})
    commit_all(project, "adopt")

    ledger = project / ".proofline/line-identities.json"
    ledger.unlink()
    assert codes(project) == {"ledger.missing"}

    write_ledger(project, set())
    assert "ledger.regressed" in codes(project)

    write_ledger(project, {"line-0001"})
    add_pair(project, "line-0002")
    assert "ledger.stale" in codes(project)

    shutil.rmtree(project / ".proofline/lines/line-0002")
    write_ledger(project, {"line-0001", "line-0003"})
    assert "ledger.orphan" in codes(project)


def test_post_bootstrap_candidate_and_commit_require_matching_new_pair(
    tmp_path: Path,
) -> None:
    project = init_repo(tmp_path)
    write_ledger(project, set())
    commit_all(project, "adopt empty project")

    add_pair(project, "line-0002")
    write_ledger(project, {"line-0002"})
    assert codes(project) == set()
    commit_all(project, "allocate with pair")
    assert codes(project) == set()

    write_ledger(project, {"line-0002", "line-0003"})
    commit_all(project, "orphan committed delta")
    assert "ledger.orphan" in codes(project)


def test_ledger_rejects_symlink_and_wrong_type(tmp_path: Path) -> None:
    project = init_repo(tmp_path)
    ledger = project / ".proofline/line-identities.json"
    target = tmp_path / "outside.json"
    target.write_bytes(encode_ledger(set()))
    ledger.symlink_to(target)
    assert codes(project) == {"ledger.symlink"}

    ledger.unlink()
    ledger.mkdir()
    assert codes(project) == {"ledger.type"}


def test_validator_uses_shared_ledger_diagnostics(tmp_path: Path) -> None:
    project = init_repo(tmp_path)
    (project / ".proofline/line-identities.json").write_text("{}\n", encoding="utf-8")

    diagnostics = validate_project(project)

    assert any(error.code == "ledger.malformed" for error in diagnostics)
    assert not any(error.code == "topology.support.unsupported" for error in diagnostics)


def test_allocation_preflight_rejects_reserved_id_and_non_main(tmp_path: Path) -> None:
    project = init_repo(tmp_path)
    add_pair(project, "line-0001")
    write_ledger(project, {"line-0001"})
    commit_all(project, "adopt")

    with pytest.raises(IdentityLedgerError) as reused:
        require_allocation_preflight(project, "line-0001")
    assert reused.value.code == "line.id.reused"

    git(project, "switch", "-qc", "topic")
    with pytest.raises(IdentityLedgerError) as authority:
        require_allocation_preflight(project, "line-0002")
    assert authority.value.code == "ledger.authority.required"
