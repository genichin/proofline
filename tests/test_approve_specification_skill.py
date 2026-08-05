from pathlib import Path
import hashlib
import importlib.util
import os
import subprocess
import sys
import time
from typing import Any

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/proofline-approve-specification/SKILL.md"
SCRIPT = ROOT / "skills/proofline-approve-specification/scripts/audit_transition.py"
CONTRACT = ROOT / "docs/contracts/requirements-and-criteria.md"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args), cwd=repo, text=True, capture_output=True, check=True
    )


def stage_canonical_mode(repo: Path, path: str, mode: str) -> None:
    payload = (repo / path).read_bytes()
    hashed = subprocess.run(
        ("git", "hash-object", "-w", "--stdin"),
        cwd=repo,
        input=payload,
        capture_output=True,
        check=True,
    )
    oid = hashed.stdout.decode("ascii").strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}")


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


def run_audit(
    repo: Path, commit: str, *, script: Path = SCRIPT, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            str(script),
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
        env=env,
        timeout=6,
    )


def install_flooding_git(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    fake_git = bin_dir / "git"
    fake_git.write_text(
        "#!/usr/bin/env python3\n"
        "import os, threading\n"
        "def flood(fd):\n"
        "    chunk = b'x' * 65536\n"
        "    while True:\n"
        "        os.write(fd, chunk)\n"
        "threads = [threading.Thread(target=flood, args=(fd,)) for fd in (1, 2)]\n"
        "[thread.start() for thread in threads]\n"
        "[thread.join() for thread in threads]\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    return env


def load_audit_module() -> Any:
    spec = importlib.util.spec_from_file_location("proofline_test_audit_transition", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install_descendant_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    bin_dir = tmp_path / "descendant-bin"
    bin_dir.mkdir()
    pid_file = tmp_path / "descendant.pid"
    terminated = tmp_path / "descendant.terminated"
    child_code = (
        "import os, signal, sys, time\n"
        "pid_file, terminated = sys.argv[1:]\n"
        "open(pid_file, 'w', encoding='utf-8').write(str(os.getpid()))\n"
        "def stop(signum, frame):\n"
        "    open(terminated, 'w', encoding='utf-8').write('terminated')\n"
        "    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "time.sleep(60)\n"
    )
    fake_git = bin_dir / "git"
    fake_git.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, subprocess, sys, time\n"
        f"child_code = {child_code!r}\n"
        "pid_file = pathlib.Path(os.environ['PROOFLINE_DESCENDANT_PID'])\n"
        "subprocess.Popen([sys.executable, '-c', child_code, str(pid_file), "
        "os.environ['PROOFLINE_DESCENDANT_TERMINATED']])\n"
        "deadline = time.monotonic() + 2\n"
        "while not pid_file.exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PROOFLINE_DESCENDANT_PID", str(pid_file))
    monkeypatch.setenv("PROOFLINE_DESCENDANT_TERMINATED", str(terminated))
    return pid_file, terminated


def make_typed_transition_repo(
    tmp_path: Path, *, path: str, mode: str
) -> tuple[Path, str]:
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "proofline@example.invalid")
    git(repo, "config", "user.name", "ProofLine Test")
    git(repo, "config", "core.symlinks", "false")
    write_spec(repo, req_status="draft", ac_status="draft")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Draft specification")
    write_spec(repo, req_status="approved", ac_status="active")
    git(repo, "add", ".")
    stage_canonical_mode(repo, path, mode)
    git(repo, "commit", "-qm", f"Approval with mode {mode}")
    return repo, git(repo, "rev-parse", "HEAD").stdout.strip()


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


@pytest.mark.parametrize(
    "path",
    [
        ".proofline/lines/line-0007/req-0007.md",
        ".proofline/criteria/ac-0011.md",
    ],
)
def test_audit_rejects_symlink_mode_canonical_transition_artifact_without_mutation(
    tmp_path: Path, path: str
) -> None:
    repo, approval = make_typed_transition_repo(tmp_path, path=path, mode="120000")
    before = snapshot(repo)

    result = run_audit(repo, approval)

    assert result.returncode == 2
    assert result.stderr.strip() == f"error: canonical artifact must be a regular blob: {path}"
    assert result.stdout == ""
    assert snapshot(repo) == before


def test_audit_accepts_executable_regular_blob_transition_artifact(tmp_path: Path) -> None:
    path = ".proofline/criteria/ac-0011.md"
    repo, approval = make_typed_transition_repo(tmp_path, path=path, mode="100755")
    before = snapshot(repo)

    result = run_audit(repo, approval)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "transition: recorded"
    assert snapshot(repo) == before


def test_audit_ignores_replace_object_that_makes_invalid_raw_approval_valid(
    tmp_path: Path,
) -> None:
    repo, invalid = make_repo(tmp_path, mode="unapproved")
    write_spec(repo, req_status="approved", ac_status="active")
    git(repo, "add", ".")
    valid_tree = git(repo, "write-tree").stdout.strip()
    replacement = git(repo, "commit-tree", valid_tree, "-m", "replacement").stdout.strip()
    git(repo, "replace", invalid, replacement)
    git(repo, "reset", "--hard", "-q", invalid)
    before = snapshot(repo)

    result = run_audit(repo, invalid)

    assert result.returncode == 2
    assert "REQ.status must be approved" in result.stderr
    assert snapshot(repo) == before


def test_audit_fails_promptly_when_git_combined_output_exceeds_limit(tmp_path: Path) -> None:
    repo, approval = make_repo(tmp_path, mode="recorded")
    started = time.monotonic()

    result = run_audit(repo, approval, env=install_flooding_git(tmp_path))

    assert time.monotonic() - started < 4
    assert result.returncode == 2
    assert result.stderr.strip() == "error: git command output exceeds limit"


def test_audit_ignores_inherited_git_routing_and_external_config(tmp_path: Path) -> None:
    repo, approval = make_repo(tmp_path, mode="recorded")
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    git(attacker, "init", "-q", "-b", "main")
    marker = tmp_path / "external-command-ran"
    hook = tmp_path / "attacker-hook"
    hook.write_text(f"#!/bin/sh\nprintf owned > {marker}\n", encoding="utf-8")
    hook.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "GIT_DIR": str(attacker / ".git"),
            "GIT_WORK_TREE": str(attacker),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": str(hook),
        }
    )

    result = run_audit(repo, approval, env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "transition: recorded"
    assert not marker.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group regression")
def test_run_git_times_out_and_kills_descendant_holding_pipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_audit_module()
    pid_file, terminated = install_descendant_git(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "GIT_READ_TIMEOUT_SECONDS", 0.2)
    started = time.monotonic()

    with pytest.raises(module.AuditError, match="git command timed out"):
        module.run_git(tmp_path, "rev-parse", "HEAD")

    assert time.monotonic() - started < 2
    assert pid_file.exists()
    assert terminated.read_text(encoding="utf-8") == "terminated"
