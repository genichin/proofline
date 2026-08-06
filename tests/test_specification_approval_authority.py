from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Any
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/proofline-approve-specification/scripts/audit_approval_authority.py"
REVIEW_SCHEMA = "proofline.independent-review/v1"
APPROVAL_SCHEMA = "proofline.user-approval/v1"


def _hosted_candidate_wheel() -> Path | None:
    if os.environ.get("PROOFLINE_HOSTED_CANDIDATE_MODE") != "1":
        return None
    provided = os.environ.get("PROOFLINE_HOSTED_CANDIDATE_WHEEL")
    expected = os.environ.get("PROOFLINE_HOSTED_CANDIDATE_WHEEL_SHA256")
    installed = os.environ.get("PROOFLINE_INSTALLED_EXECUTABLE")
    assert provided and expected and installed, "hosted candidate controls are incomplete"
    wheel = Path(provided)
    executable = Path(installed)
    assert wheel.is_absolute() and wheel.is_file(), "candidate wheel must be an absolute file"
    assert executable.is_absolute() and executable.is_file(), "installed executable must be an absolute file"
    assert len(expected) == 64 and expected == expected.lower() and all(
        character in "0123456789abcdef" for character in expected
    ), "candidate wheel SHA256 must be lowercase hexadecimal"
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == expected, "candidate wheel SHA256 mismatch"
    python = executable.parent / ("python.exe" if os.name == "nt" else "python")
    assert python.is_absolute() and python.is_file(), (
        "installed executable has no absolute candidate environment Python"
    )
    try:
        provenance = subprocess.run(
            (
                str(python),
                "-I",
                "-c",
                "from importlib.metadata import distribution; "
                "print(distribution('proofline').read_text('direct_url.json'))",
            ),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise AssertionError("installed candidate provenance probe failed") from exc
    assert provenance.returncode == 0, provenance.stderr
    try:
        direct_url = json.loads(provenance.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError("installed candidate provenance is malformed") from exc
    assert isinstance(direct_url, dict), "installed candidate provenance must be an object"
    archive_info = direct_url.get("archive_info")
    assert direct_url.get("url") == wheel.resolve().as_uri(), (
        "installed candidate wheel path mismatch"
    )
    assert isinstance(archive_info, dict), "installed candidate archive provenance is missing"
    assert archive_info.get("hash") == f"sha256={expected}", (
        "installed candidate wheel digest mismatch"
    )
    return wheel


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args), cwd=repo, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


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


def micro_spec(status: str, *, body: str = "Implement exact gate.") -> str:
    return (
        "---\nid: ms-0007-001\nparent_req: req-0007\ncriteria:\n"
        "  - ac-0011\nspec_status: " + status + "\n"
        "implementation_status: not_started\n---\n\n# Gate\n\n"
        "## Scope\n\nScope.\n\n## Implementation\n\n" + body + "\n\n"
        "## Verification\n\nRun tests.\n"
    )


def bootstrap_req(status: str, *, criteria: str | None = None) -> str:
    admission = criteria or (
        '  create:\n    - "ac-0011"\n'
        '  update:\n    - "ac-0012"\n'
        '  retire:\n    - "ac-0013"\n'
        '  satisfy:\n    - "ac-0014"\n'
    )
    return (
        '---\nid: "req-0007"\nstatus: ' + status + '\ndiscovery: "dcy-0007"\n'
        "criteria:\n" + admission + "---\n\n# Requirement\n\n## Objective\n\nObjective.\n\n"
        "## Scope\n\nScope.\n\n## Non-Goals\n\nNone.\n"
    )


def bootstrap_ac(ac_id: str, status: str) -> str:
    return (
        f'---\nid: "{ac_id}"\nstatus: ' + status + "\n---\n\n# Criterion\n\n"
        "## Criterion\n\nCriterion.\n\n## Verification\n\nVerification.\n"
    )


def make_repo(
    tmp_path: Path,
    *,
    mode: str,
    approval_change: str = "status",
    bootstrap_criteria: str | None = None,
    tracked_filter_victim: bool = False,
) -> tuple[Path, str, str]:
    repo = tmp_path / "project"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "proofline@example.invalid")
    git(repo, "config", "user.name", "ProofLine Test")
    ms = repo / ".proofline/lines/line-0007/micro-specs/ms-0007-001.md"
    ms.parent.mkdir(parents=True)
    if mode == "bootstrap":
        req = repo / ".proofline/lines/line-0007/req-0007.md"
        criteria_dir = repo / ".proofline/criteria"
        criteria_dir.mkdir(parents=True)
        req.write_text(bootstrap_req("draft", criteria=bootstrap_criteria), encoding="utf-8")
        for ac_id, status in (("ac-0011", "draft"), ("ac-0012", "draft"),
                              ("ac-0013", "active"), ("ac-0014", "active")):
            (criteria_dir / f"{ac_id}.md").write_text(bootstrap_ac(ac_id, status), encoding="utf-8")
    ms.write_text(micro_spec("draft"), encoding="utf-8")
    if tracked_filter_victim:
        (repo / ".gitattributes").write_text("victim filter=evil\n", encoding="utf-8")
        (repo / "victim").write_text("unchanged victim\n", encoding="utf-8")
    target = commit(repo, "exact draft target")

    if mode == "bootstrap":
        req.write_text(bootstrap_req("approved", criteria=bootstrap_criteria), encoding="utf-8")
        for ac_id, status in (("ac-0011", "active"), ("ac-0012", "active"),
                              ("ac-0013", "retired"), ("ac-0014", "active")):
            (repo / f".proofline/criteria/{ac_id}.md").write_text(
                bootstrap_ac(ac_id, status), encoding="utf-8"
            )
    body = "Changed during approval." if approval_change == "body" else "Implement exact gate."
    ms.write_text(micro_spec("approved", body=body), encoding="utf-8")
    if approval_change == "unrelated":
        (repo / "concurrent.txt").write_text("mutation\n", encoding="utf-8")
    approval = commit(repo, "status-only approval")
    return repo, target, approval


