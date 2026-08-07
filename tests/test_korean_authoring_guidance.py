import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "valid-minimal"
TEMPLATES = (
    "templates/schema-v1/artifacts/discovery.md",
    "templates/schema-v1/artifacts/requirement.md",
    "templates/schema-v1/artifacts/acceptance-criterion.md",
)
SKILLS = (
    "skills/proofline-start-line/SKILL.md",
    "skills/proofline-start-requirement/SKILL.md",
    "skills/proofline-approve-specification/SKILL.md",
)
KOREAN_GUIDANCE = "사람이 작성하는 본문은 원칙적으로 한국어로 작성"
ENGLISH_EXCEPTIONS = (
    "H1 제목·구조적 heading, ID, status, YAML key, CLI, 코드, path, "
    "고유 기술 용어 및 영어가 의미를 더 정확하게 전달하는 내용은 영어로 유지"
)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_current_templates_contract_and_authoring_skills_share_language_guidance() -> None:
    resources = (
        *TEMPLATES,
        "docs/contracts/document-format.md",
        *SKILLS,
    )

    for relative in resources:
        text = read(relative)
        assert KOREAN_GUIDANCE in text, relative
        assert ENGLISH_EXCEPTIONS in text, relative

    contract = read("docs/contracts/document-format.md")
    assert "authoring guidance" in contract
    assert "자연어 validation gate가 아니다" in contract


def test_structurally_valid_english_authored_content_is_not_rejected(tmp_path: Path) -> None:
    project = tmp_path / "project"
    shutil.copytree(FIXTURE, project)
    line = project / ".proofline/lines/line-0001"

    discovery = line / "dcy-0001.md"
    discovery_frontmatter = discovery.read_text(encoding="utf-8").split("---", 2)[1]
    discovery.write_text(
        f"---{discovery_frontmatter}---\n\n"
        "# English Discovery\n\n"
        "## Problem\n\nThe current behavior needs clarification.\n\n"
        "## Evidence\n\n- Repository evidence is recorded here.\n\n"
        "## Scope\n\n- Keep the observable contract explicit.\n\n"
        "## Out of Scope\n\n- None.\n",
        encoding="utf-8",
    )

    requirement = line / "req-0001.md"
    requirement_frontmatter = requirement.read_text(encoding="utf-8").split("---", 2)[1]
    requirement.write_text(
        f"---{requirement_frontmatter}---\n\n"
        "# English Requirement\n\n"
        "## Objective\n\nProvide an observable result.\n\n"
        "## Scope\n\n- Implement the approved behavior.\n\n"
        "## Non-Goals\n\n- None.\n",
        encoding="utf-8",
    )

    for index, criterion in enumerate(sorted((project / ".proofline/criteria").glob("ac-*.md")), 1):
        frontmatter = criterion.read_text(encoding="utf-8").split("---", 2)[1]
        criterion.write_text(
            f"---{frontmatter}---\n\n"
            f"# English Criterion {index}\n\n"
            "## Criterion\n\nThe system provides an independently testable behavior.\n\n"
            "## Verification\n\n- Observe the expected behavior.\n",
            encoding="utf-8",
        )

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        (
            sys.executable,
            "-c",
            "from proofline.cli import main; raise SystemExit(main())",
            "validate",
        ),
        cwd=project,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
