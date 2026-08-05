from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pytest

from proofline.validator import ValidationError, validate_project
import proofline.implementation_history as implementation_history

ROOT = Path(__file__).resolve().parents[1]
LINE = ".proofline/lines/line-0001/line-0001.md"
MS = ".proofline/lines/line-0001/micro-specs/ms-0001-001.md"
IQC = ".proofline/lines/line-0001/micro-specs/iqc-0001-001.md"


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args), cwd=repo, text=True, capture_output=True, check=check
    )


def unlink_git_object(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IWRITE)
    path.unlink()


@dataclass
class HistoryRepo:
    path: Path
    commits: dict[str, str] = field(default_factory=dict)

    @classmethod
    def create(cls, tmp_path: Path, *, specs: int = 1) -> "HistoryRepo":
        path = tmp_path / "project"
        (path / ".proofline/lines/line-0001/micro-specs").mkdir(parents=True)
        (path / ".proofline/criteria").mkdir(parents=True)
        (path / ".proofline/lines/.gitkeep").write_bytes(b"")
        (path / ".proofline/criteria/.gitkeep").write_bytes(b"")
        (path / "proofline.yaml").write_text(
            "schema_version: 1\nartifact_root: .proofline\n", encoding="utf-8"
        )
        git(path, "init", "-q", "-b", "main")
        git(path, "config", "user.email", "proofline@example.invalid")
        git(path, "config", "user.name", "ProofLine Test")
        git(path, "config", "core.autocrlf", "false")
        git(path, "config", "gc.auto", "0")
        git(path, "config", "maintenance.auto", "false")
        repo = cls(path)
        repo.write_line("not_started", policy=None)
        repo.write_discovery()
        repo.write_requirement(specs)
        for number in range(1, specs + 1):
            repo.write_ac(number)
            repo.write_ms(number, "not_started")
        repo.commit("approval", "approve specification")
        return repo
    def commit(self, name: str, message: str) -> str:
        git(self.path, "add", "-A")
        git(self.path, "commit", "-qm", message)
        commit = git(self.path, "rev-parse", "HEAD").stdout.strip()
        self.commits[name] = commit
        return commit

    def write_line(self, status: str, *, policy: str | None) -> None:
        policy_line = f"implementation_history: {policy}\n" if policy is not None else ""
        (self.path / LINE).write_text(
            f'---\nid: "line-0001"\nexecution_status: {status}\n{policy_line}---\n',
            encoding="utf-8",
        )

    def write_discovery(self) -> None:
        path = self.path / ".proofline/lines/line-0001/dcy-0001.md"
        path.write_text(
            '---\nid: "dcy-0001"\nstatus: confirmed\n---\n\n'
            "# Discovery\n\n## Problem\n\n문제이다.\n\n## Evidence\n\n근거이다.\n\n"
            "## Scope\n\n범위이다.\n\n## Out of Scope\n\n제외 범위이다.\n",
            encoding="utf-8",
        )

    def write_requirement(self, specs: int) -> None:
        criteria = "\n".join(f'    - "ac-{number:04d}"' for number in range(1, specs + 1))
        path = self.path / ".proofline/lines/line-0001/req-0001.md"
        path.write_text(
            '---\nid: "req-0001"\nstatus: approved\ndiscovery: "dcy-0001"\n'
            f"criteria:\n  create:\n{criteria}\n  update: []\n  retire: []\n  satisfy: []\n"
            "---\n\n# Requirement\n\n## Objective\n\n목표이다.\n\n## Scope\n\n범위이다."
            "\n\n## Non-Goals\n\n비목표이다.\n",
            encoding="utf-8",
        )

    def write_ac(self, number: int) -> None:
        path = self.path / f".proofline/criteria/ac-{number:04d}.md"
        path.write_text(
            f'---\nid: "ac-{number:04d}"\nstatus: active\n---\n\n'
            f"# AC {number}\n\n## Criterion\n\n조건이다.\n\n## Verification\n\n검증한다.\n",
            encoding="utf-8",
        )

    def write_ms(
        self,
        number: int,
        status: str,
        *,
        malformed: bool = False,
        spec_status: str = "approved",
    ) -> None:
        path = self.path / f".proofline/lines/line-0001/micro-specs/ms-0001-{number:03d}.md"
        if malformed:
            path.write_text("---\nid: [\n---\n", encoding="utf-8")
            return
        path.write_text(
            f'---\nid: "ms-0001-{number:03d}"\nparent_req: "req-0001"\ncriteria:\n'
            f'  - "ac-{number:04d}"\nspec_status: {spec_status}\nimplementation_status: {status}\n---\n\n'
            f"# Micro-SPEC {number}\n\n## Scope\n\n범위이다.\n\n## Implementation\n\n구현한다."
            "\n\n## Verification\n\n검증한다.\n",
            encoding="utf-8",
        )

    def write_iqc(
        self,
        number: int,
        implementation: str,
        *,
        micro_spec_commit: str | None = None,
    ) -> None:
        path = self.path / f".proofline/lines/line-0001/micro-specs/iqc-0001-{number:03d}.md"
        path.write_text(
            f'---\nid: "iqc-0001-{number:03d}"\nmicro_spec: "ms-0001-{number:03d}"\n'
            f'micro_spec_commit: "{micro_spec_commit or self.commits["approval"]}"\n'
            f'implementation_commit: "{implementation}"\nresult: passed\n---\n\n'
            f"# IQC {number}\n\n## Target\n\n대상이다.\n\n## Checks\n\n통과했다."
            "\n\n## Criteria Results\n\n통과했다.\n\n## Result\n\n통과했다.\n",
            encoding="utf-8",
        )

    def product_commit(self, name: str = "implementation") -> str:
        product = self.path / "product.py"
        product.write_text(
            product.read_text(encoding="utf-8") + f"VALUE_{name.upper()} = True\n"
            if product.exists()
            else f"VALUE_{name.upper()} = True\n",
            encoding="utf-8",
        )
        return self.commit(name, name)

    def adopt(self, name: str = "baseline") -> str:
        current = (self.path / LINE).read_text(encoding="utf-8")
        status_line = next(
            line for line in current.splitlines() if line.startswith("execution_status:")
        )
        (self.path / LINE).write_text(
            current.replace(
                f"{status_line}\n",
                f"{status_line}\nimplementation_history: first_parent\n",
                1,
            )
            if "implementation_history:" not in current
            else current,
            encoding="utf-8",
        )
        return self.commit(name, "adopt history policy")

    def start(self, name: str = "start", *, numbers: tuple[int, ...] = (1,)) -> str:
        self.write_line("in_progress", policy=self.current_policy())
        for number in numbers:
            self.write_ms(number, "in_progress")
        return self.commit(name, name)

    def finish(
        self,
        implementation: str,
        name: str = "quality",
        *,
        numbers: tuple[int, ...] = (1,),
        micro_spec_commit: str | None = None,
    ) -> str:
        for number in numbers:
            self.write_ms(number, "implemented")
            self.write_iqc(number, implementation, micro_spec_commit=micro_spec_commit)
        return self.commit(name, name)

    def current_policy(self) -> str | None:
        text = (self.path / LINE).read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("implementation_history:"):
                return line.split(":", 1)[1].strip()
        return None


def test_history_repo_disables_background_git_maintenance(tmp_path: Path) -> None:
    repo = HistoryRepo.create(tmp_path)
    assert git(repo.path, "config", "--get", "gc.auto").stdout.strip() == "0"
    assert git(repo.path, "config", "--get", "maintenance.auto").stdout.strip() == "false"


def build_valid_cycle(tmp_path: Path, *, order: str = "baseline-first") -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    if order == "baseline-first":
        repo.adopt()
        repo.start()
    elif order == "start-first":
        repo.start()
        repo.adopt()
    else:
        raise AssertionError(order)
    implementation = repo.product_commit()
    repo.finish(implementation)
    return repo


def history_codes(repo: HistoryRepo | Path) -> set[tuple[str, str]]:
    root = repo.path if isinstance(repo, HistoryRepo) else repo
    return {(error.path, error.code) for error in validate_project(root)}


def assert_history_error(repo: HistoryRepo, path: str, code: str) -> None:
    assert (path, code) in history_codes(repo)


@pytest.mark.parametrize("order", ["baseline-first", "start-first"])
def test_valid_first_cycle_accepts_both_baseline_start_orders(
    tmp_path: Path, order: str
) -> None:
    repo = build_valid_cycle(tmp_path, order=order)

    assert validate_project(repo.path) == []


def test_valid_rework_requires_and_accepts_fresh_start(tmp_path: Path) -> None:
    repo = build_valid_cycle(tmp_path)
    repo.start("rework-start")
    implementation = repo.product_commit("rework-implementation")
    repo.finish(implementation, "rework-quality")

    assert validate_project(repo.path) == []


def test_multiple_meaningful_first_parent_implementations_bind_final_commit(
    tmp_path: Path,
) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    first = repo.product_commit("implementation-one")
    second = repo.product_commit("implementation-two")
    repo.finish(second)

    assert first != second
    assert validate_project(repo.path) == []


def test_multiple_meaningful_first_parent_implementations_may_bind_covered_first(
    tmp_path: Path,
) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    first = repo.product_commit("implementation-one")
    repo.product_commit("implementation-two")
    repo.finish(first)

    assert validate_project(repo.path) == []


def test_in_progress_transition_commit_must_be_governance_only(
    tmp_path: Path,
) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.write_line("in_progress", policy="first_parent")
    repo.write_ms(1, "in_progress")
    (repo.path / "product.py").write_text("product = True\n", encoding="utf-8")
    repo.commit("product-and-start", "product and start")
    implementation = repo.product_commit("implementation")
    repo.finish(implementation)

    assert_history_error(repo, MS, "history.ms.order")


