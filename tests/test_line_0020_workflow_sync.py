from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "skills/proofline-run-dqc/scripts/preflight_integration_candidate.py"
TEMPLATE = ROOT / "templates/schema-v1/artifacts/integration.md"
RUN_DQC = ROOT / "skills/proofline-run-dqc/SKILL.md"
START = ROOT / "skills/proofline-start-implementation/SKILL.md"
APPROVE = ROOT / "skills/proofline-approve-specification/SKILL.md"


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ("git", *args), cwd=repo, text=True, capture_output=True, check=False
    )
    if check:
        assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def make_candidate(tmp_path: Path, *, mismatch: bool = False) -> tuple[Path, str, str, str]:
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "proofline@example.invalid")
    git(repo, "config", "user.name", "ProofLine Test")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    commit(repo, "base")

    git(repo, "switch", "-qc", "line-0007")
    line = repo / ".proofline/lines/line-0007/line-0007.md"
    line.parent.mkdir(parents=True)
    line.write_text(
        '---\nid: "line-0007"\nexecution_status: verifying\n'
        "implementation_history: first_parent\n---\n",
        encoding="utf-8",
    )
    (repo / "line.txt").write_text("line\n", encoding="utf-8")
    q = commit(repo, "Line Q")

    git(repo, "switch", "-q", "main")
    (repo / "main.txt").write_text("main\n", encoding="utf-8")
    m = commit(repo, "Main M")
    git(repo, "switch", "-qc", "candidate/line-0007")
    merged = subprocess.run(
        ("git", "merge", "--no-ff", "--no-commit", "line-0007"),
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert merged.returncode == 0, merged.stderr
    manifest = repo / ".proofline/lines/line-0007/integration-0007.md"
    manifest.write_text(
        "---\n"
        'id: "integration-0007"\n'
        'line_id: "line-0007"\n'
        f'main_parent: "{m}"\n'
        f'line_head: "{"f" * 40 if mismatch else q}"\n'
        "---\n",
        encoding="utf-8",
    )
    v = commit(repo, "Integration V")
    assert git(repo, "rev-parse", f"{v}^1") == m
    assert git(repo, "rev-parse", f"{v}^2") == q
    return repo, m, q, v


def snapshot(repo: Path) -> tuple[str, str, str, int, dict[str, str]]:
    files = {
        path.relative_to(repo).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(repo.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(repo).parts
    }
    return (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "show-ref"),
        git(repo, "status", "--porcelain=v1", "--untracked-files=all"),
        int(git(repo, "count-objects", "-v").splitlines()[0].split(":", 1)[1]),
        files,
    )


def run_helper(repo: Path, m: str, q: str, v: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            str(HELPER),
            "--repo",
            str(repo),
            "--line-id",
            "line-0007",
            "--main-ref",
            "refs/heads/main",
            "--line-ref",
            "refs/heads/line-0007",
            "--main-parent",
            m,
            "--line-head",
            q,
            "--candidate",
            v,
        ),
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )


def test_pre_admission_helper_passes_exact_m_q_v_without_mutation(tmp_path: Path) -> None:
    repo, m, q, v = make_candidate(tmp_path)
    before = snapshot(repo)

    result = run_helper(repo, m, q, v)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"pre-admission: passed M={m} Q={q} V={v}"
    assert snapshot(repo) == before


@pytest.mark.parametrize("drift", ["main", "line"])
def test_pre_admission_helper_rejects_stale_mutable_ref_without_mutation(
    tmp_path: Path, drift: str
) -> None:
    repo, m, q, v = make_candidate(tmp_path)
    target = "refs/heads/main" if drift == "main" else "refs/heads/line-0007"
    git(repo, "update-ref", target, git(repo, "rev-parse", f"{target}^"))
    before = snapshot(repo)

    result = run_helper(repo, m, q, v)

    assert result.returncode == 2
    assert f"stale {drift} ref" in result.stderr
    assert snapshot(repo) == before


def test_pre_admission_helper_rejects_manifest_parent_mismatch_without_mutation(
    tmp_path: Path,
) -> None:
    repo, m, q, v = make_candidate(tmp_path, mismatch=True)
    before = snapshot(repo)

    result = run_helper(repo, m, q, v)

    assert result.returncode == 2
    assert "manifest binding mismatch" in result.stderr
    assert snapshot(repo) == before


def test_pre_admission_helper_rejects_dirty_candidate_worktree(tmp_path: Path) -> None:
    repo, m, q, v = make_candidate(tmp_path)
    (repo / "collision.txt").write_text("dirty\n", encoding="utf-8")
    before = snapshot(repo)

    result = run_helper(repo, m, q, v)

    assert result.returncode == 2
    assert "candidate worktree is not clean" in result.stderr
    assert snapshot(repo) == before


def test_line_0020_contracts_define_approved_bootstrap_future_and_integration_boundaries() -> None:
    contracts = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "docs/artifact-layout.md",
            "docs/contracts/requirements-and-criteria.md",
            "docs/contracts/micro-spec-and-iqc.md",
            "docs/contracts/line-delivery.md",
            "docs/contracts/document-format.md",
        )
    )
    for required in (
        "S=A < H < P < I < Q",
        "A < H < S0 < S < P < I < Q",
        "사용자만",
        "독립 specification reviewer",
        "status-only",
        "exact `H`",
        ".proofline/lines/line-<NNNN>/integration-<NNNN>.md",
        "parent[0]",
        "parent[1]",
        "pre-integration",
        "post-integration",
        "mutable",
        "immutable",
        "manifest",
        "ancestry",
        "criteria coverage",
    ):
        assert required in contracts


def test_integration_template_is_canonical_frontmatter_only_and_packaged() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert text == (
        '---\nid: "{{INTEGRATION_ID}}"\nline_id: "{{LINE_ID}}"\n'
        'main_parent: "{{MAIN_PARENT}}"\nline_head: "{{LINE_HEAD}}"\n---\n'
    )
    assert "{{TODO" not in text and "{{UNKNOWN" not in text and "{{NEEDS_EVIDENCE" not in text
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    readme = (ROOT / "templates/README.md").read_text(encoding="utf-8")
    assert "integration.md" in readme
    assert '"templates/schema-v1" = "proofline_schema_v1_templates"' in pyproject
    assert '"templates" = "proofline_home/templates"' in pyproject


def test_synchronized_skill_versions_and_authority_language() -> None:
    expected = {START: "1.4.0", APPROVE: "1.4.0", RUN_DQC: "1.3.0"}
    for path, version in expected.items():
        _, frontmatter, body = path.read_text(encoding="utf-8").split("---", 2)
        assert yaml.safe_load(frontmatter)["version"] == version
        assert body.strip()
    assert "exact `H`" in START.read_text(encoding="utf-8")
    approval = APPROVE.read_text(encoding="utf-8")
    assert "사용자만" in approval and "S=A" in approval and "S0" in approval
    dqc = RUN_DQC.read_text(encoding="utf-8")
    assert "preflight_integration_candidate.py" in dqc
    assert "main-first" in dqc and "pre-integration" in dqc and "post-integration" in dqc
