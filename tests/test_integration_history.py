from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from proofline.validator import validate_project
from test_implementation_history import HistoryRepo, LINE


INTEGRATION = ".proofline/lines/line-0001/integration-0001.md"


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
    main_parent: str | None = None,
    line_head: str | None = None,
    merge_only_product: bool = False,
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

    git(repo.path, "switch", "-q", "main")
    (repo.path / "main-governance.txt").write_text("main advanced\n", encoding="utf-8")
    main_sha = repo.commit("main", "advance main governance")
    git(repo.path, "merge", "--no-ff", "--no-commit", "line-0001")
    if merge_only_product:
        (repo.path / "merge-only.py").write_text("forbidden = True\n", encoding="utf-8")
    if manifest:
        integration = repo.path / INTEGRATION
        integration.write_text(
            "---\n"
            'id: "integration-0001"\n'
            'line_id: "line-0001"\n'
            f'main_parent: "{main_parent or main_sha}"\n'
            f'line_head: "{line_head or line_head_sha}"\n'
            "---\n",
            encoding="utf-8",
        )
    git(repo.path, "add", "-A")
    git(repo.path, "commit", "-qm", "integrate Line 0001")
    candidate = git(repo.path, "rev-parse", "HEAD")
    assert git(repo.path, "rev-parse", "HEAD^1") == main_sha
    assert git(repo.path, "rev-parse", "HEAD^2") == line_head_sha
    assert git(repo.path, "merge-base", approval, handoff) == approval
    return repo, main_sha, line_head_sha, candidate


def history_codes(repo: HistoryRepo) -> set[tuple[str, str]]:
    return {(error.path, error.code) for error in validate_project(repo.path)}


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
        kwargs["merge_only_product"] = True
    repo, _, _, _ = build_candidate(tmp_path, **kwargs)

    assert (INTEGRATION, expected_code) in history_codes(repo)


def test_post_candidate_first_parent_commit_keeps_immutable_manifest_binding(
    tmp_path: Path,
) -> None:
    repo, _, _, candidate = build_candidate(tmp_path)
    (repo.path / "later-governance.txt").write_text("later\n", encoding="utf-8")
    repo.commit("later", "later unrelated governance")

    assert candidate == git(repo.path, "rev-parse", "HEAD^")
    assert validate_project(repo.path) == []
