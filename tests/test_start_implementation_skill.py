from pathlib import Path
import importlib.util
import os
import subprocess
import sys
import time

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/proofline-start-implementation/SKILL.md"
CONTRACT = ROOT / "docs/contracts/line-delivery.md"
GITIGNORE = ROOT / ".gitignore"
SCRIPT = ROOT / "skills/proofline-start-implementation/scripts/create_worktree.py"


def load_worktree_script():
    spec = importlib.util.spec_from_file_location("create_worktree", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )


def make_approved_repo(
    tmp_path: Path, *, req_status: str = "approved", config: bool = False
) -> tuple[Path, str]:
    repo = tmp_path / "project"
    line_dir = repo / ".proofline/lines/line-0007"
    line_dir.mkdir(parents=True)
    (repo / ".gitignore").write_text("/.worktrees/\n")
    if config:
        (repo / "proofline.yaml").write_text(
            "schema_version: 1\nartifact_root: .proofline\n"
        )
    (line_dir / "line-0007.md").write_text(
        '---\nid: "line-0007"\nexecution_status: not_started\n---\n'
    )
    (line_dir / "dcy-0007.md").write_text(
        '---\nid: "dcy-0007"\nstatus: confirmed\n---\n\n# Discovery\n'
    )
    (line_dir / "req-0007.md").write_text(
        f'---\nid: "req-0007"\nstatus: {req_status}\n'
        'discovery: "dcy-0007"\ncriteria:\n  create: []\n  update: []\n  retire: []\n'
        '---\n\n# Requirement\n'
    )
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "proofline@example.invalid")
    git(repo, "config", "user.name", "ProofLine Test")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Approve specification")
    approval = git(repo, "rev-parse", "HEAD").stdout.strip()
    return repo, approval


def run_script(repo: Path, approval: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(repo),
            "--line-id",
            "line-0007",
            "--branch",
            "line/line-0007-implementation",
            "--approval-commit",
            approval,
        ),
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )


def run_script_isolated(
    repo: Path, approval: str, executable_dir: Path
) -> subprocess.CompletedProcess[str]:
    environment = {"PATH": f"{executable_dir}:/usr/bin:/bin"}
    return subprocess.run(
        (
            sys.executable,
            "-I",
            str(SCRIPT),
            "--repo",
            str(repo),
            "--line-id",
            "line-0007",
            "--branch",
            "line/line-0007-implementation",
            "--approval-commit",
            approval,
        ),
        cwd=repo,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (
            ".proofline/lines/line-0007/line-0007.md: history.line.policy.missing: gap\n",
            0,
        ),
        ("not canonical\n", 2),
        (
            ".proofline/lines/line-0007/line-0007.md: history.line.policy.missing: gap\n"
            ".proofline/lines/line-0007/line-0007.md: other: extra\n",
            2,
        ),
    ],
)
def test_standalone_preflight_uses_path_executable_and_parses_diagnostics(
    tmp_path: Path, stderr: str, expected: int
) -> None:
    repo, approval = make_approved_repo(tmp_path, config=True)
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    executable = executable_dir / "proofline"
    executable.write_text(
        "#!/bin/sh\n"
        "cat >&2 <<'EOF'\n"
        f"{stderr}"
        "EOF\n"
        "exit 1\n"
    )
    executable.chmod(0o755)

    result = run_script_isolated(repo, approval, executable_dir)

    assert result.returncode == expected, result.stderr
    assert "proofline.validator" not in SCRIPT.read_text()
    if expected:
        assert not (repo / ".worktrees/line-0007").exists()


@pytest.mark.parametrize(
    "duplicate",
    [
        ('id: "line-0007"\nid: "wrong"'),
        ('execution_status: not_started\nexecution_status: in_progress'),
        ('implementation_history: first_parent\nimplementation_history: invalid'),
    ],
)
def test_standalone_preflight_rejects_duplicate_frontmatter_before_mutation(
    tmp_path: Path, duplicate: str
) -> None:
    repo, approval = make_approved_repo(tmp_path, config=True)
    line = repo / ".proofline/lines/line-0007/line-0007.md"
    line.write_text(
        f'---\nid: "line-0007"\nexecution_status: not_started\n{duplicate}\n---\n',
        encoding="utf-8",
    )
    git(repo, "add", ".proofline/lines/line-0007/line-0007.md")
    git(repo, "commit", "-qm", "duplicate frontmatter fixture")
    approval = git(repo, "rev-parse", "HEAD").stdout.strip()
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    marker = tmp_path / "executed"
    executable = executable_dir / "proofline"
    executable.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    executable.chmod(0o755)

    result = run_script_isolated(repo, approval, executable_dir)

    assert result.returncode == 2
    assert not marker.exists()
    assert not (repo / ".worktrees/line-0007").exists()


