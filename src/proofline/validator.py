import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from .identity_allocator import ALLOCATOR_PATH, LEGACY_PATH, validate_allocator
from .project_schema import REQUIRED_DIRECTORIES, SUPPORT_MARKERS
from .transaction import read_regular_beneath
from .yaml_strict import safe_load_unique


@dataclass(frozen=True, order=True)
class ValidationError:
    path: str
    code: str
    message: str


ARTIFACT_FIELDS = {
    "line": {"id"},
    "dcy": {"id", "status"},
    "req": {"id", "status", "discovery", "criteria"},
    "ac": {"id", "status"},
}

LEGACY_OPTIONAL_FIELDS = {
    "line": {"execution_status", "implementation_history"},
}

ARTIFACT_STATUSES = {
    "dcy": {"status": {"draft", "confirmed", "withdrawn"}},
    "req": {"status": {"draft", "approved", "withdrawn"}},
    "ac": {"status": {"draft", "active", "retired"}},
}

REQUIRED_H2 = {
    "dcy": ("Problem", "Evidence", "Scope", "Out of Scope"),
    "req": ("Objective", "Scope", "Non-Goals"),
    "ac": ("Criterion", "Verification"),
}

PLACEHOLDER = re.compile(
    r"\{\{(?:TODO|UNKNOWN|NEEDS_EVIDENCE)(?:: [^{}\n]+)?\}\}"
)
PLACEHOLDER_CANDIDATE = re.compile(r"\{\{[^\n]*?\}\}")

ARTIFACT_PATHS = {
    "line": re.compile(r"^\.proofline/lines/line-((?!0000)\d{4})/line-\1\.md$"),
    "dcy": re.compile(r"^\.proofline/lines/line-((?!0000)\d{4})/dcy-\1\.md$"),
    "req": re.compile(r"^\.proofline/lines/line-((?!0000)\d{4})/req-\1\.md$"),
    "ac": re.compile(r"^\.proofline/criteria/ac-(?!0000)\d{4}\.md$"),
}

LEGACY_RETAINED_PATHS = (
    re.compile(
        r"^\.proofline/lines/line-((?!0000)\d{4})/micro-specs/ms-\1-\d{3}\.md$"
    ),
    re.compile(
        r"^\.proofline/lines/line-((?!0000)\d{4})/micro-specs/iqc-\1-\d{3}\.md$"
    ),
    re.compile(r"^\.proofline/lines/line-((?!0000)\d{4})/dqc-\1\.md$"),
    re.compile(r"^\.proofline/lines/line-((?!0000)\d{4})/integration-\1\.md$"),
    re.compile(r"^\.proofline/lines/line-((?!0000)\d{4})/legacy-migration-\1\.md$"),
)

EVIDENCE_DIRECTORY = re.compile(
    r"^\.proofline/lines/line-(?!0000)\d{4}/evidence$"
)
EVIDENCE_PATH = re.compile(
    r"^\.proofline/lines/line-(?!0000)\d{4}/evidence/[^/]+\.md$"
)

LEGACY_CRITERIA_KEYS = {"create", "update", "retire"}
CURRENT_CRITERIA_KEYS = LEGACY_CRITERIA_KEYS | {"satisfy"}


def _artifact_kind(path: Path) -> str | None:
    stem = path.stem
    for kind in ARTIFACT_FIELDS:
        if stem.startswith(f"{kind}-"):
            return kind
    return None


def _is_legacy_retained_path(relative: str) -> bool:
    return any(pattern.fullmatch(relative) for pattern in LEGACY_RETAINED_PATHS)


def _is_evidence_path(relative: str) -> bool:
    return EVIDENCE_PATH.fullmatch(relative) is not None


def _headings(body: str) -> tuple[list[str], list[str]]:
    h1: list[str] = []
    h2: list[str] = []
    fenced = False
    for line in body.splitlines():
        if line.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced:
            continue
        if line.startswith("# "):
            h1.append(line[2:].strip())
        elif line.startswith("## "):
            h2.append(line[3:].strip())
    return h1, h2


def _headings_are_valid(kind: str, body: str) -> bool:
    if kind == "line":
        return not body.strip()
    h1, h2 = _headings(body)
    if len(h1) != 1:
        return False
    required = list(REQUIRED_H2[kind])
    if kind == "dcy" and h2 == required + ["Risks and Unknowns"]:
        return True
    if kind == "req" and h2 == ["Objective", "Scope", "Constraints", "Non-Goals"]:
        return True
    return h2 == required


