#!/usr/bin/env python3
"""Fail-closed read-back of exact hosted candidate evidence for DQC."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import subprocess
import sys
import threading
import time
from urllib.parse import quote
import zipfile


WORKFLOW = ".github/workflows/candidate-verification.yml"
REQUIRED_JOBS = ("build-candidate", "ubuntu-python311", "windows-python311")
PROVENANCE_KEYS = {
    "schema_version",
    "candidate_sha",
    "run_id",
    "run_attempt",
    "workflow_path",
    "artifact_name",
    "wheel_filename",
    "wheel_sha256",
}
CHECKSUM_RE = re.compile(r"^([0-9a-fA-F]{64})  \*?([^\s]+)$")
WHEEL_RE = re.compile(r"^proofline-[0-9]+\.[0-9]+\.[0-9]+-py3-none-any\.whl$")
DQC_PATH_RE = re.compile(
    r"\.proofline/lines/(?P<line>line-(?P<number>[0-9]{4}))/dqc-(?P=number)\.md\Z"
)
CANDIDATE_BRANCH_RE = re.compile(r"candidate/(?P<line>line-[0-9]{4})\Z")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
GH_TIMEOUT_SECONDS = 30.0
GIT_TIMEOUT_SECONDS = 10.0
JSON_OUTPUT_LIMIT = 8 * 1024 * 1024
ARTIFACT_ARCHIVE_LIMIT = 128 * 1024 * 1024
ARTIFACT_EXTRACTED_LIMIT = 128 * 1024 * 1024
PROCESS_CLEANUP_GRACE_SECONDS = 0.2
_REAPER_LOCK = threading.Lock()
_REAPER_REGISTRY: dict[int, subprocess.Popen[bytes]] = {}


class EvidenceError(RuntimeError):
    """Stable expected rejection from the evidence admission helper."""


def _reap_process(key: int, process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait()
    finally:
        with _REAPER_LOCK:
            if _REAPER_REGISTRY.get(key) is process:
                del _REAPER_REGISTRY[key]


def _transfer_reap(process: subprocess.Popen[bytes]) -> None:
    key = id(process)
    with _REAPER_LOCK:
        if key in _REAPER_REGISTRY:
            return
        _REAPER_REGISTRY[key] = process
        threading.Thread(
            target=_reap_process,
            args=(key, process),
            name="proofline-evidence-reaper",
            daemon=True,
        ).start()


def _cleanup_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except OSError:
        pass
    try:
        process.wait(timeout=PROCESS_CLEANUP_GRACE_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        pass
    if process.poll() is not None:
        return
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=PROCESS_CLEANUP_GRACE_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        _transfer_reap(process)


def _run_bounded(
    command: tuple[str, ...], *, timeout: float, output_limit: int, environment: dict[str, str] | None = None
) -> tuple[bytes, bytes]:
    """Capture both child streams concurrently with one aggregate hard cap."""
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
    except OSError as error:
        raise EvidenceError("bounded command failed to start") from error
    if process.stdout is None or process.stderr is None:
        _cleanup_process(process)
        raise EvidenceError("bounded command pipes are unavailable")

    buffers = [bytearray(), bytearray()]
    finished = [threading.Event(), threading.Event()]
    overflow = threading.Event()
    read_error = threading.Event()
    lock = threading.Lock()
    captured = 0

    def drain(index: int, stream: object) -> None:
        nonlocal captured
        try:
            while True:
                chunk = stream.read(65536)  # type: ignore[attr-defined]
                if not chunk:
                    break
                with lock:
                    remaining = output_limit - captured
                    if len(chunk) > remaining:
                        accepted = max(0, remaining)
                        buffers[index].extend(chunk[:accepted])
                        captured += accepted
                        overflow.set()
                        break
                    buffers[index].extend(chunk)
                    captured += len(chunk)
        except (OSError, ValueError):
            read_error.set()
        finally:
            finished[index].set()

    readers = [
        threading.Thread(target=drain, args=(0, process.stdout), daemon=True),
        threading.Thread(target=drain, args=(1, process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout
    failure: EvidenceError | None = None
    try:
        while not all(event.is_set() for event in finished):
            if overflow.is_set():
                raise EvidenceError("bounded command output limit exceeded")
            if read_error.is_set():
                raise EvidenceError("bounded command pipe read failed")
            if time.monotonic() >= deadline:
                raise EvidenceError("bounded command timed out")
            time.sleep(0.005)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise EvidenceError("bounded command timed out")
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as error:
            raise EvidenceError("bounded command timed out") from error
        if overflow.is_set():
            raise EvidenceError("bounded command output limit exceeded")
        if read_error.is_set():
            raise EvidenceError("bounded command pipe read failed")
        if returncode != 0:
            detail = bytes(buffers[1]).decode("utf-8", errors="replace").strip()
            raise EvidenceError(detail or "bounded command exited unsuccessfully")
        return bytes(buffers[0]), bytes(buffers[1])
    except EvidenceError as error:
        failure = error
        raise
    finally:
        try:
            _cleanup_process(process)
        except EvidenceError:
            if failure is None:
                raise
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except OSError:
                pass
        for reader in readers:
            reader.join(timeout=PROCESS_CLEANUP_GRACE_SECONDS)


def _gh_json(repository: str, endpoint: str) -> object:
    stdout, _ = _run_bounded(
        ("gh", "api", f"repos/{repository}/{endpoint}"),
        timeout=GH_TIMEOUT_SECONDS,
        output_limit=JSON_OUTPUT_LIMIT,
    )
    try:
        return json.loads(stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise EvidenceError("gh returned malformed JSON") from error


def _download_artifact_zip(repository: str, artifact_id: int) -> bytes:
    stdout, _ = _run_bounded(
        ("gh", "api", f"repos/{repository}/actions/artifacts/{artifact_id}/zip"),
        timeout=GH_TIMEOUT_SECONDS,
        output_limit=ARTIFACT_ARCHIVE_LIMIT,
    )
    return stdout


def _safe_extract_archive(archive_bytes: bytes, target: Path) -> None:
    """Extract only flat regular files after validating the complete ZIP directory."""
    if len(archive_bytes) > ARTIFACT_ARCHIVE_LIMIT:
        raise EvidenceError("artifact archive exceeds size limit")
    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
        entries = archive.infolist()
    except (OSError, zipfile.BadZipFile) as error:
        raise EvidenceError("artifact archive is malformed") from error
    entry_names = [entry.filename for entry in entries]
    wheels = [name for name in entry_names if name.endswith(".whl")]
    if (
        len(entries) != 3
        or len(set(entry_names)) != 3
        or len(wheels) != 1
        or set(entry_names) != {wheels[0], "SHA256SUMS", "CANDIDATE_PROVENANCE.json"}
    ):
        archive.close()
        raise EvidenceError("unsafe artifact archive entry set")
    names: set[str] = set()
    total = 0
    for entry in entries:
        posix = PurePosixPath(entry.filename)
        windows = PureWindowsPath(entry.filename)
        mode = (entry.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        unsafe = (
            not entry.filename
            or posix.is_absolute()
            or windows.is_absolute()
            or len(posix.parts) != 1
            or posix.parts[0] in {".", ".."}
            or "\\" in entry.filename
            or ":" in entry.filename
            or entry.is_dir()
            or stat.S_ISLNK(mode)
            or file_type not in {0, stat.S_IFREG}
            or entry.filename in names
        )
        if unsafe:
            archive.close()
            raise EvidenceError("unsafe artifact archive entry")
        names.add(entry.filename)
        total += entry.file_size
        if total > ARTIFACT_EXTRACTED_LIMIT:
            archive.close()
            raise EvidenceError("artifact archive exceeds extracted size limit")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    absolute = target.absolute()
    parent_fd = os.open(os.path.sep, directory_flags)
    try:
        for component in absolute.parent.parts[1:]:
            next_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = next_fd
        os.mkdir(absolute.name, mode=0o700, dir_fd=parent_fd)
        directory_fd = os.open(absolute.name, directory_flags, dir_fd=parent_fd)
    except OSError as error:
        os.close(parent_fd)
        archive.close()
        raise EvidenceError("download directory parent or target is unsafe") from error
    os.close(parent_fd)
    try:
        for entry in entries:
            data = archive.read(entry)
            if len(data) != entry.file_size:
                raise EvidenceError("artifact archive entry is truncated")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            file_fd = os.open(entry.filename, flags, 0o600, dir_fd=directory_fd)
            try:
                view = memoryview(data)
                while view:
                    written = os.write(file_fd, view)
                    if written <= 0:
                        raise EvidenceError("artifact archive extraction failed")
                    view = view[written:]
            finally:
                os.close(file_fd)
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise EvidenceError("artifact archive extraction failed") from error
    finally:
        os.close(directory_fd)
        archive.close()


def _git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def _git_changed_paths(candidate_sha: str) -> list[str]:
    environment = _git_environment()
    _run_bounded(
        ("git", "merge-base", "--is-ancestor", candidate_sha, "HEAD"),
        timeout=GIT_TIMEOUT_SECONDS,
        output_limit=JSON_OUTPUT_LIMIT,
        environment=environment,
    )
    stdout, _ = _run_bounded(
        ("git", "diff", "--name-only", "--no-renames", candidate_sha, "HEAD", "--"),
        timeout=GIT_TIMEOUT_SECONDS,
        output_limit=JSON_OUTPUT_LIMIT,
        environment=environment,
    )
    status, _ = _run_bounded(
        ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"),
        timeout=GIT_TIMEOUT_SECONDS,
        output_limit=JSON_OUTPUT_LIMIT,
        environment=environment,
    )
    try:
        paths = stdout.decode("utf-8").splitlines()
        dirty = []
        for record in status.decode("utf-8").split("\0"):
            if not record:
                continue
            if len(record) < 4 or record[2] != " ":
                raise EvidenceError("git returned malformed worktree status")
            dirty.append(record[3:])
    except UnicodeError as error:
        raise EvidenceError("git returned malformed changed paths") from error
    paths.extend(dirty)
    if any(not path or "\x00" in path for path in paths):
        raise EvidenceError("git returned malformed changed paths")
    return sorted(set(paths))


def _require_mapping(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} is malformed")
    return value


def _validate_payload(
    args: argparse.Namespace,
    run_value: object,
    jobs_value: object,
    artifacts_value: object,
    archive_bytes: bytes,
    *,
    ref_sha: str,
    changed_paths: list[str],
) -> dict:
    try:
        run_id = int(args.run_id)
        run_attempt = int(args.run_attempt)
    except (TypeError, ValueError) as error:
        raise EvidenceError("run identity is malformed") from error
    if run_id <= 0 or run_attempt <= 0 or not SHA_RE.fullmatch(args.candidate_sha):
        raise EvidenceError("run identity or candidate SHA is malformed")
    if run_attempt != 1:
        raise EvidenceError("same-V rerun attempt is forbidden")

    run = _require_mapping(run_value, "run")
    expected_artifact = f"proofline-candidate-{run_id}-{run_attempt}"
    if run.get("id") != run_id or run.get("run_attempt") != run_attempt:
        raise EvidenceError("run identity or attempt mismatch")
    branch = run.get("head_branch")
    branch_match = CANDIDATE_BRANCH_RE.fullmatch(branch) if isinstance(branch, str) else None
    if run.get("event") != "push" or branch_match is None:
        raise EvidenceError("candidate event or branch mismatch")
    if run.get("path") != WORKFLOW:
        raise EvidenceError("workflow path mismatch")
    if run.get("head_sha") != args.candidate_sha:
        raise EvidenceError("head SHA mismatch")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise EvidenceError("run is not terminal success")

    jobs_payload = _require_mapping(jobs_value, "jobs payload")
    jobs = jobs_payload.get("jobs")
    if not isinstance(jobs, list) or jobs_payload.get("total_count", len(jobs)) != len(jobs):
        raise EvidenceError("required jobs response is incomplete")
    names = [job.get("name") if isinstance(job, dict) else None for job in jobs]
    if len(names) != len(set(names)) or set(names) != set(REQUIRED_JOBS):
        raise EvidenceError("required jobs are missing or duplicated")
    for job in jobs:
        if not isinstance(job, dict) or not isinstance(job.get("id"), int) or job["id"] <= 0:
            raise EvidenceError("required job identity is malformed")
        if job.get("run_attempt") != run_attempt:
            raise EvidenceError("required job attempt mismatch")
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            raise EvidenceError("required jobs are not successful")

    artifacts_payload = _require_mapping(artifacts_value, "artifacts payload")
    artifacts = artifacts_payload.get("artifacts")
    if not isinstance(artifacts, list) or artifacts_payload.get("total_count", len(artifacts)) != len(artifacts):
        raise EvidenceError("artifact response is incomplete")
    matches = [item for item in artifacts if isinstance(item, dict) and item.get("name") == expected_artifact]
    if len(matches) != 1:
        raise EvidenceError("candidate artifact is missing or duplicated")
    artifact = matches[0]
    if not isinstance(artifact.get("id"), int) or artifact["id"] <= 0:
        raise EvidenceError("candidate artifact identity is malformed")
    if artifact.get("expired") is not False:
        raise EvidenceError("candidate artifact is expired")
    expires_at = artifact.get("expires_at")
    if not isinstance(expires_at, str) or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", expires_at
    ) is None:
        raise EvidenceError("candidate artifact expiry is malformed")
    try:
        expiry = datetime.fromisoformat(expires_at[:-1] + "+00:00")
    except ValueError as error:
        raise EvidenceError("candidate artifact expiry is malformed") from error
    if expiry <= datetime.now(timezone.utc):
        raise EvidenceError("candidate artifact is expired")
    workflow_run = artifact.get("workflow_run")
    if not isinstance(workflow_run, dict) or workflow_run.get("id") != run_id or workflow_run.get("head_sha") != args.candidate_sha:
        raise EvidenceError("candidate artifact identity mismatch")

    if ref_sha != args.candidate_sha:
        raise EvidenceError("candidate ref drift")
    changed = set(changed_paths)
    if len(changed_paths) > 1 or any(
        (match := DQC_PATH_RE.fullmatch(path)) is None
        or match.group("line") != branch_match.group("line")
        for path in changed_paths
    ):
        raise EvidenceError("stale evidence: post-candidate changes are not DQC-only")

    download = Path(args.download_dir)
    _safe_extract_archive(archive_bytes, download)
    extracted = {item.name for item in download.iterdir() if item.is_file() and not item.is_symlink()}
    wheels = sorted(name for name in extracted if name.endswith(".whl"))
    if len(wheels) != 1 or not WHEEL_RE.fullmatch(wheels[0]):
        raise EvidenceError("artifact must contain exactly one valid wheel")
    wheel_name = wheels[0]
    if extracted != {wheel_name, "SHA256SUMS", "CANDIDATE_PROVENANCE.json"}:
        raise EvidenceError("artifact contains missing or unexpected files")

    try:
        provenance = json.loads((download / "CANDIDATE_PROVENANCE.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EvidenceError("malformed provenance") from error
    if not isinstance(provenance, dict) or set(provenance) != PROVENANCE_KEYS or provenance.get("schema_version") != 1:
        raise EvidenceError("malformed provenance schema")
    expected_identity = (
        args.candidate_sha,
        run_id,
        run_attempt,
        WORKFLOW,
        expected_artifact,
        wheel_name,
    )
    actual_identity = (
        provenance.get("candidate_sha"),
        provenance.get("run_id"),
        provenance.get("run_attempt"),
        provenance.get("workflow_path"),
        provenance.get("artifact_name"),
        provenance.get("wheel_filename"),
    )
    if actual_identity != expected_identity:
        raise EvidenceError("provenance identity mismatch")

    try:
        records = (download / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as error:
        raise EvidenceError("malformed checksum") from error
    if len(records) != 1:
        raise EvidenceError("checksum must contain exactly one record")
    match = CHECKSUM_RE.fullmatch(records[0])
    if match is None or match.group(2) != wheel_name:
        raise EvidenceError("malformed checksum record")
    digest = hashlib.sha256((download / wheel_name).read_bytes()).hexdigest()
    if match.group(1).lower() != digest or provenance.get("wheel_sha256") != digest:
        raise EvidenceError("wheel SHA-256 mismatch")

    return {
        "candidate_sha": args.candidate_sha,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "workflow_path": WORKFLOW,
        "required_jobs": {
            job["name"]: {"id": job["id"], "conclusion": job["conclusion"]}
            for job in sorted(jobs, key=lambda item: item["name"])
        },
        "artifact": {"id": artifact["id"], "name": artifact["name"], "expires_at": expires_at},
        "wheel": {"filename": wheel_name, "sha256": digest},
    }


def _collect_and_validate(args: argparse.Namespace) -> dict:
    run = _gh_json(args.repository, f"actions/runs/{args.run_id}")
    jobs = _gh_json(
        args.repository,
        f"actions/runs/{args.run_id}/attempts/{args.run_attempt}/jobs?per_page=100",
    )
    artifacts = _gh_json(args.repository, f"actions/runs/{args.run_id}/artifacts?per_page=100")
    run_mapping = _require_mapping(run, "run")
    branch = run_mapping.get("head_branch")
    if not isinstance(branch, str) or not branch.startswith("candidate/"):
        raise EvidenceError("candidate event or branch mismatch")
    ref = _gh_json(args.repository, f"git/ref/heads/{quote(branch, safe='')}")
    ref_mapping = _require_mapping(ref, "candidate ref")
    ref_object = ref_mapping.get("object")
    if not isinstance(ref_object, dict) or not isinstance(ref_object.get("sha"), str):
        raise EvidenceError("candidate ref identity is malformed")
    artifacts_mapping = _require_mapping(artifacts, "artifacts payload")
    artifact_items = artifacts_mapping.get("artifacts")
    expected_name = f"proofline-candidate-{args.run_id}-{args.run_attempt}"
    matches = [
        item for item in artifact_items or []
        if isinstance(item, dict) and item.get("name") == expected_name
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("id"), int):
        raise EvidenceError("candidate artifact is missing or duplicated")
    archive = _download_artifact_zip(args.repository, matches[0]["id"])
    return _validate_payload(
        args,
        run,
        jobs,
        artifacts,
        archive,
        ref_sha=ref_object["sha"],
        changed_paths=_git_changed_paths(args.candidate_sha),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--download-dir", required=True)
    args = parser.parse_args(argv)
    try:
        evidence = _collect_and_validate(args)
    except (EvidenceError, KeyError, TypeError, ValueError) as error:
        print(f"candidate evidence rejected: {error}", file=sys.stderr)
        return 1
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