def test_standalone_preflight_rejects_duplicate_status_before_mutation(
    tmp_path: Path,
) -> None:
    repo, approval = make_approved_repo(tmp_path, config=True)
    req = repo / ".proofline/lines/line-0007/req-0007.md"
    req.write_text(
        req.read_text(encoding="utf-8").replace(
            "status: approved", "status: approved\nstatus: draft"
        ),
        encoding="utf-8",
    )
    git(repo, "add", str(req.relative_to(repo)))
    git(repo, "commit", "-qm", "duplicate status fixture")
    approval = git(repo, "rev-parse", "HEAD").stdout.strip()

    result = run_script_isolated(repo, approval, tmp_path / "missing-bin")

    assert result.returncode == 2
    assert "duplicate keys" in result.stderr
    assert not (repo / ".worktrees/line-0007").exists()


@pytest.mark.parametrize(
    "frontmatter",
    [
        'id: "line-0007"\nexecution_status: not_started\n  stray: value',
        'id: "line-0007"\nexecution_status: not_started\n  continuation',
        'id: "line-0007"\n\tstatus: bad',
        'id: "line-0007"\nexecution_status: not_started\n    status: bad',
    ],
)
def test_standalone_preflight_rejects_malformed_indentation_before_worktree(
    tmp_path: Path, frontmatter: str
) -> None:
    repo, approval = make_approved_repo(tmp_path)
    line = repo / ".proofline/lines/line-0007/line-0007.md"
    line.write_text(f"---\n{frontmatter}\n---\n", encoding="utf-8")
    git(repo, "add", str(line.relative_to(repo)))
    git(repo, "commit", "-qm", "malformed indentation fixture")
    approval = git(repo, "rev-parse", "HEAD").stdout.strip()

    result = run_script(repo, approval)

    assert result.returncode == 2
    assert not (repo / ".worktrees/line-0007").exists()


def test_standalone_preflight_accepts_nested_req_criteria_without_proofline_yaml(
    tmp_path: Path,
) -> None:
    repo, approval = make_approved_repo(tmp_path)
    (repo / "proofline.yaml").unlink(missing_ok=True)
    req = repo / ".proofline/lines/line-0007/req-0007.md"
    req.write_text(
        '---\nid: "req-0007"\nstatus: approved\ndiscovery: "dcy-0007"\n'
        'criteria:\n  create:\n    - "ac-0001"\n  update: []\n  retire: []\n---\n\n# Requirement\n',
        encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "nested criteria fixture")
    approval = git(repo, "rev-parse", "HEAD").stdout.strip()

    result = run_script(repo, approval)

    assert result.returncode == 0, result.stderr
    assert (repo / ".worktrees/line-0007").exists()


def test_standalone_preflight_rejects_nested_duplicate_key_before_worktree(
    tmp_path: Path,
) -> None:
    repo, approval = make_approved_repo(tmp_path)
    (repo / "proofline.yaml").unlink(missing_ok=True)
    req = repo / ".proofline/lines/line-0007/req-0007.md"
    req.write_text(
        '---\nid: "req-0007"\nstatus: approved\ndiscovery: "dcy-0007"\n'
        'criteria:\n  create: ["ac-0001"]\n  create: []\n  update: []\n  retire: []\n'
        '---\n\n# Requirement\n',
        encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "nested duplicate criteria fixture")
    approval = git(repo, "rev-parse", "HEAD").stdout.strip()

    result = run_script(repo, approval)

    assert result.returncode == 2
    assert not (repo / ".worktrees/line-0007").exists()


def test_standalone_preflight_accepts_inline_comments_and_quoted_scalars(
    tmp_path: Path,
) -> None:
    repo, approval = make_approved_repo(tmp_path)
    line = repo / ".proofline/lines/line-0007/line-0007.md"
    line.write_text(
        '---\nid: "line-0007" # canonical identity\n'
        'execution_status: not_started # lifecycle\n---\n',
        encoding="utf-8",
    )
    git(repo, "add", str(line.relative_to(repo)))
    git(repo, "commit", "-qm", "inline comment fixture")
    approval = git(repo, "rev-parse", "HEAD").stdout.strip()

    result = run_script(repo, approval)

    assert result.returncode == 0, result.stderr
    assert (repo / ".worktrees/line-0007").exists()


