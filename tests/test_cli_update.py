from __future__ import annotations

import pytest

from proofline import cli


def test_update_cli_prints_contract_status(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        cli,
        "run_update",
        lambda **kwargs: cli.UpdateResult(
            current="0.1.0",
            target="0.2.0",
            provenance="archive",
            status="update-available",
            exit_code=0,
            mutate=False,
        ),
    )
    assert cli.main(["update", "--check"]) == 0
    assert capsys.readouterr().out == (
        "current: 0.1.0\n"
        "target: 0.2.0\n"
        "provenance: archive\n"
        "status: update-available\n"
    )


def test_update_cli_does_not_require_a_live_current_directory(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def deleted_cwd() -> None:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(cli.Path, "cwd", deleted_cwd)
    monkeypatch.setattr(
        cli,
        "run_update",
        lambda **kwargs: cli.UpdateResult(
            current="0.4.1",
            target="0.4.1",
            provenance="archive",
            status="already-current",
            exit_code=0,
            mutate=False,
        ),
    )

    assert cli.main(["update"]) == 0
    assert capsys.readouterr().out.endswith("status: already-current\n")


def test_update_cli_reports_operational_failure(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "run_update", lambda **kwargs: (_ for _ in ()).throw(cli.UpdateError("checksum mismatch")))
    assert cli.main(["update"]) == 1
    assert capsys.readouterr().err == "update failed: checksum mismatch\n"
