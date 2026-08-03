from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import proofline.identity_ledger as ledger_module
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


def stage_as_symlink_blob(project: Path, path: str) -> None:
    object_id = git(project, "hash-object", "-w", path).stdout.strip()
    git(project, "update-index", "--add", "--cacheinfo", f"120000,{object_id},{path}")


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


def test_authority_rejects_unborn_attached_main(tmp_path: Path) -> None:
    project = tmp_path / "unborn"
    project.mkdir()
    git(project, "init", "-q", "-b", "main")

    with pytest.raises(IdentityLedgerError) as raised:
        require_allocation_authority(project)

    assert raised.value.code == "ledger.authority.required"


def test_bootstrap_history_failure_is_typed_and_fail_closed(tmp_path: Path) -> None:
    project = init_repo(tmp_path)
    head = git(project, "rev-parse", "HEAD").stdout.strip()
    (project / ".git" / "objects" / head[:2] / head[2:]).unlink()

    with pytest.raises(IdentityLedgerError) as raised:
        bootstrap_allocation_ids(project)

    assert raised.value.code == "ledger.history.unavailable"


def test_validator_history_failure_is_typed_and_fail_closed(tmp_path: Path) -> None:
    project = init_repo(tmp_path)
    head = git(project, "rev-parse", "HEAD").stdout.strip()
    (project / ".git" / "objects" / head[:2] / head[2:]).unlink()

    assert codes(project) == {"ledger.history.unavailable"}


def failed_git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(["git", *args], 128, b"", b"injected failure")