def test_standalone_preflight_ignores_policy_literal_in_artifact_bodies(
    tmp_path: Path,
) -> None:
    repo, _ = make_approved_repo(tmp_path)
    artifact_paths = (
        ".proofline/lines/line-0007/line-0007.md",
        ".proofline/lines/line-0007/dcy-0007.md",
        ".proofline/lines/line-0007/req-0007.md",
    )
    for relative_path in artifact_paths:
        artifact = repo / relative_path
        artifact.write_text(
            artifact.read_text(encoding="utf-8")
            + "\n본문 예시: implementation_history: first_parent\n",
            encoding="utf-8",
        )
    git(repo, "add", *artifact_paths)
    git(repo, "commit", "-qm", "policy literal body fixtures")
    approval = git(repo, "rev-parse", "HEAD").stdout.strip()

    result = run_script(repo, approval)

    assert result.returncode == 0, result.stderr
    assert (repo / ".worktrees/line-0007").exists()


def test_standalone_preflight_reports_missing_executable(tmp_path: Path) -> None:
    repo, approval = make_approved_repo(tmp_path, config=True)

    result = run_script_isolated(repo, approval, tmp_path / "missing-bin")

    assert result.returncode == 2
    assert "ProofLine validate executable is unavailable" in result.stderr


def test_standalone_preflight_rejects_repository_local_absolute_executable(
    tmp_path: Path,
) -> None:
    repo, approval = make_approved_repo(tmp_path, config=True)
    executable = repo / "proofline"
    marker = repo / "executed"
    executable.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    executable.chmod(0o755)
    git(repo, "add", "proofline")
    git(repo, "commit", "-qm", "add local validator fixture")
    approval = git(repo, "rev-parse", "HEAD").stdout.strip()

    result = run_script_isolated(repo, approval, repo)

    assert result.returncode == 2
    assert "must not be inside the repository" in result.stderr
    assert not marker.exists()
    assert not (repo / ".worktrees/line-0007").exists()


