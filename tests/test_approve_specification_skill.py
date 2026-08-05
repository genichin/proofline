from pathlib import Path
import hashlib
import subprocess
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/proofline-approve-specification/SKILL.md"
SCRIPT = ROOT / "skills/proofline-approve-specification/scripts/audit_transition.py"
CONTRACT = ROOT / "docs/contracts/requirements-and-criteria.md"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args), cwd=repo, text=True, capture_output=True, check=True
    )


def write_spec(repo: Path, *, req_status: str, ac_status: str) -> None:
    line = repo / ".proofline/lines/line-0007"
    criteria = repo / ".proofline/criteria"
    line.mkdir(parents=True, exist_ok=True)
    criteria.mkdir(parents=True, exist_ok=True)
    (line / "req-0007.md").write_text(
        "---\n"
        "id: req-0007\n"
        f"status: {req_status}\n"
        "discovery: dcy-0007\n"
        "criteria:\n"
        "  create:\n"
        "    - ac-0011\n"
        "  update: []\n"
        "  retire: []\n"
        "---\n\n"
        "# Requirement\n\n"
        "## Objective\n\nObjective.\n\n"
        "## Scope\n\nScope.\n\n"
        "## Non-Goals\n\nNone.\n",
        encoding="utf-8",
    )
    (criteria / "ac-0011.md").write_text(
        "---\n"
        "id: ac-0011\n"
        f"status: {ac_status}\n"
        "---\n\n"
        "# Criterion\n\n"
        "## Criterion\n\nCriterion.\n\n"
        "## Verification\n\nVerification.\n",
        encoding="utf-8",
    )


def make_repo(tmp_path: Path, *, mode: str) -> tuple[Path, str]:
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "proofline@example.invalid")
    git(repo, "config", "user.name", "ProofLine Test")
    if mode == "recorded":
        write_spec(repo, req_status="draft", ac_status="draft")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "Draft specification")
        write_spec(repo, req_status="approved", ac_status="active")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "Approve specification")
    elif mode == "direct":
        write_spec(repo, req_status="approved", ac_status="active")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "Direct approval")
    elif mode == "unapproved":
        write_spec(repo, req_status="draft", ac_status="draft")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "Draft specification")
    else:
        raise AssertionError(mode)
    return repo, git(repo, "rev-parse", "HEAD").stdout.strip()


def snapshot(repo: Path) -> tuple[str, str, str, dict[str, str]]:
    files = {
        path.relative_to(repo).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(repo.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(repo).parts
    }
    return (
        git(repo, "rev-parse", "HEAD").stdout,
        git(repo, "show-ref").stdout,
        git(repo, "status", "--porcelain", "--untracked-files=all").stdout,
        files,
    )


def run_audit(repo: Path, commit: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(repo),
            "--line-id",
            "line-0007",
            "--approval-commit",
            commit,
        ),
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )


def test_approval_skill_has_valid_metadata_and_minimal_gate_policy() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == "proofline-approve-specification"
    assert metadata["description"].startswith("Use when ")
    assert metadata["version"] == "1.4.0"
    assert "~/.proofline/skills/proofline-approve-specification/" in body
    for required in [
        "## When to Use",
        "권장",
        "direct approval",
        "recorded",
        "not recorded",
        "차단하지 않는다",
        "사용자",
        "no-mutation",
        "사용자만",
        "S0",
        "S=A",
        "proofline.independent-review/v1",
        "proofline.user-approval/v1",
        "audit_approval_authority.py",
        "cryptographically authenticate",
    ]:
        assert required in body


def test_contract_accepts_exact_direct_approval_as_baseline() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    for required in [
        "권장 감사 경로",
        "direct approval",
        "transition evidence",
        "implementation을 차단하지 않는다",
    ]:
        assert required in text
    assert "implementation branch는 `approved` transition을 기록한 exact main commit에서만" not in text


def test_contract_separates_durable_acceptance_from_release_evidence() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    for required in [
        "version-independent product behavior",
        "새 version publication만으로 새 AC를 만들지 않는다",
        "Micro-SPEC implementation parameter와 IQC·DQC·release evidence",
    ]:
        assert required in text


def test_contract_defines_external_exact_evidence_authority_gate() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    for required in [
        "proofline.independent-review/v1",
        "proofline.user-approval/v1",
        "review_evidence_sha256",
        "mutation_performed",
        "operational identity label",
        "암호학적으로 인증하지 않는다",
        "read-only",
    ]:
        assert required in text


def test_audit_reports_recorded_transition_without_mutation(tmp_path: Path) -> None:
    repo, approval = make_repo(tmp_path, mode="recorded")
    before = snapshot(repo)

    result = run_audit(repo, approval)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "transition: recorded"
    assert snapshot(repo) == before


def test_audit_reports_direct_approval_without_blocking_or_mutation(tmp_path: Path) -> None:
    repo, approval = make_repo(tmp_path, mode="direct")
    before = snapshot(repo)

    result = run_audit(repo, approval)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "transition: not recorded"
    assert snapshot(repo) == before


def test_audit_rejects_non_approved_commit_without_mutation(tmp_path: Path) -> None:
    repo, commit = make_repo(tmp_path, mode="unapproved")
    before = snapshot(repo)

    result = run_audit(repo, commit)

    assert result.returncode != 0
    assert "REQ.status must be approved" in result.stderr
    assert snapshot(repo) == before
