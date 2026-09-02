import argparse
import json
import sys
from importlib import metadata
from pathlib import Path

from proofline.agent_skills import (
    AgentSkillError,
    inspect_registry,
    load_packaged_payload,
    status_document,
    summarize,
)
from proofline.agent_skills import (
    remove as remove_agent_skills,
)
from proofline.agent_skills import (
    repair as repair_agent_skills,
)
from proofline.agent_skills import (
    setup as setup_agent_skills,
)
from proofline.agent_skills import (
    unregister as unregister_agent_skills,
)
from proofline.line_writer import LineInitError, initialize_line
from proofline.project_writer import ProjectInitError, initialize_project
from proofline.requirement_writer import (
    RequirementInitError,
    initialize_requirement,
)
from proofline.updater import UpdateError, UpdateResult, run_update  # noqa: F401
from proofline.validator import validate_project, validate_project_warnings


class _VersionAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None) -> None:
        print(f"{parser.prog} {metadata.version('proofline')}")
        parser.exit(0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proofline")
    parser.add_argument(
        "--version",
        action=_VersionAction,
        nargs=0,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="Validate a ProofLine project")

    status = commands.add_parser("status", help="Show local package, project, and agent skill status")
    status.add_argument("--json", action="store_true", dest="as_json")

    agent_skill = commands.add_parser("agent-skill", help="Manage installed agent skills")
    agent_commands = agent_skill.add_subparsers(dest="agent_skill_command", required=True)
    for operation in ("setup", "remove", "repair", "unregister"):
        command = agent_commands.add_parser(operation)
        command.add_argument("agent", choices=("hermes", "codex"))
        command.add_argument("--profile", "--scope", dest="scope")
        if operation == "setup":
            command.add_argument("--adopt-existing", action="store_true")
    for operation in ("status", "doctor"):
        command = agent_commands.add_parser(operation)
        command.add_argument("agent", nargs="?", choices=("hermes", "codex"))
        command.add_argument("--profile", "--scope", dest="scope")
        command.add_argument("--json", action="store_true", dest="as_json")

    update = commands.add_parser("update", help="Update the ProofLine uv tool")
    update.add_argument("--check", action="store_true", help="Check without changing the installed tool")
    update.add_argument("--version", dest="target_version", help="Use an exact stable version")
    update.add_argument(
        "--adopt-official",
        action="store_true",
        help="Explicitly replace a source installation with the official wheel",
    )
    update.add_argument(
        "--no-sync-agent-skills",
        action="store_true",
        help="Update only the package and leave registered agent skills unchanged",
    )

    project = commands.add_parser("project", help="Manage a ProofLine project")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    project_init = project_commands.add_parser("init", help="Create the schema-v1 scaffold")
    project_init.add_argument(
        "--dry-run", action="store_true", help="Preflight without writing project files"
    )

    line = commands.add_parser("line", help="Manage ProofLine Lines")
    line_commands = line.add_subparsers(dest="line_command", required=True)
    init = line_commands.add_parser("init", help="Create a Line and draft Discovery")
    init.add_argument("--title", required=True, help="Discovery H1 title")
    init.add_argument(
        "--dry-run", action="store_true", help="Preflight and render without writing"
    )
    requirement = commands.add_parser("requirement", help="Manage ProofLine Requirements")
    requirement_commands = requirement.add_subparsers(dest="requirement_command", required=True)
    requirement_init = requirement_commands.add_parser(
        "init", help="Create AC drafts and a Requirement draft"
    )
    requirement_init.add_argument("line_id", help="Existing stable ID in line-NNNN form")
    requirement_init.add_argument("--manifest", required=True, type=Path)
    requirement_init.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate":
        root = Path.cwd()
        errors = validate_project(root)
        warnings = validate_project_warnings(root)
        for warning in warnings:
            print(
                f"warning: {warning.path}: {warning.code}: {warning.message}",
                file=sys.stderr,
            )
        for error in errors:
            print(f"{error.path}: {error.code}: {error.message}", file=sys.stderr)
        return 1 if errors else 0

    if args.command == "agent-skill":
        try:
            operation = args.agent_skill_command
            if operation == "setup":
                result = setup_agent_skills(args.agent, args.scope, adopt_existing=args.adopt_existing)
                print(f"{result.agent}/{result.scope}: {result.status}: {result.target_root}")
                return 0
            if operation == "remove":
                remove_agent_skills(args.agent, args.scope)
                print(f"removed: {args.agent}/{args.scope or ('user' if args.agent == 'codex' else 'default')}")
                return 0
            if operation == "repair":
                result = repair_agent_skills(args.agent, args.scope)
                print(f"{result.agent}/{result.scope}: {result.status}: {result.target_root}")
                return 0
            if operation == "unregister":
                invalid = unregister_agent_skills(args.agent, args.scope)
                print(f"unregistered: {args.agent}/{args.scope or ('user' if args.agent == 'codex' else 'default')}")
                if invalid:
                    print("warning: invalid registration removed; target files remain unmanaged", file=sys.stderr)
                return 0
            payload = load_packaged_payload()
            inspections = inspect_registry(payload=payload, agent=args.agent, scope=args.scope)
            document = status_document(inspections)
            if args.as_json:
                print(json.dumps(document, sort_keys=True, separators=(",", ":")))
            elif operation == "status":
                _print_agent_status(inspections)
            else:
                _print_agent_doctor(inspections)
            return 1 if document["counts"]["blocked"] else 0
        except AgentSkillError as exc:
            print(f"agent-skill {args.agent_skill_command} failed: {exc}", file=sys.stderr)
            return 1

    if args.command == "status":
        return _aggregate_status(args.as_json)

    if args.command == "update":
        try:
            result = run_update(
                check=args.check,
                version=args.target_version,
                adopt=args.adopt_official,
                no_sync_agent_skills=args.no_sync_agent_skills,
            )
        except UpdateError as exc:
            print(f"update failed: {exc}", file=sys.stderr)
            return 1
        print(f"current: {result.current}")
        print(f"target: {result.target}")
        print(f"provenance: {result.provenance}")
        print(f"status: {result.status}")
        return result.exit_code

    if args.command == "project" and args.project_command == "init":
        try:
            result = initialize_project(Path.cwd(), dry_run=args.dry_run)
        except ProjectInitError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        prefix = "would create" if result.status == "planned" else result.status
        for path in result.paths:
            print(f"{prefix}: {path}")
        return 0

    if args.command == "line" and args.line_command == "init":
        root = Path.cwd()
        try:
            result = initialize_line(root, args.title, dry_run=args.dry_run)
        except LineInitError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        prefix = "would create" if result.dry_run else "created"
        for path in result.paths:
            print(f"{prefix}: {path}")
        return 0

    if args.command == "requirement" and args.requirement_command == "init":
        try:
            result = initialize_requirement(
                Path.cwd(), args.line_id, args.manifest, dry_run=args.dry_run
            )
        except RequirementInitError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        prefix = "would create" if result.dry_run else "created"
        for path in result.paths:
            print(f"{prefix}: {path}")
        return 0

    raise AssertionError("unreachable command")


def _print_agent_status(inspections) -> None:
    print("AGENT\tSCOPE\tTARGET\tVERSION\tSTATUS")
    for item in inspections:
        print(
            f"{item.agent}\t{item.scope}\t{item.target_root or '-'}\t"
            f"{item.installed_version or '-'}\t{item.status}"
        )
    counts = summarize(inspections)
    print(" ".join(f"{key}={value}" for key, value in counts.items()))


def _print_agent_doctor(inspections) -> None:
    for item in inspections:
        print(f"[{item.agent}/{item.scope}] {item.status}")
        print(f"manifest: {item.manifest_path}")
        print(f"target: {item.target_root or '-'}")
        for detail in item.details:
            print(f"detail: {detail}")
    if not inspections:
        print("no registered agent skill installations")


def _package_status() -> dict[str, str]:
    version = metadata.version("proofline")
    distribution = metadata.distribution("proofline")
    raw = distribution.read_text("direct_url.json")
    provenance = "unknown"
    if raw:
        try:
            value = json.loads(raw)
            provenance = "archive" if "archive_info" in value else "source" if "dir_info" in value else "unknown"
        except (json.JSONDecodeError, TypeError):
            pass
    return {"version": version, "provenance": provenance, "status": "healthy" if provenance != "unknown" else "unknown"}


def _aggregate_status(as_json: bool) -> int:
    package = _package_status()
    root = Path.cwd()
    detected = (root / "proofline.yaml").exists() or (root / ".proofline").exists()
    errors = validate_project(root) if detected else []
    project = {
        "status": "invalid" if errors else "valid" if detected else "not-detected",
        "root": str(root) if detected else None,
        "errors": [
            {"path": str(error.path), "code": error.code, "message": error.message}
            for error in errors
        ],
    }
    try:
        payload = load_packaged_payload()
        inspections = inspect_registry(payload=payload)
        agent_error = None
    except AgentSkillError as exc:
        inspections = []
        agent_error = str(exc)
    counts = summarize(inspections)
    worst = next(
        (state for state in ("invalid-manifest", "unsupported", "conflict", "drifted", "missing", "outdated") if any(item.status == state for item in inspections)),
        "healthy",
    )
    agents = {"counts": counts, "worst_status": worst, "error": agent_error}
    document = {"schema_version": 1, "package": package, "project": project, "agent_skills": agents}
    blocked = bool(errors or counts["blocked"] or agent_error or package["status"] != "healthy")
    if as_json:
        print(json.dumps(document, sort_keys=True, separators=(",", ":")))
    else:
        print(f"package: {package['version']} ({package['provenance']}) {package['status']}")
        print(f"project: {project['status']}{f' ({root})' if detected else ''}")
        print(f"agent-skills: {worst} " + " ".join(f"{key}={value}" for key, value in counts.items()))
        if agent_error:
            print(f"agent-skills error: {agent_error}")
    return 1 if blocked else 0
