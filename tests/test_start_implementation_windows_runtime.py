from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/proofline-start-implementation/scripts/create_worktree.py"


def _module():
    spec = importlib.util.spec_from_file_location("portable_create_worktree", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "proofline.yaml").write_text(
        "schema_version: 1\nartifact_root: .proofline\n", encoding="utf-8"
    )
    return repo


def _replace_popen(module, monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    original = subprocess.Popen

    def spawn(*args: object, **kwargs: object):
        del args
        return original((sys.executable, "-c", code), **kwargs)

    monkeypatch.setattr(module.subprocess, "Popen", spawn)


def _prepare(module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module.shutil, "which", lambda *args, **kwargs: sys.executable)


def test_standalone_capture_does_not_require_os_set_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    repo = _repo(tmp_path)
    _prepare(module, monkeypatch)
    _replace_popen(module, monkeypatch, "raise SystemExit(0)")
    monkeypatch.delattr(module.os, "set_blocking", raising=False)

    module.validate_transitional_history(
        repo, ".proofline/lines/line-0001/line-0001.md"
    )


def test_standalone_capture_drains_both_streams_without_deadlock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    repo = _repo(tmp_path)
    _prepare(module, monkeypatch)
    _replace_popen(
        module,
        monkeypatch,
        "import sys,threading; n=100000; "
        "a=threading.Thread(target=lambda:sys.stdout.buffer.write(b'o'*n)); "
        "b=threading.Thread(target=lambda:sys.stderr.buffer.write(b'e'*n)); "
        "a.start();b.start();a.join();b.join()",
    )

    with pytest.raises(module.WorkflowError, match="unexpected stdout"):
        module.validate_transitional_history(
            repo, ".proofline/lines/line-0001/line-0001.md"
        )


def test_standalone_capture_timeout_cap_nonzero_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    repo = _repo(tmp_path)
    cases = (
        ("import time; time.sleep(2)", "timed out", {"VALIDATE_TIMEOUT_SECONDS": 0.05}),
        (
            "import sys; sys.stdout.buffer.write(b'o'*40); sys.stderr.buffer.write(b'e'*40)",
            "excessive output",
            {"VALIDATE_OUTPUT_LIMIT": 64},
        ),
        ("raise SystemExit(3)", "exited unexpectedly", {}),
    )
    for code, message, limits in cases:
        with monkeypatch.context() as scoped:
            _prepare(module, scoped)
            _replace_popen(module, scoped, code)
            for name, value in limits.items():
                scoped.setattr(module, name, value)
            with pytest.raises(module.WorkflowError, match=message):
                module.validate_transitional_history(
                    repo, ".proofline/lines/line-0001/line-0001.md"
                )


def test_standalone_preflight_timeout_policy_allows_observed_validation_runtime() -> None:
    module = _module()
    assert module.VALIDATE_TIMEOUT_SECONDS == 30


def test_standalone_capture_fails_closed_when_a_pipe_reader_errors() -> None:
    module = _module()

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

    with pytest.raises(module.WorkflowError, match="pipe read failed"):
        module._capture_validate_process(  # type: ignore[attr-defined]
            Child(), deadline=module.time.monotonic() + 1
        )


def test_standalone_capture_cleans_up_when_pipes_are_missing() -> None:
    module = _module()

    class Child:
        stdout = None
        stderr = None
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            del timeout
            return 0

    child = Child()
    with pytest.raises(module.WorkflowError, match="failed to start"):
        module._capture_validate_process(child, deadline=module.time.monotonic() + 1)
    assert child.terminated


def test_standalone_script_has_no_installed_proofline_import() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "import proofline" not in text
    assert "from proofline" not in text