def make_symlink_artifact_repo(
    tmp_path: Path, *, mode: str = "120000"
) -> tuple[Path, str, str]:
    repo = tmp_path / "project"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "proofline@example.invalid")
    git(repo, "config", "user.name", "ProofLine Test")
    git(repo, "config", "core.symlinks", "false")
    path = ".proofline/lines/line-0007/micro-specs/ms-0007-001.md"
    artifact = repo / path
    artifact.parent.mkdir(parents=True)
    artifact.write_text(micro_spec("draft"), encoding="utf-8")
    if mode == "100755":
        artifact.chmod(0o755)
    stage_canonical_mode(repo, path, mode)
    git(repo, "commit", "-qm", "symlink-mode draft target")
    target = git(repo, "rev-parse", "HEAD")
    artifact.write_text(micro_spec("approved"), encoding="utf-8")
    stage_canonical_mode(repo, path, mode)
    git(repo, "commit", "-qm", "symlink-mode status approval")
    approval = git(repo, "rev-parse", "HEAD")
    assert git(repo, "status", "--porcelain=v1", "--untracked-files=all") == ""
    return repo, target, approval


def write_evidence(
    tmp_path: Path,
    repo: Path,
    target: str,
    *,
    reviewer: str = "reviewer-1",
    review_result: str = "PASS",
    mutation_performed: bool = False,
    user: str = "user-1",
    user_role: str = "user",
    decision: str = "approved",
    stale_target: bool = False,
    stale_digest: bool = False,
) -> tuple[Path, Path]:
    tree = git(repo, "rev-parse", f"{target}^{{tree}}")
    evidence_dir = tmp_path / "external-evidence"
    evidence_dir.mkdir(exist_ok=True)
    review = evidence_dir / "review.json"
    review_payload = {
        "schema": REVIEW_SCHEMA,
        "target_commit": "0" * 40 if stale_target else target,
        "target_tree": tree,
        "result": review_result,
        "reviewer_actor_id": reviewer,
        "mutation_performed": mutation_performed,
    }
    review.write_text(json.dumps(review_payload, sort_keys=True) + "\n", encoding="utf-8")
    digest = hashlib.sha256(review.read_bytes()).hexdigest()
    approval = evidence_dir / "approval.json"
    approval_payload = {
        "schema": APPROVAL_SCHEMA,
        "target_commit": target,
        "target_tree": tree,
        "decision": decision,
        "user_actor_id": user,
        "actor_role": user_role,
        "review_evidence_sha256": "f" * 64 if stale_digest else digest,
    }
    approval.write_text(json.dumps(approval_payload, sort_keys=True) + "\n", encoding="utf-8")
    return review, approval


