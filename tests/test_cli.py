import subprocess
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "valid-minimal"


def run_validate(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["proofline", "validate"],
        cwd=root,
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
    assert result.returncode == 0
    assert result.stderr == ""
    assert after == before
