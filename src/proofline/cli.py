import argparse
from importlib import metadata
from pathlib import Path
import sys

from proofline.line_writer import LineInitError, initialize_line
from proofline.updater import UpdateError, UpdateResult, run_update
from proofline.validator import validate_project


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proofline")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {metadata.version('proofline')}",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="Validate a ProofLine project")

    update = commands.add_parser("update", help="Update the ProofLine uv tool")
    update.add_argument("--check", action="store_true", help="Check without changing the installed tool")
    update.add_argument("--version", dest="target_version", help="Use an exact stable version")
    update.add_argument(
        "--adopt-official",
        action="store_true",
        help="Explicitly replace a source installation with the official wheel",
    )

    line = commands.add_parser("line", help="Manage ProofLine Lines")
    line_commands = line.add_subparsers(dest="line_command", required=True)
    init = line_commands.add_parser("init", help="Create a Line and draft Discovery")
    init.add_argument("line_id", help="Explicit stable ID in line-NNNN form")
    init.add_argument("--title", required=True, help="Discovery H1 title")
    init.add_argument(
        "--dry-run", action="store_true", help="Preflight and render without writing"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path.cwd()
    if args.command == "validate":
        errors = validate_project(root)
        for error in errors:
            print(f"{error.path}: {error.code}: {error.message}", file=sys.stderr)
        return 1 if errors else 0

    if args.command == "update":
        try:
            result = run_update(
                check=args.check,
                version=args.target_version,
                adopt=args.adopt_official,
            )
        except UpdateError as exc:
            print(f"update failed: {exc}", file=sys.stderr)
            return 1
        print(f"current: {result.current}")
        print(f"target: {result.target}")
        print(f"provenance: {result.provenance}")
        print(f"status: {result.status}")
        return result.exit_code

    if args.command == "line" and args.line_command == "init":
        try:
            result = initialize_line(
                root, args.line_id, args.title, dry_run=args.dry_run
            )
        except LineInitError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        prefix = "would create" if result.dry_run else "created"
        for path in result.paths:
            print(f"{prefix}: {path}")
        return 0

    raise AssertionError("unreachable command")
