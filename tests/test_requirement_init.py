from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from proofline import requirement_writer
from proofline.identity_allocator import IdentityAllocator, decode_allocator, encode_allocator
from proofline.line_writer import initialize_line
from proofline.project_writer import initialize_project
from proofline.requirement_writer import RequirementInitError, initialize_requirement
from proofline.validator import validate_project
from proofline.validator import ValidationError

ROOT = Path(__file__).resolve().parents[1]


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    initialize_project(root)
    initialize_line(root, "Confirmed title")
    discovery = root / ".proofline/lines/line-0001/dcy-0001.md"
    discovery.write_text(discovery.read_text().replace("status: draft", "status: confirmed").replace("{{TODO:", "TODO_REMOVED:").replace("{{NEEDS_EVIDENCE:", "EVIDENCE_REMOVED:").replace("{{UNKNOWN:", "UNKNOWN_REMOVED:").replace("}}", ""))
    assert validate_project(root) == []
    return root


def manifest(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "admission.yaml"
    path.write_text(text, encoding="utf-8")
    return path


VALID = """create:
  - First criterion
  - Second criterion
update: []
retire: []
satisfy: []
"""


def canonical_snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in (root / ".proofline").rglob("*") if path.is_file()}


def test_requirement_init_dry_run_and_multi_ac_atomic_success(tmp_path: Path) -> None:
    root = project(tmp_path)
    admission = manifest(tmp_path, VALID)
    before = canonical_snapshot(root)
    dry = initialize_requirement(root, "line-0001", admission, dry_run=True)
    assert dry.ac_ids == ("ac-0001", "ac-0002")
    assert canonical_snapshot(root) == before
    result = initialize_requirement(root, "line-0001", admission)
    assert result.ac_ids == dry.ac_ids and result.paths == dry.paths
    req = (root / ".proofline/lines/line-0001/req-0001.md").read_text()
    assert "# Confirmed title" in req
    assert '    - "ac-0001"' in req and '    - "ac-0002"' in req
    for ac_id, title in (("ac-0001", "First criterion"), ("ac-0002", "Second criterion")):
        text = (root / f".proofline/criteria/{ac_id}.md").read_text()
        assert "status: draft" in text and f"# {title}" in text
    assert decode_allocator((root / ".proofline/identities.json").read_bytes()) == IdentityAllocator(2, 3)
    assert validate_project(root) == []


def test_candidate_preserves_preexisting_history_context_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    admission = manifest(tmp_path, VALID)
    real = requirement_writer.validate_project
    history_only = ValidationError(
        ".proofline/lines/line-0099/req-0099.md",
        "reference.inactive",
        "candidate에는 원본 Git history context가 없습니다.",
    )

    def validate(path: Path):
        if path != root:
            return [history_only]
        return real(path)

    monkeypatch.setattr(requirement_writer, "validate_project", validate)
    result = initialize_requirement(root, "line-0001", admission)

    assert result.ac_ids == ("ac-0001", "ac-0002")
    assert real(root) == []


def test_candidate_rejects_non_history_baseline_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    admission = manifest(tmp_path, VALID)
    real = requirement_writer.validate_project
    candidate_error = ValidationError(
        ".proofline",
        "topology.unavailable",
        "candidate topology를 검사할 수 없습니다.",
    )

    def validate(path: Path):
        if path != root:
            return [candidate_error]
        return real(path)

    monkeypatch.setattr(requirement_writer, "validate_project", validate)
    with pytest.raises(RequirementInitError, match="candidate.invalid"):
        initialize_requirement(root, "line-0001", admission)


