from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_built_wheel_contains_and_reads_canonical_schema_templates(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    build = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    wheel = next(dist.glob("proofline-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert "proofline_schema_v1_templates/artifacts/line.md" in names
        assert "proofline_schema_v1_templates/artifacts/discovery.md" in names
        assert "proofline_schema_v1_templates/artifacts/dqc.md" in names
        unpacked = tmp_path / "wheel"
        archive.extractall(unpacked)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(unpacked)
    check = subprocess.run(
        [
            sys.executable,
            "-c",
            "from proofline.line_writer import _read_template; "
            "assert '{{LINE_ID}}' in _read_template('line.md'); "
            "assert '{{DISCOVERY_ID}}' in _read_template('discovery.md'); "
            "assert 'Mandatory Line-Level Checks' in _read_template('dqc.md')",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert check.returncode == 0, check.stderr

    venv = tmp_path / "venv"
    create_venv = subprocess.run(
        ["uv", "venv", "--python", sys.executable, str(venv)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert create_venv.returncode == 0, create_venv.stderr
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    proofline = venv / ("Scripts/proofline.exe" if os.name == "nt" else "bin/proofline")
    install = subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(wheel)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert install.returncode == 0, install.stderr
    provenance = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            "from importlib.metadata import version; "
            "from pathlib import Path; import proofline; "
            "p=Path(proofline.__file__).resolve(); "
            "assert 'site-packages' in p.parts; "
            "assert version('proofline') == '0.2.0'",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert provenance.returncode == 0, provenance.stderr
    installed_version = subprocess.run(
        [str(proofline), "--version"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert installed_version.returncode == 0, installed_version.stderr
    assert installed_version.stdout == "proofline 0.2.0\n"
