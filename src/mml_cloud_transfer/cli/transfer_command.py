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
from mml_cloud_transfer.cli.service_client import ApiClient, ServiceError
from mml_cloud_transfer.service.security import read_token
from mml_cloud_transfer.service.config import load_config
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


def parse_size_policy(text: str) -> SizePolicy:
    return SizePolicy.parse(text)


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


def _api_client(args) -> ApiClient:
    token_path = (
        Path(args.token_file) if args.token_file else load_config().token_path
    )
    return ApiClient(args.service_url, read_token(token_path))


def _watch(client: ApiClient, job_id: int) -> JobStatus:
    """Print progress lines until the server closes the stream, then
    return the job's final status."""
    last_line = ""
    final_status = None
    for event in client.stream(job_id):
        final_status = event["status"]
        progress = event["progress"]
        line = (
            f"[{event['status']}] "
            f"{progress['files_done']}/{progress['files_total']} files, "
            f"{progress['bytes_done']}/{progress['bytes_total']} bytes"
        )
        if line != last_line:
            print(line)
            last_line = line
        for entry in event["events"]:
            if entry["kind"] in ("job_stalled", "job_unstalled", "run_paused"):
                print(f"  ! {entry['kind']}: {entry['detail'] or ''}")
    if final_status is None:
        raise ServiceError(0, "stream ended without any event")
    return JobStatus(final_status)


def _finish_via_service(client: ApiClient, job_id: int, status: JobStatus) -> int:
    report = client.report(job_id)
    print(f"Job {job_id}: {status.value.upper()}")
    print(f"Report: {report['report_html']}")
    # A COMPLETE verdict only means every PLANNED file transferred — a scan
    # that silently skipped files never gets them into the manifest, so the
    # report alone can't reveal the gap. scan_error events are persisted
    # even in service mode; surface them here the same way direct mode does.
    scan_errors = [e for e in client.events(job_id, after_id=0) if e["kind"] == "scan_error"]
    if scan_errors:
        print(
            f"{len(scan_errors)} scan error(s) — some files were never"
            " planned; see the report"
        )
        return 1
    return 0 if status is JobStatus.COMPLETE else 1


_SERVICE_TERMINAL_STATUSES = {
    JobStatus.COMPLETE.value, JobStatus.INCOMPLETE.value,
    JobStatus.PAUSED.value, JobStatus.CANCELLED.value,
}


def _watch_until_settled(client: ApiClient, job_id: int) -> JobStatus:
    """`_watch` can return INCOMPLETE for a job the service is about to mark
    STALLED and quietly retry: the SSE stream always emits the terminal
    INCOMPLETE tick before the stall is decided (see service/sse.py), so an
    in-flight watcher sees it first. Re-check the job once the stream
    closes and keep watching if it actually moved on rather than settled."""
    while True:
        _watch(client, job_id)
        current = client.get_job(job_id)["status"]
        if current in _SERVICE_TERMINAL_STATUSES:
            return JobStatus(current)
        print(
            f"job moved to {current} — still being retried by the service;"
            " watching..."
        )


def run_transfer_via_service(args) -> int:
    client = _api_client(args)
    job_id = client.submit_job({
        "name": args.name,
        "direction": args.direction,
        "source_root": args.source,
        "dest_prefix": args.prefix,
        "bucket": args.bucket,
        "credentials_path": args.credentials,
        "emulator_endpoint": args.emulator_endpoint,
        "audit_hash": args.audit_hash,
        "scheduled_start_at": args.scheduled_at,
    })
    print(f"Job {job_id} submitted")
    if args.scheduled_at:
        print(f"Scheduled to start at {args.scheduled_at}; check progress with"
              f" 'mmlct status --service-url {args.service_url}'")
        return 0
    return _finish_via_service(client, job_id, _watch_until_settled(client, job_id))


def run_transfer(args) -> int:
    if args.scheduled_at and not args.service_url:
        raise ValueError(
            "--scheduled-at requires the service; pass --service-url"
        )
    if args.service_url:
        return run_transfer_via_service(args)
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
    if args.service_url:
        client = _api_client(args)
        client.resume(args.job_id)
        return _finish_via_service(
            client, args.job_id, _watch_until_settled(client, args.job_id)
        )
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
    if args.service_url:
        client = _api_client(args)
        jobs = client.list_jobs()
        if not jobs:
            print("No jobs.")
            return 0
        for job in jobs:
            print(
                f"#{job['id']} {job['name']} [{job['direction']}] {job['status']}"
                f" — {display_path(job['source_root'])} ->"
                f" {job['dest_prefix'] or '(root)'}"
            )
        return 0
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
    if args.service_url:
        client = _api_client(args)
        print(f"Report: {client.report(args.job_id)['report_html']}")
        return 0
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
