from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/valid-minimal"
SKILL = ROOT / "skills/proofline-create-worktree/SKILL.md"
HELPER = ROOT / "skills/proofline-create-worktree/scripts/inspect_worktree_readiness.py"
APPROVAL_SKILL = ROOT / "skills/proofline-approve-specification/SKILL.md"


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args), cwd=root, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def make_repository(
    tmp_path: Path,
    *,
    ignore_worktrees: bool = True,
    discovery_status: str = "confirmed",
    requirement_status: str = "approved",
    criterion_status: str = "active",
) -> Path:
    repository = tmp_path / "repository"
    shutil.copytree(FIXTURE, repository)
    if ignore_worktrees:
        (repository / ".gitignore").write_text("/.worktrees/\n", encoding="utf-8")
    discovery = repository / ".proofline/lines/line-0001/dcy-0001.md"
    discovery.write_text(
        discovery.read_text(encoding="utf-8").replace(
            "status: confirmed", f"status: {discovery_status}"
        ),
        encoding="utf-8",
    )
    requirement = repository / ".proofline/lines/line-0001/req-0001.md"
    requirement.write_text(
        requirement.read_text(encoding="utf-8").replace(
            "status: approved", f"status: {requirement_status}"
        ),
        encoding="utf-8",
    )
    criterion = repository / ".proofline/criteria/ac-0001.md"
    criterion.write_text(
        criterion.read_text(encoding="utf-8").replace(
            "status: active", f"status: {criterion_status}"
        ),
        encoding="utf-8",
    )
    git(repository, "init", "-q", "-b", "main")
    git(repository, "config", "user.name", "ProofLine Test")
    git(repository, "config", "user.email", "proofline@example.invalid")
    git(repository, "add", "-A")
    git(repository, "commit", "-qm", "fixture baseline")
    return repository


def snapshot(repository: Path) -> dict[str, object]:
    canonical = {
        path.relative_to(repository).as_posix(): path.read_bytes()
        for path in sorted((repository / ".proofline").rglob("*"))
        if path.is_file()
    }
    index = Path(git(repository, "rev-parse", "--git-path", "index"))
    if not index.is_absolute():
        index = repository / index
    target = repository / ".worktrees/line-0001"
    if target.is_symlink():
        target_state: object = ("symlink", os.readlink(target))
    elif target.is_file():
        target_state = ("file", target.read_bytes())
    elif target.is_dir():
        target_state = ("directory", tuple(sorted(path.name for path in target.iterdir())))
    else:
        target_state = None
    return {
        "head": git(repository, "rev-parse", "HEAD"),
        "refs": git(repository, "for-each-ref", "--format=%(refname) %(objectname)"),
        "index": index.read_bytes(),
        "status": git(repository, "status", "--porcelain", "--untracked-files=all"),
        "worktrees": git(repository, "worktree", "list", "--porcelain"),
        "canonical": canonical,
        "target_state": target_state,
    }


def run_helper(repository: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            str(HELPER),
            "--repository",
            str(repository),
            "--line",
            "line-0001",
        ),
        text=True,
        capture_output=True,
        check=False,
    )


def follow_documented_creation(repository: Path, payload: dict[str, object]) -> bool:
    observations = payload["observations"]
    assert isinstance(observations, dict)
    primary = observations["primary_worktree"]
    target = observations["target"]
    assert isinstance(primary, dict)
    assert isinstance(target, dict)
    if not all(
        target[key]
        for key in ("branch_available", "path_available", "registration_available")
    ):
        return False
    git(
        repository,
        "worktree",
        "add",
        "-qb",
        "line/line-0001-implementation",
        str(target["path"]),
        "HEAD",
    )
    return True


def test_worktree_skill_contract_is_optional_and_create_only() -> None:
    text = SKILL.read_text(encoding="utf-8")
    _, frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)

    assert metadata["name"] == "proofline-create-worktree"
    assert metadata["version"] == "1.0.0"
    for required in (
        "optional next action",
        "inspect_worktree_readiness.py",
        "recommendation: review",
        "사용자가 명시적으로 생성을 선택",
        "git worktree add -b",
        "starting commit",
        "현재 agent",
        "다른 agent·subagent",
        "Main에서는",
        "다른 agent·subagent에게 인계하기로 선택한 경우에만",
        "현재 agent가 생성된 worktree에서 계속하기로 선택하면",
        ".proofline/lines/line-NNNN/evidence/activity-log.md",
        "계획",
        "TODO",
        "진행 상황",
        "blocker",
        "주요 결정",
        "검증 결과",
        "다음 행동",
        "기존 chronology",
        "외부 동시 변경",
        "자동 삭제",
        "요약 교체",
        "분할하지 않는다",
        "비정본 참고 문서",
        "상대 링크",
        "원시 transcript",
    ):
        assert required in body
    for forbidden in (
        "git worktree add --force",
        "git branch -D",
        "git worktree remove",
        "proofline worktree",
        "--show-object-format",
        "full HEAD OID",
        "observed HEAD와 creation 직전 HEAD",
    ):
        assert forbidden not in body
    helper = HELPER.read_text(encoding="utf-8")
    assert "--show-object-format" not in helper
    assert "OID_LENGTH" not in helper


