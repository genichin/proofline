from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from proofline.validator import validate_project
import proofline.implementation_history as implementation_history
from test_implementation_history import HistoryRepo, LINE, repository_snapshot


INTEGRATION = ".proofline/lines/line-0001/integration-0001.md"
DQC = ".proofline/lines/line-0001/dqc-0001.md"


def git(repo: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ("git", *args), cwd=repo, text=True, capture_output=True, check=False
    )
    if check:
        assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def build_candidate(
    tmp_path: Path,
    *,
    manifest: bool = True,
    manifest_path: str = INTEGRATION,
    manifest_id: str = "integration-0001",
    line_id: str = "line-0001",
    main_parent: str | None = None,
    line_head: str | None = None,
    merge_only_path: str | None = None,
    malformed_manifest: bool = False,
    duplicate_manifest: bool = False,
    line_tip_after_quality: bool = False,
    conflict_resolution: bool = False,
) -> tuple[HistoryRepo, str, str, str]:
    repo = HistoryRepo.create(tmp_path)
    repo.write_line("not_started", policy="first_parent")
    approval = repo.commit("specification", "approve policy-bearing specification")
    repo.write_line("in_progress", policy="first_parent")
    handoff = repo.commit("handoff", "status-only handoff")

    git(repo.path, "switch", "-qc", "line-0001")
    repo.write_ms(1, "in_progress")
    repo.commit("start", "start implementation")
    implementation = repo.product_commit()
    repo.write_line("verifying", policy="first_parent")
    line_head_sha = repo.finish(implementation, "quality")
    if line_tip_after_quality:
        (repo.path / "line-after-quality.txt").write_text("late\n", encoding="utf-8")
        line_head_sha = repo.commit("late-line-tip", "advance Line after quality")

    git(repo.path, "switch", "-q", "main")
    (repo.path / "main-governance.txt").write_text("main advanced\n", encoding="utf-8")
    if conflict_resolution:
        (repo.path / "product.py").write_text("MAIN = True\n", encoding="utf-8")
    main_sha = repo.commit("main", "advance main governance")
    merged = subprocess.run(
        ("git", "merge", "--no-ff", "--no-commit", "line-0001"),
        cwd=repo.path,
        text=True,
        capture_output=True,
        check=False,
    )
    if conflict_resolution:
        assert merged.returncode != 0
        (repo.path / "product.py").write_text("RESOLVED = True\n", encoding="utf-8")
    else:
        assert merged.returncode == 0, merged.stderr
    if merge_only_path is not None:
        target = repo.path / merge_only_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("forbidden = True\n", encoding="utf-8")
    if manifest:
        integration = repo.path / manifest_path
        integration.parent.mkdir(parents=True, exist_ok=True)
        if malformed_manifest:
            integration.write_text("---\nid: [\n---\n", encoding="utf-8")
        else:
            integration.write_text(
                "---\n"
                f'id: "{manifest_id}"\n'
                f'line_id: "{line_id}"\n'
                f'main_parent: "{main_parent or main_sha}"\n'
                f'line_head: "{line_head or line_head_sha}"\n'
                "---\n",
                encoding="utf-8",
            )
        if duplicate_manifest:
            duplicate = integration.with_name("integration-duplicate.md")
            duplicate.write_bytes(integration.read_bytes())
    git(repo.path, "add", "-A")
    git(repo.path, "commit", "-qm", "integrate Line 0001")
    candidate = git(repo.path, "rev-parse", "HEAD")
    assert git(repo.path, "rev-parse", "HEAD^1") == main_sha
    assert git(repo.path, "rev-parse", "HEAD^2") == line_head_sha
    assert git(repo.path, "merge-base", approval, handoff) == approval
    return repo, main_sha, line_head_sha, candidate


