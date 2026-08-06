from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import warnings
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / ".github/scripts/verify-candidate-evidence.py"
SHA = "a" * 40
RUN_ID = "12345"
ATTEMPT = "1"
ARTIFACT = f"proofline-candidate-{RUN_ID}-{ATTEMPT}"
WHEEL = "proofline-0.6.0-py3-none-any.whl"


def _module():
    spec = importlib.util.spec_from_file_location("candidate_evidence", HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        repository="owner/repo",
        run_id=RUN_ID,
        run_attempt=ATTEMPT,
        candidate_sha=SHA,
        download_dir=str(tmp_path / "download"),
    )


def _archive(entries: dict[str, bytes], *, symlink: str | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
        if symlink is not None:
            info = zipfile.ZipInfo(symlink)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"target")
    return output.getvalue()


def _fixture() -> tuple[dict, bytes]:
    wheel = b"exact wheel bytes"
    digest = hashlib.sha256(wheel).hexdigest()
    provenance = {
        "schema_version": 1,
        "candidate_sha": SHA,
        "run_id": int(RUN_ID),
        "run_attempt": int(ATTEMPT),
        "workflow_path": ".github/workflows/candidate-verification.yml",
        "artifact_name": ARTIFACT,
        "wheel_filename": WHEEL,
        "wheel_sha256": digest,
    }
    payload = {
        "run": {
            "id": int(RUN_ID),
            "run_attempt": int(ATTEMPT),
            "event": "push",
            "head_sha": SHA,
            "head_branch": "candidate/line-0019",
            "path": ".github/workflows/candidate-verification.yml",
            "status": "completed",
            "conclusion": "success",
        },
        "jobs": [
            {
                "id": index,
                "name": name,
                "run_attempt": int(ATTEMPT),
                "status": "completed",
                "conclusion": "success",
            }
            for index, name in enumerate(
                ("build-candidate", "ubuntu-python311", "windows-python311"), 101
            )
        ],
        "artifacts": [
            {
                "id": 77,
                "name": ARTIFACT,
                "expired": False,
                "expires_at": "2999-01-01T00:00:00Z",
                "workflow_run": {"id": int(RUN_ID), "head_sha": SHA},
            }
        ],
    }
    archive = _archive(
        {
            WHEEL: wheel,
            "SHA256SUMS": f"{digest}  {WHEEL}\n".encode("ascii"),
            "CANDIDATE_PROVENANCE.json": (
                json.dumps(provenance, sort_keys=True) + "\n"
            ).encode("utf-8"),
        }
    )
    return payload, archive


def _validate(tmp_path: Path, payload: dict, archive: bytes) -> dict:
    module = _module()
    return module._validate_payload(
        _args(tmp_path),
        payload["run"],
        {"jobs": payload["jobs"]},
        {"artifacts": payload["artifacts"]},
        archive,
        ref_sha=SHA,
        changed_paths=[".proofline/lines/line-0019/dqc-0019.md"],
    )


def test_helper_emits_normalized_exact_job_artifact_and_wheel_evidence(
    tmp_path: Path,
) -> None:
    payload, archive = _fixture()
    evidence = _validate(tmp_path, payload, archive)
    assert evidence["candidate_sha"] == SHA
    assert evidence["run_id"] == int(RUN_ID)
    assert evidence["run_attempt"] == 1
    assert evidence["required_jobs"] == {
        "build-candidate": {"id": 101, "conclusion": "success"},
        "ubuntu-python311": {"id": 102, "conclusion": "success"},
        "windows-python311": {"id": 103, "conclusion": "success"},
    }
    assert evidence["artifact"] == {
        "id": 77,
        "name": ARTIFACT,
        "expires_at": "2999-01-01T00:00:00Z",
    }
    assert evidence["wheel"]["filename"] == WHEEL
    assert len(evidence["wheel"]["sha256"]) == 64


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda p: p["run"].update(head_sha="b" * 40), "head SHA"),
        (lambda p: p["run"].update(run_attempt=2), "attempt"),
        (lambda p: p["run"].update(conclusion="failure"), "terminal success"),
        (lambda p: p["jobs"].pop(), "required jobs"),
        (lambda p: p["jobs"].append(dict(p["jobs"][0])), "required jobs"),
        (lambda p: p["jobs"][1].update(conclusion="failure"), "required jobs"),
        (lambda p: p["jobs"][1].update(run_attempt=2), "attempt"),
        (lambda p: p["jobs"][1].update(id=None), "job identity"),
        (lambda p: p["artifacts"].clear(), "artifact"),
        (lambda p: p["artifacts"].append(dict(p["artifacts"][0])), "artifact"),
        (lambda p: p["artifacts"][0].update(expired=True), "expired"),
        (lambda p: p["artifacts"][0].update(expires_at=None), "expiry"),
        (lambda p: p["artifacts"][0].update(expires_at="not-a-time"), "expiry"),
        (lambda p: p["artifacts"][0].update(expires_at="2000-01-01T00:00:00Z"), "expired"),
        (
            lambda p: p["artifacts"][0].update(
                workflow_run={"id": int(RUN_ID), "head_sha": "b" * 40}
            ),
            "artifact identity",
        ),
    ],
)
def test_helper_rejects_remote_identity_failures(
    tmp_path: Path, mutation, message: str
) -> None:
    payload, archive = _fixture()
    mutation(payload)
    with pytest.raises(RuntimeError, match=message):
        _validate(tmp_path, payload, archive)


