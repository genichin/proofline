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
MIGRATION = ".proofline/lines/line-0001/legacy-migration-0001.md"
DQC = ".proofline/lines/line-0001/dqc-0001.md"


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args), cwd=repo, text=True, capture_output=True, check=check
    )


def unlink_git_object(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IWRITE)
    path.unlink()


def remove_readonly(
    function: Callable[[str], object], path: str, exc_info: object
) -> None:
    del exc_info
    target = Path(path)
    target.chmod(target.stat().st_mode | stat.S_IWRITE)
    function(path)


@dataclass
class HistoryRepo:
    path: Path
    commits: dict[str, str] = field(default_factory=dict)

    @classmethod
    def create(
        cls, tmp_path: Path, *, specs: int = 1, object_format: str = "sha1"
    ) -> "HistoryRepo":
        path = tmp_path / "project"
        (path / ".proofline/lines/line-0001/micro-specs").mkdir(parents=True)
        (path / ".proofline/criteria").mkdir(parents=True)
        (path / ".proofline/lines/.gitkeep").write_bytes(b"")
        (path / ".proofline/criteria/.gitkeep").write_bytes(b"")
        (path / "proofline.yaml").write_text(
            "schema_version: 1\nartifact_root: .proofline\n", encoding="utf-8"
        )
        git(path, "init", "-q", "-b", "main", f"--object-format={object_format}")
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
        policy_line = (
            f"implementation_history: {policy}\n" if policy is not None else ""
        )
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
        criteria = "\n".join(
            f'    - "ac-{number:04d}"' for number in range(1, specs + 1)
        )
        path = self.path / ".proofline/lines/line-0001/req-0001.md"
        path.write_text(
            '---\nid: "req-0001"\nstatus: approved\ndiscovery: "dcy-0001"\n'
            f"criteria:\n  create:\n{criteria}\n  update: []\n  retire: []\n  satisfy: []\n"
            "---\n\n# Requirement\n\n## Objective\n\n목표이다.\n\n## Scope\n\n범위이다."
            "\n\n## Non-Goals\n\n비목표이다.\n",
            encoding="utf-8",
        )

    def write_requirement_admissions(self) -> None:
        path = self.path / ".proofline/lines/line-0001/req-0001.md"
        path.write_text(
            '---\nid: "req-0001"\nstatus: draft\ndiscovery: "dcy-0001"\n'
            'criteria:\n  create:\n    - "ac-0001"\n  update:\n    - "ac-0002"\n'
            '  retire:\n    - "ac-0003"\n  satisfy:\n    - "ac-0004"\n'
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
        criteria_numbers: tuple[int, ...] | None = None,
    ) -> None:
        path = (
            self.path
            / f".proofline/lines/line-0001/micro-specs/ms-0001-{number:03d}.md"
        )
        if malformed:
            path.write_text("---\nid: [\n---\n", encoding="utf-8")
            return
        criteria = "".join(
            f'  - "ac-{criterion:04d}"\n'
            for criterion in (criteria_numbers or (number,))
        )
        path.write_text(
            f'---\nid: "ms-0001-{number:03d}"\nparent_req: "req-0001"\ncriteria:\n'
            f"{criteria}spec_status: {spec_status}\nimplementation_status: {status}\n---\n\n"
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
        path = (
            self.path
            / f".proofline/lines/line-0001/micro-specs/iqc-0001-{number:03d}.md"
        )
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
            line
            for line in current.splitlines()
            if line.startswith("execution_status:")
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
        criteria_numbers: tuple[int, ...] | None = None,
    ) -> str:
        for number in numbers:
            self.write_ms(number, "implemented", criteria_numbers=criteria_numbers)
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
    assert (
        git(repo.path, "config", "--get", "maintenance.auto").stdout.strip() == "false"
    )


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


def build_legacy_migration(
    tmp_path: Path,
    *,
    parent: str | None = None,
    evidence: list[tuple[str, str]] | None = None,
    extra_change: bool = False,
    activate_before_cycle: bool = False,
    incomplete: str | None = None,
    dqc_defect: str | None = None,
    include_dqc: bool = False,
    target_state: str = "in_progress",
    policy_before: str | None = None,
    inventory_defect: str | None = None,
    inventory_mode: str | None = None,
    object_format: str = "sha1",
    iqc_binding_defect: str | None = None,
    iqc_binding_field: str = "micro_spec_commit",
    line_body_delta: bool = False,
    split_baseline: bool = False,
) -> HistoryRepo:
    """Build an eligible fieldless S/P/I/Q cycle followed by exact migration B."""
    repo = HistoryRepo.create(tmp_path, object_format=object_format)
    if activate_before_cycle:
        line2 = repo.path / ".proofline/lines/line-0002"
        line2.mkdir()
        (line2 / "line-0002.md").write_text(
            '---\nid: "line-0002"\nexecution_status: not_started\n'
            "implementation_history: first_parent\n---\n",
            encoding="utf-8",
        )
        repo.commit("activation", "activate history policy")
    if incomplete == "evidence-absent":
        repo.write_line("in_progress", policy=None)
        repo.commit("legacy-state", "legacy state without cycle evidence")
    elif incomplete == "iqc-only":
        repo.write_line("in_progress", policy=None)
        repo.write_iqc(1, repo.commits["approval"])
        repo.commit("legacy-state", "legacy IQC without implementation cycle")
    else:
        repo.start()
        if incomplete != "p-only":
            implementation = repo.product_commit()
            if incomplete not in {"no-finish", "i-without-q"}:
                repo.finish(implementation)
    if include_dqc or dqc_defect is not None:
        quality = repo.commits.get("quality", repo.commits["approval"])
        git(repo.path, "switch", "-qc", "legacy-integration-line")
        repo.start("legacy-integration-start")
        integration_implementation = repo.product_commit(
            "legacy-integration-implementation"
        )
        repo.write_line("verifying", policy=None)
        line_head = repo.finish(
            integration_implementation, "legacy-integration-quality"
        )
        git(repo.path, "switch", "-q", "main")
        (repo.path / "legacy-main.txt").write_text("main\n", encoding="utf-8")
        main_parent = repo.commit("legacy-main", "advance legacy main")
        merged = git(
            repo.path,
            "merge",
            "--no-ff",
            "--no-commit",
            "legacy-integration-line",
            check=False,
        )
        assert merged.returncode == 0, merged.stderr
        integration = repo.path / ".proofline/lines/line-0001/integration-0001.md"
        manifest_main = quality if dqc_defect == "manifest-parent" else main_parent
        integration.write_text(
            "---\n"
            'id: "integration-0001"\nline_id: "line-0001"\n'
            f'main_parent: "{manifest_main}"\nline_head: "{line_head}"\n'
            "---\n",
            encoding="utf-8",
        )
        candidate = repo.commit("legacy-candidate", "integrate legacy Line")
        values = {
            "id": "dqc-wrong" if dqc_defect == "wrong-identity" else "dqc-0001",
            "line": "line-9999" if dqc_defect == "wrong-line" else "line-0001",
            "candidate": (
                "0" * len(candidate)
                if dqc_defect == "stale"
                else quality
                if dqc_defect in {"mismatched", "no-applicable-candidate"}
                else candidate
            ),
            "result": "invalid" if dqc_defect == "wrong-result" else "passed",
        }
        dqc = repo.path / DQC
        if dqc_defect == "malformed":
            dqc.write_text("---\nid: [\n---\n", encoding="utf-8")
        else:
            dqc.write_text(
                "---\n"
                f'id: "{values["id"]}"\nline: "{values["line"]}"\n'
                f'candidate_commit: "{values["candidate"]}"\nresult: {values["result"]}\n'
                "---\n\n# DQC\n\n## Target\n\n대상.\n\n## IQC Results\n\n통과.\n\n"
                "## Checks\n\n통과.\n\n## Criteria Results\n\n통과.\n\n## Result\n\n통과.\n",
                encoding="utf-8",
            )
        repo.commit("legacy-dqc", "persist legacy DQC")
        if target_state == "in_progress":
            repo.start("legacy-post-dqc-rework")
    current_line = (repo.path / LINE).read_text(encoding="utf-8")
    if (
        f"execution_status: {target_state}" not in current_line
        or policy_before is not None
    ):
        repo.write_line(target_state, policy=policy_before)
        repo.commit("legacy-target", "set migration target state")
    if iqc_binding_defect is not None:
        iqc_path = repo.path / IQC
        text = iqc_path.read_text(encoding="utf-8")
        native_value = (
            repo.commits["approval"]
            if iqc_binding_field == "micro_spec_commit"
            else repo.commits["implementation"]
        )
        native_length = len(native_value)
        replacement = {
            "opposite": "a" * (64 if native_length == 40 else 40),
            "uppercase": native_value.upper(),
            "nonhex": "g" * native_length,
            "wrong-length": "a" * (native_length - 1),
        }[iqc_binding_defect]
        iqc_path.write_text(
            text.replace(native_value, replacement, 1), encoding="utf-8"
        )
        repo.commit("bad-iqc-binding", "persist invalid native IQC binding")
    if inventory_mode is not None:
        ms_oid = git(repo.path, "rev-parse", f"HEAD:{MS}").stdout.strip()
        if inventory_mode == "160000":
            object_oid = repo.commits["approval"]
        elif inventory_mode == "040000":
            object_oid = git(
                repo.path, "hash-object", "-t", "tree", "/dev/null"
            ).stdout.strip()
        else:
            object_oid = ms_oid
        git(
            repo.path,
            "update-index",
            "--add",
            "--cacheinfo",
            f"{inventory_mode},{object_oid},{MS}",
        )
        git(repo.path, "commit", "-qm", f"persist {inventory_mode} migration evidence")
    pre_migration_parent = git(repo.path, "rev-parse", "HEAD").stdout.strip()
    inventory_paths = [
        path
        for path in (DQC, IQC, MS)
        if git(
            repo.path, "cat-file", "-e", f"{pre_migration_parent}:{path}", check=False
        ).returncode
        == 0
    ]
    inventory = evidence or [
        (
            path,
            git(
                repo.path, "rev-parse", f"{pre_migration_parent}:{path}"
            ).stdout.strip(),
        )
        for path in inventory_paths
    ]
    if inventory_defect == "missing":
        inventory = inventory[1:]
    elif inventory_defect == "extra":
        inventory.append(
            (
                "product.py",
                git(
                    repo.path, "rev-parse", f"{pre_migration_parent}:product.py"
                ).stdout.strip(),
            )
        )
    elif inventory_defect == "duplicate":
        inventory.insert(1, inventory[0])
    entries = "".join(
        f'  - path: "{path}"\n    blob_oid: "{oid}"\n' for path, oid in inventory
    )
    (repo.path / MIGRATION).write_text(
        '---\nid: "legacy-migration-0001"\nline: "line-0001"\n'
        f'pre_migration_parent: "{parent or pre_migration_parent}"\nevidence:\n{entries}---\n',
        encoding="utf-8",
    )
    if split_baseline:
        git(repo.path, "add", "--", MIGRATION)
        git(repo.path, "commit", "-qm", "persist migration authority separately")
    current = (repo.path / LINE).read_text(encoding="utf-8")
    if "implementation_history:" not in current:
        status_line = next(
            line
            for line in current.splitlines()
            if line.startswith("execution_status:")
        )
        (repo.path / LINE).write_text(
            current.replace(
                f"{status_line}\n",
                f"{status_line}\nimplementation_history: first_parent\n",
                1,
            ),
            encoding="utf-8",
        )
    if line_body_delta:
        (repo.path / LINE).write_text(
            (repo.path / LINE).read_text(encoding="utf-8") + "\nchanged body\n",
            encoding="utf-8",
        )
    if extra_change:
        (repo.path / "product.py").write_text(
            "MIGRATION_CHANGED = True\n", encoding="utf-8"
        )
    if inventory_mode is None:
        repo.commit("migration", "migrate legacy line")
    else:
        git(repo.path, "add", "--", LINE, MIGRATION)
        git(repo.path, "commit", "-qm", "migrate legacy line")
        repo.commits["migration"] = git(repo.path, "rev-parse", "HEAD").stdout.strip()
    return repo