def build_missing_line_second_parent_candidate(tmp_path: Path) -> HistoryRepo:
    """Build V(M, X) whose new manifest binds an unrelated Line-less X."""
    repo = HistoryRepo.create(tmp_path)
    repo.write_line("not_started", policy="first_parent")
    approval = repo.commit("specification", "approve policy-bearing specification")
    repo.write_line("in_progress", policy="first_parent")
    main_parent = repo.commit("handoff", "status-only handoff")

    git(repo.path, "switch", "-qc", "unrelated", approval)
    (repo.path / LINE).unlink()
    (repo.path / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
    line_head = repo.commit("unrelated", "create unrelated Line-less parent")
    assert LINE not in git(repo.path, "ls-tree", "-r", "--name-only", line_head).splitlines()

    git(repo.path, "switch", "-q", "main")
    merged = subprocess.run(
        ("git", "merge", "--no-ff", "--no-commit", "-s", "ours", "unrelated"),
        cwd=repo.path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert merged.returncode == 0, merged.stderr
    (repo.path / INTEGRATION).write_text(
        "---\n"
        'id: "integration-0001"\n'
        'line_id: "line-0001"\n'
        f'main_parent: "{main_parent}"\n'
        f'line_head: "{line_head}"\n'
        "---\n",
        encoding="utf-8",
    )
    repo.commit("candidate", "bind unrelated Line-less parent")
    assert git(repo.path, "rev-parse", "HEAD^1") == main_parent
    assert git(repo.path, "rev-parse", "HEAD^2") == line_head
    return repo


def history_codes(repo: HistoryRepo) -> set[tuple[str, str]]:
    return {(error.path, error.code) for error in validate_project(repo.path)}


def quarantined_merge_tree(repo: Path, main_parent: str, line_head: str) -> str:
    common_dir = Path(git(repo, "rev-parse", "--git-common-dir"))
    if not common_dir.is_absolute():
        common_dir = repo / common_dir
    object_directory = common_dir.resolve() / "objects"
    with tempfile.TemporaryDirectory(prefix="proofline-red-objects-") as directory:
        quarantine = Path(directory) / "objects"
        quarantine.mkdir()
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_OBJECT_DIRECTORY": str(quarantine),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(object_directory),
            }
        )
        completed = subprocess.run(
            ("git", "merge-tree", "--write-tree", main_parent, line_head),
            cwd=repo,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        return completed.stdout.splitlines()[0]


def rewrite_candidate_parents(repo: HistoryRepo, *parents: str) -> str:
    tree = git(repo.path, "rev-parse", "HEAD^{tree}")
    arguments = ["commit-tree", tree, "-m", "rewritten integration candidate"]
    for parent in parents:
        arguments.extend(("-p", parent))
    candidate = git(repo.path, *arguments)
    git(repo.path, "reset", "--hard", "-q", candidate)
    return candidate


def write_dqc(repo: HistoryRepo, candidate: str, *, result: str = "passed") -> str:
    (repo.path / DQC).write_text(
        "---\n"
        'id: "dqc-0001"\n'
        'line: "line-0001"\n'
        f'candidate_commit: "{candidate}"\n'
        f"result: {result}\n"
        "---\n\n# DQC\n\n## Target\n\n대상.\n\n## IQC Results\n\n통과.\n\n"
        "## Checks\n\n통과.\n\n## Criteria Results\n\n통과.\n\n## Result\n\n통과.\n",
        encoding="utf-8",
    )
    return repo.commit("dqc", "record DQC PASS")


def deliver(repo: HistoryRepo) -> str:
    repo.write_line("delivered", policy="first_parent")
    return repo.commit("delivery", "deliver Line")


def test_main_first_candidate_accepts_designated_line_spine_and_manifest(
    tmp_path: Path,
) -> None:
    repo, _, _, _ = build_candidate(tmp_path)

    assert validate_project(repo.path) == []


def test_integration_parent_topology_uses_one_bulk_git_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, _, _ = build_candidate(tmp_path)
    original_git = implementation_history._git
    parent_queries: list[tuple[str, ...]] = []

    def counting_git(
        session: implementation_history._GitSession,
        *arguments: str,
        **kwargs: object,
    ) -> bytes:
        if arguments and arguments[0] == "rev-list" and "--parents" in arguments:
            parent_queries.append(arguments)
        return original_git(session, *arguments, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(implementation_history, "_git", counting_git)

    assert validate_project(repo.path) == []
    assert len(parent_queries) == 1
    assert parent_queries[0][:4] == (
        "rev-list",
        "--first-parent",
        "--parents",
        "--reverse",
    )


def test_bulk_parent_rows_preserve_root_normal_and_merge_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commits = ["a" * 40, "b" * 40, "c" * 40]
    rows = b"\n".join(
        (
            commits[0].encode("ascii"),
            f"{commits[1]} {commits[0]}".encode("ascii"),
            f"{commits[2]} {commits[1]} {'d' * 40}".encode("ascii"),
        )
    ) + b"\n"
    monkeypatch.setattr(implementation_history, "_git", lambda *args, **kwargs: rows)

    assert implementation_history._first_parent_parent_rows(
        implementation_history._GitSession(tmp_path), commits
    ) == [
        [commits[0]],
        [commits[1], commits[0]],
        [commits[2], commits[1], "d" * 40],
    ]


@pytest.mark.parametrize(
    "rows",
    [
        f"{'a' * 40}\n".encode("ascii"),
        f"{'b' * 40} {'a' * 40}\n{'a' * 40}\n".encode("ascii"),
        f"{'a' * 40}\n{'b' * 40} {'x' * 40}\n".encode("ascii"),
        f"{'a' * 40}\n{'b' * 40} {'a' * 40}\n".encode("ascii") + b"\xff",
    ],
)
def test_bulk_parent_rows_fail_closed_on_malformed_or_misaligned_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rows: bytes
) -> None:
    commits = ["a" * 40, "b" * 40]
    monkeypatch.setattr(implementation_history, "_git", lambda *args, **kwargs: rows)

    with pytest.raises(implementation_history.HistoryUnavailable):
        implementation_history._first_parent_parent_rows(
            implementation_history._GitSession(tmp_path), commits
        )


def test_unchanged_history_reads_each_artifact_blob_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, _, _ = build_candidate(tmp_path)
    for index in range(24):
        path = repo.path / f"governance-{index:02d}.txt"
        path.write_text(f"governance {index}\n", encoding="utf-8")
        repo.commit(f"governance-{index:02d}", "advance unrelated governance")
    commit_count = len(git(repo.path, "rev-list", "--first-parent", "HEAD").splitlines())
    original_git = implementation_history._git
    uncached: list[tuple[str, ...]] = []

    def counting_git(
        session: implementation_history._GitSession,
        *arguments: str,
        **kwargs: object,
    ) -> bytes:
        before = session.commands
        result = original_git(session, *arguments, **kwargs)  # type: ignore[arg-type]
        if session.commands == before + 1:
            uncached.append(arguments)
        return result

    monkeypatch.setattr(implementation_history, "_git", counting_git)

    assert validate_project(repo.path) == []
    blob_reads = [command for command in uncached if command[:2] == ("cat-file", "blob")]
    assert blob_reads
    assert len(blob_reads) == len({command[2] for command in blob_reads})
    assert len(blob_reads) < commit_count
    assert not [command for command in uncached if command[:1] == ("show",)]


@pytest.mark.parametrize(
    "tree_output",
    [
        b"100644 blob " + b"a" * 40 + b" missing-tab\0",
        b"100644 blob " + b"a" * 40 + b"\t.proofline/lines/line-0001/line-0001.md",
        (
            b"100644 blob "
            + b"a" * 40
            + b"\t.proofline/lines/line-0001/line-0001.md\0"
        )
        * 2,
        b"100644 blob " + b"A" * 40 + b"\t.proofline/lines/line-0001/line-0001.md\0",
        b"100644 blob " + b"a" * 40 + b"\t.proofline/lines/\xff.md\0",
    ],
)
def test_tree_entries_fail_closed_on_malformed_or_duplicate_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tree_output: bytes
) -> None:
    monkeypatch.setattr(
        implementation_history, "_git", lambda *args, **kwargs: tree_output
    )

    with pytest.raises(implementation_history.HistoryUnavailable):
        implementation_history._tree_paths(
            implementation_history._GitSession(tmp_path), "a" * 40
        )


