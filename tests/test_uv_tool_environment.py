from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_uv_tool_install_is_isolated_from_application_environment(tmp_path: Path) -> None:
    tool_dir = tmp_path / "tools"
    bin_dir = tmp_path / "bin"
    app = tmp_path / "application"
    app.mkdir()

    shutil.copy2(ROOT / "proofline.yaml", app / "proofline.yaml")
    shutil.copytree(ROOT / ".proofline", app / ".proofline")
    (app / ".proofline/line-identities.json").unlink()
    pyproject = app / "pyproject.toml"
    lockfile = app / "uv.lock"
    pyproject.write_text("[project]\nname = 'application-fixture'\nversion = '0.0.0'\n")
    lockfile.write_text("version = 1\nrevision = 1\n")
    before = {path.name: path.read_bytes() for path in (pyproject, lockfile)}

    env = os.environ.copy()
    env.update(
        {
            "UV_TOOL_DIR": str(tool_dir),
            "UV_TOOL_BIN_DIR": str(bin_dir),
        }
    )
    env.pop("PYTHONPATH", None)

    installed = run(
        "uv", "tool", "install", "--refresh", str(ROOT), cwd=tmp_path, env=env
    )
    assert installed.returncode == 0, installed.stderr

    executable = bin_dir / "proofline"
    assert executable.is_file()
    shebang = executable.read_text().splitlines()[0]
    assert str(tool_dir) in shebang

    tool_python = tool_dir / "proofline" / "bin" / "python"
    provenance = run(
        str(tool_python),
        "-I",
        "-c",
        "import pathlib, proofline; print(pathlib.Path(proofline.__file__).resolve())",
        cwd=tmp_path,
        env=env,
    )
    assert provenance.returncode == 0, provenance.stderr
    installed_module = Path(provenance.stdout.strip())
    assert installed_module.is_relative_to(tool_dir)
    assert not installed_module.is_relative_to(ROOT)

    validated = run(str(executable), "validate", cwd=app, env=env)
    assert validated.returncode == 1, validated.stderr
    assert "history.unavailable" in validated.stderr
    assert not (app / ".venv").exists()
    assert {path.name: path.read_bytes() for path in (pyproject, lockfile)} == before