def test_requirement_requires_confirmed_discovery(tmp_path: Path) -> None:
    root = project(tmp_path)
    discovery = root / ".proofline/lines/line-0001/dcy-0001.md"
    discovery.write_text(discovery.read_text().replace("status: confirmed", "status: draft"))
    with pytest.raises(RequirementInitError, match="discovery.unconfirmed"):
        initialize_requirement(root, "line-0001", manifest(tmp_path, VALID))


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("create: []\nupdate: []\nretire: []\nsatisfy: []\n", "manifest.empty"),
        ("create: [same, same]\nupdate: []\nretire: []\nsatisfy: []\n", "manifest.entry"),
        ("create: []\nupdate: [ac-0001]\nretire: [ac-0001]\nsatisfy: []\n", "manifest.overlap"),
        ("create: []\nupdate: []\nretire: []\nsatisfy: []\nextra: []\n", "manifest.schema"),
        ("create: string\nupdate: []\nretire: []\nsatisfy: []\n", "manifest.schema"),
    ],
)
def test_manifest_rejections_are_non_mutating(tmp_path: Path, text: str, code: str) -> None:
    root = project(tmp_path)
    before = canonical_snapshot(root)
    with pytest.raises(RequirementInitError, match=code):
        initialize_requirement(root, "line-0001", manifest(tmp_path, text))
    assert canonical_snapshot(root) == before


def test_manifest_symlink_is_rejected(tmp_path: Path) -> None:
    root = project(tmp_path)
    target = manifest(tmp_path, VALID)
    link = tmp_path / "link.yaml"
    link.symlink_to(target)
    with pytest.raises(RequirementInitError, match="manifest.symlink"):
        initialize_requirement(root, "line-0001", link)


def test_manifest_is_parsed_from_single_pinned_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    admission = manifest(tmp_path, VALID)
    real = requirement_writer.read_regular
    reads = 0

    def mutate_after_read(path: Path):
        nonlocal reads
        result = real(path)
        if path == admission.absolute():
            reads += 1
            admission.write_text("not: the admission snapshot\n")
        return result

    monkeypatch.setattr(requirement_writer, "read_regular", mutate_after_read)
    with pytest.raises(RequirementInitError, match="project.concurrent.changed"):
        initialize_requirement(root, "line-0001", admission)
    assert reads == 1
    assert not (root / ".proofline/lines/line-0001/req-0001.md").exists()


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("create: []\ncreate: [duplicate]\nupdate: []\nretire: []\nsatisfy: []\n", "manifest.malformed"),
        ("create: [' leading']\nupdate: []\nretire: []\nsatisfy: []\n", "manifest.entry"),
        ("create: [true]\nupdate: []\nretire: []\nsatisfy: []\n", "manifest.entry"),
        ("create: [1.5]\nupdate: []\nretire: []\nsatisfy: []\n", "manifest.entry"),
        ("create: []\nupdate: [AC-0001]\nretire: []\nsatisfy: []\n", "manifest.entry"),
    ],
)
def test_manifest_duplicate_and_title_variants_fail_closed(
    tmp_path: Path, text: str, code: str
) -> None:
    root = project(tmp_path)
    before = canonical_snapshot(root)
    with pytest.raises(RequirementInitError, match=code):
        initialize_requirement(root, "line-0001", manifest(tmp_path, text))
    assert canonical_snapshot(root) == before


def test_existing_admission_targets_must_be_active(tmp_path: Path) -> None:
    root = project(tmp_path)
    ac = root / ".proofline/criteria/ac-0001.md"
    ac.write_text('---\nid: "ac-0001"\nstatus: draft\n---\n\n# Existing\n\n## Criterion\n\n{{TODO: x}}\n\n## Verification\n\n{{TODO: x}}\n')
    allocator = root / ".proofline/identities.json"
    allocator.write_text('{\n  "schema_version": 1,\n  "next_line_number": 2,\n  "next_ac_number": 2\n}\n')
    text = "create: []\nupdate: [ac-0001]\nretire: []\nsatisfy: []\n"
    with pytest.raises(RequirementInitError, match="criteria.target.inactive"):
        initialize_requirement(root, "line-0001", manifest(tmp_path, text))