def test_canonical_artifact_must_be_a_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = ".proofline/lines/line-0001/line-0001.md"
    tree_output = b"160000 commit " + b"a" * 40 + b"\t" + path.encode() + b"\0"
    monkeypatch.setattr(
        implementation_history, "_git", lambda *args, **kwargs: tree_output
    )
    session = implementation_history._GitSession(tmp_path)
    paths = implementation_history._tree_paths(session, "b" * 40)

    with pytest.raises(implementation_history.HistoryUnavailable):
        implementation_history._file(session, "b" * 40, path, paths)


def test_positive_integration_validation_preserves_exact_repository_snapshot(
    tmp_path: Path,
) -> None:
    repo, main_parent, line_head, _ = build_candidate(tmp_path)
    expected_tree = quarantined_merge_tree(repo.path, main_parent, line_head)
    expected_object = Path(
        git(
            repo.path,
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            f"objects/{expected_tree[:2]}/{expected_tree[2:]}",
        )
    )
    assert expected_object.is_file(), "fixture must begin with the computed merge tree loose"
    expected_object.unlink()
    absent = subprocess.run(
        ("git", "cat-file", "-e", f"{expected_tree}^{{tree}}"),
        cwd=repo.path,
        capture_output=True,
        check=False,
    )
    assert absent.returncode != 0
    before = repository_snapshot(repo.path)

    assert validate_project(repo.path) == []

    assert repository_snapshot(repo.path) == before