def _is_draft(kind: str, frontmatter: dict[str, object]) -> bool:
    if kind in {"dcy", "req", "ac"}:
        return frontmatter.get("status") == "draft"
    return False


def _criteria_lists(value: object) -> dict[str, list[str]] | None:
    if not isinstance(value, dict) or set(value) not in {
        frozenset(LEGACY_CRITERIA_KEYS),
        frozenset(CURRENT_CRITERIA_KEYS),
    }:
        return None
    result: dict[str, list[str]] = {}
    for key, items in value.items():
        if not isinstance(items, list) or not all(
            isinstance(item, str) and re.fullmatch(r"ac-(?!0000)\d{4}", item)
            for item in items
        ):
            return None
        result[key] = items
    return result


def _reference_targets(
    kind: str, relative: str, frontmatter: dict[str, object]
) -> tuple[list[str], bool]:
    line_match = re.search(r"\.proofline/lines/(line-(?!0000)\d{4})/", relative)
    line_id = line_match.group(1) if line_match else None
    targets: list[str] = []
    valid = True

    def add(value: object, pattern: str, target: str) -> None:
        nonlocal valid
        if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
            valid = False
            return
        targets.append(target.format(value=value))

    if kind == "req" and line_id:
        if "discovery" in frontmatter:
            add(
                frontmatter["discovery"],
                r"dcy-(?!0000)\d{4}",
                f".proofline/lines/{line_id}/{{value}}.md",
            )
        criteria = _criteria_lists(frontmatter.get("criteria"))
        if criteria is not None:
            for values in criteria.values():
                for value in values:
                    add(value, r"ac-(?!0000)\d{4}", ".proofline/criteria/{value}.md")
        elif "criteria" in frontmatter:
            valid = False
    return targets, valid


def _validate_topology(root: Path) -> list[ValidationError]:
    errors: list[ValidationError] = []
    required = REQUIRED_DIRECTORIES
    valid_directories = (
        re.compile(r"^\.proofline$"),
        re.compile(r"^\.proofline/(?:lines|criteria)$"),
        re.compile(r"^\.proofline/lines/line-(?!0000)\d{4}$"),
        re.compile(r"^\.proofline/lines/line-(?!0000)\d{4}/micro-specs$"),
        EVIDENCE_DIRECTORY,
    )
    markers = SUPPORT_MARKERS
    artifact_root = root / ".proofline"
    try:
        initial_root_state = artifact_root.stat(follow_symlinks=False)
    except OSError:
        initial_root_state = None
    if initial_root_state is not None and stat.S_ISLNK(initial_root_state.st_mode):
        return [
            ValidationError(
                ".proofline",
                "topology.directory.symlink",
                "필수 project directory symlink는 허용하지 않습니다.",
            )
        ]
    if initial_root_state is not None and not stat.S_ISDIR(initial_root_state.st_mode):
        return [
            ValidationError(
                ".proofline",
                "topology.directory.type",
                "필수 project path는 directory여야 합니다.",
            )
        ]
    for relative in required:
        path = root / relative
        try:
            state = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            errors.append(
                ValidationError(
                    relative,
                    "topology.directory.missing",
                    "필수 project directory가 없습니다.",
                )
            )
            continue
        except OSError:
            errors.append(
                ValidationError(
                    relative,
                    "topology.directory.type",
                    "필수 project directory를 검사할 수 없습니다.",
                )
            )
            continue
        if stat.S_ISLNK(state.st_mode):
            errors.append(
                ValidationError(
                    relative,
                    "topology.directory.symlink",
                    "필수 project directory symlink는 허용하지 않습니다.",
                )
            )
        elif not stat.S_ISDIR(state.st_mode):
            errors.append(
                ValidationError(
                    relative,
                    "topology.directory.type",
                    "필수 project path는 directory여야 합니다.",
                )
            )

    artifact_root = root / ".proofline"
    try:
        root_state = artifact_root.stat(follow_symlinks=False)
    except OSError:
        return errors
    if not stat.S_ISDIR(root_state.st_mode) or stat.S_ISLNK(root_state.st_mode):
        return errors
    try:
        topology_paths = sorted(artifact_root.rglob("*"))
    except OSError:
        errors.append(
            ValidationError(
                ".proofline",
                "topology.unavailable",
                "project topology를 순회할 수 없습니다.",
            )
        )
        return errors
    for path in topology_paths:
        relative = path.relative_to(root).as_posix()
        try:
            state = path.stat(follow_symlinks=False)
        except OSError:
            errors.append(
                ValidationError(
                    relative,
                    "topology.path.unavailable",
                    "project path를 검사할 수 없습니다.",
                )
            )
            continue
        if stat.S_ISLNK(state.st_mode):
            if relative not in required:
                errors.append(
                    ValidationError(
                        relative,
                        "topology.support.unsupported",
                        "project topology에서 symlink는 허용하지 않습니다.",
                    )
                )
            continue
        if stat.S_ISDIR(state.st_mode):
            if not any(pattern.fullmatch(relative) for pattern in valid_directories):
                errors.append(
                    ValidationError(
                        relative,
                        "topology.support.unsupported",
                        "허용되지 않은 project directory입니다.",
                    )
                )
            continue
        if relative in markers:
            if not stat.S_ISREG(state.st_mode) or state.st_size != 0:
                errors.append(
                    ValidationError(
                        relative,
                        "topology.support.invalid",
                        ".gitkeep marker는 regular zero-byte file이어야 합니다.",
                    )
                )
            continue
        if stat.S_ISREG(state.st_mode) and (
            path.suffix == ".md" or relative in {ALLOCATOR_PATH, LEGACY_PATH}
        ):
            if _is_evidence_path(relative):
                try:
                    snapshot = read_regular_beneath(root, relative)
                    if snapshot.identity != (state.st_dev, state.st_ino):
                        raise OSError("evidence changed while opening")
                    snapshot.data.decode("utf-8")
                except (OSError, UnicodeError):
                    errors.append(
                        ValidationError(
                            relative,
                            "evidence.read",
                            "비정식 evidence를 UTF-8 text로 읽을 수 없습니다.",
                        )
                    )
            continue
        errors.append(
            ValidationError(
                relative,
                "topology.support.unsupported",
                "허용되지 않은 project support path입니다.",
            )
        )
    return errors


