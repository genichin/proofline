import argparse
from pathlib import Path
import sys

from proofline.line_writer import LineInitError, initialize_line
from proofline.validator import validate_project


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proofline")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="Validate a ProofLine project")

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