def test_conflicting_integration_validation_cleans_quarantine_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, _, _ = build_candidate(tmp_path, conflict_resolution=True)
    quarantines: list[Path] = []
    original = implementation_history.tempfile.TemporaryDirectory

    def tracked_directory(*args: object, **kwargs: object):
        created = original(*args, **kwargs)
        quarantines.append(Path(created.name))
        return created

    monkeypatch.setattr(implementation_history.tempfile, "TemporaryDirectory", tracked_directory)
    before = repository_snapshot(repo.path)

    assert (INTEGRATION, "history.integration.tree") in history_codes(repo)

    assert quarantines and all(not path.exists() for path in quarantines)
    assert repository_snapshot(repo.path) == before


def test_concurrent_integration_validation_preserves_exact_repository_snapshot(
    tmp_path: Path,
) -> None:
    repo, _, _, _ = build_candidate(tmp_path)
    before = repository_snapshot(repo.path)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: validate_project(repo.path), range(8)))

    assert results == [[]] * 8
    assert repository_snapshot(repo.path) == before


def test_linked_worktree_validation_uses_common_objects_read_only(tmp_path: Path) -> None:
    repo, _, _, candidate = build_candidate(tmp_path / "source")
    linked = tmp_path / "linked"
    git(repo.path, "worktree", "add", "-q", "--detach", str(linked), candidate)
    before = repository_snapshot(linked)

    assert validate_project(linked) == []

    assert repository_snapshot(linked) == before


def test_object_quarantine_quotes_platform_path_separator(tmp_path: Path) -> None:
    repo, _, _, _ = build_candidate(tmp_path / f"contains{os.pathsep}separator")
    before = repository_snapshot(repo.path)

    assert validate_project(repo.path) == []

    assert repository_snapshot(repo.path) == before