def test_helper_forbids_same_v_rerun_attempt(tmp_path: Path) -> None:
    payload, archive = _fixture()
    args = _args(tmp_path)
    args.run_attempt = "2"
    payload["run"]["run_attempt"] = 2
    for job in payload["jobs"]:
        job["run_attempt"] = 2
    with pytest.raises(RuntimeError, match="same-V rerun"):
        _module()._validate_payload(
            args,
            payload["run"],
            {"jobs": payload["jobs"]},
            {"artifacts": payload["artifacts"]},
            archive,
            ref_sha=SHA,
            changed_paths=[],
        )


@pytest.mark.parametrize(
    "entries,symlink",
    [
        ({"../escape": b"x"}, None),
        ({"nested/escape": b"x"}, None),
        ({"/absolute": b"x"}, None),
        ({"C:\\escape": b"x"}, None),
        ({}, "linked-wheel.whl"),
    ],
)
def test_archive_extraction_rejects_traversal_nested_absolute_and_symlink_entries(
    tmp_path: Path, entries: dict[str, bytes], symlink: str | None
) -> None:
    module = _module()
    with pytest.raises(module.EvidenceError, match="unsafe artifact archive"):
        module._safe_extract_archive(_archive(entries, symlink=symlink), tmp_path / "out")
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("failure", ["extra", "malformed", "digest", "provenance", "second-wheel"])
def test_helper_rejects_malformed_or_extra_artifact_content(
    tmp_path: Path, failure: str
) -> None:
    payload, archive = _fixture()
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    if failure == "extra":
        entries["unexpected.txt"] = b"extra"
    elif failure == "malformed":
        entries["SHA256SUMS"] = b"not-a-checksum\n"
    elif failure == "digest":
        entries[WHEEL] = b"changed wheel"
    elif failure == "provenance":
        provenance = json.loads(entries["CANDIDATE_PROVENANCE.json"])
        provenance["extra"] = True
        entries["CANDIDATE_PROVENANCE.json"] = json.dumps(provenance).encode()
    else:
        entries["proofline-0.5.1-py3-none-any.whl"] = b"extra"
    with pytest.raises(RuntimeError):
        _validate(tmp_path, payload, _archive(entries))


def test_helper_rejects_ref_drift_and_non_dqc_changes(tmp_path: Path) -> None:
    module = _module()
    payload, archive = _fixture()
    args = _args(tmp_path)
    with pytest.raises(module.EvidenceError, match="ref drift"):
        module._validate_payload(
            args, payload["run"], {"jobs": payload["jobs"]},
            {"artifacts": payload["artifacts"]}, archive,
            ref_sha="b" * 40, changed_paths=[],
        )
    with pytest.raises(module.EvidenceError, match="stale evidence"):
        module._validate_payload(
            args, payload["run"], {"jobs": payload["jobs"]},
            {"artifacts": payload["artifacts"]}, archive,
            ref_sha=SHA, changed_paths=["src/proofline/cli.py"],
        )


def test_bounded_process_capture_rejects_aggregate_output_and_timeout() -> None:
    module = _module()
    with pytest.raises(module.EvidenceError, match="output limit"):
        module._run_bounded(
            (sys.executable, "-c", "import sys;sys.stdout.write('o'*40);sys.stderr.write('e'*40)"),
            timeout=2,
            output_limit=64,
        )
    with pytest.raises(module.EvidenceError, match="timed out"):
        module._run_bounded(
            (sys.executable, "-c", "import time;time.sleep(2)"),
            timeout=0.05,
            output_limit=64,
        )