MIGRATION_SCENARIO_IDS = (
    "sha1-positive",
    "sha256-positive",
    "sha1-parent-opposite",
    "sha256-parent-opposite",
    "sha1-iqc-micro-uppercase",
    "sha256-iqc-implementation-nonhex",
    "incomplete-no-finish",
    "inventory-missing",
    "nonregular-symlink",
    "split-baseline",
    "post-activation-cycle",
    "dqc-stale",
    "artifact-mutation",
    "policy-mutation",
    "fresh-recovery",
)


def build_migration_scenario(tmp_path: Path, scenario_id: str) -> HistoryRepo:
    assert scenario_id in MIGRATION_SCENARIO_IDS
    kwargs: dict[str, object] = {}
    if scenario_id == "sha256-positive":
        kwargs["object_format"] = "sha256"
    elif scenario_id == "sha1-parent-opposite":
        kwargs["parent"] = "a" * 64
    elif scenario_id == "sha256-parent-opposite":
        kwargs.update(object_format="sha256", parent="a" * 40)
    elif scenario_id == "sha1-iqc-micro-uppercase":
        kwargs["iqc_binding_defect"] = "uppercase"
    elif scenario_id == "sha256-iqc-implementation-nonhex":
        kwargs.update(
            object_format="sha256",
            iqc_binding_defect="nonhex",
            iqc_binding_field="implementation_commit",
        )
    elif scenario_id == "incomplete-no-finish":
        kwargs["incomplete"] = "no-finish"
    elif scenario_id == "inventory-missing":
        kwargs["inventory_defect"] = "missing"
    elif scenario_id == "nonregular-symlink":
        kwargs["inventory_mode"] = "120000"
    elif scenario_id == "split-baseline":
        kwargs["split_baseline"] = True
    elif scenario_id == "post-activation-cycle":
        kwargs["activate_before_cycle"] = True
    elif scenario_id == "dqc-stale":
        kwargs["dqc_defect"] = "stale"
    repo = build_legacy_migration(tmp_path, **kwargs)  # type: ignore[arg-type]
    if scenario_id == "artifact-mutation":
        artifact = repo.path / MIGRATION
        artifact.write_text(artifact.read_text() + "mutated\n", encoding="utf-8")
        repo.commit("artifact-mutation", "mutate migration authority")
    elif scenario_id == "policy-mutation":
        repo.write_line("in_progress", policy=None)
        repo.commit("policy-mutation", "remove migration policy")
    elif scenario_id == "fresh-recovery":
        repo.start("registry-recovery-start")
        implementation = repo.product_commit("registry-recovery-implementation")
        repo.finish(implementation, "registry-recovery-quality")
    return repo