def test_standalone_preflight_rejects_combined_output_over_limit(
    tmp_path: Path,
) -> None:
    repo, approval = make_approved_repo(tmp_path, config=True)
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    executable = executable_dir / "proofline"
    amount = 128 * 1024 + 1
    executable.write_text(
        f"#!/bin/sh\nprintf '%*s' {amount} ''\nprintf '%*s' {amount} '' >&2\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)

    result = run_script_isolated(repo, approval, executable_dir)

    assert result.returncode == 2
    assert "excessive output" in result.stderr
    assert not (repo / ".worktrees/line-0007").exists()


def test_standalone_preflight_timeout_kills_and_reaps_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_worktree_script()
    repo, _ = make_approved_repo(tmp_path, config=True)
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    pid_file = tmp_path / "child.pid"
    executable = executable_dir / "proofline"
    executable.write_text(
        "#!/bin/sh\n"
        f"printf '%s' $$ > {pid_file}\n"
        "trap '' TERM\n"
        "while :; do :; done\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setattr(module, "VALIDATE_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setenv("PATH", str(executable_dir))

    with pytest.raises(module.WorkflowError, match="timed out"):
        module.validate_transitional_history(repo, ".proofline/lines/line-0007/line-0007.md")

    pid = int(pid_file.read_text(encoding="utf-8"))
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    else:
        pytest.fail("timed-out validator child was not reaped")


@pytest.mark.parametrize("path_value", ["", ".", "relative/bin"])
def test_standalone_preflight_rejects_unsafe_path_entries_without_mutation(
    tmp_path: Path, path_value: str
) -> None:
    repo, approval = make_approved_repo(tmp_path, config=True)
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    marker = repo / "executed"
    executable = executable_dir / "proofline"
    executable.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    executable.chmod(0o755)
    env_path = f"{path_value}{os.pathsep}{executable_dir}"

    result = subprocess.run(
        (sys.executable, "-I", str(SCRIPT), "--repo", str(repo), "--line-id",
         "line-0007", "--branch", "line/line-0007-implementation",
         "--approval-commit", approval),
        cwd=repo, env={"PATH": env_path}, text=True, capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert not marker.exists()
    assert not (repo / ".worktrees/line-0007").exists()


def test_start_implementation_skill_has_valid_frontmatter() -> None:
    text = SKILL.read_text()
    assert text.startswith("---\n")
    _, frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == "proofline-start-implementation"
    assert metadata["description"].startswith("Use when ")
    assert metadata["version"] == "1.3.0"
    assert "~/.proofline/skills/proofline-start-implementation/" in body
    for boundary in [
        "micro_spec_commit < P < I < Q",
        "B < I < Q",
        "second parent",
        "`P`를 `implementation_commit`으로 bind하지 않는다",
    ]:
        assert boundary in body
    assert "## When to Use" in body
    assert body.strip()


def test_fieldless_line_start_documents_separate_p_then_b_gate() -> None:
    text = SKILL.read_text()
    assert "fieldless non-terminal" in text
    assert "P < B < I < Q" in text
    assert "history.line.policy.missing" in text
    assert "sole history-policy error" in text


def test_start_implementation_skill_requires_fail_closed_preflight() -> None:
    text = SKILL.read_text()
    workflow = text.index("## Workflow")
    preflight = text.index("### 1. Preflight", workflow)
    create = text.index("### 2. Worktree 생성", workflow)
    verify = text.index("### 3. 생성 후 검증", workflow)
    handoff = text.index("### 4. Implementation handoff", workflow)
    assert preflight < create < verify < handoff

    for required in [
        "git status --porcelain",
        "exact REQ approval commit",
        "path 충돌",
        "branch 충돌",
        "worktree registration",
        "no-mutation",
    ]:
        assert required in text


def test_start_implementation_skill_preserves_workspace_and_authority() -> None:
    text = SKILL.read_text()
    for required in [
        ".worktrees/line-NNNN/",
        "git worktree add",
        "main checkout",
        "공용 `proofline`",
        "ProofLine 전용 `.venv`를 생성하지 않는다",
        "fast-forward",
        "DQC",
    ]:
        assert required in text
    assert "ProofLine CLI가 Git branch" in text
    assert "자동으로 강제 삭제하지 않는다" in text


def test_line_delivery_contract_defines_linked_worktree_boundary() -> None:
    text = CONTRACT.read_text()
    for required in [
        ".worktrees/line-NNNN/",
        "exact REQ approval commit",
        "linked worktree",
        "공용 `proofline`",
        "ProofLine 전용 `.venv`",
        "main checkout",
    ]:
        assert required in text


def test_repository_ignores_worktree_container() -> None:
    patterns = {
        line.strip()
        for line in GITIGNORE.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "/.worktrees/" in patterns


def test_worktree_script_accepts_direct_approval_without_draft_parent(tmp_path: Path) -> None:
    repo, approval = make_approved_repo(tmp_path)
    assert len(git(repo, "rev-list", "--parents", "-n", "1", approval).stdout.split()) == 1

    result = run_script(repo, approval)

    assert result.returncode == 0, result.stderr
    worktree = repo / ".worktrees/line-0007"
    assert git(worktree, "rev-parse", "HEAD").stdout.strip() == approval
    assert git(worktree, "branch", "--show-current").stdout.strip() == (
        "line/line-0007-implementation"
    )
    assert git(repo, "branch", "--show-current").stdout.strip() == "main"
    assert git(repo, "status", "--porcelain").stdout == ""
    assert not (worktree / ".venv").exists()


def test_worktree_script_dirty_main_fails_without_mutation(tmp_path: Path) -> None:
    repo, approval = make_approved_repo(tmp_path)
    (repo / "dirty.txt").write_text("uncommitted")
    refs_before = git(repo, "show-ref").stdout
    worktrees_before = git(repo, "worktree", "list", "--porcelain").stdout

    result = run_script(repo, approval)

    assert result.returncode != 0
    assert "main working tree is not clean" in result.stderr
    assert git(repo, "show-ref").stdout == refs_before
    assert git(repo, "worktree", "list", "--porcelain").stdout == worktrees_before
    assert not (repo / ".worktrees/line-0007").exists()


def test_worktree_script_rejects_unapproved_req_without_mutation(tmp_path: Path) -> None:
    repo, approval = make_approved_repo(tmp_path, req_status="draft")

    result = run_script(repo, approval)

    assert result.returncode != 0
    assert "REQ.status must be approved" in result.stderr
    assert not (repo / ".worktrees/line-0007").exists()
    branch_check = subprocess.run(
        (
            "git",
            "show-ref",
            "--verify",
            "refs/heads/line/line-0007-implementation",
        ),
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert branch_check.returncode != 0
