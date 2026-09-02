import subprocess
import os
import sys
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "valid-minimal"
ROOT = Path(__file__).resolve().parents[1]


def run_validate(root: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-c", "from proofline.cli import main; raise SystemExit(main())", "validate"],
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_cli_returns_zero_for_valid_project_config(tmp_path: Path) -> None:
    (tmp_path / ".proofline" / "lines").mkdir(parents=True)
    (tmp_path / ".proofline" / "criteria").mkdir()
    (tmp_path / "proofline.yaml").write_text(
        "schema_version: 1\nartifact_root: .proofline\n",
        encoding="utf-8",
    )
    (tmp_path / ".proofline/identities.json").write_text(
        '{\n  "schema_version": 1,\n  "next_line_number": 1,\n  "next_ac_number": 1\n}\n'
    )

    result = run_validate(tmp_path)

    assert result.returncode == 0
    assert result.stderr == ""


def test_cli_reports_config_error_and_returns_nonzero(tmp_path: Path) -> None:
    result = run_validate(tmp_path)

    assert result.returncode == 1
    assert "proofline.yaml" in result.stderr
    assert "config.missing" in result.stderr


def test_cli_validates_minimal_fixture_without_modifying_inputs() -> None:
    before = {
        path.relative_to(FIXTURE): path.read_bytes()
        for path in FIXTURE.rglob("*")
        if path.is_file()
    }

    result = run_validate(FIXTURE)

    after = {
        path.relative_to(FIXTURE): path.read_bytes()
        for path in FIXTURE.rglob("*")
        if path.is_file()
    }
    assert result.returncode == 0, result.stderr
    assert result.stderr == (
        "warning: .proofline/lines/line-0001/line-0001.md: "
        "line.status.missing: Line status가 없습니다.\n"
    )
    assert after == before


def test_cli_does_not_warn_when_line_status_exists(tmp_path: Path) -> None:
    import shutil

    fixture = tmp_path / "project"
    shutil.copytree(FIXTURE, fixture)
    line = fixture / ".proofline/lines/line-0001/line-0001.md"
    line.write_text(
        line.read_text(encoding="utf-8").replace(
            "id: line-0001", "id: line-0001\nstatus: custom"
        ),
        encoding="utf-8",
    )

    result = run_validate(fixture)

    assert result.returncode == 0
    assert result.stderr == ""