@pytest.mark.parametrize(
    ("object_format", "oid_length"), [("sha1", 40), ("sha256", 64)]
)
def test_eligible_legacy_nonterminal_migration_passes_in_native_repository_without_mutation(
    tmp_path: Path, object_format: str, oid_length: int
) -> None:
    repo = build_legacy_migration(tmp_path, object_format=object_format)
    before = repository_snapshot(repo.path)
    artifact = (repo.path / MIGRATION).read_text(encoding="utf-8")

    assert len(repo.commits["migration"]) == oid_length
    assert f'pre_migration_parent: "{repo.commits["quality"]}"' in artifact
    assert validate_project(repo.path) == []
    assert repository_snapshot(repo.path) == before


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
@pytest.mark.parametrize(
    "binding_field", ["micro_spec_commit", "implementation_commit"]
)
@pytest.mark.parametrize("defect", ["opposite", "uppercase", "nonhex", "wrong-length"])
def test_real_git_migration_rejects_non_native_iqc_commit_binding(
    tmp_path: Path, object_format: str, binding_field: str, defect: str
) -> None:
    repo = build_legacy_migration(
        tmp_path,
        object_format=object_format,
        iqc_binding_defect=defect,
        iqc_binding_field=binding_field,
    )
    before = repository_snapshot(repo.path)

    assert (MS, "history.ms.transition") in history_codes(repo)
    assert repository_snapshot(repo.path) == before


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
@pytest.mark.parametrize("defect", ["opposite", "uppercase", "nonhex", "wrong-length"])
def test_real_git_migration_rejects_non_native_parent_oid(
    tmp_path: Path, object_format: str, defect: str
) -> None:
    seed = HistoryRepo.create(tmp_path / "seed", object_format=object_format)
    native = seed.commits["approval"]
    shutil.rmtree(seed.path, onerror=remove_readonly)
    replacement = {
        "opposite": "a" * (64 if len(native) == 40 else 40),
        "uppercase": native.upper(),
        "nonhex": "g" * len(native),
        "wrong-length": "a" * (len(native) - 1),
    }[defect]
    repo = build_legacy_migration(
        tmp_path / "case", object_format=object_format, parent=replacement
    )
    before = repository_snapshot(repo.path)

    assert (MIGRATION, "migration.parent.mismatch") in history_codes(repo)
    assert repository_snapshot(repo.path) == before


def test_migration_does_not_exempt_cycle_completed_after_policy_activation(
    tmp_path: Path,
) -> None:
    repo = build_legacy_migration(tmp_path, activate_before_cycle=True)

    assert (MIGRATION, "migration.eligibility.cycle") in history_codes(repo)


@pytest.mark.parametrize(
    "incomplete",
    ["evidence-absent", "no-finish", "iqc-only", "p-only", "i-without-q"],
)
def test_migration_requires_a_complete_valid_pre_activation_cycle(
    tmp_path: Path, incomplete: str
) -> None:
    repo = build_legacy_migration(tmp_path, incomplete=incomplete)
    before = repository_snapshot(repo.path)

    assert (MIGRATION, "migration.eligibility.cycle") in history_codes(repo)
    assert repository_snapshot(repo.path) == before


def inventory_dqc_result(repo: HistoryRepo) -> bool:
    commits = git(
        repo.path, "rev-list", "--first-parent", "--reverse", "HEAD"
    ).stdout.splitlines()
    session = implementation_history._GitSession(repo.path)
    trees = [implementation_history._tree_paths(session, commit) for commit in commits]
    baseline = commits.index(repo.commits["migration"])
    content = git(repo.path, "show", f"{commits[baseline - 1]}:{DQC}").stdout.encode()
    return implementation_history._valid_inventory_dqc(
        session,
        DQC,
        content,
        commits,
        trees,
        baseline,
    )


def test_migration_inventory_accepts_applicable_integration_bound_dqc(
    tmp_path: Path,
) -> None:
    repo = build_legacy_migration(tmp_path, include_dqc=True)
    before = repository_snapshot(repo.path)

    assert inventory_dqc_result(repo)
    assert repository_snapshot(repo.path) == before


@pytest.mark.parametrize(
    "defect",
    [
        "malformed",
        "wrong-identity",
        "wrong-line",
        "wrong-result",
        "stale",
        "mismatched",
        "no-applicable-candidate",
        "manifest-parent",
    ],
)
def test_migration_inventory_rejects_unprovable_existing_dqc(
    tmp_path: Path, defect: str
) -> None:
    repo = build_legacy_migration(tmp_path, dqc_defect=defect)
    before = repository_snapshot(repo.path)

    assert not inventory_dqc_result(repo)
    assert repository_snapshot(repo.path) == before


@pytest.mark.parametrize(
    ("target_state", "policy_before"),
    [("not_started", None), ("delivered", None), ("in_progress", "first_parent")],
)
def test_migration_rejects_wrong_target_state_or_existing_policy(
    tmp_path: Path, target_state: str, policy_before: str | None
) -> None:
    repo = build_legacy_migration(
        tmp_path, target_state=target_state, policy_before=policy_before
    )

    assert (MIGRATION, "migration.eligibility.state") in history_codes(repo)


@pytest.mark.parametrize(
    ("defect", "code"),
    [
        ("missing", "migration.inventory.paths"),
        ("extra", "migration.inventory.paths"),
        ("duplicate", "migration.inventory.order"),
    ],
)
def test_migration_inventory_is_exact_and_unique(
    tmp_path: Path, defect: str, code: str
) -> None:
    repo = build_legacy_migration(tmp_path, inventory_defect=defect)

    assert (MIGRATION, code) in history_codes(repo)


def test_migration_inventory_accepts_executable_regular_blob(tmp_path: Path) -> None:
    repo = build_legacy_migration(tmp_path, inventory_mode="100755")

    assert validate_project(repo.path) == []


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("120000", (MS, "history.unavailable")),
        ("160000", (MS, "history.unavailable")),
        ("040000", (MIGRATION, "migration.eligibility.cycle")),
    ],
)
def test_migration_inventory_rejects_non_regular_git_entry(
    tmp_path: Path, mode: str, expected: tuple[str, str]
) -> None:
    repo = build_legacy_migration(tmp_path, inventory_mode=mode)
    before = repository_snapshot(repo.path)

    assert expected in history_codes(repo)
    assert repository_snapshot(repo.path) == before


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"split_baseline": True}, "migration.baseline.paths"),
        ({"line_body_delta": True}, "migration.baseline.policy"),
    ],
)
def test_migration_baseline_requires_same_commit_policy_only_delta(
    tmp_path: Path, kwargs: dict[str, object], code: str
) -> None:
    repo = build_legacy_migration(tmp_path, **kwargs)  # type: ignore[arg-type]

    assert (MIGRATION, code) in history_codes(repo)


@pytest.mark.parametrize(
    ("defect", "code"),
    [
        ("wrong-parent", "migration.parent.mismatch"),
        ("unsorted", "migration.inventory.order"),
        ("stale-oid", "migration.inventory.oid"),
        ("extra-change", "migration.baseline.paths"),
    ],
)
def test_legacy_migration_fail_closed_boundaries(
    tmp_path: Path, defect: str, code: str
) -> None:
    seed = HistoryRepo.create(tmp_path / "seed")
    oid_length = len(seed.commits["approval"])
    shutil.rmtree(seed.path, onerror=remove_readonly)
    kwargs: dict[str, object] = {}
    if defect == "wrong-parent":
        kwargs["parent"] = "0" * oid_length
    elif defect == "unsorted":
        repo = build_legacy_migration(tmp_path / "inventory")
        parent = repo.commits["quality"]
        values = [
            (path, git(repo.path, "rev-parse", f"{parent}:{path}").stdout.strip())
            for path in (MS, IQC)
        ]
        shutil.rmtree(repo.path, onerror=remove_readonly)
        kwargs["evidence"] = values
    elif defect == "stale-oid":
        kwargs["evidence"] = [(IQC, "0" * oid_length), (MS, "0" * oid_length)]
    elif defect == "extra-change":
        kwargs["extra_change"] = True
    repo = build_legacy_migration(tmp_path / "case", **kwargs)
    before = repository_snapshot(repo.path)

    assert (MIGRATION, code) in history_codes(repo)
    assert repository_snapshot(repo.path) == before


@pytest.mark.parametrize("action", ["mutate", "remove", "reintroduce"])
def test_migration_artifact_is_immutable_and_cannot_be_reapplied(
    tmp_path: Path, action: str
) -> None:
    repo = build_legacy_migration(tmp_path)
    artifact = repo.path / MIGRATION
    original = artifact.read_bytes()
    if action == "mutate":
        artifact.write_text(
            artifact.read_text().replace("evidence:", "evidence: # changed")
        )
        repo.commit("mutation", "mutate migration authority")
    else:
        artifact.unlink()
        repo.commit("removal", "remove migration authority")
        if action == "reintroduce":
            artifact.write_bytes(original)
            repo.commit("reintroduction", "reintroduce migration authority")

    assert (MIGRATION, "migration.immutable") in history_codes(repo)


