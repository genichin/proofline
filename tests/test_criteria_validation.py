import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from proofline.validator import _validate_schema_candidate, validate_project

FIXTURE = Path(__file__).parent / "fixtures" / "valid-minimal"
ROOT = Path(__file__).resolve().parents[1]
REQ = ".proofline/lines/line-0001/req-0001.md"


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
    assert direct_url.get("url") == wheel.resolve().as_uri(), (
        "installed candidate wheel path mismatch"
    )
    return wheel


def copy_valid_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    shutil.copytree(FIXTURE, project)
    return project


def replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def errors_for(project: Path, path: str):
    return [error for error in validate_project(project) if error.path == path]


def git(project: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=project,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def initialize_main(project: Path) -> None:
    git(project, "init", "-b", "main")
    git(project, "config", "user.name", "Criteria Test")
    git(project, "config", "user.email", "criteria@example.invalid")
    commit_all(project, "active baseline")


def commit_all(project: Path, message: str) -> None:
    git(project, "add", ".")
    git(project, "commit", "-m", message)


def add_update_owner(project: Path, number: int = 2, status: str = "draft") -> str:
    line_id = f"line-{number:04d}"
    req_id = f"req-{number:04d}"
    line = project / ".proofline/lines" / line_id
    line.mkdir(parents=True)
    allocator = project / ".proofline/identities.json"
    value = json.loads(allocator.read_text(encoding="utf-8"))
    value["next_line_number"] = max(value["next_line_number"], number + 1)
    allocator.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    (line / f"{line_id}.md").write_text(
        f'---\nid: "{line_id}"\n---\n',
        encoding="utf-8",
    )
    (line / f"dcy-{number:04d}.md").write_text(
        f'''---
id: "dcy-{number:04d}"
status: confirmed
---

# Update discovery

## Problem

Update.

## Evidence

Evidence.

## Scope

Scope.

## Out of Scope

None.
''',
        encoding="utf-8",
    )
    req_path = f".proofline/lines/{line_id}/{req_id}.md"
    (project / req_path).write_text(
        f'''---
id: "{req_id}"
status: {status}
discovery: dcy-{number:04d}
criteria:
  create: []
  update:
    - ac-0003
  retire: []
  satisfy: []
---

# Update REQ

## Objective

Update.

## Scope

Scope.

## Non-Goals

None.
''',
        encoding="utf-8",
    )
    return req_path


def make_satisfy_binding(project: Path) -> None:
    req = project / REQ
    replace(req, "    - ac-0003\n  update: []", "  update: []")
    replace(req, "  retire: []", "  retire: []\n  satisfy:\n    - ac-0003")


def snapshot(project: Path) -> tuple[dict[str, bytes], str, str, str, str]:
    files = {
        path.relative_to(project).as_posix(): path.read_bytes()
        for path in project.rglob("*")
        if path.is_file() and ".git" not in path.parts
    }
    return (
        files,
        git(project, "status", "--porcelain=v1"),
        git(project, "show-ref"),
        git(project, "symbolic-ref", "-q", "HEAD"),
        git(project, "rev-parse", "HEAD"),
    )


def validate_without_mutation(project: Path):
    before = snapshot(project)
    errors = validate_project(project)
    assert snapshot(project) == before
    return errors


def inactive_errors(project: Path):
    return [
        error
        for error in validate_without_mutation(project)
        if error.path == REQ and error.code == "reference.inactive"
    ]


def prepare_update_history(tmp_path: Path, *, owner_count: int = 1) -> Path:
    project = copy_valid_project(tmp_path)
    make_satisfy_binding(project)
    for number in range(2, 2 + owner_count):
        add_update_owner(project, number)
    initialize_main(project)
    return project


def test_req_accepts_optional_satisfy_with_active_target(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    req = project / REQ
    replace(req, "    - ac-0003\n  update: []", "  update: []")
    replace(req, "  retire: []", "  retire: []\n  satisfy:\n    - ac-0003")

    assert _validate_schema_candidate(project) == []


def test_active_satisfy_baseline_is_history_independent_and_read_only(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    make_satisfy_binding(project)
    initialize_main(project)

    assert validate_without_mutation(project) == []


def test_uncommitted_update_draft_accepts_exact_prior_approved_satisfy(
    tmp_path: Path,
) -> None:
    project = prepare_update_history(tmp_path)
    ac = project / ".proofline/criteria/ac-0003.md"
    replace(ac, "status: active", "status: draft")

    assert validate_without_mutation(project) == []


def test_committed_active_to_draft_accepts_exact_prior_approved_satisfy(
    tmp_path: Path,
) -> None:
    project = prepare_update_history(tmp_path)
    ac = project / ".proofline/criteria/ac-0003.md"
    replace(ac, "status: active", "status: draft")
    commit_all(project, "begin AC update")

    assert validate_without_mutation(project) == []


def test_non_main_committed_draft_requires_main_transition_evidence(tmp_path: Path) -> None:
    project = prepare_update_history(tmp_path)
    ac_path = ".proofline/criteria/ac-0003.md"
    ac = project / ac_path
    git(project, "switch", "-c", "update-ac")
    replace(ac, "status: active", "status: draft")
    commit_all(project, "commit AC draft outside main")

    assert git(project, "status", "--porcelain=v1") == ""
    assert ac.read_bytes() == subprocess.run(
        ("git", "show", f"HEAD:{ac_path}"),
        cwd=project,
        capture_output=True,
        check=True,
    ).stdout
    assert b"status: active" in subprocess.run(
        ("git", "show", f"refs/heads/main:{ac_path}"),
        cwd=project,
        capture_output=True,
        check=True,
    ).stdout

    errors = inactive_errors(project)
    assert errors and "ac-0003" in errors[0].message


def test_status_only_draft_to_active_approval_commit_validates(tmp_path: Path) -> None:
    project = prepare_update_history(tmp_path)
    ac = project / ".proofline/criteria/ac-0003.md"
    replace(ac, "status: active", "status: draft")
    commit_all(project, "begin AC update")
    replace(ac, "status: draft", "status: active")
    commit_all(project, "approve AC update")

    assert validate_without_mutation(project) == []


def test_source_cli_accepts_committed_update_draft_lifecycle(tmp_path: Path) -> None:
    project = prepare_update_history(tmp_path)
    replace(project / ".proofline/criteria/ac-0003.md", "status: active", "status: draft")
    commit_all(project, "begin AC update")
    before = snapshot(project)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from proofline.cli import main; raise SystemExit(main())",
            "validate",
        ],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert snapshot(project) == before


def test_installed_wheel_cli_accepts_committed_update_draft_lifecycle(
    tmp_path: Path,
) -> None:
    project = prepare_update_history(tmp_path)
    replace(project / ".proofline/criteria/ac-0003.md", "status: active", "status: draft")
    commit_all(project, "begin AC update")
    before = snapshot(project)
    wheel = _hosted_candidate_wheel()
    if wheel is not None:
        pass
    else:
        dist = tmp_path / "dist"
        dist.mkdir()
        built = subprocess.run(
            ["uv", "build", "--refresh", "--wheel", "--out-dir", str(dist)],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        assert built.returncode == 0, built.stderr
        wheel = next(dist.glob("proofline-*.whl"))
    venv = tmp_path / "wheel-venv"
    created = subprocess.run(
        ["uv", "venv", "--python", sys.executable, str(venv)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable = venv / ("Scripts/proofline.exe" if os.name == "nt" else "bin/proofline")
    installed = subprocess.run(
        ["uv", "pip", "install", "--refresh", "--python", str(python), str(wheel)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert installed.returncode == 0, installed.stderr
    provenance = subprocess.run(
        [str(python), "-I", "-c", "import proofline; print(proofline.__file__)"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert provenance.returncode == 0, provenance.stderr
    assert "site-packages" in provenance.stdout
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}

    completed = subprocess.run(
        [str(executable), "validate"],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert snapshot(project) == before


def test_update_transition_rejects_new_approved_satisfy_binding(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    add_update_owner(project)
    initialize_main(project)
    make_satisfy_binding(project)
    replace(project / ".proofline/criteria/ac-0003.md", "status: active", "status: draft")

    errors = inactive_errors(project)
    assert errors and "ac-0003" in errors[0].message


@pytest.mark.parametrize(
    "baseline_change",
    [
        "body",
        "status",
        "criteria",
    ],
)
def test_update_transition_rejects_changed_approved_satisfy_bytes(
    tmp_path: Path, baseline_change: str
) -> None:
    project = prepare_update_history(tmp_path)
    req = project / REQ
    if baseline_change == "body":
        replace(req, "검증 가능한 결과를 제공한다.", "변경된 결과를 제공한다.")
    elif baseline_change == "status":
        replace(req, "status: approved", "status: draft")
        commit_all(project, "draft satisfy owner")
        replace(req, "status: draft", "status: approved")
    else:
        replace(req, "    - ac-0002\n  update: []", "  update:\n    - ac-0002")
    replace(project / ".proofline/criteria/ac-0003.md", "status: active", "status: draft")

    errors = inactive_errors(project)
    assert errors and "ac-0003" in errors[0].message


@pytest.mark.parametrize("status", ["draft", "withdrawn"])
def test_update_transition_rejects_non_approved_satisfy_owner(
    tmp_path: Path, status: str
) -> None:
    project = prepare_update_history(tmp_path)
    replace(project / REQ, "status: approved", f"status: {status}")
    replace(project / ".proofline/criteria/ac-0003.md", "status: active", "status: draft")

    errors = inactive_errors(project)
    assert errors and "ac-0003" in errors[0].message


@pytest.mark.parametrize("owner_count", [0, 2])
def test_update_transition_rejects_non_unique_update_owner(
    tmp_path: Path, owner_count: int
) -> None:
    project = prepare_update_history(tmp_path, owner_count=owner_count)
    replace(project / ".proofline/criteria/ac-0003.md", "status: active", "status: draft")

    errors = inactive_errors(project)
    assert errors and "ac-0003" in errors[0].message


def test_create_only_draft_without_prior_active_revision_is_rejected(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    make_satisfy_binding(project)
    add_update_owner(project)
    replace(project / ".proofline/criteria/ac-0003.md", "status: active", "status: draft")

    errors = [
        error
        for error in validate_project(project)
        if error.path == REQ and error.code == "reference.inactive"
    ]
    assert errors and "ac-0003" in errors[0].message


def test_initial_git_revision_with_draft_target_is_rejected(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    make_satisfy_binding(project)
    add_update_owner(project)
    ac = project / ".proofline/criteria/ac-0003.md"
    replace(ac, "status: active", "status: draft")
    initialize_main(project)

    errors = inactive_errors(project)
    assert errors and "ac-0003" in errors[0].message


def test_draft_history_with_no_prior_active_revision_is_rejected(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    make_satisfy_binding(project)
    owner = add_update_owner(project)
    ac = project / ".proofline/criteria/ac-0003.md"
    replace(ac, "status: active", "status: draft")
    initialize_main(project)
    replace(project / owner, "Update.", "Still updating.")
    commit_all(project, "continue create-only draft")

    errors = inactive_errors(project)
    assert errors and "ac-0003" in errors[0].message


def test_req_rejects_unknown_criteria_key(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    req = project / REQ
    replace(req, "  retire: []", "  retire: []\n  verify: []")

    assert any(error.code == "criteria.invalid" for error in errors_for(project, REQ))


def test_req_rejects_non_list_criteria_value(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    req = project / REQ
    replace(req, "  update: []", "  update: ac-0002")

    assert any(error.code == "criteria.invalid" for error in errors_for(project, REQ))


def test_req_rejects_empty_criteria_union(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    req = project / REQ
    frontmatter, body = req.read_text(encoding="utf-8").split("---", 2)[1:]
    data = yaml.safe_load(frontmatter)
    data["criteria"] = {"create": [], "update": [], "retire": [], "satisfy": []}
    req.write_text("---\n" + yaml.safe_dump(data, sort_keys=False) + "---" + body, encoding="utf-8")

    assert any(error.code == "criteria.empty" for error in errors_for(project, REQ))


def test_req_rejects_ac_in_multiple_criteria_lists(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    req = project / REQ
    replace(req, "  retire: []", "  retire: []\n  satisfy:\n    - ac-0001")

    duplicate = [error for error in errors_for(project, REQ) if error.code == "criteria.duplicate"]
    assert duplicate
    assert "ac-0001" in duplicate[0].message


def test_req_rejects_same_ac_repeated_inside_one_list(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    req = project / REQ
    replace(
        req,
        "    - ac-0001\n    - ac-0002",
        "    - ac-0001\n    - ac-0001\n    - ac-0002",
    )

    assert [error.code for error in errors_for(project, REQ)] == ["criteria.duplicate"]


def test_satisfy_rejects_retired_ac(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    req = project / REQ
    ac = project / ".proofline/criteria/ac-0003.md"
    replace(req, "    - ac-0003\n  update: []", "  update: []")
    replace(req, "  retire: []", "  retire: []\n  satisfy:\n    - ac-0003")
    replace(ac, "status: active", "status: retired")

    status_errors = [
        error for error in errors_for(project, REQ) if error.code == "reference.inactive"
    ]
    assert status_errors
    assert "ac-0003" in status_errors[0].message


def test_approved_satisfy_keeps_binding_after_later_retirement(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    make_satisfy_binding(project)
    initialize_main(project)
    retirement_req = project / add_update_owner(project, status="approved")
    replace(retirement_req, "  update:\n    - ac-0003", "  update: []")
    replace(retirement_req, "  retire: []", "  retire:\n    - ac-0003")
    replace(project / ".proofline/criteria/ac-0003.md", "status: active", "status: retired")

    assert inactive_errors(project) == []


def test_withdrawn_req_still_rejects_inactive_satisfy_target(tmp_path: Path) -> None:
    project = copy_valid_project(tmp_path)
    req = project / REQ
    ac = project / ".proofline/criteria/ac-0003.md"
    replace(req, "status: approved", "status: withdrawn")
    replace(req, "    - ac-0003\n  update: []", "  update: []")
    replace(req, "  retire: []", "  retire: []\n  satisfy:\n    - ac-0003")
    replace(ac, "status: active", "status: retired")

    assert any(error.code == "reference.inactive" for error in errors_for(project, REQ))
