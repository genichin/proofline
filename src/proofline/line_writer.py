"""ProofLine Line bootstrap writer."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from .validator import validate_project

LINE_ID_RE = re.compile(r"^line-(\d{4})$")
TEMPLATE_PACKAGE = "proofline_schema_v1_templates"


@dataclass(frozen=True)
class LineInitError(Exception):
    code: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.code}: {self.message}"


@dataclass(frozen=True)
class LineInitResult:
    paths: tuple[str, str]
    dry_run: bool


def _run_git(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=project_root,
        text=True,
        capture_output=True,
        check=False,
    )


def _require_git_root(project_root: Path) -> None:
    result = _run_git(project_root, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise LineInitError("git.repository.required", ".", "Git 저장소가 아닙니다.")
    actual = Path(result.stdout.strip()).resolve()
    if actual != project_root.resolve():
        raise LineInitError(
            "git.root.mismatch", ".", "현재 directory가 Git 저장소 root가 아닙니다."
        )


def _require_valid_project(project_root: Path) -> None:
    diagnostics = validate_project(project_root)
    if diagnostics:
        first = diagnostics[0]
        raise LineInitError(
            "project.invalid", first.path, f"{first.code}: {first.message}"
        )


def _require_safe_paths(project_root: Path, line_id: str) -> Path:
    artifact_root = project_root / ".proofline"
    if artifact_root.is_symlink():
        raise LineInitError(
            "artifact_root.symlink", ".proofline", "artifact root symlink는 허용하지 않습니다."
        )
    lines_root = artifact_root / "lines"
    if lines_root.is_symlink():
        raise LineInitError(
            "lines_root.symlink",
            ".proofline/lines",
            "Line root symlink는 허용하지 않습니다.",
        )
    if not lines_root.is_dir():
        raise LineInitError(
            "lines_root.missing", ".proofline/lines", "Line root directory가 없습니다."
        )
    target = lines_root / line_id
    if target.exists() or target.is_symlink():
        raise LineInitError(
            "line.path.exists",
            target.relative_to(project_root).as_posix(),
            "대상 Line path가 이미 존재합니다.",
        )
    return target


def _require_unused_history(project_root: Path, line_id: str) -> None:
    relative = f".proofline/lines/{line_id}"
    result = _run_git(
        project_root, "log", "--all", "--format=%H", "--", relative
    )
    if result.returncode != 0:
        raise LineInitError(
            "git.history.failed", relative, result.stderr.strip() or "Git history 조회 실패"
        )
    if result.stdout.strip():
        raise LineInitError(
            "line.id.reused", relative, "Git history에 이미 사용된 Line ID입니다."
        )


def _read_template(name: str) -> str:
    relative = f"templates/schema-v1/artifacts/{name}"
    try:
        resource = files(TEMPLATE_PACKAGE).joinpath("artifacts", name)
        return resource.read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        source_resource = Path(__file__).resolve().parents[2] / relative
        try:
            return source_resource.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise LineInitError(
                "template.missing", relative, "template을 읽을 수 없습니다."
            ) from exc


def _render(line_id: str, title: str) -> tuple[str, str]:
    suffix = line_id.removeprefix("line-")
    values = {
        "{{LINE_ID}}": line_id,
        "{{DISCOVERY_ID}}": f"dcy-{suffix}",
        "{{TITLE}}": title,
    }
    line_text = _read_template("line.md")
    discovery_text = _read_template("discovery.md")
    for token, value in values.items():
        line_text = line_text.replace(token, value)
        discovery_text = discovery_text.replace(token, value)
    leftovers = [token for token in values if token in line_text or token in discovery_text]
    if leftovers:
        raise LineInitError(
            "template.variable.unresolved",
            "templates/schema-v1",
            f"치환되지 않은 template variable: {', '.join(leftovers)}",
        )
    return line_text, discovery_text


def _validate_rendered(line_id: str, line_text: str, discovery_text: str) -> None:
    with tempfile.TemporaryDirectory(prefix="proofline-render-") as raw:
        root = Path(raw)
        (root / "proofline.yaml").write_text(
            "schema_version: 1\nartifact_root: .proofline\n", encoding="utf-8"
        )
        target = root / ".proofline" / "lines" / line_id
        target.mkdir(parents=True)
        (root / ".proofline" / "criteria").mkdir()
        suffix = line_id.removeprefix("line-")
        (target / f"{line_id}.md").write_text(line_text, encoding="utf-8")
        (target / f"dcy-{suffix}.md").write_text(discovery_text, encoding="utf-8")
        diagnostics = validate_project(root)
        if diagnostics:
            first = diagnostics[0]
            raise LineInitError(
                "render.invalid", first.path, f"{first.code}: {first.message}"
            )


def initialize_line(
    project_root: Path, line_id: str, title: str, *, dry_run: bool = False
) -> LineInitResult:
    project_root = project_root.absolute()
    if LINE_ID_RE.fullmatch(line_id) is None:
        raise LineInitError(
            "line.id.invalid", line_id, "Line ID는 line-NNNN 형식이어야 합니다."
        )
    title = title.strip()
    if not title:
        raise LineInitError("line.title.empty", line_id, "제목은 비어 있을 수 없습니다.")
    if any(ord(character) < 32 or ord(character) == 127 for character in title):
        raise LineInitError(
            "line.title.invalid", line_id, "제목은 control character가 없는 한 줄이어야 합니다."
        )

    _require_git_root(project_root)
    artifact_root = project_root / ".proofline"
    if artifact_root.is_symlink():
        raise LineInitError(
            "artifact_root.symlink", ".proofline", "artifact root symlink는 허용하지 않습니다."
        )
    target = _require_safe_paths(project_root, line_id)
    _require_valid_project(project_root)
    _require_unused_history(project_root, line_id)
    line_text, discovery_text = _render(line_id, title)
    _validate_rendered(line_id, line_text, discovery_text)

    suffix = line_id.removeprefix("line-")
    paths = (
        f".proofline/lines/{line_id}/{line_id}.md",
        f".proofline/lines/{line_id}/dcy-{suffix}.md",
    )
    if dry_run:
        return LineInitResult(paths=paths, dry_run=True)

    temp = Path(tempfile.mkdtemp(prefix=f".{line_id}-", dir=project_root))
    try:
        (temp / f"{line_id}.md").write_text(line_text, encoding="utf-8")
        (temp / f"dcy-{suffix}.md").write_text(discovery_text, encoding="utf-8")
        if target.exists() or target.is_symlink():
            raise LineInitError(
                "line.path.exists", paths[0], "대상 Line path가 생성 중 나타났습니다."
            )
        try:
            os.rename(temp, target)
        except FileExistsError as exc:
            raise LineInitError(
                "line.path.exists", paths[0], "대상 Line path가 생성 중 나타났습니다."
            ) from exc
    except Exception:
        if temp.exists():
            shutil.rmtree(temp)
        raise
    return LineInitResult(paths=paths, dry_run=False)
