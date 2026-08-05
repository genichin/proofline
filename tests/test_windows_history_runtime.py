from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

import proofline.implementation_history as history


ROOT = Path(__file__).resolve().parents[1]


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(("git", "init", "-q", "-b", "main"), cwd=repo, check=True)
    subprocess.run(("git", "config", "user.email", "proofline@example.invalid"), cwd=repo, check=True)
    subprocess.run(("git", "config", "user.name", "ProofLine Test"), cwd=repo, check=True)
    (repo / "file.txt").write_text("fixture\n", encoding="utf-8")
    subprocess.run(("git", "add", "."), cwd=repo, check=True)
    subprocess.run(("git", "commit", "-qm", "fixture"), cwd=repo, check=True)
    return repo


def _python_popen(monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    original = subprocess.Popen

    def spawn(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        del args
        return original((sys.executable, "-c", code), **kwargs)

    monkeypatch.setattr(history.subprocess, "Popen", spawn)


def test_git_capture_does_not_require_os_set_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.delattr(history.os, "set_blocking", raising=False)

    assert history._git(history._GitSession(repo), "rev-parse", "HEAD").strip()


def test_git_capture_drains_simultaneous_stdout_and_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    amount = 512 * 1024
    _python_popen(
        monkeypatch,
        "import sys,threading; "
        f"n={amount}; "
        "a=threading.Thread(target=lambda:sys.stdout.buffer.write(b'o'*n)); "
        "b=threading.Thread(target=lambda:sys.stderr.buffer.write(b'e'*n)); "
        "a.start();b.start();a.join();b.join()",
    )

    assert history._git(history._GitSession(repo), "status") == b"o" * amount


def test_git_capture_rejects_timeout_output_cap_and_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    cases = (
        ("import time; time.sleep(2)", {"GIT_TIMEOUT_SECONDS": 0.05}),
        (
            "import sys; n=40; sys.stdout.buffer.write(b'o'*n); sys.stderr.buffer.write(b'e'*n)",
            {"GIT_OUTPUT_LIMIT": 64},
        ),
        ("raise SystemExit(7)", {}),
    )
    for code, limits in cases:
        with monkeypatch.context() as scoped:
            _python_popen(scoped, code)
            for name, value in limits.items():
                scoped.setattr(history, name, value)
            with pytest.raises(history.HistoryUnavailable):
                history._git(history._GitSession(repo), "status")


def test_git_capture_handles_eof_exit_race_and_closes_pipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    original = subprocess.Popen
    processes: list[subprocess.Popen[bytes]] = []

    def spawn(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = original(
            (sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'ok\\n')"),
            **kwargs,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(history.subprocess, "Popen", spawn)
    assert history._git(history._GitSession(repo), "status") == b"ok\n"
    assert processes[0].poll() == 0
    assert processes[0].stdout is not None and processes[0].stdout.closed
    assert processes[0].stderr is not None and processes[0].stderr.closed


def test_git_capture_fails_closed_when_a_pipe_reader_errors() -> None:
    class BrokenStream:
        def read(self, size: int) -> bytes:
            del size
            raise OSError("broken pipe reader")

        def close(self) -> None:
            pass

    class Child:
        stdout = BrokenStream()
        stderr = BrokenStream()

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

    with pytest.raises(history.HistoryUnavailable):
        history._capture_process(Child(), deadline=history.time.monotonic() + 1, output_limit=64)  # type: ignore[arg-type]


def test_git_capture_enforces_remaining_session_output_budget_while_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    observed: list[int] = []

    class Child:
        stdout = object()
        stderr = object()

    monkeypatch.setattr(history.subprocess, "Popen", lambda *args, **kwargs: Child())

    def capture(process: object, *, deadline: float, output_limit: int):
        del process, deadline
        observed.append(output_limit)
        return 0, b"ok", b""

    monkeypatch.setattr(history, "_capture_process", capture)
    session = history._GitSession(repo)
    session.output_bytes = history.GIT_SESSION_OUTPUT_LIMIT - 7
    assert history._git(session, "status") == b"ok"
    assert observed == [7]


def test_cleanup_transfers_unreaped_child_to_single_reaper() -> None:
    release = threading.Event()
    waited = threading.Event()

    class Child:
        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            if timeout is not None:
                raise subprocess.TimeoutExpired("child", timeout)
            waited.set()
            release.wait(2)
            return 0

    child = Child()
    history._cleanup_process(child, deadline=0.0, grace=0.001)  # type: ignore[arg-type]
    assert waited.wait(1)
    assert sum(owner is child for owner in history._REAPER_REGISTRY.values()) == 1
    release.set()


def test_windows_workflow_runs_policy_history_source_and_installed_wheel() -> None:
    workflow = (ROOT / ".github/workflows/candidate-verification.yml").read_text(
        encoding="utf-8"
    )
    assert "tests/test_windows_history_runtime.py" in workflow
    assert "PROOFLINE_INSTALLED_EXECUTABLE" in workflow
    assert "tests/test_implementation_history.py" in workflow