def snapshot(repo: Path) -> tuple[str, str, str, dict[str, str]]:
    files = {
        p.relative_to(repo).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(repo.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(repo).parts
    }
    return (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "show-ref"),
        git(repo, "status", "--porcelain=v1", "--untracked-files=all"),
        files,
    )


def run_gate(
    script: Path,
    repo: Path,
    mode: str,
    target: str,
    approval: str,
    review: Path,
    user_approval: Path,
    *,
    author: str = "author-1",
    recorder: str = "recorder-1",
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            "-I",
            str(script),
            "--repo", str(repo),
            "--mode", mode,
            "--line-id", "line-0007",
            "--target-commit", target,
            "--target-tree", git(repo, "rev-parse", f"{target}^{{tree}}"),
            "--approval-commit", approval,
            "--approval-tree", git(repo, "rev-parse", f"{approval}^{{tree}}"),
            "--review-evidence", str(review),
            "--user-approval-evidence", str(user_approval),
            "--draft-author-actor-id", author,
            "--governance-recorder-actor-id", recorder,
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


def load_authority_module() -> Any:
    spec = importlib.util.spec_from_file_location("proofline_test_approval_authority", SCRIPT)
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


@pytest.mark.parametrize("mode", ["normal", "bootstrap"])
def test_exact_authority_gate_accepts_real_git_paths_without_mutation(tmp_path: Path, mode: str) -> None:
    repo, target, approval = make_repo(tmp_path, mode=mode)
    review, user_approval = write_evidence(tmp_path, repo, target)
    before = snapshot(repo)

    result = run_gate(SCRIPT, repo, mode, target, approval, review, user_approval)

    assert result.returncode == 0, result.stderr
    assert f"approval-authority: passed mode={mode} target={target} approval={approval}" in result.stdout
    assert "validates supplied evidence; does not cryptographically authenticate a human" in result.stdout
    assert snapshot(repo) == before


def test_authority_gate_rejects_symlink_mode_artifact_without_mutation(tmp_path: Path) -> None:
    repo, target, approval = make_symlink_artifact_repo(tmp_path)
    review, user_approval = write_evidence(tmp_path, repo, target)
    before = snapshot(repo)

    result = run_gate(SCRIPT, repo, "normal", target, approval, review, user_approval)

    assert result.returncode == 2
    assert result.stderr.strip() == (
        "approval-authority[TRANSITION_PATH]: canonical artifact must be a regular blob: "
        ".proofline/lines/line-0007/micro-specs/ms-0007-001.md"
    )
    assert snapshot(repo) == before


def test_authority_gate_accepts_executable_regular_artifact_without_mutation(tmp_path: Path) -> None:
    repo, target, approval = make_symlink_artifact_repo(tmp_path, mode="100755")
    review, user_approval = write_evidence(tmp_path, repo, target)
    before = snapshot(repo)

    result = run_gate(SCRIPT, repo, "normal", target, approval, review, user_approval)

    assert result.returncode == 0, result.stderr
    assert snapshot(repo) == before


def test_bootstrap_accepts_copied_real_req_0020_quoted_admissions_without_mutation(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "proofline@example.invalid")
    git(repo, "config", "user.name", "ProofLine Test")
    req = repo / ".proofline/lines/line-0007/req-0007.md"
    req.parent.mkdir(parents=True)
    canonical_req = (ROOT / ".proofline/lines/line-0020/req-0020.md").read_text(encoding="utf-8")
    assert canonical_req.count("status: approved") == 1
    req.write_text(canonical_req.replace("status: approved", "status: draft", 1), encoding="utf-8")
    criteria_dir = repo / ".proofline/criteria"
    criteria_dir.mkdir(parents=True)
    for ac_id in ("ac-0022", "ac-0003", "ac-0007", "ac-0021", "ac-0010"):
        canonical = (ROOT / f".proofline/criteria/{ac_id}.md").read_text(encoding="utf-8")
        before_text = (
            canonical.replace("status: active", "status: draft", 1)
            if ac_id in {"ac-0022", "ac-0003", "ac-0007", "ac-0021"}
            else canonical
        )
        (criteria_dir / f"{ac_id}.md").write_text(before_text, encoding="utf-8")
    ms = repo / ".proofline/lines/line-0007/micro-specs/ms-0007-001.md"
    ms.parent.mkdir(parents=True)
    ms.write_text(micro_spec("draft"), encoding="utf-8")
    target = commit(repo, "copied real req-0020 draft bytes")

    req.write_text(canonical_req, encoding="utf-8")
    for ac_id in ("ac-0022", "ac-0003", "ac-0007", "ac-0021"):
        (criteria_dir / f"{ac_id}.md").write_bytes(
            (ROOT / f".proofline/criteria/{ac_id}.md").read_bytes()
        )
    ms.write_text(micro_spec("approved"), encoding="utf-8")
    approval = commit(repo, "copied real req-0020 combined approval")
    review, user_approval = write_evidence(tmp_path, repo, target)
    before = snapshot(repo)

    result = run_gate(SCRIPT, repo, "bootstrap", target, approval, review, user_approval)

    assert result.returncode == 0, result.stderr
    assert snapshot(repo) == before


@pytest.mark.parametrize(
    "criteria",
    [
        '  create: []\n  create: []\n  update: []\n  retire: []\n  satisfy: []\n',
        '  create: [\n  update: []\n  retire: []\n  satisfy: []\n',
        '  - "ac-0011"\n',
        '  create: []\n  update: []\n  retire: []\n  satisfy: []\n  unknown: []\n',
        '  create: "ac-0011"\n  update: []\n  retire: []\n  satisfy: []\n',
        '  create:\n    - "AC-0011"\n  update: []\n  retire: []\n  satisfy: []\n',
    ],
    ids=("duplicate-key", "malformed-yaml", "criteria-not-mapping", "unknown-admission",
         "admission-not-list", "noncanonical-ac-id"),
)
def test_bootstrap_req_frontmatter_fails_closed_for_noncanonical_criteria_structure(
    tmp_path: Path, criteria: str
) -> None:
    repo, target, approval = make_repo(
        tmp_path, mode="bootstrap", bootstrap_criteria=criteria
    )
    review, user_approval = write_evidence(tmp_path, repo, target)
    before = snapshot(repo)

    result = run_gate(SCRIPT, repo, "bootstrap", target, approval, review, user_approval)

    assert result.returncode == 2
    assert "approval-authority[TRANSITION_CONTENT]" in result.stderr
    assert snapshot(repo) == before


@pytest.mark.parametrize(
    "criteria",
    [
        '  create:\n    - "ac-0011"\n    - "ac-0011"\n  update: []\n  retire: []\n  satisfy: []\n',
        '  create:\n    - "ac-0011"\n  update:\n    - "ac-0011"\n  retire: []\n  satisfy: []\n',
        '  create: []\n  update: []\n  retire: []\n  satisfy: []\n',
    ],
    ids=("intra-list-duplicate", "cross-list-duplicate", "all-empty"),
)
def test_bootstrap_rejects_duplicate_or_empty_admission_targets_without_mutation(
    tmp_path: Path, criteria: str
) -> None:
    repo, target, approval = make_repo(
        tmp_path, mode="bootstrap", bootstrap_criteria=criteria
    )
    review, user_approval = write_evidence(tmp_path, repo, target)
    before = snapshot(repo)

    result = run_gate(SCRIPT, repo, "bootstrap", target, approval, review, user_approval)

    assert result.returncode == 2
    assert "approval-authority[TRANSITION_CONTENT]" in result.stderr
    assert snapshot(repo) == before


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("self_approval", "ACTOR_SEPARATION"),
        ("missing_user", "EVIDENCE_MISSING"),
        ("reviewer_is_recorder", "ACTOR_SEPARATION"),
        ("review_mutation", "REVIEW_MUTATION"),
        ("stale_review", "TARGET_BINDING"),
        ("failed_review", "REVIEW_RESULT"),
        ("recorder_only", "ACTOR_SEPARATION"),
        ("denied_user", "USER_DECISION"),
        ("wrong_user_role", "USER_ROLE"),
        ("stale_digest", "REVIEW_DIGEST"),
        ("body_change", "TRANSITION_CONTENT"),
        ("concurrent_file", "TRANSITION_PATH"),
    ],
)
def test_authority_gate_fails_closed_with_stable_scenario_and_no_mutation(
    tmp_path: Path, case: str, expected: str
) -> None:
    change = "body" if case == "body_change" else "unrelated" if case == "concurrent_file" else "status"
    repo, target, approval = make_repo(tmp_path, mode="normal", approval_change=change)
    kwargs: dict[str, object] = {}
    author = "author-1"
    recorder = "recorder-1"
    if case == "self_approval":
        kwargs["user"] = author
    elif case == "reviewer_is_recorder":
        kwargs["reviewer"] = recorder
    elif case == "review_mutation":
        kwargs["mutation_performed"] = True
    elif case == "stale_review":
        kwargs["stale_target"] = True
    elif case == "failed_review":
        kwargs["review_result"] = "FAIL"
    elif case == "recorder_only":
        kwargs["user"] = recorder
    elif case == "denied_user":
        kwargs["decision"] = "denied"
    elif case == "wrong_user_role":
        kwargs["user_role"] = "recorder"
    elif case == "stale_digest":
        kwargs["stale_digest"] = True
    review, user_approval = write_evidence(tmp_path, repo, target, **kwargs)
    if case == "missing_user":
        user_approval.unlink()
    before = snapshot(repo)

    result = run_gate(SCRIPT, repo, "normal", target, approval, review, user_approval, author=author, recorder=recorder)

    assert result.returncode == 2
    assert f"approval-authority[{expected}]" in result.stderr
    assert snapshot(repo) == before