def test_documented_activity_log_append_preserves_chronology_and_relative_link(
    tmp_path: Path,
) -> None:
    line = tmp_path / ".proofline/lines/line-0001"
    evidence = line / "evidence"
    evidence.mkdir(parents=True)
    log = evidence / "activity-log.md"
    initial = b"# Activity Log\n\n## initial\n\n- existing bytes\n"
    log.write_bytes(initial)

    observed = log.read_bytes()
    first = b"\n## first agent append\n\n- next action: verify\n"
    assert log.read_bytes() == observed
    with log.open("ab") as stream:
        stream.write(first)

    stale = log.read_bytes()
    external = b"\n## external append\n\n- blocker: none\n"
    with log.open("ab") as stream:
        stream.write(external)
    assert log.read_bytes() != stale

    latest = log.read_bytes()
    second = b"\n## second agent append\n\n- result: PASS\n"
    assert log.read_bytes() == latest
    with log.open("ab") as stream:
        stream.write(second)

    body = (
        "---\nid: line-0001\nstatus: implementation\n---\n\n"
        "# Activity\n\n"
        "- 최근 활동: 검증 완료\n"
        "- 로그: [activity-log.md](evidence/activity-log.md)\n"
    )
    (line / "line-0001.md").write_text(body, encoding="utf-8")

    assert log.read_bytes() == initial + first + external + second
    assert log.read_bytes().index(first) < log.read_bytes().index(external)
    assert log.read_bytes().index(external) < log.read_bytes().index(second)
    assert "(evidence/activity-log.md)" in body


def test_approval_skill_offers_worktree_as_optional_next_action() -> None:
    text = APPROVAL_SKILL.read_text(encoding="utf-8")
    _, frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)

    assert metadata["version"] == "3.3.0"
    assert "proofline-create-worktree" in body
    assert "optional next action" in body
    assert "main에서 직접 구현" in body
    assert "필수 gate" in body