def test_migration_policy_is_immutable_after_baseline(tmp_path: Path) -> None:
    repo = build_legacy_migration(tmp_path)
    repo.write_line("in_progress", policy=None)
    repo.commit("policy-removal", "remove migrated history policy")

    assert (LINE, "history.line.policy.changed") in history_codes(repo)


def test_migration_requires_and_accepts_fresh_post_baseline_recovery_cycle(
    tmp_path: Path,
) -> None:
    repo = build_legacy_migration(tmp_path)
    baseline = repo.commits["migration"]
    p2 = repo.start("recovery-start")
    i2 = repo.product_commit("recovery-implementation")
    q2 = repo.finish(i2, "recovery-quality")
    history = git(
        repo.path, "rev-list", "--first-parent", "--reverse", "HEAD"
    ).stdout.splitlines()

    assert (
        history.index(baseline)
        < history.index(p2)
        < history.index(i2)
        < history.index(q2)
    )
    assert validate_project(repo.path) == []


@pytest.mark.parametrize("oid_length", [40, 64])
def test_repository_native_commit_parser_accepts_only_exact_lowercase_oid(
    oid_length: int,
) -> None:
    oid = "a" * oid_length
    positions = {oid: 7}

    assert implementation_history._resolved_commit(oid, positions, oid_length) == 7
    assert (
        implementation_history._resolved_commit(oid.upper(), positions, oid_length)
        is None
    )
    assert (
        implementation_history._resolved_commit(
            "a" * (104 - oid_length), positions, oid_length
        )
        is None
    )


def history_codes(repo: HistoryRepo | Path) -> set[tuple[str, str]]:
    root = repo.path if isinstance(repo, HistoryRepo) else repo
    return {(error.path, error.code) for error in validate_project(root)}


def assert_history_error(repo: HistoryRepo, path: str, code: str) -> None:
    assert (path, code) in history_codes(repo)


def commit_index_mode(repo: HistoryRepo, path: str, mode: str, message: str) -> str:
    """Commit existing artifact bytes with an exact index mode, without OS symlinks."""
    payload = (repo.path / path).read_bytes()
    hashed = subprocess.run(
        ("git", "hash-object", "-w", "--stdin"),
        cwd=repo.path,
        input=payload,
        text=False,
        capture_output=True,
        check=True,
    )
    oid = hashed.stdout.decode("ascii").strip()
    git(repo.path, "update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}")
    git(repo.path, "commit", "-qm", message)
    return oid


def parity_symlink_canonical_artifact(tmp_path: Path, path: str = IQC) -> HistoryRepo:
    repo = build_valid_cycle(tmp_path)
    git(repo.path, "config", "core.symlinks", "false")
    oid = commit_index_mode(repo, path, "120000", "symlink-mode canonical artifact")
    git(repo.path, "update-index", "--cacheinfo", f"100644,{oid},{path}")
    git(repo.path, "commit", "-qm", "restore regular canonical artifact")
    return repo


def parity_tree_canonical_artifact(tmp_path: Path, path: str = LINE) -> HistoryRepo:
    repo = HistoryRepo.create(tmp_path)
    artifact = repo.path / path
    artifact.unlink()
    artifact.mkdir()
    (artifact / "child").write_text("historical tree entry\n", encoding="utf-8")
    tree_commit = repo.commit("tree-artifact", "tree-mode canonical artifact")
    tree_entry = git(repo.path, "ls-tree", tree_commit, "--", path).stdout
    assert tree_entry.startswith("040000 tree ")
    shutil.rmtree(artifact)
    repo.write_line("not_started", policy=None)
    repo.commit("restore-artifact", "restore regular canonical artifact")
    repo.adopt()
    repo.start()
    implementation = repo.product_commit()
    repo.finish(implementation)
    return repo


def replace_frontmatter_status(
    repo: HistoryRepo, path: str, field: str, value: str
) -> None:
    artifact = repo.path / path
    text = artifact.read_text(encoding="utf-8")
    current = next(line for line in text.splitlines() if line.startswith(f"{field}:"))
    artifact.write_text(text.replace(current, f"{field}: {value}", 1), encoding="utf-8")