def test_authority_gate_rejects_dirty_or_stale_head_without_mutation(tmp_path: Path) -> None:
    repo, target, approval = make_repo(tmp_path, mode="normal")
    review, user_approval = write_evidence(tmp_path, repo, target)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    before = snapshot(repo)

    result = run_gate(SCRIPT, repo, "normal", target, approval, review, user_approval)

    assert result.returncode == 2
    assert "approval-authority[WORKTREE_STATE]" in result.stderr
    assert snapshot(repo) == before

    (repo / "dirty.txt").unlink()
    git(repo, "checkout", "-q", target)
    before = snapshot(repo)

    result = run_gate(SCRIPT, repo, "normal", target, approval, review, user_approval)

    assert result.returncode == 2
    assert "approval-authority[WORKTREE_STATE]" in result.stderr
    assert snapshot(repo) == before


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("unknown_key", "EVIDENCE_FORMAT"),
        ("missing_key", "EVIDENCE_FORMAT"),
        ("actor_type", "EVIDENCE_FORMAT"),
        ("boolean_type", "EVIDENCE_FORMAT"),
        ("commit_format", "GIT_OBJECT"),
        ("tree_format", "GIT_OBJECT"),
        ("digest_format", "EVIDENCE_FORMAT"),
    ],
)
def test_authority_gate_rejects_non_strict_envelope_without_mutation(
    tmp_path: Path, case: str, expected: str
) -> None:
    repo, target, approval = make_repo(tmp_path, mode="normal")
    review, user_approval = write_evidence(tmp_path, repo, target)
    review_payload = json.loads(review.read_text(encoding="utf-8"))
    user_payload = json.loads(user_approval.read_text(encoding="utf-8"))
    if case == "unknown_key":
        review_payload["unexpected"] = "value"
    elif case == "missing_key":
        user_payload.pop("decision")
    elif case == "actor_type":
        review_payload["reviewer_actor_id"] = 7
    elif case == "boolean_type":
        review_payload["mutation_performed"] = "false"
    elif case == "commit_format":
        review_payload["target_commit"] = "abc"
    elif case == "tree_format":
        user_payload["target_tree"] = "A" * 40
    elif case == "digest_format":
        user_payload["review_evidence_sha256"] = "not-a-digest"
    review.write_text(json.dumps(review_payload, sort_keys=True) + "\n", encoding="utf-8")
    if case not in {"missing_key", "tree_format", "digest_format"}:
        user_payload["review_evidence_sha256"] = hashlib.sha256(review.read_bytes()).hexdigest()
    user_approval.write_text(json.dumps(user_payload, sort_keys=True) + "\n", encoding="utf-8")
    before = snapshot(repo)

    result = run_gate(SCRIPT, repo, "normal", target, approval, review, user_approval)

    assert result.returncode == 2
    assert f"approval-authority[{expected}]" in result.stderr
    assert snapshot(repo) == before