def test_ready_helper_returns_one_create_advisory_without_mutation(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    before = snapshot(repository)

    result = run_helper(repository)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert result.stdout.count("\n") == 1
    assert payload["advisory"] is True
    assert payload["recommendation"] == "create"
    assert payload["reasons"] == []
    observations = payload["observations"]
    assert observations["repository_root"] == str(repository.resolve())
    assert observations["line_id"] == "line-0001"
    assert observations["canonical_validation"]["passed"] is True
    assert observations["discovery"]["status"] == "confirmed"
    assert observations["requirement"]["status"] == "approved"
    assert all(item["ready"] for item in observations["criteria"])
    assert observations["primary_worktree"] == {
        "path": str(repository.resolve()),
        "branch": "main",
        "head": before["head"],
        "clean": True,
    }
    assert observations["target"]["ref"] == "refs/heads/line/line-0001-implementation"
    assert observations["target"]["path"] == str(
        (repository / ".worktrees/line-0001").resolve()
    )
    assert observations["target"]["branch_available"] is True
    assert observations["target"]["path_available"] is True
    assert observations["target"]["registration_available"] is True
    assert observations["ignore"]["ignored"] is True
    assert snapshot(repository) == before


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("discovery", "discovery-not-confirmed"),
        ("requirement", "requirement-not-approved"),
        ("criterion", "criterion-status-mismatch:ac-0001"),
        ("detached", "primary-branch-not-main"),
        ("branch", "primary-branch-not-main"),
        ("dirty", "primary-worktree-dirty"),
        ("ignore", "worktrees-ignore-missing"),
        ("target_branch", "target-branch-unavailable"),
        ("target_file", "target-path-unavailable"),
        ("target_directory", "target-path-unavailable"),
        ("target_symlink", "target-path-unavailable"),
        ("registered_branch", "target-registration-unavailable"),
        ("registered_path", "target-registration-unavailable"),
    ],
)
def test_observed_unready_state_returns_review_without_mutation(
    tmp_path: Path, case: str, reason: str
) -> None:
    repository = make_repository(
        tmp_path,
        ignore_worktrees=case != "ignore",
        discovery_status="draft" if case == "discovery" else "confirmed",
        requirement_status="draft" if case == "requirement" else "approved",
        criterion_status="draft" if case == "criterion" else "active",
    )
    if case == "detached":
        git(repository, "switch", "-q", "--detach")
    elif case == "branch":
        git(repository, "switch", "-qc", "topic")
    elif case == "dirty":
        (repository / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    elif case == "target_branch":
        git(repository, "branch", "line/line-0001-implementation")
    elif case in {"target_file", "target_directory", "target_symlink"}:
        target = repository / ".worktrees/line-0001"
        target.parent.mkdir()
        if case == "target_file":
            target.write_text("occupied\n", encoding="utf-8")
        elif case == "target_directory":
            target.mkdir()
        else:
            target.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    elif case == "registered_branch":
        target = tmp_path / "registered-elsewhere"
        git(
            repository,
            "worktree",
            "add",
            "-qb",
            "line/line-0001-implementation",
            str(target),
            "HEAD",
        )
    elif case == "registered_path":
        target = repository / ".worktrees/line-0001"
        git(
            repository,
            "worktree",
            "add",
            "-qb",
            "line/other-implementation",
            str(target),
            "HEAD",
        )
    before = snapshot(repository)

    result = run_helper(repository)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["advisory"] is True
    assert payload["recommendation"] == "review"
    assert reason in payload["reasons"]
    assert snapshot(repository) == before
    if case in {
        "target_branch",
        "target_file",
        "target_directory",
        "target_symlink",
        "registered_branch",
        "registered_path",
    }:
        assert follow_documented_creation(repository, payload) is False
        assert snapshot(repository) == before


def test_invalid_repository_is_observation_error_without_success_json(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    result = run_helper(missing)

    assert result.returncode != 0
    assert result.stdout == ""
    assert "error:" in result.stderr


def test_no_worktree_choice_preserves_ready_repository(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    before = snapshot(repository)

    inspected = run_helper(repository)
    payload = json.loads(inspected.stdout)

    assert payload["recommendation"] == "create"
    assert snapshot(repository) == before
    assert not (repository / ".worktrees/line-0001").exists()


def test_head_change_after_inspection_uses_creation_time_head(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    inspected = run_helper(repository)
    payload = json.loads(inspected.stdout)
    (repository / "drift.txt").write_text("new head\n", encoding="utf-8")
    git(repository, "add", "drift.txt")
    git(repository, "commit", "-qm", "advance primary")
    creation_time_head = git(repository, "rev-parse", "HEAD")

    assert follow_documented_creation(repository, payload) is True
    target = repository / ".worktrees/line-0001"
    assert git(target, "rev-parse", "HEAD") == creation_time_head


def test_documented_creation_uses_current_head_and_exact_branch_and_path(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    inspected = run_helper(repository)
    payload = json.loads(inspected.stdout)
    starting_commit = git(repository, "rev-parse", "HEAD")
    target = repository / ".worktrees/line-0001"

    assert follow_documented_creation(repository, payload) is True

    assert git(target, "branch", "--show-current") == "line/line-0001-implementation"
    assert git(target, "rev-parse", "HEAD") == starting_commit
    assert Path(git(target, "rev-parse", "--show-toplevel")) == target.resolve()
    assert git(target, "status", "--porcelain", "--untracked-files=all") == ""
    assert not list(target.rglob("*handoff*"))


def test_primary_line_init_does_not_change_linked_worktree(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    payload = json.loads(run_helper(repository).stdout)
    assert follow_documented_creation(repository, payload) is True
    target = repository / ".worktrees/line-0001"
    before = {
        "branch": git(target, "branch", "--show-current"),
        "head": git(target, "rev-parse", "HEAD"),
        "status": git(target, "status", "--porcelain", "--untracked-files=all"),
    }

    initialized = subprocess.run(
        ("proofline", "line", "init", "--title", "Later governance line"),
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )

    assert initialized.returncode == 0, initialized.stderr
    assert (repository / ".proofline/lines/line-0002/dcy-0002.md").is_file()
    assert {
        "branch": git(target, "branch", "--show-current"),
        "head": git(target, "rev-parse", "HEAD"),
        "status": git(target, "status", "--porcelain", "--untracked-files=all"),
    } == before