@pytest.mark.parametrize(
    ("variant", "expected_code"),
    [
        ("missing", "history.integration.manifest"),
        ("main-mismatch", "history.integration.parent"),
        ("line-mismatch", "history.integration.parent"),
        ("merge-only", "history.integration.tree"),
    ],
)
def test_main_first_candidate_rejects_invalid_manifest_and_merge_only_changes(
    tmp_path: Path, variant: str, expected_code: str
) -> None:
    kwargs: dict[str, object] = {}
    if variant == "missing":
        kwargs["manifest"] = False
    elif variant == "main-mismatch":
        kwargs["main_parent"] = "f" * 40
    elif variant == "line-mismatch":
        kwargs["line_head"] = "e" * 40
    else:
        kwargs["merge_only_path"] = "merge-only.py"
    repo, _, _, _ = build_candidate(tmp_path, **kwargs)  # type: ignore[arg-type]

    assert (INTEGRATION, expected_code) in history_codes(repo)


def test_post_candidate_first_parent_commit_keeps_immutable_manifest_binding(
    tmp_path: Path,
) -> None:
    repo, _, _, candidate = build_candidate(tmp_path)
    (repo.path / "later-governance.txt").write_text("later\n", encoding="utf-8")
    repo.commit("later", "later unrelated governance")

    assert candidate == git(repo.path, "rev-parse", "HEAD^")
    assert validate_project(repo.path) == []


def test_integration_candidate_rejects_parent_reversal(tmp_path: Path) -> None:
    repo, main, line_head, _ = build_candidate(tmp_path)
    rewrite_candidate_parents(repo, line_head, main)

    assert (INTEGRATION, "history.integration.parent") in history_codes(repo)


def test_integration_candidate_rejects_octopus_parent(tmp_path: Path) -> None:
    repo, main, line_head, _ = build_candidate(tmp_path)
    extra = git(repo.path, "rev-parse", f"{main}^")
    rewrite_candidate_parents(repo, main, line_head, extra)

    assert (INTEGRATION, "history.integration.parent") in history_codes(repo)


@pytest.mark.parametrize(
    ("kwargs", "expected_path", "expected_code"),
    [
        ({"duplicate_manifest": True}, INTEGRATION, "history.integration.manifest"),
        ({"malformed_manifest": True}, INTEGRATION, "history.unavailable"),
        (
            {"manifest_path": ".proofline/lines/line-0001/integration-9999.md"},
            INTEGRATION,
            "history.integration.manifest",
        ),
        ({"manifest_id": "integration-9999"}, INTEGRATION, "history.integration.parent"),
        ({"line_id": "line-9999"}, INTEGRATION, "history.integration.parent"),
    ],
)
def test_integration_manifest_identity_is_unambiguous_and_canonical(
    tmp_path: Path,
    kwargs: dict[str, object],
    expected_path: str,
    expected_code: str,
) -> None:
    repo, _, _, _ = build_candidate(tmp_path, **kwargs)  # type: ignore[arg-type]

    assert (expected_path, expected_code) in history_codes(repo)


def test_integration_candidate_rejects_arbitrary_second_parent_after_quality(
    tmp_path: Path,
) -> None:
    repo, _, _, _ = build_candidate(tmp_path, line_tip_after_quality=True)

    assert (INTEGRATION, "history.integration.parent") in history_codes(repo)


def test_manifest_candidate_rejects_second_parent_without_target_line_read_only(
    tmp_path: Path,
) -> None:
    repo = build_missing_line_second_parent_candidate(tmp_path)
    before = repository_snapshot(repo.path)

    errors = [
        (error.path, error.code)
        for error in validate_project(repo.path)
        if error.code.startswith("history.")
    ]

    assert errors == [(INTEGRATION, "history.integration.parent")]
    assert repository_snapshot(repo.path) == before


@pytest.mark.parametrize(
    "path",
    ["product/merge-only.py", "tests/test_merge_only.py", "runtime/config.toml"],
)
def test_integration_candidate_rejects_every_non_manifest_merge_only_path(
    tmp_path: Path, path: str
) -> None:
    repo, _, _, _ = build_candidate(tmp_path, merge_only_path=path)

    assert (INTEGRATION, "history.integration.tree") in history_codes(repo)