def test_tree_listing_failure_is_typed_not_an_empty_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = init_repo(tmp_path)
    original_git = ledger_module._git

    def fail_ls_tree(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
        if args and args[0] == "ls-tree":
            return failed_git(*args)
        return original_git(root, *args)

    monkeypatch.setattr(ledger_module, "_git", fail_ls_tree)

    with pytest.raises(IdentityLedgerError) as raised:
        ledger_module._tree_line_ids(project, "HEAD")

    assert raised.value.code == "ledger.history.unavailable"


@pytest.mark.parametrize("reader", ["ledger", "tree"])
def test_tree_blob_read_failure_is_typed_not_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    project = init_repo(tmp_path)
    write_ledger(project, set())
    commit_all(project, "ledger")
    original_git = ledger_module._git

    def fail_blob_read(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
        if args and args[0] == "show":
            return failed_git(*args)
        if len(args) >= 2 and args[:2] == ("cat-file", "blob"):
            return failed_git(*args)
        return original_git(root, *args)

    monkeypatch.setattr(ledger_module, "_git", fail_blob_read)

    with pytest.raises(IdentityLedgerError) as raised:
        if reader == "ledger":
            ledger_module._ledger_at(project, "HEAD")
        else:
            ledger_module._tree_file(project, "HEAD", "proofline.yaml")

    assert raised.value.code == "ledger.history.unavailable"


def test_historical_ledger_symlink_blob_is_rejected_despite_working_correction(
    tmp_path: Path,
) -> None:
    project = init_repo(tmp_path)
    write_ledger(project, set())
    git(project, "add", ".proofline/line-identities.json")
    stage_as_symlink_blob(project, ".proofline/line-identities.json")
    git(project, "commit", "-qm", "symlink-mode ledger")

    assert (project / ".proofline/line-identities.json").is_file()
    assert codes(project) == {"ledger.history.unavailable"}


def test_historical_pair_symlink_blob_is_rejected_despite_working_correction(
    tmp_path: Path,
) -> None:
    project = init_repo(tmp_path)
    write_ledger(project, set())
    commit_all(project, "adopt empty project")
    add_pair(project, "line-0001")
    write_ledger(project, {"line-0001"})
    git(project, "add", "-A")
    stage_as_symlink_blob(
        project, ".proofline/lines/line-0001/line-0001.md"
    )
    git(project, "commit", "-qm", "allocate with symlink-mode pair")

    assert (project / ".proofline/lines/line-0001/line-0001.md").is_file()
    assert codes(project) == {"ledger.history.unavailable"}


def test_parent_lookup_failure_is_typed_while_root_parent_is_legitimately_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_project = tmp_path / "root-project"
    (root_project / ".proofline/lines").mkdir(parents=True)
    (root_project / ".proofline/criteria").mkdir()
    (root_project / "proofline.yaml").write_text(
        "schema_version: 1\nartifact_root: .proofline\n", encoding="utf-8"
    )
    git(root_project, "init", "-q", "-b", "main")
    git(root_project, "config", "user.email", "proofline@example.invalid")
    git(root_project, "config", "user.name", "ProofLine Test")
    write_ledger(root_project, set())
    commit_all(root_project, "root ledger")
    assert codes(root_project) == set()

    project = init_repo(tmp_path)
    write_ledger(project, set())
    commit_all(project, "first ledger")
    ledger_commit = git(project, "rev-parse", "HEAD").stdout.strip()
    original_git = ledger_module._git

    def fail_parent_lookup(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
        if args == ("rev-parse", f"{ledger_commit}^"):
            return failed_git(*args)
        if args == ("rev-list", "--parents", "-n", "1", ledger_commit):
            return failed_git(*args)
        return original_git(root, *args)

    monkeypatch.setattr(ledger_module, "_git", fail_parent_lookup)

    assert codes(project) == {"ledger.history.unavailable"}


def test_git_spawn_oserror_is_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = init_repo(tmp_path)

    def fail_spawn(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise OSError("git unavailable")

    monkeypatch.setattr(ledger_module.subprocess, "run", fail_spawn)

    with pytest.raises(IdentityLedgerError) as raised:
        bootstrap_allocation_ids(project)

    assert raised.value.code == "ledger.history.unavailable"


def test_later_git_log_failure_is_typed_through_validator_and_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = init_repo(tmp_path)
    write_ledger(project, set())
    commit_all(project, "adopt")
    add_pair(project, "line-0001")
    write_ledger(project, {"line-0001"})
    original_git = ledger_module._git

    def fail_log(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
        if args and args[0] == "log":
            return failed_git(*args)
        return original_git(root, *args)

    monkeypatch.setattr(ledger_module, "_git", fail_log)

    project_codes = {error.code for error in validate_project(project)}
    assert "ledger.history.unavailable" in project_codes
    with pytest.raises(IdentityLedgerError) as raised:
        require_allocation_preflight(project, "line-0001")
    assert raised.value.code == "ledger.history.unavailable"


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


@pytest.mark.parametrize(
    "frontmatter",
    [
        'id: "line-0001"\nid: "line-9999"\nexecution_status: not_started\n',
        'id: "line-0001"\ninvalid: [\n',
    ],
    ids=["conflicting-duplicate-id", "malformed-yaml"],
)
def test_allocation_history_rejects_invalid_identity_frontmatter_after_working_correction(
    tmp_path: Path, frontmatter: str
) -> None:
    project = init_repo(tmp_path)
    write_ledger(project, set())
    commit_all(project, "adopt empty project")
    add_pair(project, "line-0001")
    line = project / ".proofline/lines/line-0001/line-0001.md"
    line.write_text(f"---\n{frontmatter}---\n", encoding="utf-8")
    write_ledger(project, {"line-0001"})
    commit_all(project, "allocate with invalid identity frontmatter")

    line.write_text(
        '---\nid: "line-0001"\nexecution_status: not_started\n---\n', encoding="utf-8"
    )

    assert "ledger.orphan" in codes(project)


@pytest.mark.parametrize(
    ("frontmatter", "expected"),
    [
        ('id: "line-0001"\nexecution_status: not_started\n', True),
        ('id: "line-0001"\nid: "line-0001"\n', False),
        ('id: "line-0001"\nid: "line-9999"\n', False),
        ('id: "line-0001"\ninvalid: [\n', False),
        ('- id: "line-0001"\n', False),
        ('id: 1\n', False),
    ],
    ids=[
        "valid-mapping",
        "duplicate-id",
        "conflicting-id",
        "malformed-yaml",
        "non-mapping",
        "non-string-id",
    ],
)
def test_frontmatter_id_requires_unique_string_id_in_parseable_mapping(
    frontmatter: str, expected: bool
) -> None:
    data = f"---\n{frontmatter}---\nbody\n".encode()

    assert ledger_module._frontmatter_id(data, "line-0001") is expected


def deleted_unledgered_line_history(tmp_path: Path, line_id: str) -> Path:
    project = init_repo(tmp_path)
    write_ledger(project, set())
    commit_all(project, "adopt empty project")
    add_pair(project, line_id)
    commit_all(project, "add Line without ledger")
    shutil.rmtree(project / ".proofline/lines" / line_id)
    commit_all(project, "delete unledgered Line")
    return project


def test_post_bootstrap_candidate_rejects_recreated_line_seen_earlier_on_main(
    tmp_path: Path,
) -> None:
    project = deleted_unledgered_line_history(tmp_path, "line-0004")
    add_pair(project, "line-0004")
    write_ledger(project, {"line-0004"})

    assert "ledger.orphan" in codes(project)


def test_post_bootstrap_commit_rejects_recreated_line_seen_earlier_on_main(
    tmp_path: Path,
) -> None:
    project = deleted_unledgered_line_history(tmp_path, "line-0004")
    add_pair(project, "line-0004")
    write_ledger(project, {"line-0004"})
    commit_all(project, "recreate Line with ledger")

    assert "ledger.orphan" in codes(project)


def test_repaired_working_ledger_reports_malformed_head_without_exception(
    tmp_path: Path,
) -> None:
    project = init_repo(tmp_path)
    ledger = project / ".proofline/line-identities.json"
    ledger.write_text("{}\n", encoding="utf-8")
    commit_all(project, "commit malformed ledger")
    write_ledger(project, set())

    assert codes(project) == {"ledger.malformed"}
    assert {error.code for error in validate_project(project)} >= {"ledger.malformed"}
    with pytest.raises(IdentityLedgerError) as raised:
        require_allocation_preflight(project, "line-0005")
    assert raised.value.code == "ledger.malformed"


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