@pytest.mark.parametrize("mutation", ["body", "status", "missing", "malformed"])
def test_policy_bearing_current_line_must_match_candidate_head(
    tmp_path: Path, mutation: str
) -> None:
    repo = build_valid_cycle(tmp_path)
    line = repo.path / LINE
    if mutation == "body":
        line.write_text(line.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    elif mutation == "status":
        line.write_text(
            line.read_text(encoding="utf-8").replace(
                "execution_status: in_progress", "execution_status: verifying"
            ),
            encoding="utf-8",
        )
    elif mutation == "missing":
        line.unlink()
    else:
        line.write_bytes(b"---\nid: [\n---\n")

    assert (LINE, "history.line.current.unpersisted") in {
        (error.path, error.code) for error in validate_project(repo.path)
    }


@pytest.mark.parametrize("mode", ["deleted", "deleted_then_restored"])
def test_policy_line_history_path_union_rejects_absence_continuity(
    tmp_path: Path, mode: str
) -> None:
    repo = build_valid_cycle(tmp_path)
    line = repo.path / LINE
    original = line.read_bytes()
    line.unlink()
    repo.commit("delete-policy-line", "delete policy-bearing Line")
    if mode == "deleted_then_restored":
        line.write_bytes(original)
        repo.commit("restore-policy-line", "restore policy-bearing Line")

    errors = validate_project(repo.path)

    assert (LINE, "history.line.policy.changed") in {
        (error.path, error.code) for error in errors
    }


@pytest.mark.parametrize(
    "payload",
    [
        b'---\nid: "line-0001"\nid: "wrong"\nexecution_status: not_started\n---\n',
        b'---\nid: "line-0001"\nimplementation_history: first_parent\nimplementation_history: invalid\n---\n',
    ],
)
def test_history_frontmatter_rejects_duplicate_top_level_keys(payload: bytes) -> None:
    with pytest.raises(implementation_history.HistoryUnavailable):
        implementation_history._frontmatter(payload)


def test_spec_revision_bytes_rejects_duplicate_status_key() -> None:
    payload = (
        b'---\nid: "ms-0001-001"\nspec_status: approved\n'
        b'implementation_status: in_progress\nimplementation_status: implemented\n---\n'
    )

    with pytest.raises(implementation_history.HistoryUnavailable):
        implementation_history._spec_revision_bytes(payload)


def test_persisted_fresh_rework_in_progress_is_valid(tmp_path: Path) -> None:
    repo = build_valid_cycle(tmp_path)
    repo.start("rework-start")

    assert validate_project(repo.path) == []


@pytest.mark.parametrize("status", ["in_progress", "not_started"])
def test_dirty_lifecycle_reset_cannot_reuse_previous_implemented_history(
    tmp_path: Path, status: str
) -> None:
    repo = build_valid_cycle(tmp_path)
    repo.write_ms(1, status)

    errors = validate_project(repo.path)

    assert [(error.path, error.code) for error in errors] == [
        (MS, "history.ms.current.unpersisted")
    ]


def test_dirty_micro_spec_edit_with_same_lifecycle_status_fails_closed(
    tmp_path: Path,
) -> None:
    repo = build_valid_cycle(tmp_path)
    ms = repo.path / MS
    ms.write_text(
        ms.read_text(encoding="utf-8").replace("범위이다.", "변경된 범위이다."),
        encoding="utf-8",
    )

    errors = validate_project(repo.path)

    assert [(error.path, error.code) for error in errors] == [
        (MS, "history.ms.current.unpersisted")
    ]


@pytest.mark.parametrize("mode", ["missing", "malformed"])
def test_current_micro_spec_missing_or_malformed_fails_closed(
    tmp_path: Path, mode: str
) -> None:
    repo = build_valid_cycle(tmp_path)
    ms = repo.path / MS
    if mode == "missing":
        ms.unlink()
    else:
        ms.write_bytes(b"---\nid: [\n---\n")

    errors = validate_project(repo.path)

    history_errors = [error for error in errors if error.code.startswith("history.")]
    assert [(error.path, error.code) for error in history_errors] == [
        (MS, "history.ms.current.unpersisted")
    ]


@pytest.mark.parametrize("mode", ["rollback", "edit", "missing", "malformed"])
def test_current_iqc_must_equal_exact_head_bytes(tmp_path: Path, mode: str) -> None:
    repo = build_valid_cycle(tmp_path)
    iqc = repo.path / IQC
    old_iqc = iqc.read_bytes()
    if mode == "rollback":
        repo.start("rework-start")
        implementation = repo.product_commit("rework-implementation")
        repo.write_ms(1, "implemented")
        repo.finish(implementation, "rework-quality")
        iqc.write_bytes(old_iqc)
    elif mode == "edit":
        iqc.write_bytes(
            old_iqc.replace("통과했다.".encode(), "변경했다.".encode())
        )
    elif mode == "missing":
        iqc.unlink()
    else:
        iqc.write_bytes(b"---\nid: [\n---\n")

    errors = validate_project(repo.path)

    assert (IQC, "history.iqc.current.unpersisted") in {
        (error.path, error.code) for error in errors
    }


def test_rework_rejects_unchanged_iqc_from_previous_cycle(tmp_path: Path) -> None:
    repo = build_valid_cycle(tmp_path)
    old_iqc = (repo.path / IQC).read_bytes()
    repo.start("rework-start")
    implementation = repo.product_commit("rework-implementation")
    (repo.path / IQC).write_bytes(old_iqc)
    repo.write_ms(1, "implemented")
    repo.commit("rework-quality-stale-iqc", "reuse stale IQC")

    assert_history_error(repo, MS, "history.ms.order")


def test_current_active_micro_spec_must_still_be_approved(tmp_path: Path) -> None:
    repo = build_valid_cycle(tmp_path)
    repo.write_ms(1, "implemented", spec_status="draft")
    repo.commit("draft-current", "withdraw approval")

    assert_history_error(repo, MS, "history.ms.order")


def test_approved_bytes_change_without_status_transition_rejects_stale_binding(
    tmp_path: Path,
) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.write_ms(1, "in_progress")
    repo.commit("start", "start")
    ms = repo.path / MS
    ms.write_text(ms.read_text(encoding="utf-8").replace("범위이다.", "변경된 승인 범위이다."), encoding="utf-8")
    repo.commit("approved-bytes-change", "edit approved spec")
    implementation = repo.product_commit()
    repo.finish(implementation, micro_spec_commit=repo.commits["start"])

    assert_history_error(repo, MS, "history.ms.order")


def test_body_implementation_status_line_is_not_lifecycle_normalization(
    tmp_path: Path,
) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    ms = repo.path / MS
    ms.write_bytes(ms.read_bytes() + b"\nimplementation_status: body-v1\n")
    repo.commit("start-with-body-marker", "body marker")
    ms.write_bytes(ms.read_bytes().replace(b"body-v1", b"body-v2"))
    repo.commit("body-change", "change body marker")
    implementation = repo.product_commit()
    repo.finish(implementation, micro_spec_commit=repo.commits["start-with-body-marker"])

    assert_history_error(repo, MS, "history.ms.order")


@pytest.mark.parametrize("terminal_status", ["delivered", "cancelled"])
def test_pre_adoption_fieldless_terminal_line_is_legacy(
    tmp_path: Path, terminal_status: str
) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.write_line(terminal_status, policy=None)
    repo.commit("terminal", f"legacy {terminal_status}")

    assert validate_project(repo.path) == []


def add_policy_only_line(repo: HistoryRepo) -> None:
    line_dir = repo.path / ".proofline/lines/line-0002"
    line_dir.mkdir()
    (line_dir / "line-0002.md").write_text(
        '---\nid: "line-0002"\nexecution_status: not_started\n'
        "implementation_history: first_parent\n---\n",
        encoding="utf-8",
    )
    (line_dir / "dcy-0002.md").write_text(
        '---\nid: "dcy-0002"\nstatus: draft\n---\n\n# Discovery\n\n'
        "## Problem\n\n{{TODO}}\n\n## Evidence\n\n{{TODO}}\n\n## Scope\n\n{{TODO}}\n\n"
        "## Out of Scope\n\n{{TODO}}\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("terminal_status", ["delivered", "cancelled"])
def test_unpersisted_fieldless_terminal_without_activation_is_unprovable(
    tmp_path: Path, terminal_status: str
) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.write_line(terminal_status, policy=None)

    assert_history_error(repo, LINE, "history.line.legacy.invalid")


@pytest.mark.parametrize("terminal_status", ["delivered", "cancelled"])
def test_fieldless_terminal_before_repository_activation_is_legacy(
    tmp_path: Path, terminal_status: str
) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.write_line(terminal_status, policy=None)
    repo.commit("terminal", f"legacy {terminal_status}")
    add_policy_only_line(repo)
    repo.commit("activation", "activate policy")

    assert validate_project(repo.path) == []


def test_fieldless_non_terminal_line_fails_after_enforcement(tmp_path: Path) -> None:
    repo = HistoryRepo.create(tmp_path)

    assert_history_error(repo, LINE, "history.line.policy.missing")


@pytest.mark.parametrize("status", ["not_started", "cancelled"])
def test_unpersisted_policy_marker_is_not_a_public_history_exemption(
    tmp_path: Path, status: str
) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.write_line(status, policy="first_parent")

    assert_history_error(repo, LINE, "history.unavailable")


@pytest.mark.parametrize("status", ["not_started", "cancelled"])
def test_second_parent_only_policy_marker_fails_closed(
    tmp_path: Path, status: str
) -> None:
    repo = HistoryRepo.create(tmp_path)
    git(repo.path, "switch", "-qc", "policy-side")
    repo.write_line(status, policy="first_parent")
    repo.commit("side-policy", "policy side")
    git(repo.path, "switch", "-q", "main")
    repo.write_line("in_progress", policy=None)
    repo.commit("main-change", "main change")
    git(repo.path, "merge", "-q", "-s", "ours", "policy-side", "-m", "merge policy side")
    repo.write_line(status, policy="first_parent")

    assert_history_error(repo, LINE, "history.unavailable")


def test_non_git_canonical_project_fails_closed(tmp_path: Path) -> None:
    repo = HistoryRepo.create(tmp_path)
    git_dir = repo.path / ".git"
    shutil.rmtree(git_dir)

    assert_history_error(repo, LINE, "history.unavailable")


def test_nested_project_root_fails_closed(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    git(parent, "init", "-q", "-b", "main") if parent.exists() else None
    if not parent.exists():
        parent.mkdir()
        git(parent, "init", "-q", "-b", "main")
    nested = parent / "nested"
    nested_repo = HistoryRepo.create(tmp_path / "nested-source")
    shutil.copytree(nested_repo.path / ".proofline", nested / ".proofline")
    shutil.copy(nested_repo.path / "proofline.yaml", nested / "proofline.yaml")
    git(parent, "add", "nested")
    git(parent, "config", "user.email", "proofline@example.invalid")
    git(parent, "config", "user.name", "ProofLine Test")
    git(parent, "commit", "-qm", "nested project")

    assert_history_error(nested, LINE, "history.unavailable")


def test_git_eof_before_exit_waits_with_remaining_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = HistoryRepo.create(tmp_path)
    original_popen = subprocess.Popen
    waits: list[float | None] = []

    class EofBeforeExit:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._process = original_popen(*args, **kwargs)
            self.stdout = self._process.stdout
            self.stderr = self._process.stderr
            self._waited = False

        def poll(self) -> int | None:
            if not self._waited:
                return None
            return self._process.poll()

        def wait(self, timeout: float | None = None) -> int:
            waits.append(timeout)
            if timeout is None or timeout <= 0:
                raise AssertionError("EOF 이후에는 양수 deadline으로 wait해야 한다")
            result = self._process.wait(timeout=timeout)
            self._waited = True
            return result

        def kill(self) -> None:
            self._process.kill()
            self._waited = True

    monkeypatch.setattr(implementation_history.subprocess, "Popen", EofBeforeExit)

    output = implementation_history._git(
        implementation_history._GitSession(repo.path),
        "rev-parse",
        "--is-inside-work-tree",
    )

    assert output == b"true\n"
    assert waits and waits[-1] is not None and waits[-1] > 0


def test_git_spawn_failure_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = HistoryRepo.create(tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("git unavailable")

    monkeypatch.setattr(implementation_history.subprocess, "Popen", fail)

    assert_history_error(repo, LINE, "history.unavailable")


def test_git_session_cache_uses_command_key_and_stdout_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = HistoryRepo.create(tmp_path)
    original_popen = subprocess.Popen
    spawned = 0

    def counting_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        nonlocal spawned
        spawned += 1
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(implementation_history.subprocess, "Popen", counting_popen)
    session = implementation_history._GitSession(repo.path)
    first = implementation_history._git(session, "rev-parse", "HEAD")
    second = implementation_history._git(session, "rev-parse", "HEAD")

    assert first == second
    assert spawned == 1
    assert all(isinstance(key, tuple) and all(isinstance(part, str) for part in key)
               for key in session.cache)
    assert not any(hasattr(key, "fileno") for key in session.cache)


def test_git_command_output_limit_is_aggregate_across_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = HistoryRepo.create(tmp_path)
    original_popen = subprocess.Popen
    chunk = b"x" * (implementation_history.GIT_OUTPUT_LIMIT // 2 + 1)
    code = "import sys; data = b'x' * %d; sys.stdout.buffer.write(data); sys.stderr.buffer.write(data)" % len(chunk)

    def noisy_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        del args
        return original_popen((sys.executable, "-c", code), **kwargs)

    monkeypatch.setattr(implementation_history.subprocess, "Popen", noisy_popen)
    with pytest.raises(implementation_history.HistoryUnavailable):
        implementation_history._git(
            implementation_history._GitSession(repo.path), "rev-parse", "HEAD"
        )


def test_git_cleanup_does_not_wait_forever_after_output_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = HistoryRepo.create(tmp_path)
    waits: list[float | None] = []
    release_reaper = threading.Event()
    reaped = threading.Event()

    class NeverReaps:
        stdout = None
        stderr = None

        def __init__(self, *args: object, **kwargs: object) -> None:
            self.returncode = None

        def poll(self) -> int | None:
            return None

        def kill(self) -> None:
            pass

        def terminate(self) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            waits.append(timeout)
            if timeout is None:
                release_reaper.wait(timeout=2)
                reaped.set()
                return 0
            raise subprocess.TimeoutExpired("git", timeout)

    monkeypatch.setattr(implementation_history.subprocess, "Popen", NeverReaps)
    with pytest.raises(implementation_history.HistoryUnavailable):
        implementation_history._git(
            implementation_history._GitSession(repo.path), "status"
        )
    assert waits and all(value is not None and value >= 0 for value in waits[:2])
    assert any(owner is not None for owner in implementation_history._REAPER_REGISTRY.values())
    release_reaper.set()
    assert reaped.wait(timeout=1)
    assert not implementation_history._REAPER_REGISTRY


def test_git_cleanup_transfers_unreaped_child_to_eventual_reaper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_wait = threading.Event()
    wait_called = threading.Event()
    reaped = threading.Event()

    class NeverReapsUntilReleased:
        stdout = None
        stderr = None

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            if timeout is not None:
                raise subprocess.TimeoutExpired("git", timeout)
            wait_called.set()
            release_wait.wait(timeout=2)
            reaped.set()
            return 0

    process = NeverReapsUntilReleased()
    implementation_history._cleanup_process(process, deadline=0.0, grace=0.001)  # type: ignore[arg-type]
    assert wait_called.wait(timeout=1)
    assert any(owner is process for owner in implementation_history._REAPER_REGISTRY.values())
    release_wait.set()
    assert reaped.wait(timeout=1)
    assert not implementation_history._REAPER_REGISTRY


@pytest.mark.parametrize(
    "key",
    ["implementation_status:", "implementation_status :", "'implementation_status':", '"implementation_status":'],
)
def test_spec_revision_normalizes_all_supported_top_level_key_spellings(key: str) -> None:
    content = (
        b"---\n"
        + key.encode()
        + b" in_progress\nother: keep\n---\n\nbody\n"
    )
    normalized = implementation_history._spec_revision_bytes(content)
    assert normalized == b"---\nother: keep\n---\n\nbody\n"


def test_spec_revision_preserves_body_and_rejects_non_scalar_lifecycle_field() -> None:
    content = b"---\nimplementation_status: [in_progress]\n---\n\nimplementation_status: body\n"
    with pytest.raises(implementation_history.HistoryUnavailable):
        implementation_history._spec_revision_bytes(content)


def test_expired_deadline_cleanup_kills_and_reaps_with_bounded_budget() -> None:
    waits: list[float | None] = []

    class KillNeedsWait:
        def __init__(self) -> None:
            self.killed = False
            self.reaped = False

        def poll(self) -> int | None:
            return 0 if self.reaped else None

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: float | None = None) -> int:
            waits.append(timeout)
            assert timeout is not None and timeout > 0
            if not self.killed or len(waits) == 1:
                raise subprocess.TimeoutExpired("git", timeout)
            self.reaped = True
            return 0

    process = KillNeedsWait()
    implementation_history._cleanup_process(
        process, deadline=implementation_history.time.monotonic() - 1  # type: ignore[arg-type]
    )

    assert process.killed
    assert process.reaped
    assert len(waits) == 2
    assert all(value is not None and 0 < value <= 0.5 for value in waits)


def test_non_utf8_git_root_is_stable_history_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = HistoryRepo.create(tmp_path)
    original = implementation_history._git

    def raw_root(session: object, *args: str) -> bytes:
        if args == ("rev-parse", "--show-toplevel"):
            return b"\xff\n"
        return original(session, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(implementation_history, "_git", raw_root)
    assert_history_error(repo, LINE, "history.unavailable")


@pytest.mark.parametrize("terminal_status", ["delivered", "cancelled"])
@pytest.mark.parametrize("ordering", ["equal", "after"])
def test_fieldless_terminal_at_or_after_activation_fails(
    tmp_path: Path, terminal_status: str, ordering: str
) -> None:
    repo = HistoryRepo.create(tmp_path)
    if ordering == "equal":
        repo.write_line(terminal_status, policy=None)
        add_policy_only_line(repo)
        repo.commit("activation", f"activate and {terminal_status}")
    else:
        add_policy_only_line(repo)
        repo.commit("activation", "activate policy")
        repo.write_line(terminal_status, policy=None)
        repo.commit("terminal", f"late fieldless {terminal_status}")

    assert_history_error(repo, LINE, "history.line.legacy.invalid")


@pytest.mark.parametrize("terminal_status", ["delivered", "cancelled"])
def test_legacy_cutoff_uses_current_terminal_transition(
    tmp_path: Path, terminal_status: str
) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.write_line(terminal_status, policy=None)
    repo.commit("pre-terminal", "pre-adoption terminal")
    add_policy_only_line(repo)
    repo.commit("activation", "activate policy")
    repo.write_line("in_progress", policy=None)
    repo.commit("resurrected", "resurrect line")
    repo.write_line(terminal_status, policy=None)
    repo.commit("post-terminal", "post-adoption terminal")

    assert_history_error(repo, LINE, "history.line.legacy.invalid")


@pytest.mark.parametrize("terminal_status", ["delivered", "cancelled"])
def test_second_parent_only_fieldless_terminal_is_not_provable(
    tmp_path: Path, terminal_status: str
) -> None:
    repo = HistoryRepo.create(tmp_path)
    add_policy_only_line(repo)
    repo.commit("activation", "activate policy")
    git(repo.path, "switch", "-qc", "terminal-side")
    repo.write_line(terminal_status, policy=None)
    repo.commit("side-terminal", f"side {terminal_status}")
    git(repo.path, "switch", "-q", "main")
    git(repo.path, "merge", "-q", "-s", "ours", "terminal-side", "-m", "ignore side")
    repo.write_line(terminal_status, policy=None)  # unpersisted bytes are not T evidence

    assert_history_error(repo, LINE, "history.line.legacy.invalid")


@pytest.mark.parametrize("change", ["remove", "change"])
def test_adopted_policy_cannot_be_removed_or_changed(tmp_path: Path, change: str) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    repo.write_line(
        "in_progress", policy=None if change == "remove" else "all_parents"
    )
    repo.commit("policy-change", change)

    assert_history_error(repo, LINE, "history.line.policy.changed")


def test_adopted_policy_deletion_and_restoration_is_not_continuous(
    tmp_path: Path,
) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.write_line("in_progress", policy=None)
    repo.commit("policy-deleted", "delete policy")
    repo.write_line("in_progress", policy="first_parent")
    repo.commit("policy-restored", "restore policy")

    assert_history_error(repo, LINE, "history.line.policy.changed")


def test_implementation_before_baseline_fails(tmp_path: Path) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.start()
    implementation = repo.product_commit()
    repo.adopt()
    repo.finish(implementation)

    assert_history_error(repo, MS, "history.ms.order")


def test_start_and_implementation_in_same_commit_fails(tmp_path: Path) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.write_line("in_progress", policy="first_parent")
    repo.write_ms(1, "in_progress")
    implementation = repo.product_commit("start-and-implementation")
    repo.finish(implementation)

    assert_history_error(repo, MS, "history.ms.order")


def test_implementation_and_implemented_transition_in_same_commit_fails(
    tmp_path: Path,
) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    repo.write_ms(1, "implemented")
    q = repo.product_commit("implementation-and-quality")
    repo.write_iqc(1, q)
    repo.commit("iqc", "bind same implementation transition")

    assert_history_error(repo, MS, "history.ms.order")


def test_direct_not_started_to_implemented_fails(tmp_path: Path) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    implementation = repo.product_commit()
    repo.finish(implementation)

    assert_history_error(repo, MS, "history.ms.transition")


def test_historical_direct_implementation_then_reset_is_not_laundered(
    tmp_path: Path,
) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    implementation = repo.product_commit("historical-direct-implementation")
    repo.finish(implementation, "historical-direct-quality")
    repo.write_ms(1, "not_started")
    repo.commit("historical-reset", "reset after invalid cycle")

    assert_history_error(repo, MS, "history.ms.transition")


def test_historical_direct_cycle_then_reset_and_valid_cycle_is_not_laundered(
    tmp_path: Path,
) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    implementation = repo.product_commit("historical-direct-implementation")
    repo.finish(implementation, "historical-direct-quality")
    repo.write_ms(1, "not_started")
    repo.commit("historical-reset", "reset after invalid cycle")
    repo.start("later-start")
    implementation = repo.product_commit("later-implementation")
    repo.finish(implementation, "later-quality")

    assert_history_error(repo, MS, "history.ms.transition")


def test_historical_invalid_rework_then_valid_rework_is_not_laundered(
    tmp_path: Path,
) -> None:
    repo = build_valid_cycle(tmp_path)
    implementation = repo.product_commit("invalid-rework-implementation")
    repo.finish(implementation, "invalid-rework-quality")
    repo.start("later-rework-start")
    implementation = repo.product_commit("later-rework-implementation")
    repo.finish(implementation, "later-rework-quality")

    assert_history_error(repo, MS, "history.ms.transition")


def test_historical_q_without_iqc_then_valid_q_is_not_laundered(
    tmp_path: Path,
) -> None:
    repo = build_valid_cycle(tmp_path)
    repo.start("invalid-rework-start")
    implementation = repo.product_commit("invalid-rework-implementation")
    repo.write_ms(1, "implemented")
    repo.commit("invalid-rework-quality", "implemented without IQC")
    repo.start("later-rework-start")
    implementation = repo.product_commit("later-rework-implementation")
    repo.finish(implementation, "later-rework-quality")

    assert_history_error(repo, MS, "history.ms.order")


def test_historical_malformed_iqc_then_valid_q_is_not_laundered(
    tmp_path: Path,
) -> None:
    repo = build_valid_cycle(tmp_path)
    repo.start("invalid-rework-start")
    implementation = repo.product_commit("invalid-rework-implementation")
    repo.write_ms(1, "implemented")
    (repo.path / IQC).write_bytes(b"---\nid: [\n---\n")
    repo.commit("invalid-rework-quality", "malformed IQC")
    repo.start("later-rework-start")
    implementation = repo.product_commit("later-rework-implementation")
    repo.finish(implementation, "later-rework-quality")

    assert (IQC, "history.unavailable") in history_codes(repo)


def test_historical_reused_iqc_then_valid_q_is_not_laundered(
    tmp_path: Path,
) -> None:
    repo = build_valid_cycle(tmp_path)
    old_iqc = (repo.path / IQC).read_bytes()
    repo.start("invalid-rework-start")
    implementation = repo.product_commit("invalid-rework-implementation")
    repo.write_ms(1, "implemented")
    (repo.path / IQC).write_bytes(old_iqc)
    repo.commit("invalid-rework-quality", "reused old IQC")
    repo.start("later-rework-start")
    implementation = repo.product_commit("later-rework-implementation")
    repo.finish(implementation, "later-rework-quality")

    assert_history_error(repo, MS, "history.ms.order")


def test_two_fully_valid_cycles_are_accepted(tmp_path: Path) -> None:
    repo = build_valid_cycle(tmp_path)
    repo.start("rework-start")
    implementation = repo.product_commit("rework-implementation")
    repo.finish(implementation, "rework-quality")

    assert validate_project(repo.path) == []


def test_rework_without_new_in_progress_transition_fails(tmp_path: Path) -> None:
    repo = build_valid_cycle(tmp_path)
    implementation = repo.product_commit("rework-implementation")
    repo.finish(implementation, "rework-quality")

    assert_history_error(repo, MS, "history.ms.transition")


def test_rework_cycle_cannot_restart_from_not_started(tmp_path: Path) -> None:
    repo = build_valid_cycle(tmp_path)
    repo.write_ms(1, "not_started")
    repo.commit("invalid-reset", "reset rework")
    repo.start("invalid-rework-start")
    implementation = repo.product_commit("invalid-rework-implementation")
    repo.finish(implementation, "invalid-rework-quality")

    assert_history_error(repo, MS, "history.ms.transition")


def test_second_parent_only_start_transition_fails(tmp_path: Path) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    git(repo.path, "switch", "-qc", "start-side")
    repo.start("side-start")
    git(repo.path, "switch", "-q", "main")
    git(repo.path, "merge", "-q", "-s", "ours", "start-side", "-m", "ignore side")
    implementation = repo.product_commit()
    repo.finish(implementation)

    assert_history_error(repo, MS, "history.ms.transition")


@pytest.mark.parametrize("_attempt", range(5))
def test_second_parent_only_implementation_binding_fails(
    tmp_path: Path, _attempt: int
) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    git(repo.path, "switch", "-qc", "implementation-side")
    implementation = repo.product_commit("side-implementation")
    git(repo.path, "switch", "-q", "main")
    (repo.path / "main.txt").write_text("main\n", encoding="utf-8")
    repo.commit("main-change", "main change")
    git(
        repo.path,
        "merge",
        "-q",
        "-s",
        "ours",
        "implementation-side",
        "-m",
        "ignore implementation side",
    )
    repo.finish(implementation)

    assert_history_error(repo, MS, "history.ms.binding")


def test_lifecycle_only_commit_cannot_be_implementation_binding(tmp_path: Path) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    lifecycle = repo.commits["start"]
    repo.finish(lifecycle)

    assert_history_error(repo, MS, "history.ms.binding")


def test_later_lifecycle_only_commit_cannot_be_implementation_binding(
    tmp_path: Path,
) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    lifecycle = repo.commits["start"]
    repo.write_ms(1, "in_progress")
    repo.write_line("verifying", policy="first_parent")
    later_lifecycle = repo.commit("later-lifecycle", "later lifecycle")
    repo.write_ms(1, "implemented")
    repo.write_iqc(1, later_lifecycle)
    repo.commit("quality", "bind later lifecycle")

    assert lifecycle != later_lifecycle
    assert_history_error(repo, MS, "history.ms.binding")


def test_start_must_follow_approved_micro_spec_commit(tmp_path: Path) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    implementation = repo.product_commit()
    repo.finish(implementation, micro_spec_commit=repo.commits["start"])

    assert_history_error(repo, MS, "history.ms.order")


def test_reapproval_and_in_progress_same_commit_cannot_bind_old_approval(
    tmp_path: Path,
) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.write_ms(1, "not_started", spec_status="draft")
    repo.commit("draft", "draft specification")
    repo.write_ms(1, "in_progress", spec_status="approved")
    repo.write_line("in_progress", policy="first_parent")
    repo.commit("reapproved-start", "reapprove and start")
    implementation = repo.product_commit()
    repo.finish(implementation, micro_spec_commit=repo.commits["approval"])

    assert_history_error(repo, MS, "history.ms.order")


def test_unresolved_implementation_commit_fails_closed(tmp_path: Path) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    repo.finish("f" * 40)

    assert_history_error(repo, MS, "history.ms.binding")


def test_iqc_boundary_does_not_float_over_later_product_commit(tmp_path: Path) -> None:
    repo = build_valid_cycle(tmp_path)
    repo.product_commit("later-product")

    assert validate_project(repo.path) == []


def test_malformed_historical_micro_spec_fails_closed(tmp_path: Path) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.write_ms(1, "not_started", malformed=True)
    repo.commit("malformed", "malformed historical micro spec")
    repo.write_ms(1, "in_progress")
    repo.write_line("in_progress", policy="first_parent")
    repo.commit("start", "restore and start")

    assert_history_error(repo, MS, "history.unavailable")


@pytest.mark.parametrize("mode", ["deleted", "normalized"])
def test_malformed_historical_line_is_not_laundered(
    tmp_path: Path, mode: str
) -> None:
    repo = build_valid_cycle(tmp_path)
    line = repo.path / LINE
    line.write_bytes(b"---\nid: [\n---\n")
    repo.commit("malformed-line", "malformed historical Line")
    if mode == "deleted":
        line.unlink()
    else:
        repo.write_line("verifying", policy="first_parent")
    repo.commit(f"line-{mode}", f"{mode} historical Line")

    errors = validate_project(repo.path)

    assert [(error.path, error.code) for error in errors if error.code.startswith("history.")] == [
        (LINE, "history.unavailable")
    ]


def test_malformed_historical_unselected_iqc_is_not_laundered(
    tmp_path: Path,
) -> None:
    repo = HistoryRepo.create(tmp_path, specs=2)
    repo.adopt()
    repo.start(numbers=(1,))
    first = repo.product_commit("first-implementation")
    repo.finish(first, "first-quality", numbers=(1,))
    iqc_two = repo.path / ".proofline/lines/line-0001/micro-specs/iqc-0001-002.md"
    iqc_two.write_bytes(b"---\nid: [\n---\n")
    repo.commit("malformed-unselected-iqc", "malformed unselected IQC")
    repo.start("later-start", numbers=(1,))
    later = repo.product_commit("later-implementation")
    repo.finish(later, "later-quality", numbers=(1,))

    errors = validate_project(repo.path)

    assert [(error.path, error.code) for error in errors if error.code.startswith("history.")] == [
        (".proofline/lines/line-0001/micro-specs/iqc-0001-002.md", "history.unavailable")
    ]


@pytest.mark.parametrize("malformed", [False, True])
def test_deleted_historical_micro_spec_is_still_checked(
    tmp_path: Path, malformed: bool
) -> None:
    repo = HistoryRepo.create(tmp_path, specs=2)
    repo.adopt()
    repo.write_ms(2, "not_started", malformed=malformed, spec_status="draft")
    repo.commit("historical-ms", "add historical micro spec")
    (repo.path / ".proofline/lines/line-0001/micro-specs/ms-0001-002.md").unlink()
    repo.commit("delete-ms", "delete historical micro spec")
    repo.start(numbers=(1,))

    errors = validate_project(repo.path)

    expected = "history.unavailable" if malformed else "history.ms.current.unpersisted"
    assert (".proofline/lines/line-0001/micro-specs/ms-0001-002.md", expected) in {
        (error.path, error.code) for error in errors
    }


def test_missing_git_object_fails_closed(tmp_path: Path) -> None:
    repo = build_valid_cycle(tmp_path)
    blob = git(repo.path, "rev-parse", f'{repo.commits["baseline"]}:{LINE}').stdout.strip()
    object_path = repo.path / ".git/objects" / blob[:2] / blob[2:]
    assert object_path.is_file()
    unlink_git_object(object_path)

    assert_history_error(repo, LINE, "history.unavailable")


def test_shallow_history_fails_closed(tmp_path: Path) -> None:
    source = build_valid_cycle(tmp_path / "source")
    clone = tmp_path / "shallow"
    cloned = subprocess.run(
        (
            "git",
            "clone",
            "-q",
            "--depth",
            "1",
            f"file://{source.path}",
            str(clone),
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    assert cloned.returncode == 0, cloned.stderr
    repo = HistoryRepo(clone)

    assert_history_error(repo, LINE, "history.unavailable")


def test_one_invalid_micro_spec_is_reported_among_multiple(tmp_path: Path) -> None:
    repo = HistoryRepo.create(tmp_path, specs=2)
    repo.adopt()
    repo.start(numbers=(1,))
    good = repo.product_commit("good")
    repo.write_ms(1, "implemented")
    repo.write_iqc(1, good)
    repo.commit("first-quality", "finish first")
    bad = repo.product_commit("bad")
    repo.write_ms(2, "implemented")
    repo.write_iqc(2, bad)
    repo.commit("second-quality", "finish second without fresh start")

    errors = validate_project(repo.path)

    assert any(error.path.endswith("ms-0001-002.md") for error in errors)
    assert not any(
        error.path.endswith("ms-0001-001.md") and error.code.startswith("history.")
        for error in errors
    )


def repository_snapshot(repo: Path) -> dict[str, object]:
    git_dir = Path(git(repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    status = git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout
    def digest(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    database = {
        path.relative_to(git_dir).as_posix(): digest(path)
        for path in sorted(git_dir.rglob("*"))
        if path.is_file() and path.name not in {"COMMIT_EDITMSG"}
    }
    canonical = {
        path.relative_to(repo).as_posix(): path.read_bytes()
        for path in sorted((repo / ".proofline").rglob("*"))
        if path.is_file()
    }
    return {
        "canonical": canonical,
        "index": (git_dir / "index").read_bytes(),
        "head": (git_dir / "HEAD").read_bytes(),
        "symbolic_head": git(repo, "symbolic-ref", "-q", "HEAD").stdout,
        "refs": git(repo, "for-each-ref", "--format=%(refname):%(objectname)").stdout,
        "object_database": database,
        "status": status,
    }


def test_validation_is_read_only_for_valid_and_invalid_history(tmp_path: Path) -> None:
    valid = build_valid_cycle(tmp_path / "valid")
    invalid = HistoryRepo.create(tmp_path / "invalid")
    for repo in (valid, invalid):
        before = repository_snapshot(repo.path)
        validate_project(repo.path)
        assert repository_snapshot(repo.path) == before


def run_source(project: Path) -> subprocess.CompletedProcess[str]:
    return run_source_with_env(project)


def run_source_with_env(
    project: Path, *, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    if extra_env is None and os.name != "nt":
        env["PATH"] = "/usr/bin:/bin"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        (
            sys.executable,
            "-I",
            "-c",
            f"import sys; sys.path.insert(0, {str(ROOT / 'src')!r}); "
            "from proofline.cli import main; raise SystemExit(main())",
            "validate",
        ),
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.fixture(scope="module")
def installed_wheel_cli(tmp_path_factory: pytest.TempPathFactory) -> Path:
    provided = os.environ.get("PROOFLINE_INSTALLED_EXECUTABLE")
    if provided:
        executable = Path(provided)
        assert executable.is_absolute(), "provided installed executable must be absolute"
        assert executable.is_file(), "provided installed executable must exist"
        return executable

    root = tmp_path_factory.mktemp("installed-wheel")
    dist = root / "dist"
    dist.mkdir()
    built = subprocess.run(
        ("uv", "build", "--refresh", "--wheel", "--out-dir", str(dist)),
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    wheel = next(dist.glob("proofline-*.whl"))
    venv = root / "venv"
    created = subprocess.run(
        ("uv", "venv", "--python", sys.executable, str(venv)),
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable = venv / ("Scripts/proofline.exe" if os.name == "nt" else "bin/proofline")
    installed = subprocess.run(
        ("uv", "pip", "install", "--refresh", "--python", str(python), str(wheel)),
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert installed.returncode == 0, installed.stderr
    provenance = subprocess.run(
        (
            str(python),
            "-I",
            "-c",
            "from pathlib import Path; import proofline; "
            "p=Path(proofline.__file__).resolve(); print(p); "
            "assert 'site-packages' in p.parts",
        ),
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert provenance.returncode == 0, provenance.stderr

    return executable


def test_installed_wheel_cli_uses_hosted_candidate_executable_without_building(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / ("proofline.exe" if os.name == "nt" else "proofline")
    executable.write_bytes(b"candidate executable")
    monkeypatch.setenv("PROOFLINE_INSTALLED_EXECUTABLE", str(executable.resolve()))

    resolved = installed_wheel_cli.__wrapped__(None)  # type: ignore[arg-type]

    assert resolved == executable.resolve()


def run_wheel(
    executable: Path,
    project: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        (str(executable), "validate"),
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@dataclass(frozen=True)
class HistoryParityScenario:
    id: str
    build: Callable[[Path], HistoryRepo]
    expected_code: str | None = None
    unavailable_git: bool = False


def read_only_snapshot(repo: Path) -> tuple[object, ...]:
    return tuple(sorted(repository_snapshot(repo).items()))


def parity_valid_initial(tmp_path: Path) -> HistoryRepo:
    return build_valid_cycle(tmp_path)


def parity_valid_rework(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    repo.start("rework-start")
    implementation = repo.product_commit("rework-implementation")
    repo.finish(implementation, "rework-quality")
    return repo


def parity_multiple_implementations_bind_final(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    repo.product_commit("implementation-one")
    second = repo.product_commit("implementation-two")
    repo.finish(second)
    return repo


def parity_multiple_implementations_bind_first(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    first = repo.product_commit("implementation-one")
    repo.product_commit("implementation-two")
    repo.finish(first)
    return repo


def parity_product_in_progress_transition(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.write_line("in_progress", policy="first_parent")
    repo.write_ms(1, "in_progress")
    (repo.path / "product.py").write_text("product = True\n", encoding="utf-8")
    repo.commit("product-and-start", "product and start")
    implementation = repo.product_commit("implementation")
    repo.finish(implementation)
    return repo


def parity_dirty_policy_line(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    line = repo.path / LINE
    line.write_text(line.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    return repo


def parity_persisted_fresh_rework_in_progress(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    repo.start("rework-start")
    return repo


def parity_dirty_lifecycle_reset(tmp_path: Path, status: str) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    repo.write_ms(1, status)
    return repo


def parity_dirty_micro_spec_edit(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    ms = repo.path / MS
    ms.write_text(
        ms.read_text(encoding="utf-8").replace("범위이다.", "변경된 범위이다."),
        encoding="utf-8",
    )
    return repo


def parity_missing_current_micro_spec(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    (repo.path / MS).unlink()
    return repo


def parity_malformed_current_micro_spec(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    (repo.path / MS).write_bytes(b"---\nid: [\n---\n")
    return repo


def parity_deleted_historical_micro_spec(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path, specs=2)
    repo.adopt()
    repo.write_ms(2, "not_started", spec_status="draft")
    repo.commit("historical-ms", "add historical micro spec revision")
    (repo.path / ".proofline/lines/line-0001/micro-specs/ms-0001-002.md").unlink()
    repo.commit("delete-ms", "delete historical micro spec")
    repo.start(numbers=(1,))
    return repo


def parity_deleted_malformed_historical_micro_spec(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path, specs=2)
    repo.adopt()
    repo.write_ms(2, "not_started", malformed=True)
    repo.commit("historical-ms", "add malformed historical micro spec")
    (repo.path / ".proofline/lines/line-0001/micro-specs/ms-0001-002.md").unlink()
    repo.commit("delete-ms", "delete malformed historical micro spec")
    repo.start(numbers=(1,))
    return repo


def parity_dirty_iqc_rollback(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    old_iqc = (repo.path / IQC).read_bytes()
    repo.start("rework-start")
    implementation = repo.product_commit("rework-implementation")
    repo.write_ms(1, "implemented")
    repo.finish(implementation, "rework-quality")
    (repo.path / IQC).write_bytes(old_iqc)
    return repo


def parity_dirty_iqc_edit(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    iqc = repo.path / IQC
    iqc.write_bytes(
        iqc.read_bytes().replace("통과했다.".encode(), "변경했다.".encode())
    )
    return repo


def parity_missing_current_iqc(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    (repo.path / IQC).unlink()
    return repo


def parity_malformed_current_iqc(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    (repo.path / IQC).write_bytes(b"---\nid: [\n---\n")
    return repo


def parity_stale_iqc_rework(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    old_iqc = (repo.path / IQC).read_bytes()
    repo.start("rework-start")
    repo.product_commit("rework-implementation")
    (repo.path / IQC).write_bytes(old_iqc)
    repo.write_ms(1, "implemented")
    repo.commit("rework-quality-stale-iqc", "reuse stale IQC")
    return repo


def parity_lifecycle_only_merge(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    git(repo.path, "switch", "-qc", "product-side")
    product = repo.product_commit("side-product")
    (repo.path / "product.py").unlink()
    repo.write_line("verifying", policy="first_parent")
    repo.commit("side-reverted-product", "remove product and add lifecycle marker")
    git(repo.path, "switch", "-q", "main")
    git(repo.path, "merge", "-q", "--no-ff", "product-side", "-m", "lifecycle-only merge")
    merge = git(repo.path, "rev-parse", "HEAD").stdout.strip()
    repo.finish(merge, "quality")
    return repo


def parity_empty_implementation(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    empty = git(repo.path, "commit", "--allow-empty", "-qm", "empty implementation")
    implementation = git(repo.path, "rev-parse", "HEAD").stdout.strip()
    assert empty.returncode == 0
    repo.finish(implementation, "quality")
    return repo


def parity_legacy_terminal(tmp_path: Path, status: str) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.write_line(status, policy=None)
    repo.commit("terminal", f"legacy {status}")
    return repo


def parity_fieldless_terminal_before_later_activation(
    tmp_path: Path, status: str
) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.write_line(status, policy=None)
    repo.commit("terminal", f"fieldless {status} before activation")
    add_policy_only_line(repo)
    repo.commit("activation", "activate history from another Line")
    return repo


def parity_terminal_at_activation(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.write_line("delivered", policy=None)
    add_policy_only_line(repo)
    repo.commit("activation", "activate and deliver")
    return repo


def parity_direct_transition(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    implementation = repo.product_commit()
    repo.finish(implementation)
    return repo


def parity_direct_transition_then_valid_cycle(tmp_path: Path) -> HistoryRepo:
    repo = parity_direct_transition(tmp_path)
    repo.write_ms(1, "not_started")
    repo.commit("historical-reset", "reset after invalid cycle")
    repo.start("later-start")
    implementation = repo.product_commit("later-implementation")
    repo.finish(implementation, "later-quality")
    return repo


def parity_invalid_rework_then_valid_rework(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    implementation = repo.product_commit("invalid-rework-implementation")
    repo.finish(implementation, "invalid-rework-quality")
    repo.start("later-rework-start")
    implementation = repo.product_commit("later-rework-implementation")
    repo.finish(implementation, "later-rework-quality")
    return repo


def parity_historical_missing_iqc_then_valid_rework(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    repo.start("invalid-rework-start")
    repo.product_commit("invalid-rework-implementation")
    repo.write_ms(1, "implemented")
    repo.commit("invalid-rework-quality", "implemented without IQC")
    repo.start("later-rework-start")
    implementation = repo.product_commit("later-rework-implementation")
    repo.finish(implementation, "later-rework-quality")
    return repo


def parity_historical_malformed_iqc_then_valid_rework(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    repo.start("invalid-rework-start")
    repo.product_commit("invalid-rework-implementation")
    repo.write_ms(1, "implemented")
    (repo.path / IQC).write_bytes(b"---\nid: [\n---\n")
    repo.commit("invalid-rework-quality", "malformed IQC")
    repo.start("later-rework-start")
    implementation = repo.product_commit("later-rework-implementation")
    repo.finish(implementation, "later-rework-quality")
    return repo


def parity_historical_reused_iqc_then_valid_rework(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    old_iqc = (repo.path / IQC).read_bytes()
    repo.start("invalid-rework-start")
    repo.product_commit("invalid-rework-implementation")
    repo.write_ms(1, "implemented")
    (repo.path / IQC).write_bytes(old_iqc)
    repo.commit("invalid-rework-quality", "reused old IQC")
    repo.start("later-rework-start")
    implementation = repo.product_commit("later-rework-implementation")
    repo.finish(implementation, "later-rework-quality")
    return repo


def parity_two_valid_cycles(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    repo.start("rework-start")
    implementation = repo.product_commit("rework-implementation")
    repo.finish(implementation, "rework-quality")
    return repo


def parity_implementation_before_baseline(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.start()
    implementation = repo.product_commit()
    repo.adopt()
    repo.finish(implementation)
    return repo


def parity_same_commit_p_equals_i(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.write_line("in_progress", policy="first_parent")
    repo.write_ms(1, "in_progress")
    implementation = repo.product_commit("start-and-implementation")
    repo.finish(implementation)
    return repo


def parity_same_commit_i_equals_q(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    repo.write_ms(1, "implemented")
    implementation = repo.product_commit("implementation-and-quality")
    repo.write_iqc(1, implementation)
    repo.commit("iqc", "bind same implementation transition")
    return repo


def parity_second_parent_marker(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    git(repo.path, "switch", "-qc", "policy-side")
    repo.write_line("not_started", policy="first_parent")
    repo.commit("side-policy", "policy side")
    git(repo.path, "switch", "-q", "main")
    repo.write_line("in_progress", policy=None)
    repo.commit("main-change", "main change")
    git(repo.path, "merge", "-q", "-s", "ours", "policy-side", "-m", "merge policy side")
    repo.write_line("not_started", policy="first_parent")
    return repo


def parity_later_lifecycle_binding(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    repo.write_ms(1, "in_progress")
    repo.write_line("verifying", policy="first_parent")
    later = repo.commit("later-lifecycle", "later lifecycle")
    repo.write_ms(1, "implemented")
    repo.write_iqc(1, later)
    repo.commit("quality", "bind later lifecycle")
    return repo


def parity_git_unavailable(tmp_path: Path) -> HistoryRepo:
    return build_valid_cycle(tmp_path)


def parity_second_parent_terminal(tmp_path: Path, status: str) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    add_policy_only_line(repo)
    repo.commit("activation", "activate policy")
    git(repo.path, "switch", "-qc", "terminal-side")
    repo.write_line(status, policy=None)
    repo.commit("side-terminal", f"side {status}")
    git(repo.path, "switch", "-q", "main")
    git(repo.path, "merge", "-q", "-s", "ours", "terminal-side", "-m", "ignore side")
    repo.write_line(status, policy=None)
    return repo


def parity_policy_change(tmp_path: Path, change: str) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    repo.write_line("in_progress", policy=None if change == "remove" else "all_parents")
    repo.commit(f"policy-{change}", change)
    return repo


def parity_policy_delete_restore(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.write_line("in_progress", policy=None)
    repo.commit("policy-deleted", "delete policy")
    repo.write_line("in_progress", policy="first_parent")
    repo.commit("policy-restored", "restore policy")
    return repo


def parity_line_delete(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    (repo.path / LINE).unlink()
    repo.commit("line-deleted", "delete policy-bearing Line")
    return repo


def parity_line_delete_restore(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    line = repo.path / LINE
    original = line.read_bytes()
    line.unlink()
    repo.commit("line-deleted", "delete policy-bearing Line")
    line.write_bytes(original)
    repo.commit("line-restored", "restore policy-bearing Line")
    return repo


def parity_historical_line_duplicate(tmp_path: Path, field: str) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    line = repo.path / LINE
    text = line.read_text(encoding="utf-8")
    marker = (
        "implementation_history: first_parent"
        if field == "implementation_history"
        else "execution_status: in_progress"
    )
    line.write_text(text.replace(marker, f"{marker}\n{marker}"), encoding="utf-8")
    repo.commit(f"duplicate-line-{field}", f"historical duplicate {field}")
    line.write_bytes(text.encode("utf-8"))
    repo.commit(f"normalize-line-{field}", f"normalize {field}")
    return repo


def parity_malformed_historical_line(tmp_path: Path, mode: str) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    line = repo.path / LINE
    line.write_bytes(b"---\nid: [\n---\n")
    repo.commit("malformed-line", "malformed historical Line")
    if mode == "deleted":
        line.unlink()
    else:
        repo.write_line("verifying", policy="first_parent")
    repo.commit(f"line-{mode}", f"{mode} historical Line")
    return repo


def parity_historical_ms_duplicate_normalized(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    ms = repo.path / MS
    text = ms.read_text(encoding="utf-8")
    marker = "implementation_status: not_started"
    ms.write_text(text.replace(marker, f"{marker}\n{marker}"), encoding="utf-8")
    repo.commit("duplicate-ms-status", "historical duplicate implementation_status")
    ms.write_bytes(text.encode("utf-8"))
    repo.commit("normalize-ms-status", "normalize implementation_status")
    repo.start()
    implementation = repo.product_commit()
    repo.finish(implementation)
    return repo


def parity_historical_ms_duplicate_deleted(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    ms = repo.path / MS
    text = ms.read_text(encoding="utf-8")
    marker = "spec_status: approved"
    ms.write_text(text.replace(marker, f"{marker}\n{marker}"), encoding="utf-8")
    repo.commit("duplicate-ms-spec-status", "historical duplicate spec_status")
    ms.unlink()
    repo.commit("delete-ms", "delete normalized Micro-SPEC")
    return repo


def parity_historical_iqc_duplicate_reworked(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    repo.start("duplicate-iqc-start")
    implementation = repo.product_commit("duplicate-iqc-implementation")
    repo.finish(implementation, "duplicate-iqc-quality")
    iqc = repo.path / IQC
    text = iqc.read_text(encoding="utf-8")
    marker = f'implementation_commit: "{implementation}"'
    iqc.write_text(text.replace(marker, f"{marker}\n{marker}"), encoding="utf-8")
    repo.commit("duplicate-iqc", "historical duplicate implementation_commit")
    repo.start("later-rework-start")
    implementation = repo.product_commit("later-rework-implementation")
    repo.finish(implementation, "later-rework-quality")
    return repo


def parity_malformed_historical_unselected_iqc(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path, specs=2)
    repo.adopt()
    repo.start(numbers=(1,))
    first = repo.product_commit("first-implementation")
    repo.finish(first, "first-quality", numbers=(1,))
    iqc_two = repo.path / ".proofline/lines/line-0001/micro-specs/iqc-0001-002.md"
    iqc_two.write_bytes(b"---\nid: [\n---\n")
    repo.commit("malformed-unselected-iqc", "malformed unselected IQC")
    repo.start("later-start", numbers=(1,))
    later = repo.product_commit("later-implementation")
    repo.finish(later, "later-quality", numbers=(1,))
    return repo


def parity_terminal_after_activation(tmp_path: Path, status: str) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    add_policy_only_line(repo)
    repo.commit("activation", "activate policy")
    repo.write_line(status, policy=None)
    repo.commit("terminal", f"late fieldless {status}")
    return repo


def parity_fieldless_terminal_before_later_activation_delivered(
    tmp_path: Path,
) -> HistoryRepo:
    return parity_fieldless_terminal_before_later_activation(tmp_path, "delivered")


def parity_fieldless_terminal_before_later_activation_cancelled(
    tmp_path: Path,
) -> HistoryRepo:
    return parity_fieldless_terminal_before_later_activation(tmp_path, "cancelled")


def parity_current_terminal_restoration(tmp_path: Path, status: str) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.write_line(status, policy=None)
    repo.commit("pre-terminal", "pre-adoption terminal")
    add_policy_only_line(repo)
    repo.commit("activation", "activate policy")
    repo.write_line("in_progress", policy=None)
    repo.commit("resurrected", "resurrect line")
    repo.write_line(status, policy=None)
    return repo


def parity_start_before_approved_spec(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    implementation = repo.product_commit()
    repo.finish(implementation, micro_spec_commit=repo.commits["start"])
    return repo


def parity_reapproval_and_start(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.write_ms(1, "not_started", spec_status="draft")
    repo.commit("draft", "draft specification")
    repo.write_ms(1, "in_progress", spec_status="approved")
    repo.write_line("in_progress", policy="first_parent")
    repo.commit("reapproved-start", "reapprove and start")
    implementation = repo.product_commit()
    repo.finish(implementation, micro_spec_commit=repo.commits["approval"])
    return repo


def parity_stale_approved_bytes(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.write_ms(1, "in_progress")
    repo.commit("start", "start")
    ms = repo.path / MS
    ms.write_text(
        ms.read_text(encoding="utf-8").replace("범위이다.", "변경된 승인 범위이다."),
        encoding="utf-8",
    )
    repo.commit("approved-bytes-change", "edit approved spec")
    implementation = repo.product_commit()
    repo.finish(implementation, micro_spec_commit=repo.commits["start"])
    return repo


def parity_current_draft_active(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    repo.write_ms(1, "implemented", spec_status="draft")
    repo.commit("draft-current", "withdraw approval")
    return repo


def parity_current_withdrawn_active(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    repo.write_ms(1, "in_progress", spec_status="withdrawn")
    repo.commit("withdraw-current", "withdraw active specification")
    return repo


def parity_rework_missing_start(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    implementation = repo.product_commit("rework-implementation")
    repo.finish(implementation, "rework-quality")
    return repo


def parity_invalid_reset(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    repo.write_ms(1, "not_started")
    repo.commit("invalid-reset", "reset rework")
    repo.start("invalid-rework-start")
    implementation = repo.product_commit("invalid-rework-implementation")
    repo.finish(implementation, "invalid-rework-quality")
    return repo


def parity_second_parent_start(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    git(repo.path, "switch", "-qc", "start-side")
    repo.start("side-start")
    git(repo.path, "switch", "-q", "main")
    git(repo.path, "merge", "-q", "-s", "ours", "start-side", "-m", "ignore side")
    implementation = repo.product_commit()
    repo.finish(implementation)
    return repo


def parity_second_parent_implementation(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    git(repo.path, "switch", "-qc", "implementation-side")
    implementation = repo.product_commit("side-implementation")
    git(repo.path, "switch", "-q", "main")
    (repo.path / "main.txt").write_text("main\n", encoding="utf-8")
    repo.commit("main-change", "main change")
    git(repo.path, "merge", "-q", "-s", "ours", "implementation-side", "-m", "ignore implementation side")
    repo.finish(implementation)
    return repo


def parity_unresolved_implementation(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    repo.finish("f" * 40)
    return repo


def parity_malformed_history(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.write_ms(1, "not_started", malformed=True)
    repo.commit("malformed", "malformed historical micro spec")
    repo.write_ms(1, "in_progress")
    repo.write_line("in_progress", policy="first_parent")
    repo.commit("start", "restore and start")
    return repo


def parity_missing_object(tmp_path: Path) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    blob = git(repo.path, "rev-parse", f"{repo.commits['baseline']}:{LINE}").stdout.strip()
    object_path = repo.path / ".git/objects" / blob[:2] / blob[2:]
    assert object_path.is_file()
    unlink_git_object(object_path)
    return repo


def parity_shallow_history(tmp_path: Path) -> HistoryRepo:
    source = build_valid_cycle(tmp_path / "source")
    clone = tmp_path / "shallow"
    cloned = subprocess.run(
        ("git", "clone", "-q", "--depth", "1", f"file://{source.path}", str(clone)),
        text=True, capture_output=True, check=False,
    )
    assert cloned.returncode == 0, cloned.stderr
    return HistoryRepo(clone)


def parity_multi_ms_violation(tmp_path: Path) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path, specs=2)
    repo.adopt()
    repo.start(numbers=(1,))
    good = repo.product_commit("good")
    repo.write_ms(1, "implemented")
    repo.write_iqc(1, good)
    repo.commit("first-quality", "finish first")
    bad = repo.product_commit("bad")
    repo.write_ms(2, "implemented")
    repo.write_iqc(2, bad)
    repo.commit("second-quality", "finish second without fresh start")
    return repo


@pytest.mark.parametrize(
    ("builder", "expected_path"),
    [
        (lambda p: parity_historical_line_duplicate(p, "implementation_history"), LINE),
        (lambda p: parity_historical_line_duplicate(p, "execution_status"), LINE),
        (parity_historical_ms_duplicate_normalized, MS),
        (parity_historical_ms_duplicate_deleted, MS),
        (parity_historical_iqc_duplicate_reworked, IQC),
    ],
    ids=[
        "line-policy",
        "line-execution-status",
        "micro-spec-implementation-status",
        "micro-spec-spec-status",
        "iqc-implementation-commit",
    ],
)
def test_historical_duplicate_laundering_has_one_stable_path_bound_diagnostic(
    tmp_path: Path,
    builder: Callable[[Path], HistoryRepo],
    expected_path: str,
) -> None:
    repo = builder(tmp_path)

    assert [
        (error.path, error.code)
        for error in validate_project(repo.path)
        if error.code.startswith("history.")
    ] == [
        (expected_path, "history.unavailable")
    ]


PARITY_SCENARIOS = [
    pytest.param(HistoryParityScenario("p-before-b-valid", lambda p: build_valid_cycle(p, order="start-first")), id="p-before-b-valid"),
    pytest.param(HistoryParityScenario("b-before-p-valid", lambda p: build_valid_cycle(p, order="baseline-first")), id="b-before-p-valid"),
    pytest.param(
        HistoryParityScenario("initial-valid", parity_valid_initial), id="initial-valid"
    ),
    pytest.param(
        HistoryParityScenario("rework-valid", parity_valid_rework), id="rework-valid"
    ),
    pytest.param(
        HistoryParityScenario(
            "multiple-implementations-bind-final",
            parity_multiple_implementations_bind_final,
        ),
        id="multiple-implementations-bind-final",
    ),
    pytest.param(
        HistoryParityScenario(
            "multiple-implementations-bind-first",
            parity_multiple_implementations_bind_first,
        ),
        id="multiple-implementations-bind-first",
    ),
    pytest.param(
        HistoryParityScenario(
            "product-in-progress-transition",
            parity_product_in_progress_transition,
            "history.ms.order",
        ),
        id="product-in-progress-transition",
    ),
    pytest.param(
        HistoryParityScenario(
            "dirty-policy-line",
            parity_dirty_policy_line,
            "history.line.current.unpersisted",
        ),
        id="dirty-policy-line",
    ),
    pytest.param(
        HistoryParityScenario(
            "persisted-fresh-rework-in-progress",
            parity_persisted_fresh_rework_in_progress,
        ),
        id="persisted-fresh-rework-in-progress",
    ),
    pytest.param(
        HistoryParityScenario(
            "dirty-reset-in-progress",
            lambda p: parity_dirty_lifecycle_reset(p, "in_progress"),
            "history.ms.current.unpersisted",
        ),
        id="dirty-reset-in-progress",
    ),
    pytest.param(
        HistoryParityScenario(
            "dirty-reset-not-started",
            lambda p: parity_dirty_lifecycle_reset(p, "not_started"),
            "history.ms.current.unpersisted",
        ),
        id="dirty-reset-not-started",
    ),
    pytest.param(
        HistoryParityScenario(
            "dirty-edit-same-status",
            parity_dirty_micro_spec_edit,
            "history.ms.current.unpersisted",
        ),
        id="dirty-edit-same-status",
    ),
    pytest.param(
        HistoryParityScenario(
            "missing-current-micro-spec",
            parity_missing_current_micro_spec,
            "history.ms.current.unpersisted",
        ),
        id="missing-current-micro-spec",
    ),
    pytest.param(
        HistoryParityScenario(
            "malformed-current-micro-spec",
            parity_malformed_current_micro_spec,
            "history.ms.current.unpersisted",
        ),
        id="malformed-current-micro-spec",
    ),
    pytest.param(
        HistoryParityScenario(
            "deleted-historical-micro-spec",
            parity_deleted_historical_micro_spec,
            "history.ms.current.unpersisted",
        ),
        id="deleted-historical-micro-spec",
    ),
    pytest.param(
        HistoryParityScenario(
            "deleted-malformed-historical-micro-spec",
            parity_deleted_malformed_historical_micro_spec,
            "history.unavailable",
        ),
        id="deleted-malformed-historical-micro-spec",
    ),
    pytest.param(
        HistoryParityScenario(
            "dirty-iqc-rollback",
            parity_dirty_iqc_rollback,
            "history.iqc.current.unpersisted",
        ),
        id="dirty-iqc-rollback",
    ),
    pytest.param(
        HistoryParityScenario(
            "dirty-iqc-edit",
            parity_dirty_iqc_edit,
            "history.iqc.current.unpersisted",
        ),
        id="dirty-iqc-edit",
    ),
    pytest.param(
        HistoryParityScenario(
            "missing-current-iqc",
            parity_missing_current_iqc,
            "history.iqc.current.unpersisted",
        ),
        id="missing-current-iqc",
    ),
    pytest.param(
        HistoryParityScenario(
            "malformed-current-iqc",
            parity_malformed_current_iqc,
            "history.iqc.current.unpersisted",
        ),
        id="malformed-current-iqc",
    ),
    pytest.param(
        HistoryParityScenario(
            "stale-iqc-rework", parity_stale_iqc_rework, "history.ms.order"
        ),
        id="stale-iqc-rework",
    ),
    pytest.param(
        HistoryParityScenario(
            "lifecycle-only-merge", parity_lifecycle_only_merge, "history.ms.binding"
        ),
        id="lifecycle-only-merge",
    ),
    pytest.param(
        HistoryParityScenario(
            "empty-implementation", parity_empty_implementation, "history.ms.binding"
        ),
        id="empty-implementation",
    ),
    pytest.param(
        HistoryParityScenario(
            "legacy-delivered",
            lambda path: parity_legacy_terminal(path, "delivered"),
        ),
        id="legacy-delivered",
    ),
    pytest.param(
        HistoryParityScenario(
            "legacy-cancelled",
            lambda path: parity_legacy_terminal(path, "cancelled"),
        ),
        id="legacy-cancelled",
    ),
    pytest.param(
        HistoryParityScenario(
            "fieldless-terminal-before-later-activation-delivered",
            parity_fieldless_terminal_before_later_activation_delivered,
        ),
        id="fieldless-terminal-before-later-activation-delivered",
    ),
    pytest.param(
        HistoryParityScenario(
            "fieldless-terminal-before-later-activation-cancelled",
            parity_fieldless_terminal_before_later_activation_cancelled,
        ),
        id="fieldless-terminal-before-later-activation-cancelled",
    ),
    pytest.param(
        HistoryParityScenario(
            "terminal-t-equal-a",
            parity_terminal_at_activation,
            "history.line.legacy.invalid",
        ),
        id="terminal-t-equal-a",
    ),
    pytest.param(HistoryParityScenario("terminal-t-after-a-delivered", lambda p: parity_terminal_after_activation(p, "delivered"), "history.line.legacy.invalid"), id="terminal-t-after-a-delivered"),
    pytest.param(HistoryParityScenario("terminal-t-after-a-cancelled", lambda p: parity_terminal_after_activation(p, "cancelled"), "history.line.legacy.invalid"), id="terminal-t-after-a-cancelled"),
    pytest.param(HistoryParityScenario("terminal-t-after-a", lambda p: parity_terminal_after_activation(p, "delivered"), "history.line.legacy.invalid"), id="terminal-t-after-a"),
    pytest.param(HistoryParityScenario("fieldless-non-terminal", lambda p: HistoryRepo.create(p), "history.line.policy.missing"), id="fieldless-non-terminal"),
    pytest.param(HistoryParityScenario("second-parent-terminal-delivered", lambda p: parity_second_parent_terminal(p, "delivered"), "history.line.legacy.invalid"), id="second-parent-terminal-delivered"),
    pytest.param(HistoryParityScenario("second-parent-terminal-cancelled", lambda p: parity_second_parent_terminal(p, "cancelled"), "history.line.legacy.invalid"), id="second-parent-terminal-cancelled"),
    pytest.param(HistoryParityScenario("policy-removal", lambda p: parity_policy_change(p, "remove"), "history.line.policy.changed"), id="policy-removal"),
    pytest.param(HistoryParityScenario("policy-change", lambda p: parity_policy_change(p, "change"), "history.line.policy.changed"), id="policy-change"),
    pytest.param(HistoryParityScenario("policy-delete-restore", parity_policy_delete_restore, "history.line.policy.changed"), id="policy-delete-restore"),
    pytest.param(HistoryParityScenario("line-artifact-delete", parity_line_delete, "history.line.policy.changed"), id="line-artifact-delete"),
    pytest.param(HistoryParityScenario("line-artifact-delete-restore", parity_line_delete_restore, "history.line.policy.changed"), id="line-artifact-delete-restore"),
    pytest.param(HistoryParityScenario("historical-line-duplicate-policy", lambda p: parity_historical_line_duplicate(p, "implementation_history"), "history.unavailable"), id="historical-line-duplicate-policy"),
    pytest.param(HistoryParityScenario("historical-line-duplicate-execution", lambda p: parity_historical_line_duplicate(p, "execution_status"), "history.unavailable"), id="historical-line-duplicate-execution"),
    pytest.param(HistoryParityScenario("malformed-historical-line-deleted", lambda p: parity_malformed_historical_line(p, "deleted"), "history.unavailable"), id="malformed-historical-line-deleted"),
    pytest.param(HistoryParityScenario("malformed-historical-line-normalized", lambda p: parity_malformed_historical_line(p, "normalized"), "history.unavailable"), id="malformed-historical-line-normalized"),
    pytest.param(HistoryParityScenario("historical-ms-duplicate-status-normalized", parity_historical_ms_duplicate_normalized, "history.unavailable"), id="historical-ms-duplicate-status-normalized"),
    pytest.param(HistoryParityScenario("historical-ms-duplicate-spec-deleted", parity_historical_ms_duplicate_deleted, "history.unavailable"), id="historical-ms-duplicate-spec-deleted"),
    pytest.param(HistoryParityScenario("historical-iqc-duplicate-implementation-reworked", parity_historical_iqc_duplicate_reworked, "history.unavailable"), id="historical-iqc-duplicate-implementation-reworked"),
    pytest.param(HistoryParityScenario("malformed-historical-unselected-iqc", parity_malformed_historical_unselected_iqc, "history.unavailable"), id="malformed-historical-unselected-iqc"),
    pytest.param(HistoryParityScenario("implementation-before-baseline", parity_implementation_before_baseline, "history.ms.order"), id="implementation-before-baseline"),
    pytest.param(HistoryParityScenario("start-before-approved-spec", parity_start_before_approved_spec, "history.ms.order"), id="start-before-approved-spec"),
    pytest.param(HistoryParityScenario("rework-missing-in-progress", parity_rework_missing_start, "history.ms.transition"), id="rework-missing-in-progress"),
    pytest.param(HistoryParityScenario("invalid-reset", parity_invalid_reset, "history.ms.transition"), id="invalid-reset"),
    pytest.param(HistoryParityScenario("second-parent-start", parity_second_parent_start, "history.ms.transition"), id="second-parent-start"),
    pytest.param(HistoryParityScenario("second-parent-implementation", parity_second_parent_implementation, "history.ms.binding"), id="second-parent-implementation"),
    pytest.param(HistoryParityScenario("current-terminal-uncommitted-restoration-delivered", lambda p: parity_current_terminal_restoration(p, "delivered"), "history.line.legacy.invalid"), id="current-terminal-uncommitted-restoration-delivered"),
    pytest.param(HistoryParityScenario("current-terminal-uncommitted-restoration-cancelled", lambda p: parity_current_terminal_restoration(p, "cancelled"), "history.line.legacy.invalid"), id="current-terminal-uncommitted-restoration-cancelled"),
    pytest.param(HistoryParityScenario("reapproval-and-start-same-commit", parity_reapproval_and_start, "history.ms.order"), id="reapproval-and-start-same-commit"),
    pytest.param(HistoryParityScenario("stale-approved-bytes-binding", parity_stale_approved_bytes, "history.ms.order"), id="stale-approved-bytes-binding"),
    pytest.param(HistoryParityScenario("current-draft-active", parity_current_draft_active, "history.ms.order"), id="current-draft-active"),
    pytest.param(HistoryParityScenario("current-withdrawn-active", parity_current_withdrawn_active, "history.ms.order"), id="current-withdrawn-active"),
    pytest.param(
        HistoryParityScenario(
            "direct-not-started-to-implemented",
            parity_direct_transition,
            "history.ms.transition",
        ),
        id="direct-not-started-to-implemented",
    ),
    pytest.param(
        HistoryParityScenario(
            "historical-direct-then-valid-cycle",
            parity_direct_transition_then_valid_cycle,
            "history.ms.transition",
        ),
        id="historical-direct-then-valid-cycle",
    ),
    pytest.param(
        HistoryParityScenario(
            "historical-invalid-rework-then-valid",
            parity_invalid_rework_then_valid_rework,
            "history.ms.transition",
        ),
        id="historical-invalid-rework-then-valid",
    ),
    pytest.param(
        HistoryParityScenario(
            "historical-missing-iqc-then-valid",
            parity_historical_missing_iqc_then_valid_rework,
            "history.ms.order",
        ),
        id="historical-missing-iqc-then-valid",
    ),
    pytest.param(
        HistoryParityScenario(
            "historical-malformed-iqc-then-valid",
            parity_historical_malformed_iqc_then_valid_rework,
            "history.unavailable",
        ),
        id="historical-malformed-iqc-then-valid",
    ),
    pytest.param(
        HistoryParityScenario(
            "historical-reused-iqc-then-valid",
            parity_historical_reused_iqc_then_valid_rework,
            "history.ms.order",
        ),
        id="historical-reused-iqc-then-valid",
    ),
    pytest.param(
        HistoryParityScenario("two-valid-cycles", parity_two_valid_cycles),
        id="two-valid-cycles",
    ),
    pytest.param(
        HistoryParityScenario(
            "same-commit-p-equals-i",
            parity_same_commit_p_equals_i,
            "history.ms.order",
        ),
        id="same-commit-p-equals-i",
    ),
    pytest.param(
        HistoryParityScenario(
            "same-commit-i-equals-q",
            parity_same_commit_i_equals_q,
            "history.ms.order",
        ),
        id="same-commit-i-equals-q",
    ),
    pytest.param(HistoryParityScenario("unresolved-implementation-commit", parity_unresolved_implementation, "history.ms.binding"), id="unresolved-implementation-commit"),
    pytest.param(HistoryParityScenario("malformed-historical-artifact", parity_malformed_history, "history.unavailable"), id="malformed-historical-artifact"),
    pytest.param(HistoryParityScenario("missing-object-history-object-unavailable", parity_missing_object, "history.unavailable"), id="missing-object-history-object-unavailable"),
    pytest.param(HistoryParityScenario("shallow-history", parity_shallow_history, "history.unavailable"), id="shallow-history"),
    pytest.param(HistoryParityScenario("multi-ms-single-violation", parity_multi_ms_violation, "history.ms.transition"), id="multi-ms-single-violation"),
    pytest.param(
        HistoryParityScenario(
            "second-parent-only-marker",
            parity_second_parent_marker,
            "history.unavailable",
        ),
        id="second-parent-only-marker",
    ),
    pytest.param(
        HistoryParityScenario(
            "later-lifecycle-only-binding",
            parity_later_lifecycle_binding,
            "history.ms.binding",
        ),
        id="later-lifecycle-only-binding",
    ),
    pytest.param(
        HistoryParityScenario(
            "git-unavailable",
            parity_git_unavailable,
            "history.unavailable",
            True,
        ),
        id="git-unavailable",
    ),
    pytest.param(
        HistoryParityScenario(
            "git-spawn-failure",
            parity_git_unavailable,
            "history.unavailable",
            True,
        ),
        id="git-spawn-failure",
    ),
]


def test_installed_wheel_parity_matrix_is_expanded() -> None:
    expected = frozenset({
        "p-before-b-valid", "b-before-p-valid", "initial-valid", "rework-valid",
        "multiple-implementations-bind-final", "multiple-implementations-bind-first",
        "product-in-progress-transition", "dirty-policy-line",
        "persisted-fresh-rework-in-progress", "dirty-reset-in-progress",
        "dirty-reset-not-started", "dirty-edit-same-status",
        "missing-current-micro-spec", "malformed-current-micro-spec",
        "deleted-historical-micro-spec", "deleted-malformed-historical-micro-spec",
        "dirty-iqc-rollback", "dirty-iqc-edit", "missing-current-iqc",
        "malformed-current-iqc",
        "stale-iqc-rework", "lifecycle-only-merge", "empty-implementation",
        "legacy-delivered", "legacy-cancelled", "terminal-t-equal-a",
        "fieldless-terminal-before-later-activation-delivered",
        "fieldless-terminal-before-later-activation-cancelled",
        "terminal-t-after-a-delivered", "terminal-t-after-a-cancelled", "terminal-t-after-a",
        "fieldless-non-terminal", "second-parent-terminal-delivered", "second-parent-terminal-cancelled",
        "policy-removal", "policy-change", "policy-delete-restore", "implementation-before-baseline",
        "line-artifact-delete", "line-artifact-delete-restore",
        "historical-line-duplicate-policy", "historical-line-duplicate-execution",
        "malformed-historical-line-deleted", "malformed-historical-line-normalized",
        "historical-ms-duplicate-status-normalized", "historical-ms-duplicate-spec-deleted",
        "historical-iqc-duplicate-implementation-reworked", "malformed-historical-unselected-iqc",
        "start-before-approved-spec", "rework-missing-in-progress", "invalid-reset", "second-parent-start",
        "second-parent-implementation", "current-terminal-uncommitted-restoration-delivered",
        "current-terminal-uncommitted-restoration-cancelled", "reapproval-and-start-same-commit",
        "stale-approved-bytes-binding", "current-draft-active", "current-withdrawn-active",
        "direct-not-started-to-implemented", "historical-direct-then-valid-cycle",
        "historical-invalid-rework-then-valid", "historical-missing-iqc-then-valid",
        "historical-malformed-iqc-then-valid", "historical-reused-iqc-then-valid",
        "two-valid-cycles", "same-commit-p-equals-i", "same-commit-i-equals-q",
        "unresolved-implementation-commit", "malformed-historical-artifact",
        "missing-object-history-object-unavailable", "shallow-history", "multi-ms-single-violation",
        "second-parent-only-marker", "later-lifecycle-only-binding", "git-unavailable", "git-spawn-failure",
    })
    ids = [scenario.values[0].id for scenario in PARITY_SCENARIOS]
    assert len(ids) == len(set(ids))
    assert set(ids) == expected


@pytest.mark.parametrize("scenario", PARITY_SCENARIOS)
def test_installed_wheel_cli_matches_source_history_diagnostics(
    tmp_path: Path,
    installed_wheel_cli: Path,
    scenario: HistoryParityScenario,
) -> None:
    repo = scenario.build(tmp_path / scenario.id)
    before = read_only_snapshot(repo.path)
    extra_env = (
        {"PATH": str(tmp_path / "missing-git")}
        if scenario.unavailable_git
        else None
    )
    source = run_source_with_env(repo.path, extra_env=extra_env)
    assert read_only_snapshot(repo.path) == before
    wheel = run_wheel(installed_wheel_cli, repo.path, extra_env=extra_env)
    assert read_only_snapshot(repo.path) == before

    assert wheel.returncode == source.returncode
    assert wheel.stdout == source.stdout
    assert wheel.stderr == source.stderr
    if scenario.expected_code is None:
        assert source.returncode == 0
        assert wheel.returncode == 0
    else:
        assert source.returncode != 0
        assert wheel.returncode != 0
        assert f": {scenario.expected_code}:" in source.stderr
        assert f": {scenario.expected_code}:" in wheel.stderr
