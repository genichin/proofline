from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from proofline import cli


def test_global_version_uses_installed_distribution_metadata(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.metadata, "version", lambda name: "9.8.7")
    reconciled: list[str] = []
    monkeypatch.setattr(cli, "reconcile_existing_home", lambda: reconciled.append("yes"))
    monkeypatch.setattr(cli, "_is_update_postverification", lambda: True)

    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])

    assert exc.value.code == 0
    assert reconciled == ["yes"]
    assert capsys.readouterr().out == "proofline 9.8.7\n"


def test_internal_version_verification_skips_home_reconciliation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.metadata, "version", lambda name: "9.8.7")
    reconciled: list[str] = []
    monkeypatch.setattr(cli, "reconcile_existing_home", lambda: reconciled.append("yes"))
    monkeypatch.setattr(cli, "_is_update_postverification", lambda: True)

    with pytest.raises(SystemExit) as exc:
        cli.main(["--no-home-reconcile", "--version"])

    assert exc.value.code == 0
    assert reconciled == []
    assert capsys.readouterr().out == "proofline 9.8.7\n"


def test_manifest_version_is_v050() -> None:
    manifest = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert manifest["project"]["version"] == "0.5.0"