def _validate_artifacts(root: Path) -> list[ValidationError]:
    errors: list[ValidationError] = []
    artifacts: dict[str, tuple[str, dict[str, object]]] = {}
    artifact_root = root / ".proofline"
    try:
        root_state = artifact_root.stat(follow_symlinks=False)
    except OSError:
        return errors
    if stat.S_ISLNK(root_state.st_mode) or not stat.S_ISDIR(root_state.st_mode):
        return errors
    try:
        artifact_paths = sorted(artifact_root.rglob("*.md"))
    except OSError:
        return [
            ValidationError(
                ".proofline",
                "artifact.unavailable",
                "artifact root를 순회할 수 없습니다.",
            )
        ]
    for path in artifact_paths:
        relative = path.relative_to(root).as_posix()
        try:
            candidate_state = path.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISLNK(candidate_state.st_mode) or not stat.S_ISREG(
            candidate_state.st_mode
        ):
            continue
        if _is_legacy_retained_path(relative):
            continue
        if _is_evidence_path(relative):
            continue
        kind = _artifact_kind(path)
        if kind is None:
            errors.append(
                ValidationError(
                    relative,
                    "artifact.path",
                    "정본 경로에서 artifact 종류를 확인할 수 없습니다.",
                )
            )
            continue
        try:
            snapshot = read_regular_beneath(root, relative)
            if snapshot.identity != (candidate_state.st_dev, candidate_state.st_ino):
                raise OSError("artifact changed while opening")
            lines = snapshot.data.decode("utf-8").splitlines()
        except (OSError, UnicodeError):
            errors.append(
                ValidationError(
                    relative,
                    "artifact.read",
                    "artifact를 UTF-8 text로 읽을 수 없습니다.",
                )
            )
            continue
        if not ARTIFACT_PATHS[kind].fullmatch(relative):
            errors.append(
                ValidationError(
                    relative,
                    "artifact.path",
                    "정본 경로가 artifact ID와 일치하지 않습니다.",
                )
            )
        if not lines or lines[0] != "---" or "---" not in lines[1:]:
            errors.append(
                ValidationError(
                    relative,
                    "artifact.frontmatter",
                    "YAML 머리말 경계가 없습니다.",
                )
            )
            continue
        closing = lines.index("---", 1)
        frontmatter_text = "\n".join(lines[1:closing])
        try:
            frontmatter = safe_load_unique(frontmatter_text)
        except yaml.YAMLError:
            errors.append(
                ValidationError(
                    relative,
                    "artifact.frontmatter",
                    "YAML 머리말을 해석할 수 없습니다.",
                )
            )
            continue
        if not isinstance(frontmatter, dict):
            errors.append(
                ValidationError(
                    relative,
                    "artifact.frontmatter",
                    "YAML 머리말은 mapping이어야 합니다.",
                )
            )
            continue
        artifacts[relative] = (kind, frontmatter)
        body = "\n".join(lines[closing + 1 :])
        missing = ARTIFACT_FIELDS[kind] - set(frontmatter)
        if missing:
            errors.append(
                ValidationError(
                    relative,
                    "artifact.missing-field",
                    f"필수 YAML 머리말 항목이 없습니다: {', '.join(sorted(missing))}",
                )
            )
        allowed_fields = ARTIFACT_FIELDS[kind] | LEGACY_OPTIONAL_FIELDS.get(kind, set())
        if set(frontmatter) - allowed_fields:
            errors.append(
                ValidationError(
                    relative,
                    "artifact.unknown-field",
                    "정의되지 않은 YAML 머리말 항목이 있습니다.",
                )
            )
        if kind == "req" and "criteria" in frontmatter:
            criteria = _criteria_lists(frontmatter["criteria"])
            if criteria is None:
                errors.append(
                    ValidationError(
                        relative,
                        "criteria.invalid",
                        "criteria는 create/update/retire와 optional satisfy AC ID list여야 합니다.",
                    )
                )
            else:
                values = [item for items in criteria.values() for item in items]
                if not values:
                    errors.append(
                        ValidationError(
                            relative,
                            "criteria.empty",
                            "criteria 대상 AC 합집합은 비어 있을 수 없습니다.",
                        )
                    )
                memberships: dict[str, set[str]] = {}
                for key, items in criteria.items():
                    if len(items) != len(set(items)):
                        errors.append(
                            ValidationError(
                                relative,
                                "criteria.duplicate",
                                f"{key} list에 AC ID가 중복되었습니다.",
                            )
                        )
                    for item in set(items):
                        memberships.setdefault(item, set()).add(key)
                duplicates = sorted(item for item, keys in memberships.items() if len(keys) > 1)
                if duplicates:
                    errors.append(
                        ValidationError(
                            relative,
                            "criteria.duplicate",
                            f"AC ID를 둘 이상의 criteria list에 중복 기록했습니다: {', '.join(duplicates)}",
                        )
                    )
        for field, allowed in ARTIFACT_STATUSES.get(kind, {}).items():
            if field in frontmatter and frontmatter[field] not in allowed:
                errors.append(
                    ValidationError(
                        relative,
                        "artifact.status",
                        f"{field} 값이 허용되지 않습니다.",
                    )
                )
        if frontmatter.get("id") != path.stem:
            errors.append(
                ValidationError(
                    relative,
                    "artifact.id",
                    "id가 파일명과 일치하지 않습니다.",
                )
            )
        if not _headings_are_valid(kind, body):
            errors.append(
                ValidationError(
                    relative,
                    "artifact.headings",
                    "H1 또는 H2 구조가 계약과 다릅니다.",
                )
            )
        candidates = PLACEHOLDER_CANDIDATE.findall(body)
        placeholders_valid = all(PLACEHOLDER.fullmatch(value) for value in candidates)
        residual = PLACEHOLDER_CANDIDATE.sub("", body)
        placeholder_syntax_present = "{{" in residual or "}}" in residual
        frontmatter_placeholder = "{{" in frontmatter_text or "}}" in frontmatter_text
        if (
            not placeholders_valid
            or placeholder_syntax_present
            or frontmatter_placeholder
            or (candidates and not _is_draft(kind, frontmatter))
        ):
            errors.append(
                ValidationError(
                    relative,
                    "artifact.placeholder",
                    "자리표시자 문법 또는 상태별 허용 규칙을 위반했습니다.",
                )
            )
        targets, references_valid = _reference_targets(kind, relative, frontmatter)
        if not references_valid:
            errors.append(
                ValidationError(
                    relative,
                    "reference.invalid",
                    "참조 값이 canonical ID 형식과 다릅니다.",
                )
            )
        for target in targets:
            try:
                read_regular_beneath(root, target)
            except OSError:
                errors.append(
                    ValidationError(
                        relative,
                        "reference.missing",
                        f"참조 대상 파일이 없습니다: {target}",
                    )
                )
    errors.extend(_validate_criteria_bindings(root, artifacts))
    return errors