def chronology_repo(
    tmp_path: Path,
    *,
    bootstrap: bool,
    defect: str | None = None,
    complete: bool = True,
) -> HistoryRepo:
    """Build a prospective Real-Git A/H/S0/S/P/I/Q history."""
    repo = HistoryRepo.create(tmp_path)
    for number in (2, 3, 4, 5):
        repo.write_ac(number)
    repo.write_requirement_admissions()
    repo.write_ac(22)
    replace_frontmatter_status(
        repo, ".proofline/criteria/ac-0022.md", "status", "draft"
    )
    replace_frontmatter_status(
        repo, ".proofline/criteria/ac-0001.md", "status", "draft"
    )
    replace_frontmatter_status(
        repo, ".proofline/criteria/ac-0002.md", "status", "draft"
    )
    if defect == "a-create-wrong-old-status":
        replace_frontmatter_status(
            repo, ".proofline/criteria/ac-0001.md", "status", "active"
        )
    if defect == "a-update-wrong-old-status":
        replace_frontmatter_status(
            repo, ".proofline/criteria/ac-0002.md", "status", "active"
        )
    if defect == "a-retire-wrong-old-status":
        replace_frontmatter_status(
            repo, ".proofline/criteria/ac-0003.md", "status", "draft"
        )
    repo.write_ms(1, "not_started", spec_status="draft", criteria_numbers=(1, 2, 3, 4))
    repo.write_line("not_started", policy="first_parent")
    repo.commit("prospective", "record draft chronology policy")

    replace_frontmatter_status(
        repo, ".proofline/criteria/ac-0022.md", "status", "active"
    )
    repo.commit("policy-A", "activate prospective chronology")

    replace_frontmatter_status(
        repo, ".proofline/lines/line-0001/req-0001.md", "status", "approved"
    )
    replace_frontmatter_status(
        repo, ".proofline/criteria/ac-0001.md", "status", "active"
    )
    replace_frontmatter_status(
        repo, ".proofline/criteria/ac-0002.md", "status", "active"
    )
    replace_frontmatter_status(
        repo, ".proofline/criteria/ac-0003.md", "status", "retired"
    )
    if defect in {"a-create-body", "a-update-body", "a-retire-body", "a-satisfy-body"}:
        number = {
            "a-create-body": 1,
            "a-update-body": 2,
            "a-retire-body": 3,
            "a-satisfy-body": 4,
        }[defect]
        ac = repo.path / f".proofline/criteria/ac-{number:04d}.md"
        ac.write_text(
            ac.read_text(encoding="utf-8").replace(
                "조건이다.", "승인에서 바꾼 조건이다."
            ),
            encoding="utf-8",
        )
    if defect == "a-create-wrong-new-status":
        replace_frontmatter_status(
            repo, ".proofline/criteria/ac-0001.md", "status", "retired"
        )
    if defect == "a-update-wrong-new-status":
        replace_frontmatter_status(
            repo, ".proofline/criteria/ac-0002.md", "status", "retired"
        )
    if defect == "a-retire-wrong-new-status":
        replace_frontmatter_status(
            repo, ".proofline/criteria/ac-0003.md", "status", "active"
        )
    if defect == "a-satisfy-status":
        replace_frontmatter_status(
            repo, ".proofline/criteria/ac-0004.md", "status", "retired"
        )
    if defect == "a-missing-target":
        (repo.path / ".proofline/criteria/ac-0004.md").unlink()
    if defect == "a-path-id-mismatch":
        ac = repo.path / ".proofline/criteria/ac-0004.md"
        ac.write_text(
            ac.read_text(encoding="utf-8").replace('id: "ac-0004"', 'id: "ac-9999"'),
            encoding="utf-8",
        )
    if defect == "a-unrelated-ac":
        ac = repo.path / ".proofline/criteria/ac-0005.md"
        ac.write_text(ac.read_text(encoding="utf-8") + "unrelated\n", encoding="utf-8")
    if defect == "a-req-body":
        req = repo.path / ".proofline/lines/line-0001/req-0001.md"
        req.write_text(
            req.read_text(encoding="utf-8").replace(
                "목표이다.", "승인에서 바꾼 목표이다."
            ),
            encoding="utf-8",
        )
    if bootstrap and defect != "a-not-combined":
        repo.write_ms(
            1, "not_started", spec_status="approved", criteria_numbers=(1, 2, 3, 4)
        )
        if defect == "a-ms-body":
            ms = repo.path / MS
            ms.write_text(
                ms.read_text(encoding="utf-8").replace(
                    "범위이다.", "승인에서 바꾼 범위이다."
                ),
                encoding="utf-8",
            )
    repo.commit("A", "approve REQ and AC baseline")
    if bootstrap and defect == "a-not-combined":
        repo.write_ms(
            1, "not_started", spec_status="approved", criteria_numbers=(1, 2, 3, 4)
        )
        repo.commit("late-bootstrap-spec", "approve bootstrap spec separately")

    if defect == "p-before-h":
        repo.write_ms(1, "in_progress", spec_status="approved")
        repo.commit("P", "start before handoff")

    if defect != "h-missing":
        if defect == "h-non-direct":
            (repo.path / "handoff-note.txt").write_text(
                "intervening\n", encoding="utf-8"
            )
            repo.commit("between-A-H", "intervene between approval and handoff")
        repo.write_line("in_progress", policy="first_parent")
        if defect == "h-body":
            (repo.path / LINE).write_text(
                (repo.path / LINE)
                .read_text(encoding="utf-8")
                .replace('id: "line-0001"', "id: line-0001"),
                encoding="utf-8",
            )
        if defect == "h-multi-file":
            (repo.path / "handoff-extra.txt").write_text(
                "not status-only\n", encoding="utf-8"
            )
        repo.commit("H", "handoff Line")

    if bootstrap:
        if defect == "duplicate-s":
            repo.write_ms(
                1, "not_started", spec_status="draft", criteria_numbers=(1, 2, 3, 4)
            )
            repo.commit("duplicate-S0", "duplicate draft after handoff")
            repo.write_ms(
                1, "not_started", spec_status="approved", criteria_numbers=(1, 2, 3, 4)
            )
            repo.commit("duplicate-S", "duplicate approval after handoff")
    else:
        if defect not in {"missing-s0-s", "p-before-s"}:
            ms = repo.path / MS
            ms.write_text(
                ms.read_text(encoding="utf-8").replace(
                    "범위이다.", "후속 Line 범위이다."
                ),
                encoding="utf-8",
            )
            repo.commit("S0", "persist clean draft")
            if defect == "s-not-direct":
                (repo.path / ".proofline/review-note.md").write_text(
                    "stale review\n", encoding="utf-8"
                )
                repo.commit("between-S0-S", "mutate after reviewed draft")
            replace_frontmatter_status(repo, MS, "spec_status", "approved")
            if defect == "s-body":
                ms.write_text(
                    ms.read_text(encoding="utf-8").replace(
                        "후속 Line 범위이다.", "승인 때 바꾼 범위이다."
                    ),
                    encoding="utf-8",
                )
            repo.commit("S", "record user-approved specification")
            if defect == "duplicate-s":
                repo.write_ms(
                    1, "not_started", spec_status="draft", criteria_numbers=(1, 2, 3, 4)
                )
                repo.commit("second-S0", "second draft")
                repo.write_ms(
                    1,
                    "not_started",
                    spec_status="approved",
                    criteria_numbers=(1, 2, 3, 4),
                )
                repo.commit("second-S", "duplicate approval")
        elif defect == "p-before-s":
            repo.write_ms(
                1, "in_progress", spec_status="draft", criteria_numbers=(1, 2, 3, 4)
            )
            repo.commit("early-P", "start before approval")
            repo.write_ms(
                1, "in_progress", spec_status="approved", criteria_numbers=(1, 2, 3, 4)
            )
            repo.commit("late-S", "approve after start")

    if defect not in {"p-before-h", "p-before-s"}:
        replace_frontmatter_status(repo, MS, "implementation_status", "in_progress")
        repo.commit("P", "start implementation")
    if complete:
        implementation = repo.product_commit("I")
        micro_spec_commit = repo.commits.get("S", repo.commits.get("A"))
        if bootstrap:
            repo.finish(
                implementation,
                "Q",
                micro_spec_commit=micro_spec_commit,
                criteria_numbers=(1, 2, 3, 4),
            )
        else:
            replace_frontmatter_status(repo, MS, "implementation_status", "implemented")
            repo.write_iqc(1, implementation, micro_spec_commit=micro_spec_commit)
            repo.commit("Q", "Q")
    return repo


def test_bootstrap_a_h_p_i_q_chronology_passes(tmp_path: Path) -> None:
    repo = chronology_repo(tmp_path, bootstrap=True)

    assert validate_project(repo.path) == []

    parent = repo.commits["A"] + "^"
    transitions = {
        1: ("draft", "active"),
        2: ("draft", "active"),
        3: ("active", "retired"),
    }
    for number, (old, new) in transitions.items():
        path = f".proofline/criteria/ac-{number:04d}.md"
        before = git(repo.path, "show", f"{parent}:{path}").stdout.encode()
        after = git(repo.path, "show", f"{repo.commits['A']}:{path}").stdout.encode()
        assert implementation_history._status_only_change(
            before, after, "status", old, new, {"draft", "active", "retired"}
        )
    satisfy = ".proofline/criteria/ac-0004.md"
    assert (
        git(repo.path, "show", f"{parent}:{satisfy}").stdout
        == git(repo.path, "show", f"{repo.commits['A']}:{satisfy}").stdout
    )


@pytest.mark.parametrize(
    "defect",
    [
        "h-missing",
        "h-non-direct",
        "h-body",
        "h-multi-file",
        "a-not-combined",
        "p-before-h",
        "duplicate-s",
    ],
)
def test_bootstrap_chronology_fails_closed(tmp_path: Path, defect: str) -> None:
    repo = chronology_repo(tmp_path, bootstrap=True, defect=defect, complete=False)

    assert (MS, "history.spec.chronology") in history_codes(repo)


@pytest.mark.parametrize(
    "defect",
    [
        "a-create-body",
        "a-update-body",
        "a-retire-body",
        "a-satisfy-body",
        "a-create-wrong-old-status",
        "a-create-wrong-new-status",
        "a-update-wrong-old-status",
        "a-update-wrong-new-status",
        "a-retire-wrong-old-status",
        "a-retire-wrong-new-status",
        "a-satisfy-status",
        "a-missing-target",
        "a-path-id-mismatch",
        "a-unrelated-ac",
        "a-req-body",
        "a-ms-body",
    ],
)
def test_bootstrap_admission_transition_fails_closed(
    tmp_path: Path, defect: str
) -> None:
    repo = chronology_repo(tmp_path, bootstrap=True, defect=defect, complete=False)

    assert (MS, "history.spec.chronology") in history_codes(repo)


def test_future_a_h_s0_s_p_i_q_chronology_passes(tmp_path: Path) -> None:
    repo = chronology_repo(tmp_path, bootstrap=False)

    assert validate_project(repo.path) == []


def test_future_chronology_accepts_verifying_to_in_progress_rework_start(
    tmp_path: Path,
) -> None:
    repo = chronology_repo(tmp_path, bootstrap=False)
    repo.write_line("verifying", policy="first_parent")
    repo.commit("verified", "complete hosted verification")
    repo.write_line("in_progress", policy="first_parent")
    repo.write_ms(1, "in_progress", criteria_numbers=(1, 2, 3, 4))
    repo.commit("rework-P", "start hosted correction")

    assert validate_project(repo.path) == []


def test_future_chronology_rejects_an_extra_initial_handoff(tmp_path: Path) -> None:
    repo = chronology_repo(tmp_path, bootstrap=False, complete=False)
    repo.write_line("not_started", policy="first_parent")
    repo.commit("invalid-reset", "reset Line after initial handoff")
    repo.write_line("in_progress", policy="first_parent")
    repo.commit("duplicate-H", "repeat initial handoff")

    assert (MS, "history.spec.chronology") in history_codes(repo)


