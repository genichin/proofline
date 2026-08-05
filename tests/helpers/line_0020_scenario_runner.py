from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ScenarioRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class ExpectedResult:
    passed: bool
    diagnostic_code: str


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    expected: ExpectedResult


@dataclass(frozen=True)
class Registry:
    schema_version: int
    registry_id: str
    scenarios: tuple[Scenario, ...]


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    passed: bool
    diagnostic_code: str


@dataclass(frozen=True)
class ArtifactRun:
    module_path: str
    results: tuple[ScenarioResult, ...]

    @property
    def ids(self) -> set[str]:
        return {result.scenario_id for result in self.results}


@dataclass(frozen=True)
class CrossArtifactEvidence:
    source: ArtifactRun
    wheel: ArtifactRun
    expected_results: tuple[ScenarioResult, ...]
    wheel_path: str
    wheel_sha256: str
    packaged_scripts_byte_equal: bool
    packaged_script_ids: set[str]
    all_no_mutation_checks_passed: bool


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ScenarioRegistryError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_registry(path: Path) -> Registry:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ScenarioRegistryError(f"invalid strict JSON registry: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "registry_id", "scenarios"}:
        raise ScenarioRegistryError("registry must contain only schema_version, registry_id, scenarios")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ScenarioRegistryError("unsupported schema_version")
    if not isinstance(payload["registry_id"], str) or not payload["registry_id"]:
        raise ScenarioRegistryError("registry_id must be a non-empty string")
    if not isinstance(payload["scenarios"], list):
        raise ScenarioRegistryError("scenarios must be a list")
    scenarios: list[Scenario] = []
    seen: set[str] = set()
    for raw in payload["scenarios"]:
        if not isinstance(raw, dict) or set(raw) != {"scenario_id", "expected"}:
            raise ScenarioRegistryError("scenario must contain only scenario_id and expected")
        scenario_id = raw["scenario_id"]
        expected = raw["expected"]
        if not isinstance(scenario_id, str) or not scenario_id:
            raise ScenarioRegistryError("scenario_id must be a non-empty string")
        if scenario_id in seen:
            raise ScenarioRegistryError(f"duplicate scenario_id: {scenario_id}")
        seen.add(scenario_id)
        if not isinstance(expected, dict) or set(expected) != {"passed", "diagnostic_code"}:
            raise ScenarioRegistryError(f"invalid expected result: {scenario_id}")
        if type(expected["passed"]) is not bool:
            raise ScenarioRegistryError(f"passed must be boolean: {scenario_id}")
        if not isinstance(expected["diagnostic_code"], str) or not expected["diagnostic_code"]:
            raise ScenarioRegistryError(f"diagnostic_code must be a non-empty string: {scenario_id}")
        scenarios.append(
            Scenario(
                scenario_id,
                ExpectedResult(expected["passed"], expected["diagnostic_code"]),
            )
        )
    return Registry(payload["schema_version"], payload["registry_id"], tuple(scenarios))


def _python_in(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _sanitized_env(*, source_root: Path | None = None) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if source_root is not None:
        env["PYTHONPATH"] = str(source_root)
    return env


def _run_json(command: tuple[str, ...], cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise AssertionError(
            f"registry runner failed ({completed.returncode})\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return json.loads(completed.stdout)


def _artifact_run(payload: dict[str, Any]) -> ArtifactRun:
    return ArtifactRun(
        payload["module_path"],
        tuple(ScenarioResult(item["scenario_id"], item["passed"], item["diagnostic_code"])
              for item in payload["results"]),
    )


def _repo_git_snapshot(repo: Path) -> dict[str, Any]:
    def git(*args: str, check: bool = True) -> bytes:
        completed = subprocess.run(("git", *args), cwd=repo, capture_output=True, check=False)
        if check and completed.returncode != 0:
            raise AssertionError(completed.stderr.decode(errors="replace"))
        return completed.stdout

    git_dir_raw = git("rev-parse", "--path-format=absolute", "--git-dir").decode().strip()
    common_raw = git("rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()
    git_dir = Path(git_dir_raw)
    common = Path(common_raw)

    def manifest(directory: Path) -> dict[str, tuple[int, str]]:
        if not directory.exists():
            return {}
        return {
            path.relative_to(directory).as_posix(): (
                path.lstat().st_size,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in sorted(directory.rglob("*"))
            if path.is_file() and not path.is_symlink()
        }

    index = git_dir / "index"
    return {
        "head": git("rev-parse", "HEAD"),
        "refs": git("for-each-ref", "--format=%(refname):%(objectname):%(symref)"),
        "index": index.read_bytes() if index.exists() else b"",
        "status": git("status", "--porcelain=v1", "--untracked-files=all"),
        "objects": manifest(common / "objects"),
    }


def execute_cross_artifact_registry(root: Path, registry_path: Path, temp_root: Path) -> CrossArtifactEvidence:
    registry = load_registry(registry_path)
    before = _repo_git_snapshot(root)
    dist = temp_root / "candidate-dist"
    dist.mkdir()
    built = subprocess.run(
        ("uv", "build", "--offline", "--wheel", "--out-dir", str(dist)),
        cwd=root,
        env=_sanitized_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    wheel = next(dist.glob("proofline-*.whl"))
    wheel_sha256 = hashlib.sha256(wheel.read_bytes()).hexdigest()

    venv = temp_root / "candidate-venv"
    created = subprocess.run(
        ("uv", "venv", "--python", sys.executable, str(venv)),
        cwd=temp_root,
        env=_sanitized_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr
    python = _python_in(venv)
    installed = subprocess.run(
        ("uv", "pip", "install", "--offline", "--python", str(python), str(wheel), "pytest>=8"),
        cwd=temp_root,
        env=_sanitized_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    assert installed.returncode == 0, installed.stderr

    extracted = temp_root / "packaged-scripts"
    extracted.mkdir()
    members = {
        "handoff": "proofline_home/skills/proofline-start-implementation/scripts/create_worktree.py",
        "approval": "proofline_home/skills/proofline-approve-specification/scripts/audit_approval_authority.py",
    }
    source_scripts = {
        "handoff": root / "skills/proofline-start-implementation/scripts/create_worktree.py",
        "approval": root / "skills/proofline-approve-specification/scripts/audit_approval_authority.py",
    }
    extracted_scripts: dict[str, Path] = {}
    byte_equal = True
    with zipfile.ZipFile(wheel) as archive:
        for kind, member in members.items():
            data = archive.read(member)
            target = extracted / f"{kind}.py"
            target.write_bytes(data)
            extracted_scripts[kind] = target
            byte_equal = byte_equal and data == source_scripts[kind].read_bytes()

    runner = Path(__file__).resolve()
    source_workspace = temp_root / "source-run"
    wheel_workspace = temp_root / "wheel-run"
    source_workspace.mkdir()
    wheel_workspace.mkdir()
    common = ("--registry", str(registry_path), "--root", str(root))
    source_payload = _run_json(
        (sys.executable, "-I", str(runner), "run", *common, "--workspace", str(source_workspace),
         "--artifact", "source", "--handoff-script", str(source_scripts["handoff"]),
         "--approval-script", str(source_scripts["approval"]),
         "--source-root", str(root / "src")),
        source_workspace,
        _sanitized_env(),
    )
    wheel_payload = _run_json(
        (str(python), "-I", str(runner), "run", *common, "--workspace", str(wheel_workspace),
         "--artifact", "wheel", "--handoff-script", str(extracted_scripts["handoff"]),
         "--approval-script", str(extracted_scripts["approval"])),
        wheel_workspace,
        _sanitized_env(),
    )
    source = _artifact_run(source_payload)
    wheel_run = _artifact_run(wheel_payload)
    expected = tuple(
        ScenarioResult(item.scenario_id, item.expected.passed, item.expected.diagnostic_code)
        for item in registry.scenarios
    )
    assert _repo_git_snapshot(root) == before, "candidate source repository mutated during parity gate"
    return CrossArtifactEvidence(
        source=source,
        wheel=wheel_run,
        expected_results=expected,
        wheel_path=str(wheel),
        wheel_sha256=wheel_sha256,
        packaged_scripts_byte_equal=byte_equal,
        packaged_script_ids=set(wheel_payload["packaged_script_ids"]),
        all_no_mutation_checks_passed=(
            source_payload["no_mutation_checks"] > 0
            and source_payload["no_mutation_checks"] == source_payload["no_mutation_passes"]
            and wheel_payload["no_mutation_checks"] == wheel_payload["no_mutation_passes"]
            and source_payload["no_mutation_checks"] == wheel_payload["no_mutation_checks"]
        ),
    )


def _load_test_fixtures(root: Path) -> dict[str, Any]:
    tests = root / "tests"
    if str(tests) not in sys.path:
        sys.path.insert(0, str(tests))
    import test_criteria_validation as coverage
    import test_implementation_history as chronology
    import test_integration_history as integration
    import test_specification_approval_authority as approval
    import test_start_implementation_skill as handoff
    return {
        "coverage": coverage,
        "chronology": chronology,
        "integration": integration,
        "approval": approval,
        "handoff": handoff,
    }


def _normalize_errors(errors: list[Any]) -> str:
    if not errors:
        return "PASS"
    priority = (
        "criteria.uncovered", "criteria.duplicate", "criteria.out-of-scope",
        "history.spec.chronology", "history.integration.parent", "history.integration.tree",
        "history.integration.dqc",
    )
    codes = [error.code for error in errors]
    return next((code for code in priority if code in codes), codes[0])


def _coverage_scenario(module: Any, scenario_id: str, workspace: Path) -> tuple[str, bool]:
    project = module.copy_valid_project(workspace)
    req = project / module.REQ
    ms = project / module.MS
    line = project / ".proofline/lines/line-0001/line-0001.md"
    module.initialize_main(project)
    if scenario_id == "coverage.dormant-partial.pass":
        module.replace(line, "execution_status: delivered", "execution_status: not_started")
        module.replace(ms, "implementation_status: implemented", "implementation_status: not_started")
        module.replace(ms, "  - ac-0003\n", "")
    elif scenario_id == "coverage.active-missing.fail":
        module.replace(ms, "  - ac-0003\n", "")
    elif scenario_id == "coverage.active-duplicate.fail":
        module.replace(req, "  retire: []", "  retire: []\n  satisfy:\n    - ac-0001")
    elif scenario_id == "coverage.active-direct-noncanonical.fail":
        module.replace(ms, "criteria:\n", "criteria:\n  - ac-9999\n")
    elif scenario_id != "coverage.active-exact.pass":
        raise AssertionError(scenario_id)
    before = _repo_git_snapshot(project)
    errors = module._validate_schema_candidate(project)
    coverage_errors = [
        error for error in errors
        if error.code in {"criteria.uncovered", "criteria.duplicate", "criteria.out-of-scope"}
    ]
    return _normalize_errors(coverage_errors), _repo_git_snapshot(project) == before


def _handoff_code(scenario_id: str, result: subprocess.CompletedProcess[str]) -> str:
    if result.returncode == 0:
        return "PASS"
    stderr = result.stderr
    if scenario_id == "handoff.missing-target.fail" and "ac-9999.md" in stderr:
        return "HANDOFF_TARGET_MISSING"
    if scenario_id == "handoff.path-id-target.fail" and "ac-0001.md" in stderr:
        return "HANDOFF_TARGET_ID"
    if scenario_id == "handoff.draft-target.fail" and "AC.status must be active" in stderr:
        return "HANDOFF_TARGET_DRAFT"
    if scenario_id.endswith("dirty-retry.fail") and "existing worktree is not clean" in stderr:
        return "HANDOFF_RETRY_DIRTY"
    return "HANDOFF_UNEXPECTED"


def _handoff_scenario(module: Any, scenario_id: str, workspace: Path, script: Path) -> tuple[str, bool | None]:
    if scenario_id in {"handoff.exact-a-full-target.pass", "handoff.clean-exact-h-retry.pass",
                       "handoff.tracked-dirty-retry.fail", "handoff.untracked-dirty-retry.fail"}:
        repo, approval = module.make_approved_repo(workspace)
    else:
        repo, _ = module.make_approved_repo(workspace, handoff=False)
        if scenario_id == "handoff.missing-target.fail":
            module.set_criteria_target(repo, "create", ac_id="ac-9999", ac_status=None)
        elif scenario_id == "handoff.path-id-target.fail":
            module.set_criteria_target(repo, "create", ac_status="active", canonical_name="ac-0002", artifact_id="ac-0001")
        elif scenario_id == "handoff.draft-target.fail":
            module.set_criteria_target(repo, "create", ac_status="draft")
        else:
            raise AssertionError(scenario_id)
        approval = module.commit_approval_and_handoff(repo, scenario_id)

    if scenario_id == "handoff.exact-a-full-target.pass":
        return _handoff_code(scenario_id, module.run_script(repo, approval, script=script)), None
    if scenario_id.startswith("handoff.") and "retry" not in scenario_id:
        before = _repo_git_snapshot(repo)
        result = module.run_script(repo, approval, script=script)
        return _handoff_code(scenario_id, result), _repo_git_snapshot(repo) == before

    first = module.run_script(repo, approval, script=script)
    if first.returncode != 0:
        return "HANDOFF_SETUP_FAILED", False
    worktree = repo / ".worktrees/line-0007"
    if scenario_id == "handoff.tracked-dirty-retry.fail":
        target = worktree / ".proofline/lines/line-0007/req-0007.md"
        target.write_text(target.read_text(encoding="utf-8") + "dirty\n", encoding="utf-8")
    elif scenario_id == "handoff.untracked-dirty-retry.fail":
        (worktree / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    before = _repo_git_snapshot(repo)
    result = module.run_script(repo, approval, script=script)
    return _handoff_code(scenario_id, result), _repo_git_snapshot(repo) == before


def _approval_scenario(module: Any, scenario_id: str, workspace: Path, script: Path) -> tuple[str, bool]:
    bootstrap_criteria = {
        "approval.cross-admission-duplicate.fail": (
            '  create:\n    - "ac-0011"\n'
            '  update:\n    - "ac-0011"\n'
            '  retire: []\n'
            '  satisfy: []\n'
        ),
        "approval.empty-targets.fail": (
            '  create: []\n'
            '  update: []\n'
            '  retire: []\n'
            '  satisfy: []\n'
        ),
    }
    mode = (
        "bootstrap"
        if scenario_id == "approval.bootstrap.pass" or scenario_id in bootstrap_criteria
        else "normal"
    )
    if scenario_id == "approval.body-changing.fail":
        change = "body"
    elif scenario_id == "approval.concurrent-path.fail":
        change = "unrelated"
    else:
        change = "status"
    repo, target, approval = module.make_repo(
        workspace,
        mode=mode,
        approval_change=change,
        bootstrap_criteria=bootstrap_criteria.get(scenario_id),
    )
    kwargs: dict[str, Any] = {}
    if scenario_id == "approval.self-approval.fail":
        kwargs["user"] = "author-1"
    elif scenario_id == "approval.denied-user.fail":
        kwargs["decision"] = "denied"
    elif scenario_id == "approval.reviewer-mutation.fail":
        kwargs["mutation_performed"] = True
    elif scenario_id == "approval.stale-target-and-digest.fail":
        kwargs.update(stale_target=True, stale_digest=True)
    elif scenario_id == "approval.recorder-only.fail":
        kwargs["user"] = "recorder-1"
    elif scenario_id == "approval.stale-digest.fail":
        kwargs["stale_digest"] = True
    review, user = module.write_evidence(workspace, repo, target, **kwargs)
    if scenario_id == "approval.missing-user.fail":
        user.unlink()
    before = _repo_git_snapshot(repo)
    result = module.run_gate(script, repo, mode, target, approval, review, user)
    unchanged = _repo_git_snapshot(repo) == before
    if result.returncode == 0:
        return "PASS", unchanged
    marker = "approval-authority["
    if marker not in result.stderr:
        return "APPROVAL_UNEXPECTED", unchanged
    return result.stderr.split(marker, 1)[1].split("]", 1)[0], unchanged


def _chronology_scenario(module: Any, scenario_id: str, workspace: Path) -> tuple[str, bool]:
    mapping = {
        "chronology.line-0020-bootstrap.pass": (True, None, True),
        "chronology.bootstrap-create-body-change.fail": (True, "a-create-body", False),
        "chronology.bootstrap-update-body-change.fail": (True, "a-update-body", False),
        "chronology.bootstrap-retire-body-change.fail": (True, "a-retire-body", False),
        "chronology.bootstrap-satisfy-body-change.fail": (True, "a-satisfy-body", False),
        "chronology.future-a-h-s0-s-p.pass": (False, None, True),
        "chronology.missing-s.fail": (False, "missing-s0-s", False),
        "chronology.non-direct-s.fail": (False, "s-not-direct", False),
        "chronology.body-changing-s.fail": (False, "s-body", False),
        "chronology.stale-s.fail": (False, "duplicate-s", False),
        "chronology.p-before-s.fail": (False, "p-before-s", False),
    }
    bootstrap, defect, complete = mapping[scenario_id]
    repo = module.chronology_repo(workspace, bootstrap=bootstrap, defect=defect, complete=complete)
    before = _repo_git_snapshot(repo.path)
    errors = module.validate_project(repo.path)
    return _normalize_errors(errors), _repo_git_snapshot(repo.path) == before


def _integration_scenario(module: Any, scenario_id: str, workspace: Path) -> tuple[str, bool]:
    if scenario_id == "integration.missing-line-second-parent.fail":
        repo = module.build_missing_line_second_parent_candidate(workspace)
    elif scenario_id == "integration.wrong-binding.fail":
        repo, _, _, _ = module.build_candidate(workspace, line_head="e" * 40)
    elif scenario_id == "integration.merge-only-product-change.fail":
        repo, _, _, _ = module.build_candidate(workspace, merge_only_path="product/merge-only.py")
    else:
        repo, main, line_head, _ = module.build_candidate(workspace)
        if scenario_id == "integration.reversed-parents.fail":
            module.rewrite_candidate_parents(repo, line_head, main)
        elif scenario_id == "integration.octopus.fail":
            extra = module.git(repo.path, "rev-parse", f"{main}^")
            module.rewrite_candidate_parents(repo, main, line_head, extra)
        elif scenario_id != "integration.main-first-two-parent-manifest-tree.pass":
            raise AssertionError(scenario_id)
    before = _repo_git_snapshot(repo.path)
    errors = module.validate_project(repo.path)
    return _normalize_errors(errors), _repo_git_snapshot(repo.path) == before


def _dqc_scenario(module: Any, scenario_id: str, workspace: Path) -> tuple[str, bool]:
    repo, _, _, candidate = module.build_candidate(workspace)
    module.write_dqc(repo, candidate)
    if scenario_id == "dqc.pass-then-failed-delivery.fail":
        module.write_dqc(repo, candidate, result="failed")
    elif scenario_id == "dqc.pass-then-blocked-delivery.fail":
        module.write_dqc(repo, candidate, result="blocked")
    module.deliver(repo)
    if scenario_id == "dqc.exact-v-pass-delivery-and-later-commit.pass":
        (repo.path / "later-governance.txt").write_text("later\n", encoding="utf-8")
        repo.commit("later", "post-delivery unrelated commit")
    before = _repo_git_snapshot(repo.path)
    errors = module.validate_project(repo.path)
    return _normalize_errors(errors), _repo_git_snapshot(repo.path) == before


def run_registry(root: Path, registry_path: Path, workspace: Path, artifact: str,
                 handoff_script: Path, approval_script: Path,
                 source_root: Path | None = None) -> dict[str, Any]:
    registry = load_registry(registry_path)
    if source_root is not None:
        sys.path.insert(0, str(source_root))
    modules = _load_test_fixtures(root)
    import proofline
    module_path = str(Path(proofline.__file__).resolve())
    if artifact == "source":
        assert Path(module_path).is_relative_to(root / "src"), module_path
    else:
        assert "site-packages" in Path(module_path).parts and not Path(module_path).is_relative_to(root), module_path
    results: list[dict[str, Any]] = []
    no_mutation: list[bool] = []
    packaged_ids: set[str] = set()
    for index, scenario in enumerate(registry.scenarios):
        scenario_workspace = workspace / f"{index:02d}-{scenario.scenario_id.replace('.', '-')}"
        scenario_workspace.mkdir()
        if scenario.scenario_id.startswith("coverage."):
            code, unchanged = _coverage_scenario(modules["coverage"], scenario.scenario_id, scenario_workspace)
        elif scenario.scenario_id.startswith("handoff."):
            code, unchanged = _handoff_scenario(modules["handoff"], scenario.scenario_id, scenario_workspace, handoff_script)
            if artifact == "wheel":
                packaged_ids.add(scenario.scenario_id)
        elif scenario.scenario_id.startswith("approval."):
            code, unchanged = _approval_scenario(modules["approval"], scenario.scenario_id, scenario_workspace, approval_script)
            if artifact == "wheel":
                packaged_ids.add(scenario.scenario_id)
        elif scenario.scenario_id.startswith("chronology."):
            code, unchanged = _chronology_scenario(modules["chronology"], scenario.scenario_id, scenario_workspace)
        elif scenario.scenario_id.startswith("integration."):
            code, unchanged = _integration_scenario(modules["integration"], scenario.scenario_id, scenario_workspace)
        elif scenario.scenario_id.startswith("dqc."):
            code, unchanged = _dqc_scenario(modules["integration"], scenario.scenario_id, scenario_workspace)
        else:
            raise AssertionError(scenario.scenario_id)
        if unchanged is not None:
            no_mutation.append(unchanged)
        results.append({
            "scenario_id": scenario.scenario_id,
            "passed": code == "PASS",
            "diagnostic_code": code,
        })
    return {
        "artifact": artifact,
        "module_path": module_path,
        "results": results,
        "no_mutation_checks": len(no_mutation),
        "no_mutation_passes": sum(no_mutation),
        "packaged_script_ids": sorted(packaged_ids),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--registry", type=Path, required=True)
    run.add_argument("--root", type=Path, required=True)
    run.add_argument("--workspace", type=Path, required=True)
    run.add_argument("--artifact", choices=("source", "wheel"), required=True)
    run.add_argument("--handoff-script", type=Path, required=True)
    run.add_argument("--approval-script", type=Path, required=True)
    run.add_argument("--source-root", type=Path)
    args = parser.parse_args(argv)
    payload = run_registry(args.root.resolve(), args.registry.resolve(), args.workspace.resolve(),
                           args.artifact, args.handoff_script.resolve(), args.approval_script.resolve(),
                           args.source_root.resolve() if args.source_root else None)
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