def test_authority_gate_rejects_stale_tree_and_non_direct_child_without_mutation(
    tmp_path: Path,
) -> None:
    repo, target, approval = make_repo(tmp_path, mode="normal")
    review, user_approval = write_evidence(tmp_path, repo, target)
    review_payload = json.loads(review.read_text(encoding="utf-8"))
    review_payload["target_tree"] = git(repo, "rev-parse", f"{approval}^{{tree}}")
    review.write_text(json.dumps(review_payload, sort_keys=True) + "\n", encoding="utf-8")
    user_payload = json.loads(user_approval.read_text(encoding="utf-8"))
    user_payload["review_evidence_sha256"] = hashlib.sha256(review.read_bytes()).hexdigest()
    user_approval.write_text(json.dumps(user_payload, sort_keys=True) + "\n", encoding="utf-8")
    before = snapshot(repo)

    stale = run_gate(SCRIPT, repo, "normal", target, approval, review, user_approval)

    assert stale.returncode == 2
    assert "approval-authority[TARGET_BINDING]" in stale.stderr
    assert snapshot(repo) == before

    review, user_approval = write_evidence(tmp_path, repo, target)
    (repo / "extra.txt").write_text("extra\n", encoding="utf-8")
    extra = commit(repo, "not a direct child")
    before = snapshot(repo)

    indirect = run_gate(SCRIPT, repo, "normal", target, extra, review, user_approval)

    assert indirect.returncode == 2
    assert "approval-authority[TRANSITION_PARENT]" in indirect.stderr
    assert snapshot(repo) == before


