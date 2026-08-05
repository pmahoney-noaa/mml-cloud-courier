"""transfer / resume / status / report subcommands.

Thin wiring: build a GcsContext, drive scan + run_job, write the report,
translate the verdict into an exit code. All logic lives in engine/ and gcs/.
"""

from __future__ import annotations

from pathlib import Path

from mml_cloud_transfer.core.models import Direction, JobStatus
from mml_cloud_transfer.core.paths import display_path
from mml_cloud_transfer.core.slicing import SizePolicy
from mml_cloud_transfer.engine.report import write_report
from mml_cloud_transfer.engine.runner import EngineOptions, run_job, scan_remote
from mml_cloud_transfer.gcs.client import make_context
from mml_cloud_transfer.cli.scan_command import run_scan
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


def parse_size_policy(text: str) -> SizePolicy:
    parts = text.split(",")
    if len(parts) != 3:
        raise ValueError(
            "size policy must be 'single_shot_max,resumable_max,min_slice'"
        )
    single, resumable, min_slice = (int(p) for p in parts)
    return SizePolicy(
        single_shot_max=single, resumable_max=resumable,
        min_slice=min_slice, max_components=32,
    )


def _options(args) -> EngineOptions:
    options = EngineOptions()
    if args.size_policy:
        options.policy = parse_size_policy(args.size_policy)
    if args.workers is not None:
        if args.workers < 1:
            raise ValueError("--workers must be >= 1")
        options.file_workers = args.workers
    return options


def _context(args):
    return make_context(
        args.bucket,
        credentials_path=args.credentials,
        emulator_endpoint=args.emulator_endpoint,
    )


def _report_dir(args, job_id: int) -> Path:
    if args.report_dir:
        return Path(args.report_dir)
    return Path(args.db).resolve().parent / "reports" / f"job-{job_id}"


def _finish(args, db, job_id: int, status: JobStatus) -> int:
    paths = write_report(db, job_id, _report_dir(args, job_id), bucket=args.bucket)
    print(f"Job {job_id}: {status.value.upper()}")
    print(f"Report: {paths.report_html}")
    return 0 if status is JobStatus.COMPLETE else 1


def run_transfer(args) -> int:
    options = _options(args)
    ctx = _context(args)
    direction = Direction(args.direction)
    scan_error_count = 0

    if direction is Direction.UPLOAD:
        outcome = run_scan(
            db_path=args.db, source_root=args.source, dest_prefix=args.prefix,
            job_name=args.name, policy=options.policy,
        )
        job_id = outcome.job_id
        scan_error_count = len(outcome.errors)
        print(f"Scanned {outcome.file_count} files")
        if outcome.errors:
            print(f"{len(outcome.errors)} scan error(s) — see the report")
    else:
        conn = connect(args.db)
        try:
            repo = JobRepository(conn)
            job_id = repo.create_job(
                name=args.name, direction=Direction.DOWNLOAD,
                source_root=args.source, dest_prefix=args.prefix,
                audit_hash=args.audit_hash,
            )
        finally:
            conn.close()
        count = scan_remote(ctx, args.db, job_id, policy=options.policy)
        print(f"Listed {count} objects")

    if direction is Direction.UPLOAD and args.audit_hash:
        conn = connect(args.db)
        try:
            JobRepository(conn).set_audit_hash(job_id, True)
        finally:
            conn.close()

    status = run_job(args.db, job_id, ctx, options=options)
    code = _finish(args, args.db, job_id, status)
    # A scan that failed to enumerate every file is not a complete transfer,
    # even if every file it did find made it — force a nonzero exit so the
    # caller doesn't treat this as success.
    return 1 if scan_error_count else code


def run_resume(args) -> int:
    conn = connect(args.db)
    try:
        repo = JobRepository(conn)
        try:
            job = repo.get_job(args.job_id)
        except LookupError as exc:
            print(str(exc))
            return 1
    finally:
        conn.close()

    # Validate options (e.g. --workers) before building the GCS context, so
    # a bad flag fails cleanly without touching credentials or the network.
    options = _options(args)
    ctx = _context(args)
    status = run_job(args.db, args.job_id, ctx, options=options)
    return _finish(args, args.db, args.job_id, status)


def run_status(args) -> int:
    conn = connect(args.db)
    try:
        jobs = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        repo = JobRepository(conn)
        if not jobs:
            print("No jobs.")
            return 0
        for job in jobs:
            counts = repo.count_by_state(job["id"])
            states = ", ".join(f"{k.value}: {v}" for k, v in sorted(counts.items()))
            print(
                f"#{job['id']} {job['name']} [{job['direction']}] "
                f"{job['status']} — {display_path(job['source_root'])} -> "
                f"{job['dest_prefix'] or '(root)'} — {states or 'no files'}"
            )
        return 0
    finally:
        conn.close()


def run_report_cmd(args) -> int:
    conn = connect(args.db)
    try:
        JobRepository(conn).get_job(args.job_id)
    except LookupError as exc:
        print(str(exc))
        return 1
    finally:
        conn.close()
    paths = write_report(
        args.db, args.job_id, args.out or _report_dir(args, args.job_id),
        bucket=args.bucket,
    )
    print(f"Report: {paths.report_html}")
    return 0