@pytest.mark.parametrize("key", ["update", "retire", "satisfy"])
def test_requirement_existing_admission_modes_succeed_individually(
    tmp_path: Path, key: str
) -> None:
    root = project(tmp_path)
    add_active_ac(root, 1, "Existing")
    (root / ".proofline/identities.json").write_bytes(encode_allocator(IdentityAllocator(2, 2)))
    values = {name: "[]" for name in ("create", "update", "retire", "satisfy")}
    values[key] = "[ac-0001]"
    admission = manifest(tmp_path, "".join(f"{name}: {values[name]}\n" for name in values))
    result = initialize_requirement(root, "line-0001", admission)
    assert result.ac_ids == ()
    assert f'  {key}:\n    - "ac-0001"' in (root / ".proofline/lines/line-0001/req-0001.md").read_text()
    assert decode_allocator((root / ".proofline/identities.json").read_bytes()) == IdentityAllocator(2, 2)


def test_requirement_rolls_back_all_artifacts_on_commit_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path)
    before = canonical_snapshot(root)
    real = requirement_writer._commit_path_at
    calls = 0
    def fail_second(source: Path, descriptor: int, name: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected")
        real(source, descriptor, name)
    monkeypatch.setattr(requirement_writer, "_commit_path_at", fail_second)
    with pytest.raises(RequirementInitError, match="requirement.transaction.failed"):
        initialize_requirement(root, "line-0001", manifest(tmp_path, VALID))
    assert canonical_snapshot(root) == before


def test_requirement_stage_write_fault_is_stable_and_leaves_no_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    before = canonical_snapshot(root)
    real = Path.write_bytes

    def fail_second_ac(path: Path, data: bytes) -> int:
        if path.name == "ac-0002.md" and path.parent.name.startswith(".req-0001-"):
            raise OSError("injected")
        return real(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_second_ac)
    with pytest.raises(RequirementInitError, match="requirement.transaction.failed"):
        initialize_requirement(root, "line-0001", manifest(tmp_path, VALID))
    assert canonical_snapshot(root) == before
    assert not list(root.glob(".req-0001-*"))


def test_requirement_stage_cleanup_fault_rolls_back_all_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    before = canonical_snapshot(root)
    real = requirement_writer.remove_owned_tree
    failed = False

    def fail_stage_once(parent_fd, name, *args, **kwargs):
        nonlocal failed
        if not failed and name.startswith(".req-0001-"):
            failed = True
            raise OSError("injected")
        return real(parent_fd, name, *args, **kwargs)

    monkeypatch.setattr(requirement_writer, "remove_owned_tree", fail_stage_once)
    with pytest.raises(RequirementInitError, match="requirement.transaction.failed"):
        initialize_requirement(root, "line-0001", manifest(tmp_path, VALID))
    assert canonical_snapshot(root) == before
    assert not list(root.glob(".req-0001-*"))


def test_requirement_rejects_exhausted_ac_space(tmp_path: Path) -> None:
    root = project(tmp_path)
    (root / ".proofline/identities.json").write_text('{\n  "schema_version": 1,\n  "next_line_number": 2,\n  "next_ac_number": 10000\n}\n')
    with pytest.raises(RequirementInitError, match="allocator.ac.exhausted"):
        initialize_requirement(root, "line-0001", manifest(tmp_path, VALID))


def add_active_ac(root: Path, number: int, title: str) -> str:
    ac_id = f"ac-{number:04d}"
    (root / f".proofline/criteria/{ac_id}.md").write_text(
        f'---\nid: "{ac_id}"\nstatus: active\n---\n\n# {title}\n\n## Criterion\n\nExisting behavior.\n\n## Verification\n\n- Existing check.\n'
    )
    return ac_id


def test_requirement_mixed_create_update_retire_satisfy_success(tmp_path: Path) -> None:
    root = project(tmp_path)
    for number in range(1, 4):
        add_active_ac(root, number, f"Existing {number}")
    (root / ".proofline/identities.json").write_bytes(encode_allocator(IdentityAllocator(2, 4)))
    admission = manifest(
        tmp_path,
        "create: [New criterion]\nupdate: [ac-0001]\nretire: [ac-0002]\nsatisfy: [ac-0003]\n",
    )
    result = initialize_requirement(root, "line-0001", admission)
    assert result.ac_ids == ("ac-0004",)
    req = (root / ".proofline/lines/line-0001/req-0001.md").read_text()
    for ac_id in ("ac-0001", "ac-0002", "ac-0003", "ac-0004"):
        assert f'    - "{ac_id}"' in req
    assert decode_allocator((root / ".proofline/identities.json").read_bytes()) == IdentityAllocator(2, 5)
    assert validate_project(root) == []


def test_active_ac_mutation_during_commit_cannot_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    ac_id = add_active_ac(root, 1, "Existing")
    (root / ".proofline/identities.json").write_bytes(
        encode_allocator(IdentityAllocator(2, 2))
    )
    admission = manifest(
        tmp_path, "create: []\nupdate: [ac-0001]\nretire: []\nsatisfy: []\n"
    )
    ac = root / f".proofline/criteria/{ac_id}.md"
    real = requirement_writer._commit_path_at
    raced = False

    def mutate_then_commit(*args, **kwargs):
        nonlocal raced
        if not raced:
            raced = True
            ac.write_text(ac.read_text().replace("status: active", "status: draft"))
        return real(*args, **kwargs)

    monkeypatch.setattr(requirement_writer, "_commit_path_at", mutate_then_commit)
    with pytest.raises(RequirementInitError):
        initialize_requirement(root, "line-0001", admission)
    assert "status: draft" in ac.read_text()
    assert not (root / ".proofline/lines/line-0001/req-0001.md").exists()


@pytest.mark.parametrize("artifact", ["line-0001.md", "dcy-0001.md"])
def test_line_or_discovery_mutation_during_commit_cannot_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact: str
) -> None:
    root = project(tmp_path)
    admission = manifest(tmp_path, VALID)
    target = root / ".proofline/lines/line-0001" / artifact
    real = requirement_writer._commit_path_at
    raced = False

    def mutate_then_commit(*args, **kwargs):
        nonlocal raced
        if not raced:
            raced = True
            target.write_bytes(target.read_bytes() + b"\nforeign mutation\n")
        return real(*args, **kwargs)

    monkeypatch.setattr(requirement_writer, "_commit_path_at", mutate_then_commit)
    with pytest.raises(RequirementInitError):
        initialize_requirement(root, "line-0001", admission)
    assert target.read_bytes().endswith(b"foreign mutation\n")
    assert not (root / ".proofline/lines/line-0001/req-0001.md").exists()
    assert not list((root / ".proofline/criteria").glob("ac-000*.md"))


