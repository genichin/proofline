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


def _line_text(line_id: str, status: str) -> str:
    return (
        f'---\nid: "{line_id}"\nexecution_status: {status}\n'
        "implementation_history: first_parent\n---\n"
    )


def _ms_text(*, status: str, spec_status: str = "approved") -> str:
    return (
        '---\nid: "ms-0007-001"\nparent_req: "req-0007"\ncriteria:\n'
        f'  - "ac-0001"\nspec_status: {spec_status}\nimplementation_status: {status}\n'
        "---\n\n# Micro-SPEC\n\n## Scope\n\n범위.\n\n## Implementation\n\n구현."
        "\n\n## Verification\n\n검증.\n"
    )


def make_candidate(
    tmp_path: Path,
    *,
    mismatch: bool = False,
    defect: str | None = None,
) -> tuple[Path, str, str, str]:
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "proofline@example.invalid")
    git(repo, "config", "user.name", "ProofLine Test")
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "config", "gc.auto", "0")
    git(repo, "config", "maintenance.auto", "false")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    commit(repo, "base")

    if defect == "arbitrary":
        git(repo, "switch", "-qc", "line-0007")
        (repo / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
        q = commit(repo, "arbitrary X")
    else:
        git(repo, "switch", "-qc", "line-0007")
        line = repo / ".proofline/lines/line-0007/line-0007.md"
        ms = repo / ".proofline/lines/line-0007/micro-specs/ms-0007-001.md"
        ms.parent.mkdir(parents=True)
        line.write_text(_line_text("line-0007", "not_started"), encoding="utf-8")
        ms.write_text(_ms_text(status="not_started"), encoding="utf-8")
        specification = commit(repo, "approved specification")
        line.write_text(_line_text("line-0007", "in_progress"), encoding="utf-8")
        commit(repo, "Line handoff")
        ms.write_text(_ms_text(status="in_progress"), encoding="utf-8")
        commit(repo, "start implementation")
        (repo / "product.py").write_text("IMPLEMENTED = True\n", encoding="utf-8")
        implementation = commit(repo, "implementation")

        line_id = "line-9999" if defect == "wrong-id" else "line-0007"
        line.write_text(
            _line_text(line_id, "in_progress" if defect == "not-verifying" else "verifying"),
            encoding="utf-8",
        )
        ms.write_text(_ms_text(status="implemented"), encoding="utf-8")
        iqc = repo / ".proofline/lines/line-0007/micro-specs/iqc-0007-001.md"
        if defect != "missing-iqc":
            iqc_result = "failed" if defect == "failed-iqc" else "passed"
            bound_ms = "ms-0007-999" if defect == "mismatched-iqc" else "ms-0007-001"
            bound_implementation = specification if defect == "stale-iqc" else implementation
            iqc.write_text(
                "---\n"
                'id: "iqc-0007-001"\n'
                f'micro_spec: "{bound_ms}"\n'
                f'micro_spec_commit: "{specification}"\n'
                f'implementation_commit: "{bound_implementation}"\n'
                f"result: {iqc_result}\n"
                "---\n\n# IQC\n\n## Target\n\n대상.\n\n## Checks\n\n통과."
                "\n\n## Criteria Results\n\n통과.\n\n## Result\n\n통과.\n",
                encoding="utf-8",
            )
        if defect == "wrong-path":
            wrong = repo / ".proofline/lines/line-9999/line-9999.md"
            wrong.parent.mkdir(parents=True)
            wrong.write_text(_line_text("line-9999", "verifying"), encoding="utf-8")
            line.unlink()
        if defect == "multi-line":
            other = repo / ".proofline/lines/line-0008/line-0008.md"
            other.parent.mkdir(parents=True)
            other.write_text(_line_text("line-0008", "verifying"), encoding="utf-8")
        if defect == "unrelated-quality-change":
            (repo / "late.txt").write_text("late\n", encoding="utf-8")
        q = commit(repo, "Line Q")
        if defect == "not-quality-transition":
            git(repo, "commit", "--allow-empty", "-qm", "advance after Q")
            q = git(repo, "rev-parse", "HEAD")

    git(repo, "switch", "-q", "main")
    (repo / "main.txt").write_text("main\n", encoding="utf-8")
    m = commit(repo, "Main M")
    git(repo, "switch", "-qc", "candidate/line-0007")
    merge_args = ("git", "merge", "--no-ff", "--no-commit")
    if defect in {"arbitrary", "wrong-path"}:
        merge_args += ("-s", "ours")
    merged = subprocess.run(
        (*merge_args, "line-0007"),
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert merged.returncode == 0, merged.stderr
    manifest = repo / ".proofline/lines/line-0007/integration-0007.md"
    manifest.parent.mkdir(parents=True, exist_ok=True)
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


def run_helper(
    repo: Path, m: str, q: str, v: str, *, script: Path = HELPER
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            str(script),
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


@pytest.mark.parametrize(
    ("defect", "diagnostic"),
    [
        ("arbitrary", "quality head target Line is missing"),
        ("wrong-path", "quality head target Line is missing"),
        ("wrong-id", "quality head target Line identity mismatch"),
        ("not-verifying", "quality head Line status must be verifying"),
        ("missing-iqc", "quality head IQC coverage/binding invalid: missing IQC for ms-0007-001"),
        ("failed-iqc", "quality head IQC coverage/binding invalid: IQC iqc-0007-001 is not passed"),
        ("mismatched-iqc", "quality head IQC coverage/binding invalid: IQC iqc-0007-001 identity mismatch"),
        ("stale-iqc", "quality head IQC coverage/binding invalid: IQC iqc-0007-001 implementation binding is stale or invalid"),
        ("multi-line", "quality head contains multiple Lines"),
        ("unrelated-quality-change", "quality head transition contains unrelated paths"),
        ("not-quality-transition", "quality head is not the exact first-parent quality transition"),
    ],
)
def test_pre_admission_helper_rejects_non_quality_head_without_mutation(
    tmp_path: Path, defect: str, diagnostic: str
) -> None:
    repo, m, q, v = make_candidate(tmp_path, defect=defect)
    before = snapshot(repo)

    result = run_helper(repo, m, q, v)

    assert result.returncode == 2
    assert result.stderr.strip() == f"pre-admission: failed: {diagnostic}"
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
    expected = {START: "1.4.0", APPROVE: "1.4.0", RUN_DQC: "1.4.0"}
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
