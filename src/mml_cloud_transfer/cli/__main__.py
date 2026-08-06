"""Command-line entry point.

Exists so the engine is testable and scriptable long before there is a GUI.
Later phases add transfer, resume, and report subcommands here.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

import requests

from mml_cloud_transfer.cli.scan_command import run_scan
from mml_cloud_transfer.cli.service_client import ServiceError
from mml_cloud_transfer.cli.transfer_command import (
    run_report_cmd,
    run_resume,
    run_status,
    run_transfer,
)
from mml_cloud_transfer.engine.joblock import JobLockError


def add_service_options(sub):
    sub.add_argument(
        "--service-url",
        default=os.environ.get("MMLCT_SERVICE_URL"),
        help="Drive this command through the service API at this URL",
    )
    sub.add_argument(
        "--token-file", default=None,
        help="Bearer-token file (default: the service data directory's api_token)",
    )


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

    def add_gcs_options(sub):
        sub.add_argument("--bucket", required=False, default=None,
                         help="Destination bucket name (or use --profile)")
        sub.add_argument("--credentials", default=None,
                         help="Service-account key file (default: ADC)")
        sub.add_argument("--workers", type=int, default=None,
                         help="Concurrent file transfers (default 4)")
        sub.add_argument("--report-dir", default=None,
                         help="Report output directory (default: <db>/reports/job-N)")
        sub.add_argument("--size-policy", default=None, help=argparse.SUPPRESS)
        sub.add_argument("--emulator-endpoint", default=None, help=argparse.SUPPRESS)

    transfer = subparsers.add_parser("transfer", help="Scan and run a transfer job")
    transfer.add_argument("--db", required=True)
    transfer.add_argument("--name", required=True)
    transfer.add_argument("--direction", choices=["upload", "download"],
                          default="upload")
    transfer.add_argument("--source", required=True,
                          help="Local folder (upload: source; download: destination)")
    transfer.add_argument("--prefix", default="", help="Bucket object-name prefix")
    transfer.add_argument("--audit-hash", action="store_true",
                          help="Also compute SHA-256 per file")
    add_gcs_options(transfer)
    transfer.add_argument(
        "--profile", default=None,
        help="Use a named connection profile (requires --service-url)",
    )
    transfer.add_argument(
        "--scheduled-at", default=None,
        help="Queue the job to start at this ISO-8601 time (requires --service-url)",
    )
    add_service_options(transfer)

    resume = subparsers.add_parser("resume", help="Resume an interrupted job")
    resume.add_argument("--db", required=True)
    resume.add_argument("--job-id", type=int, required=True)
    add_gcs_options(resume)
    add_service_options(resume)

    status = subparsers.add_parser("status", help="List jobs and their state")
    status.add_argument("--db", required=True)
    add_service_options(status)

    report = subparsers.add_parser("report", help="Re-export a job's report")
    report.add_argument("--db", required=True)
    report.add_argument("--job-id", type=int, required=True)
    report.add_argument("--out", default=None)
    report.add_argument("--bucket", default=None)
    report.add_argument("--report-dir", default=None, help=argparse.SUPPRESS)
    add_service_options(report)

    return parser


def _dispatch_via_service(dispatch, args) -> int:
    """Run a subcommand's dispatch function. When --service-url is not set,
    direct-engine mode is untouched: call straight through, so a genuine
    ConnectionError from the GCS client (AuthorizedSession is itself a
    requests.Session subclass) propagates exactly as before. Only when
    --service-url is set do the two ways the service can fail to answer
    turn into a friendly message and exit code 1. ValueError (e.g.
    --scheduled-at without --service-url, or a bad --workers) is left to
    propagate to the caller's own handler in both modes."""
    if not args.service_url:
        return dispatch(args)
    try:
        return dispatch(args)
    except requests.exceptions.ConnectionError:
        print(f"service not reachable at {args.service_url} — is it running?")
        return 1
    except ServiceError as exc:
        print(str(exc))
        return 1


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

    if args.command in ("transfer", "resume"):
        dispatch = run_transfer if args.command == "transfer" else run_resume
        try:
            return _dispatch_via_service(dispatch, args)
        except ValueError as exc:
            print(str(exc))
            return 2
        except JobLockError as exc:
            # Catches both JobAlreadyRunning (genuine contention) and
            # JobLockUnavailable (couldn't even open the lock file) — both
            # are "this run did not happen; here is why" for the operator.
            print(str(exc))
            return 3
    if args.command == "status":
        return _dispatch_via_service(run_status, args)
    if args.command == "report":
        return _dispatch_via_service(run_report_cmd, args)

    return 2


if __name__ == "__main__":
    sys.exit(main())
