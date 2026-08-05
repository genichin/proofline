from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from proofline.validator import validate_project
from test_implementation_history import HistoryRepo, LINE


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


def history_codes(repo: HistoryRepo) -> set[tuple[str, str]]:
    return {(error.path, error.code) for error in validate_project(repo.path)}


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