@pytest.mark.parametrize(
    "defect",
    [
        "missing-s0-s",
        "s-not-direct",
        "s-body",
        "p-before-s",
        "duplicate-s",
    ],
)
def test_future_specification_handoff_fails_closed(tmp_path: Path, defect: str) -> None:
    repo = chronology_repo(tmp_path, bootstrap=False, defect=defect, complete=False)

    assert (MS, "history.spec.chronology") in history_codes(repo)


def test_current_line_0020_bootstrap_history_remains_valid_at_in_progress_head() -> (
    None
):
    assert validate_project(ROOT) == []


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
        b"implementation_status: in_progress\nimplementation_status: implemented\n---\n"
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
        iqc.write_bytes(old_iqc.replace("통과했다.".encode(), "변경했다.".encode()))
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
    ms.write_text(
        ms.read_text(encoding="utf-8").replace("범위이다.", "변경된 승인 범위이다."),
        encoding="utf-8",
    )
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
    repo.finish(
        implementation, micro_spec_commit=repo.commits["start-with-body-marker"]
    )

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
    git(
        repo.path, "merge", "-q", "-s", "ours", "policy-side", "-m", "merge policy side"
    )
    repo.write_line(status, policy="first_parent")

    assert_history_error(repo, LINE, "history.unavailable")


def test_non_git_canonical_project_fails_closed(tmp_path: Path) -> None:
    repo = HistoryRepo.create(tmp_path)
    git_dir = repo.path / ".git"
    shutil.rmtree(git_dir, onerror=remove_readonly)

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


def test_git_spawn_failure_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    assert all(
        isinstance(key, tuple) and all(isinstance(part, str) for part in key)
        for key in session.cache
    )
    assert not any(hasattr(key, "fileno") for key in session.cache)