def _git_output(root: Path, *arguments: str) -> bytes | None:
    try:
        completed = subprocess.run(
            ("git", *arguments),
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError:
        return None
    return completed.stdout if completed.returncode == 0 else None


def _historical_status(content: bytes) -> str | None:
    try:
        text = content.decode("utf-8")
        lines = text.splitlines()
        if not lines or lines[0] != "---" or "---" not in lines[1:]:
            return None
        closing = lines.index("---", 1)
        frontmatter = safe_load_unique("\n".join(lines[1:closing]))
    except (UnicodeError, yaml.YAMLError):
        return None
    if not isinstance(frontmatter, dict):
        return None
    status = frontmatter.get("status")
    return status if isinstance(status, str) else None


def _git_file(root: Path, revision: str, path: str) -> bytes | None:
    return _git_output(root, "show", f"{revision}:{path}")


def _last_active_revision(root: Path, ac_path: str, current_ac: bytes) -> str | None:
    current_head_ac = _git_file(root, "HEAD", ac_path)
    if current_head_ac is None:
        return None
    history = _git_output(root, "rev-list", "--first-parent", "refs/heads/main")
    if history is None:
        return None
    try:
        commits = history.decode("ascii").splitlines()
    except UnicodeError:
        return None
    if not commits:
        return None

    head_status = _historical_status(_git_file(root, commits[0], ac_path) or b"")
    if head_status == "active":
        return commits[0] if current_ac != current_head_ac else None
    if head_status != "draft":
        return None

    for index, commit in enumerate(commits[:-1]):
        current = _git_file(root, commit, ac_path)
        prior_commit = commits[index + 1]
        prior = _git_file(root, prior_commit, ac_path)
        if current is None or prior is None:
            return None
        if _historical_status(current) == "draft" and _historical_status(prior) == "active":
            return prior_commit
    return None


def _draft_satisfy_uses_last_active_binding(
    root: Path,
    artifacts: dict[str, tuple[str, dict[str, object]]],
    req_path: str,
    req: dict[str, object],
    ac_id: str,
) -> bool:
    owners = []
    for _, (kind, candidate) in artifacts.items():
        if kind != "req" or candidate.get("status") != "draft":
            continue
        criteria = _criteria_lists(candidate.get("criteria"))
        if criteria is not None and ac_id in criteria.get("update", []):
            owners.append(candidate)
    if len(owners) != 1 or req.get("status") != "approved":
        return False

    ac_path = f".proofline/criteria/{ac_id}.md"
    try:
        current_ac = read_regular_beneath(root, ac_path).data
        current_req = read_regular_beneath(root, req_path).data
    except OSError:
        return False
    prior_revision = _last_active_revision(root, ac_path, current_ac)
    if prior_revision is None:
        return False
    historical_req = _git_file(root, prior_revision, req_path)
    return historical_req is not None and historical_req == current_req


def _retired_satisfy_uses_historical_active_binding(
    root: Path, req_path: str, ac_id: str
) -> bool:
    try:
        current_req = read_regular_beneath(root, req_path).data
    except OSError:
        return False
    history = _git_output(root, "rev-list", "--first-parent", "refs/heads/main")
    if history is None:
        return False
    try:
        commits = history.decode("ascii").splitlines()
    except UnicodeError:
        return False
    ac_path = f".proofline/criteria/{ac_id}.md"
    return any(
        _historical_status(_git_file(root, commit, ac_path) or b"") == "active"
        and _git_file(root, commit, req_path) == current_req
        for commit in commits
    )


def _validate_criteria_bindings(
    root: Path,
    artifacts: dict[str, tuple[str, dict[str, object]]],
) -> list[ValidationError]:
    errors: list[ValidationError] = []
    retirement_owners: dict[str, int] = {}
    for kind, candidate in artifacts.values():
        if kind != "req" or candidate.get("status") != "approved":
            continue
        criteria = _criteria_lists(candidate.get("criteria"))
        if criteria is None:
            continue
        for ac_id in set(criteria.get("retire", [])):
            retirement_owners[ac_id] = retirement_owners.get(ac_id, 0) + 1
    for req_path, (kind, req) in artifacts.items():
        if kind != "req":
            continue
        criteria = _criteria_lists(req.get("criteria"))
        if criteria is None:
            continue
        for ac_id in criteria.get("satisfy", []):
            ac_path = f".proofline/criteria/{ac_id}.md"
            target = artifacts.get(ac_path)
            target_status = target[1].get("status") if target is not None else None
            allowed_draft = target_status == "draft" and _draft_satisfy_uses_last_active_binding(
                root, artifacts, req_path, req, ac_id
            )
            allowed_retired = (
                target_status == "retired"
                and req.get("status") == "approved"
                and retirement_owners.get(ac_id) == 1
                and _retired_satisfy_uses_historical_active_binding(
                    root, req_path, ac_id
                )
            )
            if (
                target is not None
                and target_status != "active"
                and not allowed_draft
                and not allowed_retired
            ):
                errors.append(
                    ValidationError(
                        req_path,
                        "reference.inactive",
                        "criteria.satisfy 대상은 active AC 또는 입증된 historical active binding이어야 합니다: "
                        f"{ac_id}",
                    )
                )

    return errors


def _validate_project(
    root: Path, *, excluded_line_path: str | tuple[str, ...] | None = None
) -> list[ValidationError]:
    config_path = root / "proofline.yaml"
    try:
        config_state = config_path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return [
            ValidationError(
                "proofline.yaml",
                "config.missing",
                "proofline.yaml 파일이 없습니다.",
            )
        ]
    except OSError:
        return [
            ValidationError(
                "proofline.yaml",
                "config.type",
                "proofline.yaml을 검사할 수 없습니다.",
            )
        ]
    if stat.S_ISLNK(config_state.st_mode):
        return [
            ValidationError(
                "proofline.yaml",
                "config.symlink",
                "proofline.yaml symlink는 허용하지 않습니다.",
            )
        ]
    if not stat.S_ISREG(config_state.st_mode):
        return [
            ValidationError(
                "proofline.yaml",
                "config.type",
                "proofline.yaml은 regular file이어야 합니다.",
            )
        ]

    try:
        config_snapshot = read_regular_beneath(root, "proofline.yaml")
        if config_snapshot.identity != (config_state.st_dev, config_state.st_ino):
            raise OSError("config changed while opening")
        config = yaml.safe_load(config_snapshot.data.decode("utf-8"))
    except yaml.YAMLError:
        return [
            ValidationError(
                "proofline.yaml",
                "config.yaml",
                "proofline.yaml을 해석할 수 없습니다.",
            )
        ]
    except (OSError, UnicodeError):
        return [
            ValidationError(
                "proofline.yaml",
                "config.read",
                "proofline.yaml을 UTF-8 text로 읽을 수 없습니다.",
            )
        ]
    if not isinstance(config, dict):
        return [
            ValidationError(
                "proofline.yaml",
                "config.yaml",
                "proofline.yaml은 mapping이어야 합니다.",
            )
        ]
    errors: list[ValidationError] = []
    allowed_fields = {"schema_version", "artifact_root"}
    if set(config) - allowed_fields:
        errors.append(
            ValidationError(
                "proofline.yaml",
                "config.unknown-field",
                "정의되지 않은 설정 항목이 있습니다.",
            )
        )
    if config.get("artifact_root") != ".proofline":
        errors.append(
            ValidationError(
                "proofline.yaml",
                "config.artifact-root",
                "artifact_root는 .proofline이어야 합니다.",
            )
        )
    if config.get("schema_version") != 1:
        errors.append(
            ValidationError(
                "proofline.yaml",
                "config.schema-version",
                "schema_version은 1이어야 합니다.",
            )
        )
    errors.extend(_validate_topology(root))
    errors.extend(_validate_artifacts(root))
    errors.extend(
        ValidationError(error.path, error.code, error.message)
        for error in validate_allocator(root)
    )
    return sorted(errors)


def validate_project(root: Path) -> list[ValidationError]:
    """Validate the complete current canonical project tree."""
    return _validate_project(root)


def _validate_schema_candidate(root: Path) -> list[ValidationError]:
    """Validate an uncommitted schema candidate without a Git history."""
    candidate = sorted(
        path.relative_to(root).as_posix()
        for path in (root / ".proofline/lines").glob("line-*/line-*.md")
    )
    if len(candidate) != 1:
        return _validate_project(root, excluded_line_path=None)
    return _validate_project(root, excluded_line_path=candidate[0])