def test_requirement_rejects_zero_line_and_ac_ids(tmp_path: Path) -> None:
    root = project(tmp_path)
    with pytest.raises(RequirementInitError, match="line.id.invalid"):
        initialize_requirement(root, "line-0000", manifest(tmp_path, VALID))
    zero_ac = manifest(
        tmp_path, "create: []\nupdate: [ac-0000]\nretire: []\nsatisfy: []\n"
    )
    with pytest.raises(RequirementInitError, match="manifest.entry"):
        initialize_requirement(root, "line-0001", zero_ac)


@pytest.mark.parametrize("status", ["draft", "withdrawn"])
def test_requirement_rejects_nonconfirmed_discovery_status(tmp_path: Path, status: str) -> None:
    root = project(tmp_path)
    discovery = root / ".proofline/lines/line-0001/dcy-0001.md"
    discovery.write_text(discovery.read_text().replace("status: confirmed", f"status: {status}"))
    with pytest.raises(RequirementInitError, match="discovery.unconfirmed"):
        initialize_requirement(root, "line-0001", manifest(tmp_path, VALID))


def test_requirement_rollback_preserves_external_artifact_and_allocator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    target = root / ".proofline/criteria/ac-0001.md"
    calls = 0
    real_validate = requirement_writer.validate_project

    def replace_on_post(path: Path):
        nonlocal calls
        calls += 1
        if calls == 4:
            target.unlink()
            target.write_bytes(b"external sentinel")
            return [ValidationError(".", "injected", "failure")]
        return real_validate(path)

    monkeypatch.setattr(requirement_writer, "validate_project", replace_on_post)
    with pytest.raises(RequirementInitError, match="artifact.rollback.failed"):
        initialize_requirement(root, "line-0001", manifest(tmp_path, VALID))
    assert target.read_bytes() == b"external sentinel"
    assert not (root / ".proofline/criteria/ac-0002.md").exists()
    assert not (root / ".proofline/lines/line-0001/req-0001.md").exists()
    assert decode_allocator((root / ".proofline/identities.json").read_bytes()) == IdentityAllocator(2, 3)


