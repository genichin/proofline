from __future__ import annotations

from importlib import metadata

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


def test_distribution_metadata_matches_manifest_version() -> None:
    assert metadata.version("proofline") == "0.1.0"