def test_integration_candidate_rejects_conflict_resolution_result(tmp_path: Path) -> None:
    repo, _, _, _ = build_candidate(tmp_path, conflict_resolution=True)

    assert (INTEGRATION, "history.integration.tree") in history_codes(repo)


def test_dqc_candidate_binding_and_delivery_chronology_passes_after_later_commit(
    tmp_path: Path,
) -> None:
    repo, _, _, candidate = build_candidate(tmp_path)
    dqc = write_dqc(repo, candidate)
    delivery = deliver(repo)
    (repo.path / "later-governance.txt").write_text("later\n", encoding="utf-8")
    later = repo.commit("later", "later unrelated main governance")

    assert candidate == git(repo.path, "rev-parse", f"{dqc}^")
    assert dqc == git(repo.path, "rev-parse", f"{delivery}^")
    assert delivery == git(repo.path, "rev-parse", f"{later}^")
    assert validate_project(repo.path) == []


def test_unrelated_governance_between_dqc_pass_and_delivery_preserves_pass(
    tmp_path: Path,
) -> None:
    repo, _, _, candidate = build_candidate(tmp_path)
    write_dqc(repo, candidate)
    (repo.path / "later-governance.txt").write_text("later\n", encoding="utf-8")
    repo.commit("later", "unrelated governance before delivery")
    deliver(repo)

    assert validate_project(repo.path) == []


@pytest.mark.parametrize("effective_result", ["failed", "blocked", "draft"])
def test_delivery_requires_effective_dqc_pass_immediately_before_transition(
    tmp_path: Path, effective_result: str
) -> None:
    repo, _, _, candidate = build_candidate(tmp_path)
    write_dqc(repo, candidate)
    write_dqc(repo, candidate, result=effective_result)
    deliver(repo)

    assert (DQC, "history.integration.dqc") in history_codes(repo)


def test_latest_corrected_dqc_pass_allows_delivery(tmp_path: Path) -> None:
    repo, _, _, candidate = build_candidate(tmp_path)
    write_dqc(repo, candidate)
    write_dqc(repo, candidate, result="failed")
    write_dqc(repo, candidate)
    deliver(repo)

    assert validate_project(repo.path) == []


def test_delivery_rejects_dqc_removed_after_pass(tmp_path: Path) -> None:
    repo, _, _, candidate = build_candidate(tmp_path)
    write_dqc(repo, candidate)
    (repo.path / DQC).unlink()
    repo.commit("dqc-removed", "remove effective DQC")
    deliver(repo)

    assert (DQC, "history.integration.dqc") in history_codes(repo)


def test_delivery_rejects_stale_candidate_binding_after_pass(tmp_path: Path) -> None:
    repo, main, _, candidate = build_candidate(tmp_path)
    assert main != candidate
    write_dqc(repo, candidate)
    write_dqc(repo, main)
    deliver(repo)

    assert (DQC, "history.integration.dqc") in history_codes(repo)


def test_delivery_without_dqc_fails_closed(tmp_path: Path) -> None:
    repo, _, _, _ = build_candidate(tmp_path)
    deliver(repo)

    assert (DQC, "history.integration.dqc") in history_codes(repo)


def test_dqc_must_bind_exact_integration_candidate(tmp_path: Path) -> None:
    repo, main, _, candidate = build_candidate(tmp_path)
    assert candidate != main
    write_dqc(repo, main)
    deliver(repo)

    assert (DQC, "history.integration.dqc") in history_codes(repo)


def test_delivery_before_dqc_pass_fails_closed(tmp_path: Path) -> None:
    repo, _, _, candidate = build_candidate(tmp_path)
    deliver(repo)
    write_dqc(repo, candidate)

    assert (DQC, "history.integration.dqc") in history_codes(repo)