def test_remote_collection_uses_attempt_jobs_exact_artifact_id_and_no_fixture_backdoor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    payload, archive = _fixture()
    endpoints: list[str] = []

    def gh_json(repository: str, endpoint: str):
        assert repository == "owner/repo"
        endpoints.append(endpoint)
        if endpoint == f"actions/runs/{RUN_ID}":
            return payload["run"]
        if endpoint == f"actions/runs/{RUN_ID}/attempts/{ATTEMPT}/jobs?per_page=100":
            return {"jobs": payload["jobs"], "total_count": 3}
        if endpoint == f"actions/runs/{RUN_ID}/artifacts?per_page=100":
            return {"artifacts": payload["artifacts"], "total_count": 1}
        if endpoint.startswith("git/ref/heads/"):
            return {"object": {"sha": SHA}}
        raise AssertionError(endpoint)

    monkeypatch.setattr(module, "_gh_json", gh_json)
    monkeypatch.setattr(module, "_download_artifact_zip", lambda *args: archive)
    monkeypatch.setattr(module, "_git_changed_paths", lambda candidate: [])
    evidence = module._collect_and_validate(_args(tmp_path))
    assert evidence["artifact"]["id"] == 77
    assert f"actions/runs/{RUN_ID}/attempts/{ATTEMPT}/jobs?per_page=100" in endpoints
    text = HELPER.read_text(encoding="utf-8")
    assert "PROOFLINE_CANDIDATE_EVIDENCE_FIXTURE" not in text
    assert "actions/artifacts/{artifact_id}/zip" in text


def test_archive_extraction_rejects_existing_symlink_duplicate_special_and_oversize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    _, simple = _fixture()
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(module.EvidenceError, match="parent or target is unsafe"):
        module._safe_extract_archive(simple, existing)
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(module.EvidenceError, match="parent or target is unsafe"):
        module._safe_extract_archive(simple, linked)
    assert not any(outside.iterdir())

    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(module.EvidenceError, match="parent or target is unsafe"):
        module._safe_extract_archive(simple, linked_parent / "download")

    duplicate = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(duplicate, "w") as archive:
            archive.writestr("same", b"one")
            archive.writestr("same", b"two")
    with pytest.raises(module.EvidenceError, match="unsafe artifact archive"):
        module._safe_extract_archive(duplicate.getvalue(), tmp_path / "duplicate")

    special = io.BytesIO()
    with zipfile.ZipFile(special, "w") as archive:
        info = zipfile.ZipInfo("fifo")
        info.create_system = 3
        info.external_attr = (stat.S_IFIFO | 0o600) << 16
        archive.writestr(info, b"x")
    with pytest.raises(module.EvidenceError, match="unsafe artifact archive"):
        module._safe_extract_archive(special.getvalue(), tmp_path / "special")

    monkeypatch.setattr(module, "ARTIFACT_EXTRACTED_LIMIT", 1)
    with pytest.raises(module.EvidenceError, match="extracted size"):
        _, valid_archive = _fixture()
        module._safe_extract_archive(valid_archive, tmp_path / "large")


def test_bounded_process_transfers_unreaped_child_ownership() -> None:
    module = _module()
    released = threading.Event()

    class Child:
        def poll(self):
            return None

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("child", timeout)
            released.wait(1)
            return 0

    child = Child()
    module._cleanup_process(child)
    assert id(child) in module._REAPER_REGISTRY
    released.set()


def test_git_staleness_collects_staged_unstaged_and_untracked_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(("git", "init", "-q", "-b", "main"), cwd=repo, check=True)
    subprocess.run(("git", "config", "user.name", "Test"), cwd=repo, check=True)
    subprocess.run(("git", "config", "user.email", "test@example.invalid"), cwd=repo, check=True)
    tracked = repo / "tracked.txt"
    tracked.write_text("base\n")
    subprocess.run(("git", "add", "tracked.txt"), cwd=repo, check=True)
    subprocess.run(("git", "commit", "-q", "-m", "base"), cwd=repo, check=True)
    candidate = subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=repo, text=True, capture_output=True, check=True
    ).stdout.strip()
    tracked.write_text("dirty\n")
    (repo / "staged.txt").write_text("staged\n")
    subprocess.run(("git", "add", "staged.txt"), cwd=repo, check=True)
    (repo / "untracked.txt").write_text("untracked\n")
    monkeypatch.chdir(repo)

    assert module._git_changed_paths(candidate) == [
        "staged.txt", "tracked.txt", "untracked.txt"
    ]


def test_helper_binds_dqc_path_to_candidate_line_identity(tmp_path: Path) -> None:
    module = _module()
    payload, archive = _fixture()
    with pytest.raises(module.EvidenceError, match="DQC-only"):
        module._validate_payload(
            _args(tmp_path), payload["run"], {"jobs": payload["jobs"]},
            {"artifacts": payload["artifacts"]}, archive,
            ref_sha=SHA,
            changed_paths=[".proofline/lines/line-0020/dqc-0020.md"],
        )


def test_helper_is_executable() -> None:
    assert os.access(HELPER, os.X_OK)