def test_git_command_output_limit_is_aggregate_across_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = HistoryRepo.create(tmp_path)
    original_popen = subprocess.Popen
    chunk = b"x" * (implementation_history.GIT_OUTPUT_LIMIT // 2 + 1)
    code = (
        "import sys; data = b'x' * %d; sys.stdout.buffer.write(data); sys.stderr.buffer.write(data)"
        % len(chunk)
    )

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
    assert any(
        owner is not None for owner in implementation_history._REAPER_REGISTRY.values()
    )
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
    assert any(
        owner is process for owner in implementation_history._REAPER_REGISTRY.values()
    )
    release_wait.set()
    assert reaped.wait(timeout=1)
    assert not implementation_history._REAPER_REGISTRY


@pytest.mark.parametrize(
    "key",
    [
        "implementation_status:",
        "implementation_status :",
        "'implementation_status':",
        '"implementation_status":',
    ],
)
def test_spec_revision_normalizes_all_supported_top_level_key_spellings(
    key: str,
) -> None:
    content = b"---\n" + key.encode() + b" in_progress\nother: keep\n---\n\nbody\n"
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
        process,
        deadline=implementation_history.time.monotonic() - 1,  # type: ignore[arg-type]
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
    repo.write_line(
        terminal_status, policy=None
    )  # unpersisted bytes are not T evidence

    assert_history_error(repo, LINE, "history.line.legacy.invalid")


@pytest.mark.parametrize("change", ["remove", "change"])
def test_adopted_policy_cannot_be_removed_or_changed(
    tmp_path: Path, change: str
) -> None:
    repo = HistoryRepo.create(tmp_path)
    repo.adopt()
    repo.start()
    repo.write_line("in_progress", policy=None if change == "remove" else "all_parents")
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
def test_malformed_historical_line_is_not_laundered(tmp_path: Path, mode: str) -> None:
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

    assert [
        (error.path, error.code)
        for error in errors
        if error.code.startswith("history.")
    ] == [(LINE, "history.unavailable")]


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

    assert [
        (error.path, error.code)
        for error in errors
        if error.code.startswith("history.")
    ] == [
        (
            ".proofline/lines/line-0001/micro-specs/iqc-0001-002.md",
            "history.unavailable",
        )
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
    blob = git(
        repo.path, "rev-parse", f"{repo.commits['baseline']}:{LINE}"
    ).stdout.strip()
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


@pytest.mark.parametrize("canonical_path", [LINE, IQC], ids=["line", "iqc"])
def test_historical_symlink_mode_canonical_artifact_fails_closed_without_mutation(
    tmp_path: Path, canonical_path: str
) -> None:
    repo = parity_symlink_canonical_artifact(tmp_path, canonical_path)
    assert git(repo.path, "config", "--get", "core.symlinks").stdout.strip() == "false"
    symlink_commit = git(repo.path, "rev-parse", "HEAD^").stdout.strip()
    assert git(
        repo.path, "ls-tree", symlink_commit, "--", canonical_path
    ).stdout.startswith("120000 blob ")
    assert (repo.path / canonical_path).is_file()
    before = repository_snapshot(repo.path)

    history_errors = [
        (error.path, error.code)
        for error in validate_project(repo.path)
        if error.code.startswith("history.")
    ]

    assert history_errors == [(canonical_path, "history.unavailable")]
    assert repository_snapshot(repo.path) == before


def test_historical_tree_mode_canonical_artifact_fails_closed_without_mutation(
    tmp_path: Path,
) -> None:
    repo = parity_tree_canonical_artifact(tmp_path)
    before = repository_snapshot(repo.path)

    history_errors = [
        (error.path, error.code)
        for error in validate_project(repo.path)
        if error.code.startswith("history.")
    ]

    assert history_errors == [(LINE, "history.unavailable")]
    assert repository_snapshot(repo.path) == before


@pytest.mark.parametrize("regular_mode", ["100644", "100755"])
def test_tree_cache_retains_exact_mode_type_oid_and_reuses_blob_read(
    tmp_path: Path, regular_mode: str
) -> None:
    repo = HistoryRepo.create(tmp_path)
    if regular_mode == "100755":
        git(repo.path, "update-index", "--chmod=+x", LINE)
        git(repo.path, "commit", "-qm", "executable canonical artifact")
    commit = git(repo.path, "rev-parse", "HEAD").stdout.strip()
    oid = git(repo.path, "rev-parse", f"{commit}:{LINE}").stdout.strip()
    session = implementation_history._GitSession(repo.path)

    paths = implementation_history._tree_paths(session, commit)

    assert session.tree_entries[commit][LINE] == (regular_mode, "blob", oid)
    assert (
        implementation_history._file(session, commit, LINE, paths)
        == (repo.path / LINE).read_bytes()
    )
    commands_after_first_read = session.commands
    assert (
        implementation_history._file(session, commit, LINE, paths)
        == (repo.path / LINE).read_bytes()
    )
    assert session.commands == commands_after_first_read


@pytest.mark.parametrize(
    "tree_output",
    [
        b"0100644 blob " + b"a" * 40 + b"\t" + LINE.encode() + b"\0",
        b"100644 blob "
        + b"a" * 40
        + b"\t"
        + LINE.encode()
        + b"\0"
        + b"100755 blob "
        + b"b" * 40
        + b"\t"
        + LINE.encode()
        + b"\0",
    ],
    ids=["non-six-character-mode", "duplicate-path"],
)
def test_tree_cache_rejects_non_exact_mode_and_duplicate_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tree_output: bytes
) -> None:
    repo = HistoryRepo.create(tmp_path)
    session = implementation_history._GitSession(repo.path)
    monkeypatch.setattr(
        implementation_history, "_git", lambda *args, **kwargs: tree_output
    )

    with pytest.raises(implementation_history.HistoryUnavailable):
        implementation_history._tree_paths(session, "a" * 40)


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
    git_dir = git_dir.resolve()
    common_dir = Path(git(repo, "rev-parse", "--git-common-dir").stdout.strip())
    if not common_dir.is_absolute():
        common_dir = repo / common_dir
    common_dir = common_dir.resolve()
    status = git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout

    def digest(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    object_database = {
        path.relative_to(common_dir / "objects").as_posix(): (
            "symlink" if path.is_symlink() else "file",
            path.lstat().st_size,
            digest(path),
        )
        for path in sorted((common_dir / "objects").rglob("*"))
        if path.is_symlink() or path.is_file()
    }
    canonical = {
        path.relative_to(repo).as_posix(): path.read_bytes()
        for path in sorted((repo / ".proofline").rglob("*"))
        if path.is_file()
    }
    return {
        "canonical": canonical,
        "index": (git_dir / "index").read_bytes(),
        "index_sha256": digest(git_dir / "index"),
        "head": (git_dir / "HEAD").read_bytes(),
        "symbolic_head": git(repo, "symbolic-ref", "-q", "HEAD", check=False).stdout,
        "refs": git(
            repo,
            "for-each-ref",
            "--format=%(refname):%(objectname):%(symref)",
        ).stdout,
        "object_database": object_database,
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
        assert executable.is_absolute(), (
            "provided installed executable must be absolute"
        )
        assert executable.is_file(), "provided installed executable must exist"
        return executable

    root = tmp_path_factory.mktemp("installed-wheel")
    hosted = os.environ.get("PROOFLINE_HOSTED_CANDIDATE_WHEEL")
    if hosted:
        wheel = Path(hosted)
        assert wheel.is_absolute() and wheel.is_file()
    else:
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
    executable = venv / (
        "Scripts/proofline.exe" if os.name == "nt" else "bin/proofline"
    )
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
    git(
        repo.path,
        "merge",
        "-q",
        "--no-ff",
        "product-side",
        "-m",
        "lifecycle-only merge",
    )
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
    git(
        repo.path, "merge", "-q", "-s", "ours", "policy-side", "-m", "merge policy side"
    )
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
    blob = git(
        repo.path, "rev-parse", f"{repo.commits['baseline']}:{LINE}"
    ).stdout.strip()
    object_path = repo.path / ".git/objects" / blob[:2] / blob[2:]
    assert object_path.is_file()
    unlink_git_object(object_path)
    return repo


def parity_shallow_history(tmp_path: Path) -> HistoryRepo:
    source = build_valid_cycle(tmp_path / "source")
    clone = tmp_path / "shallow"
    cloned = subprocess.run(
        ("git", "clone", "-q", "--depth", "1", f"file://{source.path}", str(clone)),
        text=True,
        capture_output=True,
        check=False,
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
    ] == [(expected_path, "history.unavailable")]


PARITY_SCENARIOS = [
    pytest.param(
        HistoryParityScenario(
            "p-before-b-valid", lambda p: build_valid_cycle(p, order="start-first")
        ),
        id="p-before-b-valid",
    ),
    pytest.param(
        HistoryParityScenario(
            "b-before-p-valid", lambda p: build_valid_cycle(p, order="baseline-first")
        ),
        id="b-before-p-valid",
    ),
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
    pytest.param(
        HistoryParityScenario(
            "terminal-t-after-a-delivered",
            lambda p: parity_terminal_after_activation(p, "delivered"),
            "history.line.legacy.invalid",
        ),
        id="terminal-t-after-a-delivered",
    ),
    pytest.param(
        HistoryParityScenario(
            "terminal-t-after-a-cancelled",
            lambda p: parity_terminal_after_activation(p, "cancelled"),
            "history.line.legacy.invalid",
        ),
        id="terminal-t-after-a-cancelled",
    ),
    pytest.param(
        HistoryParityScenario(
            "terminal-t-after-a",
            lambda p: parity_terminal_after_activation(p, "delivered"),
            "history.line.legacy.invalid",
        ),
        id="terminal-t-after-a",
    ),
    pytest.param(
        HistoryParityScenario(
            "fieldless-non-terminal",
            lambda p: HistoryRepo.create(p),
            "history.line.policy.missing",
        ),
        id="fieldless-non-terminal",
    ),
    pytest.param(
        HistoryParityScenario(
            "second-parent-terminal-delivered",
            lambda p: parity_second_parent_terminal(p, "delivered"),
            "history.line.legacy.invalid",
        ),
        id="second-parent-terminal-delivered",
    ),
    pytest.param(
        HistoryParityScenario(
            "second-parent-terminal-cancelled",
            lambda p: parity_second_parent_terminal(p, "cancelled"),
            "history.line.legacy.invalid",
        ),
        id="second-parent-terminal-cancelled",
    ),
    pytest.param(
        HistoryParityScenario(
            "policy-removal",
            lambda p: parity_policy_change(p, "remove"),
            "history.line.policy.changed",
        ),
        id="policy-removal",
    ),
    pytest.param(
        HistoryParityScenario(
            "policy-change",
            lambda p: parity_policy_change(p, "change"),
            "history.line.policy.changed",
        ),
        id="policy-change",
    ),
    pytest.param(
        HistoryParityScenario(
            "policy-delete-restore",
            parity_policy_delete_restore,
            "history.line.policy.changed",
        ),
        id="policy-delete-restore",
    ),
    pytest.param(
        HistoryParityScenario(
            "line-artifact-delete", parity_line_delete, "history.line.policy.changed"
        ),
        id="line-artifact-delete",
    ),
    pytest.param(
        HistoryParityScenario(
            "line-artifact-delete-restore",
            parity_line_delete_restore,
            "history.line.policy.changed",
        ),
        id="line-artifact-delete-restore",
    ),
    pytest.param(
        HistoryParityScenario(
            "historical-line-duplicate-policy",
            lambda p: parity_historical_line_duplicate(p, "implementation_history"),
            "history.unavailable",
        ),
        id="historical-line-duplicate-policy",
    ),
    pytest.param(
        HistoryParityScenario(
            "historical-line-duplicate-execution",
            lambda p: parity_historical_line_duplicate(p, "execution_status"),
            "history.unavailable",
        ),
        id="historical-line-duplicate-execution",
    ),
    pytest.param(
        HistoryParityScenario(
            "malformed-historical-line-deleted",
            lambda p: parity_malformed_historical_line(p, "deleted"),
            "history.unavailable",
        ),
        id="malformed-historical-line-deleted",
    ),
    pytest.param(
        HistoryParityScenario(
            "malformed-historical-line-normalized",
            lambda p: parity_malformed_historical_line(p, "normalized"),
            "history.unavailable",
        ),
        id="malformed-historical-line-normalized",
    ),
    pytest.param(
        HistoryParityScenario(
            "historical-ms-duplicate-status-normalized",
            parity_historical_ms_duplicate_normalized,
            "history.unavailable",
        ),
        id="historical-ms-duplicate-status-normalized",
    ),
    pytest.param(
        HistoryParityScenario(
            "historical-ms-duplicate-spec-deleted",
            parity_historical_ms_duplicate_deleted,
            "history.unavailable",
        ),
        id="historical-ms-duplicate-spec-deleted",
    ),
    pytest.param(
        HistoryParityScenario(
            "historical-iqc-duplicate-implementation-reworked",
            parity_historical_iqc_duplicate_reworked,
            "history.unavailable",
        ),
        id="historical-iqc-duplicate-implementation-reworked",
    ),
    pytest.param(
        HistoryParityScenario(
            "malformed-historical-unselected-iqc",
            parity_malformed_historical_unselected_iqc,
            "history.unavailable",
        ),
        id="malformed-historical-unselected-iqc",
    ),
    pytest.param(
        HistoryParityScenario(
            "implementation-before-baseline",
            parity_implementation_before_baseline,
            "history.ms.order",
        ),
        id="implementation-before-baseline",
    ),
    pytest.param(
        HistoryParityScenario(
            "start-before-approved-spec",
            parity_start_before_approved_spec,
            "history.ms.order",
        ),
        id="start-before-approved-spec",
    ),
    pytest.param(
        HistoryParityScenario(
            "rework-missing-in-progress",
            parity_rework_missing_start,
            "history.ms.transition",
        ),
        id="rework-missing-in-progress",
    ),
    pytest.param(
        HistoryParityScenario(
            "invalid-reset", parity_invalid_reset, "history.ms.transition"
        ),
        id="invalid-reset",
    ),
    pytest.param(
        HistoryParityScenario(
            "second-parent-start", parity_second_parent_start, "history.ms.transition"
        ),
        id="second-parent-start",
    ),
    pytest.param(
        HistoryParityScenario(
            "second-parent-implementation",
            parity_second_parent_implementation,
            "history.ms.binding",
        ),
        id="second-parent-implementation",
    ),
    pytest.param(
        HistoryParityScenario(
            "current-terminal-uncommitted-restoration-delivered",
            lambda p: parity_current_terminal_restoration(p, "delivered"),
            "history.line.legacy.invalid",
        ),
        id="current-terminal-uncommitted-restoration-delivered",
    ),
    pytest.param(
        HistoryParityScenario(
            "current-terminal-uncommitted-restoration-cancelled",
            lambda p: parity_current_terminal_restoration(p, "cancelled"),
            "history.line.legacy.invalid",
        ),
        id="current-terminal-uncommitted-restoration-cancelled",
    ),
    pytest.param(
        HistoryParityScenario(
            "reapproval-and-start-same-commit",
            parity_reapproval_and_start,
            "history.ms.order",
        ),
        id="reapproval-and-start-same-commit",
    ),
    pytest.param(
        HistoryParityScenario(
            "stale-approved-bytes-binding",
            parity_stale_approved_bytes,
            "history.ms.order",
        ),
        id="stale-approved-bytes-binding",
    ),
    pytest.param(
        HistoryParityScenario(
            "current-draft-active", parity_current_draft_active, "history.ms.order"
        ),
        id="current-draft-active",
    ),
    pytest.param(
        HistoryParityScenario(
            "current-withdrawn-active",
            parity_current_withdrawn_active,
            "history.ms.order",
        ),
        id="current-withdrawn-active",
    ),
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
    pytest.param(
        HistoryParityScenario(
            "unresolved-implementation-commit",
            parity_unresolved_implementation,
            "history.ms.binding",
        ),
        id="unresolved-implementation-commit",
    ),
    pytest.param(
        HistoryParityScenario(
            "malformed-historical-artifact",
            parity_malformed_history,
            "history.unavailable",
        ),
        id="malformed-historical-artifact",
    ),
    pytest.param(
        HistoryParityScenario(
            "missing-object-history-object-unavailable",
            parity_missing_object,
            "history.unavailable",
        ),
        id="missing-object-history-object-unavailable",
    ),
    pytest.param(
        HistoryParityScenario(
            "shallow-history", parity_shallow_history, "history.unavailable"
        ),
        id="shallow-history",
    ),
    pytest.param(
        HistoryParityScenario(
            "multi-ms-single-violation",
            parity_multi_ms_violation,
            "history.ms.transition",
        ),
        id="multi-ms-single-violation",
    ),
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
    expected = frozenset(
        {
            "p-before-b-valid",
            "b-before-p-valid",
            "initial-valid",
            "rework-valid",
            "multiple-implementations-bind-final",
            "multiple-implementations-bind-first",
            "product-in-progress-transition",
            "dirty-policy-line",
            "persisted-fresh-rework-in-progress",
            "dirty-reset-in-progress",
            "dirty-reset-not-started",
            "dirty-edit-same-status",
            "missing-current-micro-spec",
            "malformed-current-micro-spec",
            "deleted-historical-micro-spec",
            "deleted-malformed-historical-micro-spec",
            "dirty-iqc-rollback",
            "dirty-iqc-edit",
            "missing-current-iqc",
            "malformed-current-iqc",
            "stale-iqc-rework",
            "lifecycle-only-merge",
            "empty-implementation",
            "legacy-delivered",
            "legacy-cancelled",
            "terminal-t-equal-a",
            "fieldless-terminal-before-later-activation-delivered",
            "fieldless-terminal-before-later-activation-cancelled",
            "terminal-t-after-a-delivered",
            "terminal-t-after-a-cancelled",
            "terminal-t-after-a",
            "fieldless-non-terminal",
            "second-parent-terminal-delivered",
            "second-parent-terminal-cancelled",
            "policy-removal",
            "policy-change",
            "policy-delete-restore",
            "implementation-before-baseline",
            "line-artifact-delete",
            "line-artifact-delete-restore",
            "historical-line-duplicate-policy",
            "historical-line-duplicate-execution",
            "malformed-historical-line-deleted",
            "malformed-historical-line-normalized",
            "historical-ms-duplicate-status-normalized",
            "historical-ms-duplicate-spec-deleted",
            "historical-iqc-duplicate-implementation-reworked",
            "malformed-historical-unselected-iqc",
            "start-before-approved-spec",
            "rework-missing-in-progress",
            "invalid-reset",
            "second-parent-start",
            "second-parent-implementation",
            "current-terminal-uncommitted-restoration-delivered",
            "current-terminal-uncommitted-restoration-cancelled",
            "reapproval-and-start-same-commit",
            "stale-approved-bytes-binding",
            "current-draft-active",
            "current-withdrawn-active",
            "direct-not-started-to-implemented",
            "historical-direct-then-valid-cycle",
            "historical-invalid-rework-then-valid",
            "historical-missing-iqc-then-valid",
            "historical-malformed-iqc-then-valid",
            "historical-reused-iqc-then-valid",
            "two-valid-cycles",
            "same-commit-p-equals-i",
            "same-commit-i-equals-q",
            "unresolved-implementation-commit",
            "malformed-historical-artifact",
            "missing-object-history-object-unavailable",
            "shallow-history",
            "multi-ms-single-violation",
            "second-parent-only-marker",
            "later-lifecycle-only-binding",
            "git-unavailable",
            "git-spawn-failure",
        }
    )
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
        {"PATH": str(tmp_path / "missing-git")} if scenario.unavailable_git else None
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


@pytest.mark.parametrize("conflict", [False, True], ids=["positive", "conflict"])
def test_installed_wheel_and_source_preserve_integration_object_store(
    tmp_path: Path, installed_wheel_cli: Path, conflict: bool
) -> None:
    from test_integration_history import build_candidate, quarantined_merge_tree

    repo, main_parent, line_head, _ = build_candidate(
        tmp_path / "candidate", conflict_resolution=conflict
    )
    if not conflict:
        expected_tree = quarantined_merge_tree(repo.path, main_parent, line_head)
        expected_object = Path(
            git(
                repo.path,
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                f"objects/{expected_tree[:2]}/{expected_tree[2:]}",
            ).stdout.strip()
        )
        assert expected_object.is_file()
        expected_object.unlink()
    before = repository_snapshot(repo.path)

    source = run_source(repo.path)
    assert repository_snapshot(repo.path) == before
    wheel = run_wheel(installed_wheel_cli, repo.path)
    assert repository_snapshot(repo.path) == before

    assert (wheel.returncode, wheel.stdout, wheel.stderr) == (
        source.returncode,
        source.stdout,
        source.stderr,
    )
    if conflict:
        assert source.returncode != 0
        assert ": history.integration.tree:" in source.stderr
    else:
        assert source.returncode == 0


@pytest.mark.parametrize("effective_result", ["failed", "blocked"])
def test_installed_wheel_matches_source_effective_dqc_delivery_diagnostic(
    tmp_path: Path, installed_wheel_cli: Path, effective_result: str
) -> None:
    from test_integration_history import build_candidate, deliver, write_dqc

    repo, _, _, candidate = build_candidate(tmp_path / effective_result)
    write_dqc(repo, candidate)
    write_dqc(repo, candidate, result=effective_result)
    deliver(repo)
    before = repository_snapshot(repo.path)

    source = run_source(repo.path)
    assert repository_snapshot(repo.path) == before
    wheel = run_wheel(installed_wheel_cli, repo.path)
    assert repository_snapshot(repo.path) == before

    assert (wheel.returncode, wheel.stdout, wheel.stderr) == (
        source.returncode,
        source.stdout,
        source.stderr,
    )
    assert source.returncode != 0
    assert ": history.integration.dqc:" in source.stderr