def test_source_and_built_wheel_extracted_script_have_behavior_and_diagnostic_parity(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture"
    repo, target, approval = make_repo(fixture, mode="bootstrap")
    review, user_approval = write_evidence(fixture, repo, target)
    wheel = _hosted_candidate_wheel()
    if wheel is not None:
        pass
    else:
        dist = tmp_path / "dist"
        built = subprocess.run(
            ("uv", "build", "--refresh", "--wheel", "--out-dir", str(dist)),
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        assert built.returncode == 0, built.stderr
        wheel = next(dist.glob("proofline-*.whl"))
    member = "proofline_home/skills/proofline-approve-specification/scripts/audit_approval_authority.py"
    extracted = tmp_path / "packaged-audit.py"
    with zipfile.ZipFile(wheel) as archive:
        packaged = archive.read(member)
    assert packaged == SCRIPT.read_bytes()
    extracted.write_bytes(packaged)

    source_pass = run_gate(SCRIPT, repo, "bootstrap", target, approval, review, user_approval)
    packaged_pass = run_gate(extracted, repo, "bootstrap", target, approval, review, user_approval)
    assert source_pass.returncode == packaged_pass.returncode == 0
    assert source_pass.stdout == packaged_pass.stdout

    payload = review.read_text(encoding="utf-8")
    review.write_text(
        payload.replace('"mutation_performed": false', '"mutation_performed": true, "mutation_performed": false'),
        encoding="utf-8",
    )
    duplicate_digest = hashlib.sha256(review.read_bytes()).hexdigest()
    approval_payload = json.loads(user_approval.read_text(encoding="utf-8"))
    approval_payload["review_evidence_sha256"] = duplicate_digest
    user_approval.write_text(json.dumps(approval_payload, sort_keys=True) + "\n", encoding="utf-8")

    source_result = run_gate(SCRIPT, repo, "bootstrap", target, approval, review, user_approval)
    packaged_result = run_gate(extracted, repo, "bootstrap", target, approval, review, user_approval)

    assert source_result.returncode == packaged_result.returncode == 2
    assert source_result.stderr == packaged_result.stderr
    assert "approval-authority[EVIDENCE_FORMAT]" in source_result.stderr


def test_authority_gate_ignores_replace_object_that_makes_invalid_raw_approval_valid(
    tmp_path: Path,
) -> None:
    repo, target, approval = make_repo(tmp_path, mode="normal", approval_change="body")
    artifact = repo / ".proofline/lines/line-0007/micro-specs/ms-0007-001.md"
    artifact.write_text(micro_spec("approved"), encoding="utf-8")
    git(repo, "add", "-A")
    valid_tree = git(repo, "write-tree")
    replacement = git(repo, "commit-tree", valid_tree, "-p", target, "-m", "replacement")
    git(repo, "replace", approval, replacement)
    git(repo, "reset", "--hard", "-q", approval)
    review, user_approval = write_evidence(tmp_path, repo, target)
    before = snapshot(repo)

    result = run_gate(SCRIPT, repo, "normal", target, approval, review, user_approval)

    assert result.returncode == 2
    assert "approval-authority[" in result.stderr
    assert snapshot(repo) == before


def test_authority_gate_fails_promptly_when_git_combined_output_exceeds_limit(
    tmp_path: Path,
) -> None:
    repo, target, approval = make_repo(tmp_path, mode="normal")
    review, user_approval = write_evidence(tmp_path, repo, target)
    started = time.monotonic()

    result = run_gate(
        SCRIPT,
        repo,
        "normal",
        target,
        approval,
        review,
        user_approval,
        env=install_flooding_git(tmp_path),
    )

    assert time.monotonic() - started < 4
    assert result.returncode == 2
    assert result.stderr.strip() == (
        "approval-authority[REPOSITORY]: git command output exceeds limit"
    )


def test_authority_gate_ignores_inherited_git_routing_and_external_config(
    tmp_path: Path,
) -> None:
    repo, target, approval = make_repo(tmp_path, mode="normal")
    review, user_approval = write_evidence(tmp_path, repo, target)
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

    result = run_gate(
        SCRIPT, repo, "normal", target, approval, review, user_approval, env=env
    )

    assert result.returncode == 0, result.stderr
    assert f"target={target} approval={approval}" in result.stdout
    assert not marker.exists()


def configure_marker_clean_filter(
    tmp_path: Path, repo: Path, *, included: bool
) -> Path:
    marker = tmp_path / "filter-command-ran"
    filter_program = tmp_path / "marker-filter.py"
    filter_program.write_text(
        "import pathlib, sys\n"
        "marker = pathlib.Path(sys.argv[1])\n"
        "payload = sys.stdin.buffer.read()\n"
        "marker.write_text('executed', encoding='utf-8')\n"
        "sys.stdout.buffer.write(payload)\n",
        encoding="utf-8",
    )
    command_parts = (sys.executable, str(filter_program), str(marker))
    command = (
        subprocess.list2cmdline(command_parts)
        if os.name == "nt"
        else shlex.join(command_parts)
    )
    if included:
        included_config = tmp_path / "included-filter.config"
        git(repo, "config", "--file", str(included_config), "filter.evil.clean", command)
        git(repo, "config", "--file", str(included_config), "filter.evil.required", "true")
        git(repo, "config", "--local", "include.path", str(included_config))
    else:
        git(repo, "config", "--local", "filter.evil.clean", command)
        git(repo, "config", "--local", "filter.evil.required", "true")
    return marker


def make_stat_dirty(path: Path) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))


