#!/usr/bin/env python3
"""Execute the fixed Line-0021 clean-runner registry across source and wheel bytes."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path
from typing import Any

HELPER_MEMBER = "proofline_home/skills/proofline-run-dqc/scripts/preflight_clean_runner.py"
PLAN_MEMBER = "proofline_home/skills/proofline-run-dqc/resources/candidate-clean-runner-plan-v1.json"
SOURCE_HELPER = Path("skills/proofline-run-dqc/scripts/preflight_clean_runner.py")
SOURCE_PLAN = Path("skills/proofline-run-dqc/resources/candidate-clean-runner-plan-v1.json")
APPROVED_SCENARIO_IDS = (
    "valid-baseline",
    "provenance-schema",
    "provenance-duplicate",
    "provenance-type",
    "candidate-binding",
    "wheel-count",
    "wheel-filename",
    "wheel-digest",
    "plan-endpoint",
    "plan-version-source",
    "plan-network",
    "plan-publication",
    "plan-unbounded",
    "plan-undeclared",
    "online-empty-cache",
    "online-warm-cache",
    "offline-complete",
    "offline-missing",
    "offline-network-trap",
    "execution-timeout",
    "execution-output-cap",
    "protected-mutation",
    "authority-schema",
)
OUTCOME_KEYS = {
    "schema_version",
    "outcome",
    "diagnostic_code",
    "candidate_commit",
    "wheel_filename",
    "wheel_sha256",
    "network_mode",
    "plan_id",
}
DEPENDENCIES = {
    "colorama": "0.4.6",
    "iniconfig": "2.3.0",
    "packaging": "26.2",
    "pluggy": "1.6.0",
    "pygments": "2.20.0",
    "pytest": "9.1.1",
}


def _git(repo: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(
        {
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
        }
    )
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


def _tree(root: Path) -> tuple[tuple[str, str, str], ...]:
    if not root.exists():
        return ()
    records: list[tuple[str, str, str]] = []
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts:
            continue
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            records.append((relative, "link", os.readlink(path)))
        elif path.is_dir():
            records.append((relative, "dir", ""))
        else:
            records.append((relative, "file", hashlib.sha256(path.read_bytes()).hexdigest()))
    return tuple(records)


def _snapshot(case: dict[str, Any]) -> dict[str, Any]:
    repo = case["repo"]
    paths = {
        "repo": _tree(repo),
        "artifacts": _tree(case["artifacts"]),
        "wheelhouse": _tree(case["wheelhouse"]),
        "ambient_cache": _tree(case["ambient_cache"]),
        "ambient_home": _tree(case["ambient_home"]),
    }
    return {
        **paths,
        "head": _git(repo, "rev-parse", "HEAD"),
        "symbolic_head": _git(repo, "symbolic-ref", "-q", "HEAD"),
        "index": _git(repo, "ls-files", "--stage"),
        "status": _git(repo, "status", "--porcelain=v1", "--untracked-files=all"),
        "refs": _git(repo, "for-each-ref", "--format=%(refname):%(objectname)"),
        "objects": _git(
            repo,
            "cat-file",
            "--batch-all-objects",
            "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        ),
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, separators=(",", ":"), sort_keys=True), encoding="utf-8")


def _make_fake_uv(path: Path, *, behavior: str, mutation_target: Path) -> None:
    body = f"""\
    #!{sys.executable}
    import os
    import pathlib
    import sys
    import time

    behavior = {behavior!r}
    if behavior == "timeout":
        time.sleep(60)
    if behavior == "output_cap":
        os.write(1, b"x" * 8192)
    if behavior == "mutation":
        pathlib.Path({str(mutation_target)!r}).write_text("mutated\\n", encoding="utf-8")
    if sys.argv[1:2] == ["venv"]:
        target = pathlib.Path(sys.argv[-1])
        python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("fixture python\\n", encoding="utf-8")
    if behavior == "network_trap" and "--offline" in sys.argv:
        print("clean_preflight.network.forbidden", file=sys.stderr)
        raise SystemExit(86)
    """
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _make_case(base: Path, plan_bytes: bytes, axis: str) -> dict[str, Any]:
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    repo = base / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "ProofLine Registry")
    _git(repo, "config", "user.email", "proofline@example.invalid")
    tracked = repo / "tracked.txt"
    tracked.write_text("candidate\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "candidate")
    candidate = _git(repo, "rev-parse", "HEAD")

    artifacts = base / "artifacts"
    artifacts.mkdir()
    wheel = artifacts / "proofline-0.6.1-py3-none-any.whl"
    wheel.write_bytes(b"exact candidate wheel\n")
    provenance = artifacts / "provenance.json"
    provenance_value = {
        "schema_version": 1,
        "candidate_commit": candidate,
        "wheel_filename": wheel.name,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
    }
    _write_json(provenance, provenance_value)
    plan = artifacts / "candidate-clean-runner-plan-v1.json"
    plan.write_bytes(plan_bytes)
    wheelhouse = base / "wheelhouse"
    wheelhouse.mkdir()
    for name, version in DEPENDENCIES.items():
        (wheelhouse / f"{name}-{version}-py3-none-any.whl").write_bytes(
            f"fixture {name} {version}\n".encode()
        )
    ambient_cache = base / "ambient-cache"
    ambient_cache.mkdir()
    if axis == "warm_cache":
        (ambient_cache / "warm-only.whl").write_bytes(b"ambient cache must be ignored\n")
    ambient_home = base / "ambient-home"
    ambient_home.mkdir()
    (ambient_home / "sentinel.txt").write_bytes(b"ambient home must be preserved\n")

    plan_value = json.loads(plan.read_text(encoding="utf-8"))
    network_mode = "offline" if axis.startswith("offline_") else "online"
    behavior = "normal"
    if axis == "provenance_schema":
        del provenance_value["wheel_filename"]
        _write_json(provenance, provenance_value)
    elif axis == "provenance_duplicate":
        provenance.write_text(
            '{"schema_version":1,"schema_version":1,"candidate_commit":"x",'
            '"wheel_filename":"x","wheel_sha256":"x"}',
            encoding="utf-8",
        )
    elif axis == "provenance_type":
        provenance_value["schema_version"] = True
        _write_json(provenance, provenance_value)
    elif axis == "candidate":
        candidate = "0" * len(candidate)
    elif axis == "wheel_count":
        (artifacts / "proofline-0.6.2-py3-none-any.whl").write_bytes(b"second\n")
    elif axis == "wheel_filename":
        provenance_value["wheel_filename"] = "proofline-0.6.2-py3-none-any.whl"
        _write_json(provenance, provenance_value)
    elif axis == "wheel_digest":
        provenance_value["wheel_sha256"] = "0" * 64
        _write_json(provenance, provenance_value)
    elif axis == "plan_endpoint":
        plan_value["platforms"]["ubuntu-python311"]["steps"][0]["endpoint"] = "https://example.invalid/simple"
        _write_json(plan, plan_value)
    elif axis == "plan_version_source":
        plan_value["platforms"]["ubuntu-python311"]["steps"][0]["version_source"] = "requirements.txt"
        _write_json(plan, plan_value)
    elif axis == "plan_network":
        plan_value["platforms"]["ubuntu-python311"]["steps"][0]["network_mode"] = "ambient"
        _write_json(plan, plan_value)
    elif axis == "plan_publication":
        plan_value["platforms"]["ubuntu-python311"]["steps"][0]["publication_prerequisite"] = "release"
        _write_json(plan, plan_value)
    elif axis == "plan_unbounded":
        plan_value["platforms"]["ubuntu-python311"]["steps"][0]["argv"].append("proofline-*.whl")
        _write_json(plan, plan_value)
    elif axis == "plan_undeclared":
        plan_value["platforms"]["ubuntu-python311"]["steps"][2]["argv"].append("ruff==1.0.0")
        _write_json(plan, plan_value)
    elif axis == "offline_missing":
        (wheelhouse / "pytest-9.1.1-py3-none-any.whl").unlink()
    elif axis == "offline_network_trap":
        behavior = "network_trap"
    elif axis in {"timeout", "output_cap", "mutation"}:
        behavior = axis
    elif axis == "authority_schema":
        plan_value["hosted_result"] = "passed"
        _write_json(plan, plan_value)

    fake_bin = base / "fake-bin"
    fake_bin.mkdir()
    _make_fake_uv(fake_bin / "uv", behavior=behavior, mutation_target=tracked)
    return {
        "repo": repo,
        "tracked": tracked,
        "candidate": candidate,
        "artifacts": artifacts,
        "wheel": wheel,
        "provenance": provenance,
        "plan": plan,
        "wheelhouse": wheelhouse,
        "ambient_cache": ambient_cache,
        "ambient_home": ambient_home,
        "fake_bin": fake_bin,
        "network_mode": network_mode,
        "axis": axis,
    }


def _load_helper(path: Path, tag: str):
    spec = importlib.util.spec_from_file_location(f"line_0021_clean_runner_{tag}", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _execute(helper_path: Path, case: dict[str, Any], expected: list[Any], tag: str) -> dict[str, Any]:
    helper = _load_helper(helper_path, tag)
    helper.os.defpath = str(case["fake_bin"])
    original_budget = helper.ExecutionBudget
    if case["axis"] == "timeout":
        helper.ExecutionBudget = lambda: original_budget(seconds=0.25, output_limit=4096)
    elif case["axis"] == "output_cap":
        helper.ExecutionBudget = lambda: original_budget(seconds=5, output_limit=1024)
    old_cache = os.environ.get("UV_CACHE_DIR")
    old_home = os.environ.get("HOME")
    old_userprofile = os.environ.get("USERPROFILE")
    os.environ["UV_CACHE_DIR"] = str(case["ambient_cache"])
    os.environ["HOME"] = str(case["ambient_home"])
    os.environ["USERPROFILE"] = str(case["ambient_home"])
    argv = [
        "--repo", str(case["repo"].resolve()),
        "--candidate", case["candidate"],
        "--wheel", str(case["wheel"].resolve()),
        "--provenance", str(case["provenance"].resolve()),
        "--plan", str(case["plan"].resolve()),
        "--network-mode", case["network_mode"],
    ]
    if case["network_mode"] == "offline":
        argv.extend(["--wheelhouse", str(case["wheelhouse"].resolve())])
    stdout = io.StringIO()
    stderr = io.StringIO()
    before = _snapshot(case)
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            returncode = helper.main(argv)
    finally:
        for key, old_value in (
            ("UV_CACHE_DIR", old_cache),
            ("HOME", old_home),
            ("USERPROFILE", old_userprofile),
        ):
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value
    after = _snapshot(case)
    out = stdout.getvalue()
    err = stderr.getvalue()
    lines = out.splitlines()
    if len(lines) != 1:
        raise AssertionError(f"{tag}: expected one stdout line, got {lines!r}")
    outcome = json.loads(lines[0])
    if set(outcome) != OUTCOME_KEYS:
        raise AssertionError(f"{tag}: outcome keys differ: {set(outcome)!r}")
    if (returncode, outcome["diagnostic_code"]) != tuple(expected):
        raise AssertionError(
            f"{tag}: expected {tuple(expected)!r}, got {(returncode, outcome['diagnostic_code'])!r}"
        )
    if outcome["outcome"] != ("pass" if returncode == 0 else "fail"):
        raise AssertionError(f"{tag}: outcome/returncode mismatch")
    return {
        "returncode": returncode,
        "stdout": out,
        "stderr": err,
        "before": before,
        "after": after,
    }


def _validate_registry(path: Path) -> dict[str, Any]:
    registry = json.loads(path.read_text(encoding="utf-8"))
    if set(registry) != {"schema_version", "scenario_count", "scenarios"}:
        raise AssertionError("registry schema is not exact")
    scenarios = registry["scenarios"]
    ids = tuple(case["id"] for case in scenarios)
    if registry["schema_version"] != 1 or registry["scenario_count"] != 23:
        raise AssertionError("registry identity/cardinality differs")
    if ids != APPROVED_SCENARIO_IDS or len(set(ids)) != 23:
        raise AssertionError("registry fixed ID set/order differs")
    axes = [case["axis"] for case in scenarios]
    if len(set(axes)) != 23:
        raise AssertionError("registry scenarios do not isolate one unique axis")
    for case in scenarios:
        if set(case) != {"id", "axis", "expect"}:
            raise AssertionError(f"scenario schema differs: {case['id']}")
        if case["expect"][0] not in {0, 1} or not case["expect"][1].startswith("clean_preflight."):
            raise AssertionError(f"scenario expectation differs: {case['id']}")
    return registry


def run_registry(*, root: Path, registry_path: Path, wheel: Path, workspace: Path) -> dict[str, Any]:
    root = root.resolve()
    wheel = wheel.resolve()
    registry = _validate_registry(registry_path)
    source_helper = root / SOURCE_HELPER
    source_plan = root / SOURCE_PLAN
    extracted = workspace.resolve() / "extracted-wheel"
    extracted.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel) as archive:
        helper_bytes = archive.read(HELPER_MEMBER)
        plan_bytes = archive.read(PLAN_MEMBER)
    wheel_helper = extracted / HELPER_MEMBER
    wheel_plan = extracted / PLAN_MEMBER
    wheel_helper.parent.mkdir(parents=True, exist_ok=True)
    wheel_plan.parent.mkdir(parents=True, exist_ok=True)
    wheel_helper.write_bytes(helper_bytes)
    wheel_plan.write_bytes(plan_bytes)
    byte_parity = (
        helper_bytes == source_helper.read_bytes()
        and plan_bytes == source_plan.read_bytes()
    )
    if not byte_parity:
        raise AssertionError("source/wheel helper or resource bytes differ")

    results: list[dict[str, Any]] = []
    no_unexpected_mutation = True
    for index, scenario in enumerate(registry["scenarios"]):
        scenario_root = workspace.resolve() / f"{index:02d}-{scenario['id']}"
        source_case = _make_case(scenario_root, source_plan.read_bytes(), scenario["axis"])
        source = _execute(source_helper, source_case, scenario["expect"], f"source-{index}")
        shutil.rmtree(scenario_root)
        wheel_case = _make_case(scenario_root, wheel_plan.read_bytes(), scenario["axis"])
        installed = _execute(wheel_helper, wheel_case, scenario["expect"], f"wheel-{index}")
        source_tuple = (source["returncode"], source["stdout"], source["stderr"])
        wheel_tuple = (installed["returncode"], installed["stdout"], installed["stderr"])
        if source_tuple != wheel_tuple:
            raise AssertionError(f"{scenario['id']}: exact returncode/stdout/stderr parity differs")
        if source["before"] != installed["before"] or source["after"] != installed["after"]:
            raise AssertionError(f"{scenario['id']}: exact input/repository snapshot parity differs")
        expected_mutation = scenario["axis"] == "mutation"
        source_mutated = source["before"] != source["after"]
        wheel_mutated = installed["before"] != installed["after"]
        if source_mutated != expected_mutation or wheel_mutated != expected_mutation:
            no_unexpected_mutation = False
            raise AssertionError(f"{scenario['id']}: mutation contract differs")
        results.append({"id": scenario["id"], "diagnostic_code": scenario["expect"][1]})

    return {
        "scenario_count": len(results),
        "source_count": len(results),
        "wheel_count": len(results),
        "packaged_helper_count": len(results),
        "byte_parity": byte_parity,
        "no_unexpected_mutation": no_unexpected_mutation,
        "wheel": str(wheel),
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "helper_member": HELPER_MEMBER,
        "plan_member": PLAN_MEMBER,
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args(argv)
    evidence = run_registry(
        root=args.root,
        registry_path=args.registry,
        wheel=args.wheel,
        workspace=args.workspace,
    )
    print(json.dumps(evidence, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
