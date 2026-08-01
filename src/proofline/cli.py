import argparse
from pathlib import Path
import sys

from proofline.validator import validate_project


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proofline")
    parser.add_argument("command", choices=["validate"])
    return parser


def main() -> int:
    build_parser().parse_args()
    errors = validate_project(Path.cwd())
    for error in errors:
        print(f"{error.path}: {error.code}: {error.message}", file=sys.stderr)
    return 1 if errors else 0