def test_status_filter_overrides_cover_every_configured_driver_in_sorted_order(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "--local", "filter.zeta.process", "dangerous-process")
    git(repo, "config", "--local", "filter.alpha.smudge", "dangerous-smudge")
    git(repo, "config", "--local", "filter.middle.required", "true")
    module = load_authority_module()

    assert module.status_filter_overrides(repo) == (
        "-c", "filter.alpha.clean=",
        "-c", "filter.alpha.smudge=",
        "-c", "filter.alpha.process=",
        "-c", "filter.alpha.required=false",
        "-c", "filter.middle.clean=",
        "-c", "filter.middle.smudge=",
        "-c", "filter.middle.process=",
        "-c", "filter.middle.required=false",
        "-c", "filter.zeta.clean=",
        "-c", "filter.zeta.smudge=",
        "-c", "filter.zeta.process=",
        "-c", "filter.zeta.required=false",
    )


@pytest.mark.parametrize("included", [False, True], ids=("local", "local-include"))
def test_authority_status_neutralizes_repository_clean_filters(
    tmp_path: Path, included: bool
) -> None:
    repo, target, approval = make_repo(
        tmp_path, mode="normal", tracked_filter_victim=True
    )
    review, user_approval = write_evidence(tmp_path, repo, target)
    marker = configure_marker_clean_filter(tmp_path, repo, included=included)
    victim = repo / "victim"

    make_stat_dirty(victim)
    assert git(repo, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert marker.read_text(encoding="utf-8") == "executed"
    marker.unlink()
    make_stat_dirty(victim)
    head_before = git(repo, "rev-parse", "HEAD")
    refs_before = git(repo, "show-ref")
    victim_before = victim.read_bytes()

    result = run_gate(SCRIPT, repo, "normal", target, approval, review, user_approval)

    assert result.returncode == 0, result.stderr
    assert not marker.exists()
    assert git(repo, "rev-parse", "HEAD") == head_before
    assert git(repo, "show-ref") == refs_before
    assert victim.read_bytes() == victim_before
    assert git(
        repo,
        "-c", "filter.evil.clean=",
        "-c", "filter.evil.smudge=",
        "-c", "filter.evil.process=",
        "-c", "filter.evil.required=false",
        "status", "--porcelain=v1", "--untracked-files=all",
    ) == ""


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group regression")
def test_run_git_times_out_and_kills_descendant_holding_pipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_authority_module()
    pid_file, terminated = install_descendant_git(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "GIT_READ_TIMEOUT_SECONDS", 0.2)
    started = time.monotonic()

    with pytest.raises(RuntimeError, match="git command timed out"):
        module.run_git(tmp_path, "rev-parse", "HEAD")

    assert time.monotonic() - started < 2
    assert pid_file.exists()
    assert terminated.read_text(encoding="utf-8") == "terminated"
