"""Command-line entry point.

Exists so the engine is testable and scriptable long before there is a GUI.
Later phases add transfer, resume, and report subcommands here.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from mml_cloud_transfer.cli.scan_command import run_scan


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mmlct", description="MML Cloud Transfer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Build a transfer manifest from a folder")
    scan.add_argument("--db", required=True, help="Path to the job database")
    scan.add_argument("--source", required=True, help="Folder to scan")
    scan.add_argument("--prefix", default="", help="Destination object-name prefix")
    scan.add_argument("--name", required=True, help="Job name")
    scan.add_argument("--job-id", type=int, default=None, help="Re-scan an existing job")
    scan.add_argument("--csv", default=None, help="Also write the manifest to this CSV path")
    scan.add_argument(
        "--no-extended-paths",
        action="store_true",
        help=r"Do not rewrite paths to \\?\ form (used by tests on non-Windows hosts)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "scan":
        try:
            outcome = run_scan(
                db_path=args.db,
                source_root=args.source,
                dest_prefix=args.prefix,
                job_name=args.name,
                job_id=args.job_id,
                csv_path=args.csv,
                follow_extended=not args.no_extended_paths,
            )
        except LookupError as exc:
            print(str(exc))
            return 1
        gib = outcome.byte_count / 1024**3
        print(f"Job {outcome.job_id}: {outcome.file_count} files, {gib:.2f} GiB")
        if outcome.errors:
            print(f"{len(outcome.errors)} error(s) during scan:")
            for error in outcome.errors[:20]:
                print(f"  [{error.category.value}] {error.message}")
            if len(outcome.errors) > 20:
                print(f"  ... and {len(outcome.errors) - 20} more")
            return 1
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
