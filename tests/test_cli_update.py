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


def test_update_cli_reports_operational_failure(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "run_update", lambda **kwargs: (_ for _ in ()).throw(cli.UpdateError("checksum mismatch")))
    assert cli.main(["update"]) == 1
    assert capsys.readouterr().err == "update failed: checksum mismatch\n"
