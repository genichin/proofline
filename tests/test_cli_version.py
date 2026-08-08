from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from proofline import cli


def test_global_version_uses_installed_distribution_metadata(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.metadata, "version", lambda name: "9.8.7")

    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])

    assert exc.value.code == 0
    assert capsys.readouterr().out == "proofline 9.8.7\n"


def test_retired_home_cli_options_are_not_accepted() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["init"])
    assert exc.value.code == 2

    with pytest.raises(SystemExit) as exc:
        cli.main(["--no-home-reconcile", "validate"])
    assert exc.value.code == 2


def test_manifest_version_is_v080() -> None:
    manifest = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    )
    assert manifest["project"]["version"] == "0.8.0"