def test_requirement_exact_ac_exhaustion_is_non_mutating(tmp_path: Path) -> None:
    root = project(tmp_path)
    allocator = root / ".proofline/identities.json"
    allocator.write_bytes(encode_allocator(IdentityAllocator(2, 9999)))
    before = canonical_snapshot(root)
    with pytest.raises(RequirementInitError, match="allocator.ac.exhausted"):
        initialize_requirement(root, "line-0001", manifest(tmp_path, VALID))
    assert canonical_snapshot(root) == before


def test_concurrent_requirement_processes_allocate_unique_ac_ids(tmp_path: Path) -> None:
    root = project(tmp_path)
    initialize_line(root, "Second confirmed")
    second = root / ".proofline/lines/line-0002/dcy-0002.md"
    second.write_text(second.read_text().replace("status: draft", "status: confirmed").replace("{{TODO:", "TODO_REMOVED:").replace("{{NEEDS_EVIDENCE:", "EVIDENCE_REMOVED:").replace("{{UNKNOWN:", "UNKNOWN_REMOVED:").replace("}}", ""))
    manifests = []
    for number in (1, 2):
        path = tmp_path / f"admission-{number}.yaml"
        path.write_text(f"create: [Concurrent {number}]\nupdate: []\nretire: []\nsatisfy: []\n")
        manifests.append(path)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    command = [sys.executable, "-c", "from pathlib import Path; from proofline.requirement_writer import initialize_requirement; import sys; print(initialize_requirement(Path.cwd(), sys.argv[1], Path(sys.argv[2])).ac_ids[0])"]
    processes = [
        subprocess.Popen(command + [f"line-{number:04d}", str(manifests[number - 1])], cwd=root, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for number in (1, 2)
    ]
    results = [process.communicate(timeout=30) for process in processes]
    assert [process.returncode for process in processes] == [0, 0], results
    assert {stdout.strip() for stdout, _ in results} == {"ac-0001", "ac-0002"}
    assert decode_allocator((root / ".proofline/identities.json").read_bytes()) == IdentityAllocator(3, 3)
    assert validate_project(root) == []


def test_concurrent_line_and_requirement_do_not_lose_allocator_updates(tmp_path: Path) -> None:
    root = project(tmp_path)
    admission = manifest(tmp_path, "create: [Concurrent AC]\nupdate: []\nretire: []\nsatisfy: []\n")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    commands = [
        [sys.executable, "-c", "from pathlib import Path; from proofline.line_writer import initialize_line; print(initialize_line(Path.cwd(), 'Concurrent Line').line_id)"],
        [sys.executable, "-c", "from pathlib import Path; from proofline.requirement_writer import initialize_requirement; import sys; print(initialize_requirement(Path.cwd(), 'line-0001', Path(sys.argv[1])).ac_ids[0])", str(admission)],
    ]
    processes = [
        subprocess.Popen(command, cwd=root, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for command in commands
    ]
    results = [process.communicate(timeout=30) for process in processes]
    assert [process.returncode for process in processes] == [0, 0], results
    assert {stdout.strip() for stdout, _ in results} == {"line-0002", "ac-0001"}
    assert decode_allocator((root / ".proofline/identities.json").read_bytes()) == IdentityAllocator(3, 2)
    assert validate_project(root) == []
