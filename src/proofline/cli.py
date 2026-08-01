import argparse
from importlib import metadata
import os
from pathlib import Path
import sys

from proofline.home_writer import HomeInitError, initialize_home, reconcile_existing_home
from proofline.line_writer import LineInitError, initialize_line
from proofline.updater import UpdateError, UpdateResult, run_update
from proofline.validator import validate_project


def _is_update_postverification() -> bool:
    try:
        parent_cmdline = Path("/proc") / str(os.getppid()) / "cmdline"
        arguments = parent_cmdline.read_bytes().split(b"\0")
    except OSError:
        return False
    is_proofline = any(
        Path(os.fsdecode(value)).name == "proofline" for value in arguments if value
    )
    return is_proofline and b"update" in arguments


class _VersionAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None) -> None:
        if not getattr(namespace, "no_home_reconcile", False) and _is_update_postverification():
            try:
                reconcile_existing_home()
            except HomeInitError as exc:
                parser.exit(1, f"version failed: {exc}\n")
        print(f"{parser.prog} {metadata.version('proofline')}")
        parser.exit(0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proofline")
    parser.add_argument("--no-home-reconcile", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--version",
        action=_VersionAction,
        nargs=0,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="Validate a ProofLine project")
    init = commands.add_parser("init", help="Initialize ~/.proofline user resources")
    init.add_argument(
        "--dry-run", action="store_true", help="Preflight without writing user resources"
    )

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
    if args.command == "validate":
        root = Path.cwd()
        errors = validate_project(root)
        for error in errors:
            print(f"{error.path}: {error.code}: {error.message}", file=sys.stderr)
        return 1 if errors else 0

    if args.command == "init":
        try:
            result = initialize_home(dry_run=args.dry_run)
        except HomeInitError as exc:
            print(f"init failed: {exc}", file=sys.stderr)
            return 1
        prefix = "would create" if result.dry_run else result.status
        for path in result.paths:
            print(f"{prefix}: {path}")
        return 0

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
        root = Path.cwd()
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
