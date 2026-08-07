from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname
import zipfile

import yaml

from proofline import home_writer


PREDECESSOR_VERSION = "0.6.0"
PREDECESSOR_WHEEL = "proofline-0.6.0-py3-none-any.whl"
PREDECESSOR_WHEEL_SHA256 = "e17fadeb8cc6bee5eef912cf3b0af97881a128280895ed58e8625cc23ec0ab06"
BACKUP_NAME = ".proofline.backup-v0.6.0"
_LEGACY_GROUPS = {"contracts", "templates", "skills"}
_STABLE_VERSION = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\Z")


class InstallerTransitionError(RuntimeError):
    pass


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _expected_directories(payload: dict[str, bytes]) -> set[str]:
    directories: set[str] = set()
    for relative in payload:
        parent = Path(relative).parent
        while parent != Path("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _verify_tree(root: Path, payload: dict[str, bytes]) -> None:
    if root.is_symlink() or not root.is_dir():
        raise InstallerTransitionError(f"managed HOME is not a directory: {root}")
    files: dict[str, bytes] = {}
    directories: set[str] = set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        value = path.stat(follow_symlinks=False)
        if stat.S_ISLNK(value.st_mode):
            raise InstallerTransitionError(f"managed HOME contains symlink: {relative}")
        if stat.S_ISDIR(value.st_mode):
            directories.add(relative)
        elif stat.S_ISREG(value.st_mode):
            files[relative] = path.read_bytes()
        else:
            raise InstallerTransitionError(f"managed HOME contains unexpected path type: {relative}")
    if set(files) != set(payload) or directories != _expected_directories(payload):
        raise InstallerTransitionError("managed HOME inventory differs from the exact predecessor archive")
    for relative, content in payload.items():
        if files[relative] != content:
            raise InstallerTransitionError(f"managed HOME bytes differ: {relative}")


def _legacy_payload(wheel: Path) -> dict[str, bytes]:
    resources: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(wheel) as archive:
            seen: set[str] = set()
            for info in archive.infolist():
                if info.filename in seen:
                    raise InstallerTransitionError(f"duplicate predecessor wheel entry: {info.filename}")
                seen.add(info.filename)
                if not info.filename.startswith("proofline_home/") or info.is_dir():
                    continue
                relative = info.filename.removeprefix("proofline_home/")
                if relative == "__init__.py":
                    continue
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise InstallerTransitionError(f"predecessor wheel resource is a symlink: {relative}")
                candidate = Path(relative)
                if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
                    raise InstallerTransitionError(f"unsafe predecessor wheel resource: {relative}")
                resources[relative] = archive.read(info)
    except (OSError, zipfile.BadZipFile) as exc:
        raise InstallerTransitionError(f"invalid predecessor wheel: {exc}") from exc

    groups = {Path(path).parts[0] for path in resources if path != "agent-context.md"}
    if "agent-context.md" not in resources or groups != _LEGACY_GROUPS:
        raise InstallerTransitionError("predecessor wheel HOME inventory is incomplete or unexpected")
    records = [
        {"path": path, "sha256": hashlib.sha256(content).hexdigest()}
        for path, content in sorted(resources.items())
    ]
    manifest = {
        "schema_version": 1,
        "proofline_version": PREDECESSOR_VERSION,
        "source": {"type": "packaged-resource"},
        "managed_files": records,
    }
    resources["manifest.yaml"] = yaml.safe_dump(
        manifest, sort_keys=False, allow_unicode=True
    ).encode("utf-8")
    return resources


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def _probe(python: Path, *, cwd: Path) -> dict[str, Any]:
    code = (
        "import json; from importlib.metadata import distribution; from pathlib import Path; import proofline; "
        "d=distribution('proofline'); print(json.dumps({'version':d.version,'direct_url':json.loads("
        "d.read_text('direct_url.json') or 'null'),'module':str(Path(proofline.__file__).resolve())}))"
    )
    completed = _run([str(python), "-I", "-c", code], cwd=cwd)
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise InstallerTransitionError("installed package provenance probe failed") from exc
    if completed.returncode or not isinstance(value, dict):
        raise InstallerTransitionError("installed package provenance probe failed")
    return value


def _verify_archive_install(
    python: Path, version: str, wheel_digest: str, *, cwd: Path
) -> None:
    value = _probe(python, cwd=cwd)
    direct_url = value.get("direct_url")
    archive = direct_url.get("archive_info") if isinstance(direct_url, dict) else None
    hashes = archive.get("hashes") if isinstance(archive, dict) else None
    legacy_hash = archive.get("hash") if isinstance(archive, dict) else None
    observed = hashes.get("sha256") if isinstance(hashes, dict) else None
    if observed is None and isinstance(legacy_hash, str) and legacy_hash.startswith("sha256="):
        observed = legacy_hash.removeprefix("sha256=")
    module = value.get("module")
    url = direct_url.get("url") if isinstance(direct_url, dict) else None
    parsed = urlparse(url) if isinstance(url, str) else None
    wheel_path = (
        Path(url2pathname(unquote(parsed.path))).resolve()
        if parsed is not None and parsed.scheme == "file" and parsed.netloc in {"", "localhost"}
        else None
    )
    try:
        direct_digest = _digest(wheel_path) if wheel_path is not None else None
    except OSError:
        direct_digest = None
    environment = python.parent.parent.resolve()
    if (
        value.get("version") != version
        or direct_digest != wheel_digest
        or (observed is not None and observed != wheel_digest)
        or not isinstance(module, str)
        or "site-packages" not in Path(module).parts
        or not Path(module).resolve().is_relative_to(environment)
    ):
        raise InstallerTransitionError("installed package is not the exact checksum-bound archive")


def _install(uv: str, wheel: Path, *, cwd: Path) -> None:
    completed = _run(
        [uv, "tool", "install", "--force", "--no-config", str(wheel)], cwd=cwd
    )
    if completed.returncode:
        raise InstallerTransitionError("uv tool install failed")


def _target_version(target_wheel: Path) -> str:
    version = metadata.version("proofline")
    if (
        _STABLE_VERSION.fullmatch(version) is None
        or version in {"0.6.1", "0.6.2"}
        or tuple(map(int, version.split("."))) <= (0, 6, 2)
    ):
        raise InstallerTransitionError("target is not a future exact corrective release")
    expected = f"proofline-{version}-py3-none-any.whl"
    if target_wheel.name != expected:
        raise InstallerTransitionError("target wheel filename does not match target package")
    direct_url = metadata.distribution("proofline").read_text("direct_url.json")
    try:
        provenance = json.loads(direct_url or "")
    except json.JSONDecodeError as exc:
        raise InstallerTransitionError("target staging provenance is invalid") from exc
    archive = provenance.get("archive_info") if isinstance(provenance, dict) else None
    hashes = archive.get("hashes") if isinstance(archive, dict) else None
    legacy_hash = archive.get("hash") if isinstance(archive, dict) else None
    observed = hashes.get("sha256") if isinstance(hashes, dict) else None
    if observed is None and isinstance(legacy_hash, str) and legacy_hash.startswith("sha256="):
        observed = legacy_hash.removeprefix("sha256=")
    url = provenance.get("url") if isinstance(provenance, dict) else None
    parsed = urlparse(url) if isinstance(url, str) else None
    staged_path = (
        Path(url2pathname(unquote(parsed.path))).resolve()
        if parsed is not None and parsed.scheme == "file" and parsed.netloc in {"", "localhost"}
        else None
    )
    if (
        not isinstance(archive, dict)
        or (observed is not None and observed != _digest(target_wheel))
        or staged_path != target_wheel
    ):
        raise InstallerTransitionError("transition core is not running from the staged target archive")
    return version


def _tool_paths(uv: str, *, cwd: Path) -> tuple[Path, Path]:
    tool = _run([uv, "tool", "dir"], cwd=cwd)
    bins = _run([uv, "tool", "dir", "--bin"], cwd=cwd)
    if tool.returncode or bins.returncode:
        raise InstallerTransitionError("uv tool directory lookup failed")
    root = Path(tool.stdout.strip()).resolve() / "proofline"
    python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable = Path(bins.stdout.strip()).resolve() / (
        "proofline.exe" if os.name == "nt" else "proofline"
    )
    return python, executable


def _verify_console(executable: Path, version: str, *, cwd: Path) -> None:
    completed = _run(
        [str(executable), "--no-home-reconcile", "--version"], cwd=cwd
    )
    if completed.returncode or completed.stdout.strip() != f"proofline {version}":
        raise InstallerTransitionError("installed console verification failed")


def _rename_no_replace(source: Path, destination: Path) -> None:
    try:
        home_writer._commit_directory(source, destination)
    except (home_writer.HomeInitError, OSError) as exc:
        raise InstallerTransitionError(f"no-overwrite directory commit failed: {exc}") from exc


def run_transition(
    *, target_wheel: Path, predecessor_wheel: Path, home: Path, uv: str
) -> str:
    target_wheel = target_wheel.resolve()
    predecessor_wheel = predecessor_wheel.resolve()
    home = home.resolve()
    if predecessor_wheel.name != PREDECESSOR_WHEEL or _digest(predecessor_wheel) != PREDECESSOR_WHEEL_SHA256:
        raise InstallerTransitionError("predecessor wheel identity mismatch")
    target_version = _target_version(target_wheel)
    target_digest = _digest(target_wheel)
    legacy_payload = _legacy_payload(predecessor_wheel)
    target_payload = home_writer._payload()
    try:
        manifest_version = yaml.safe_load(target_payload["manifest.yaml"])[
            "proofline_version"
        ]
    except (KeyError, TypeError, yaml.YAMLError) as exc:
        raise InstallerTransitionError("target-owned HOME manifest is invalid") from exc
    if manifest_version != target_version:
        raise InstallerTransitionError("target-owned HOME manifest version mismatch")
    target = home / ".proofline"
    backup = home / BACKUP_NAME
    if backup.exists() or backup.is_symlink():
        raise InstallerTransitionError(f"deterministic backup already exists: {backup}")
    _verify_tree(target, legacy_payload)

    work = Path(tempfile.mkdtemp(prefix=".proofline-transition-", dir=home))
    stage = work / "target-home"
    stage.mkdir()
    cleanup_work = True
    try:
        home_writer._write_stage(stage, target_payload)
        _verify_tree(stage, target_payload)
        tool_python, executable = _tool_paths(uv, cwd=work)
        _verify_archive_install(
            tool_python, PREDECESSOR_VERSION, PREDECESSOR_WHEEL_SHA256, cwd=work
        )

        package_attempted = False
        backup_committed = False
        home_committed = False
        try:
            package_attempted = True
            _install(uv, target_wheel, cwd=work)
            _verify_archive_install(tool_python, target_version, target_digest, cwd=work)
            _verify_console(executable, target_version, cwd=work)
            _rename_no_replace(target, backup)
            backup_committed = True
            _rename_no_replace(stage, target)
            home_committed = True
            _verify_tree(target, target_payload)
            _verify_tree(backup, legacy_payload)
            _verify_archive_install(tool_python, target_version, target_digest, cwd=work)
            _verify_console(executable, target_version, cwd=work)
        except Exception as exc:
            failures: list[str] = []
            home_rolled_back = False
            target_recovered = False
            try:
                if home_committed:
                    _rename_no_replace(target, stage)
                if backup_committed:
                    _rename_no_replace(backup, target)
                _verify_tree(target, legacy_payload)
                home_rolled_back = True
            except Exception as rollback_exc:
                failures.append(f"HOME rollback failed: {rollback_exc}")
                try:
                    if stage.is_dir() and not target.exists():
                        _rename_no_replace(stage, target)
                    _verify_tree(target, target_payload)
                    _verify_archive_install(
                        tool_python, target_version, target_digest, cwd=work
                    )
                    _verify_console(executable, target_version, cwd=work)
                    target_recovered = True
                except Exception as recovery_exc:
                    cleanup_work = False
                    failures.append(f"target coherence recovery failed: {recovery_exc}")
            if package_attempted and home_rolled_back:
                try:
                    _install(uv, predecessor_wheel, cwd=work)
                    _verify_archive_install(
                        tool_python,
                        PREDECESSOR_VERSION,
                        PREDECESSOR_WHEEL_SHA256,
                        cwd=work,
                    )
                    _verify_console(executable, PREDECESSOR_VERSION, cwd=work)
                except Exception as rollback_exc:
                    failures.append(f"package rollback failed: {rollback_exc}")
                    recovery_backup = False
                    recovery_home = False
                    try:
                        _install(uv, target_wheel, cwd=work)
                        _verify_archive_install(
                            tool_python, target_version, target_digest, cwd=work
                        )
                        _verify_console(executable, target_version, cwd=work)
                        _verify_tree(target, legacy_payload)
                        if stage.is_dir() and target.is_dir():
                            _rename_no_replace(target, backup)
                            recovery_backup = True
                            _rename_no_replace(stage, target)
                            recovery_home = True
                        _verify_tree(target, target_payload)
                        _verify_tree(backup, legacy_payload)
                        target_recovered = True
                    except Exception as recovery_exc:
                        failures.append(
                            f"target coherence recovery failed: {recovery_exc}"
                        )
                        try:
                            if recovery_home:
                                _rename_no_replace(target, stage)
                            if recovery_backup:
                                _rename_no_replace(backup, target)
                            _verify_tree(target, legacy_payload)
                            _install(uv, predecessor_wheel, cwd=work)
                            _verify_archive_install(
                                tool_python,
                                PREDECESSOR_VERSION,
                                PREDECESSOR_WHEEL_SHA256,
                                cwd=work,
                            )
                            _verify_console(
                                executable, PREDECESSOR_VERSION, cwd=work
                            )
                        except Exception as retry_exc:
                            cleanup_work = False
                            failures.append(
                                f"previous coherence retry failed: {retry_exc}"
                            )
            elif package_attempted and not target_recovered:
                failures.append("package rollback skipped because HOME state is not coherent")
            detail = f"corrective transition failed: {exc}"
            if failures:
                detail += "; " + "; ".join(failures)
            raise InstallerTransitionError(detail) from exc
    finally:
        if cleanup_work and work.exists() and not work.is_symlink():
            shutil.rmtree(work)
    return target_version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ProofLine exact corrective installer transition")
    parser.add_argument("--target-wheel", type=Path, required=True)
    parser.add_argument("--predecessor-wheel", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--uv", required=True)
    args = parser.parse_args(argv)
    try:
        version = run_transition(
            target_wheel=args.target_wheel,
            predecessor_wheel=args.predecessor_wheel,
            home=args.home,
            uv=args.uv,
        )
    except InstallerTransitionError as exc:
        print(f"ProofLine corrective installer: {exc}", file=sys.stderr)
        return 1
    print(f"ProofLine corrective transition completed: {PREDECESSOR_VERSION} -> {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
