"""Job orchestration: scanned manifest in, COMPLETE/INCOMPLETE verdict out.

Threading model: a pool of file workers; each worker opens its own SQLite
connection (WAL + busy_timeout make this safe) and drives one file at a
time through retry/backoff. Slice-level parallelism happens inside
upload_sliced/download_file on top of this — those functions run their own
internal worker pools and invoke on_progress from those pool threads, not
from the file worker's thread. Since a sqlite3 connection may only be used
by the thread that created it, progress callbacks route through
``_callback_repo``, which hands back the file worker's own connection when
called from that thread and opens (and closes) a short-lived connection of
its own otherwise.
"""

from __future__ import annotations

import os
import random
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field

from mml_cloud_transfer.core.errors import ErrorCategory, classify
from mml_cloud_transfer.core.models import (
    Direction,
    FileState,
    JobStatus,
    PlannedFile,
    SliceState,
    TransferMethod,
)
from mml_cloud_transfer.core.paths import extended_path
from mml_cloud_transfer.core.retry import QUARANTINE_ATTEMPTS, RetrySchedule
from mml_cloud_transfer.core.slicing import SizePolicy, plan_slices
from mml_cloud_transfer.gcs.downloader import download_file
from mml_cloud_transfer.gcs.objects import get_meta, list_prefix
from mml_cloud_transfer.gcs.uploader import (
    ChecksumMismatch,
    upload_resumable,
    upload_single_shot,
    upload_sliced,
)
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


class JobPaused(Exception):
    """Raised inside a worker when retrying anything else is pointless."""


_QUERY_STRING = re.compile(r"\?[^\s\"']+")


def _scrub(message: str) -> str:
    """Strip URL query strings (session tokens are bearer credentials)."""
    return _QUERY_STRING.sub("?...", message)


@dataclass
class EngineOptions:
    policy: SizePolicy | None = None
    file_workers: int = 4
    slice_workers: int = 4
    chunk_size: int = 8 * 1024 * 1024
    download_range_bytes: int = 128 * 1024 * 1024
    retry: RetrySchedule = field(default_factory=RetrySchedule)
    audit: bool = True


def _stat_source(path: str) -> tuple[int, int]:
    """(size, mtime_ns) — module-level so tests can script stat sequences."""
    stat = os.stat(path)
    return stat.st_size, stat.st_mtime_ns


def _download_dest(source_root: str, relative_path: str) -> str:
    return os.path.join(extended_path(source_root), *relative_path.split("/"))


def scan_remote(ctx, db_path, job_id: int, *, policy: SizePolicy | None = None) -> int:
    """Plan a download job by listing the bucket prefix into the manifest."""
    conn = connect(db_path)
    try:
        repo = JobRepository(conn)
        job = repo.get_job(job_id)
        prefix = job["dest_prefix"].strip("/")
        lead = f"{prefix}/" if prefix else ""
        repo.set_job_status(job_id, JobStatus.SCANNING)
        repo.record_event(job_id, "scan_started", f"prefix={lead}")

        batch: list[PlannedFile] = []
        count = 0
        for meta in list_prefix(ctx, lead):
            if meta.name.endswith("/"):
                # A zero-byte "directory" placeholder object (gsutil/Console
                # create these for empty folders) — not a real file.
                continue
            relative = meta.name[len(lead):]
            if not relative:
                continue
            batch.append(
                PlannedFile(
                    relative_path=relative,
                    source_path=meta.name,
                    size_bytes=meta.size,
                    mtime_ns=0,
                )
            )
            count += 1
            if len(batch) >= 5000:
                repo.add_planned_files(job_id, batch, policy=policy)
                batch.clear()
        if batch:
            repo.add_planned_files(job_id, batch, policy=policy)

        repo.record_event(job_id, "scan_finished", f"files={count}")
        repo.set_job_status(job_id, JobStatus.PENDING)
        return count
    finally:
        conn.close()


def _classify_transfer_error(exc: Exception):
    if isinstance(exc, ChecksumMismatch):
        return ErrorCategory.CHECKSUM_MISMATCH, False, False
    cls = classify(exc)
    return cls.category, cls.transient, cls.pauses_job


@contextmanager
def _callback_repo(db_path, repo: JobRepository, owner_thread: int):
    """A repo bound to a connection safe for the *calling* thread.

    upload_sliced/download_file run their own internal worker pools and
    invoke on_progress from those pool threads, not from the thread that
    called _transfer_once. sqlite3 connections may only be used by the
    thread that created them, so a progress callback firing on a foreign
    thread gets its own connection, opened and closed within this single
    call — never handed back to the owning thread, which would trip the
    very same restriction on close().
    """
    if threading.get_ident() == owner_thread:
        yield repo
        return
    conn = connect(db_path)
    try:
        yield JobRepository(conn)
    finally:
        conn.close()


def _transfer_once(ctx, db_path, repo: JobRepository, job, row, options: EngineOptions) -> None:
    """One attempt at one file. Raises on failure; records success itself."""
    file_id = row["id"]
    method = TransferMethod(row["method"])
    with_sha256 = bool(job["audit_hash"])
    owner_thread = threading.get_ident()

    if job["direction"] == Direction.UPLOAD.value:
        precondition = repo.get_precondition(file_id)
        if precondition is None:
            meta = get_meta(ctx, row["object_name"])
            precondition = meta.generation if meta is not None else 0
            repo.set_precondition(file_id, precondition)

        if method is TransferMethod.SINGLE_SHOT:
            result = upload_single_shot(
                ctx, row["source_path"], row["object_name"],
                precondition_generation=precondition, with_sha256=with_sha256,
            )
        elif method is TransferMethod.RESUMABLE:
            slices = repo.get_slices(file_id)
            uri = slices[0]["session_uri"] if slices else None

            def on_progress(session_uri: str, committed: int) -> None:
                with _callback_repo(db_path, repo, owner_thread) as r:
                    r.upsert_slice(
                        file_id, 0, offset=0, length=row["size_bytes"],
                        session_uri=session_uri, state=SliceState.UPLOADING,
                        bytes_transferred=committed,
                    )
                    r.heartbeat(file_id, committed)

            result = upload_resumable(
                ctx, row["source_path"], row["object_name"], row["size_bytes"],
                precondition_generation=precondition, session_uri=uri,
                with_sha256=with_sha256, chunk_size=options.chunk_size,
                on_progress=on_progress,
            )
        else:
            specs = plan_slices(row["size_bytes"], policy=options.policy)
            spec_by_index = {s.index: s for s in specs}
            stored = {
                r["slice_index"]: (
                    r["session_uri"],
                    r["crc32c"] if r["state"] == SliceState.UPLOADED.value else None,
                )
                for r in repo.get_slices(file_id)
            }

            def on_slice(idx: int, uri: str | None, committed: int, crc: int | None) -> None:
                spec = spec_by_index[idx]
                with _callback_repo(db_path, repo, owner_thread) as r:
                    r.upsert_slice(
                        file_id, idx, offset=spec.offset, length=spec.length,
                        session_uri=uri, crc32c=crc,
                        state=SliceState.UPLOADED if crc is not None else SliceState.UPLOADING,
                        bytes_transferred=committed,
                    )
                    r.heartbeat(file_id, committed)

            result = upload_sliced(
                ctx, row["source_path"], row["object_name"], row["size_bytes"],
                precondition_generation=precondition, policy=options.policy,
                slice_states=stored, max_workers=options.slice_workers,
                chunk_size=options.chunk_size, with_sha256=with_sha256,
                on_progress=on_slice,
            )
    else:
        from mml_cloud_transfer.gcs.downloader import plan_ranges

        dest = _download_dest(job["source_root"], row["relative_path"])
        stored_ranges = {
            r["slice_index"]: r["crc32c"]
            for r in repo.get_slices(file_id)
            if r["state"] == SliceState.UPLOADED.value and r["crc32c"] is not None
        }
        range_by_index = {
            s.index: s
            for s in plan_ranges(
                row["size_bytes"], range_bytes=options.download_range_bytes
            )
        }

        def on_range(idx: int, done: int, crc: int | None) -> None:
            with _callback_repo(db_path, repo, owner_thread) as r:
                if crc is not None:
                    spec = range_by_index[idx]
                    r.upsert_slice(
                        file_id, idx, offset=spec.offset, length=spec.length,
                        crc32c=crc, state=SliceState.UPLOADED, bytes_transferred=done,
                    )
                r.heartbeat(file_id, done)

        result = download_file(
            ctx, row["object_name"], dest,
            range_states=stored_ranges,
            range_bytes=options.download_range_bytes,
            max_workers=options.slice_workers,
            with_sha256=with_sha256, on_progress=on_range,
        )

    if result.state == "skipped":
        repo.mark_skipped(
            file_id,
            local_crc32c=result.local_crc32c,
            remote_crc32c=result.remote_crc32c,
            generation=result.generation,
            sha256=result.sha256,
        )
    else:
        repo.mark_verified(
            file_id,
            local_crc32c=result.local_crc32c,
            remote_crc32c=result.remote_crc32c,
            generation=result.generation,
            sha256=result.sha256,
        )
    repo.clear_slices(file_id)


def _process_file(
    db_path, ctx, job, row, options: EngineOptions,
    sleep: Callable[[float], None], rng: random.Random,
) -> None:
    conn = connect(db_path)
    try:
        repo = JobRepository(conn)
        file_id = row["id"]

        if job["direction"] == Direction.UPLOAD.value:
            try:
                size, mtime = _stat_source(row["source_path"])
            except OSError as exc:
                cls = classify(exc)
                repo.mark_failed(file_id, cls.category, _scrub(str(exc))[:500])
                return
            if size != row["size_bytes"] or mtime != row["mtime_ns"]:
                if row["state"] == FileState.CHANGED.value:
                    repo.mark_failed(
                        file_id, ErrorCategory.SOURCE_CHANGED,
                        "source changed again while being transferred",
                    )
                else:
                    repo.mark_changed(file_id, size, mtime)
                    repo.record_event(
                        job["id"], "source_changed", row["relative_path"], file_id
                    )
                return

        delays = iter(options.retry.delays(rng))
        for attempt in range(options.retry.max_attempts):
            repo.mark_transferring(file_id)
            try:
                _transfer_once(ctx, db_path, repo, job, row, options)
                return
            except Exception as exc:
                category, transient, pauses = _classify_transfer_error(exc)
                repo.mark_failed(file_id, category, _scrub(str(exc))[:500])
                if category is ErrorCategory.CHECKSUM_MISMATCH:
                    # Don't let the next attempt reuse slice state recorded
                    # against the bytes that just failed verification.
                    repo.clear_slices(file_id)
                if pauses:
                    raise JobPaused(str(exc)) from exc
                cumulative = repo.get_file(file_id)["attempts"]
                if cumulative >= QUARANTINE_ATTEMPTS:
                    repo.quarantine(file_id)
                    repo.record_event(
                        job["id"], "quarantined", row["relative_path"], file_id
                    )
                    return
                if not transient or attempt == options.retry.max_attempts - 1:
                    return
                sleep(next(delays))
    finally:
        conn.close()


def _audit(ctx, repo: JobRepository, job) -> None:
    job_id = job["id"]
    rows = [
        r for r in repo.get_files(job_id)
        if r["state"] in (FileState.VERIFIED.value, FileState.SKIPPED.value)
    ]
    mismatches = 0
    if job["direction"] == Direction.UPLOAD.value:
        prefix = job["dest_prefix"].strip("/")
        lead = f"{prefix}/" if prefix else ""
        remote = {m.name: m for m in list_prefix(ctx, lead)}
        for row in rows:
            meta = remote.get(row["object_name"])
            if meta is None:
                repo.mark_failed(
                    row["id"], ErrorCategory.NOT_FOUND, "missing at audit"
                )
                mismatches += 1
            elif meta.size != row["size_bytes"] or (
                row["remote_crc32c"] is not None
                and meta.crc32c != row["remote_crc32c"]
            ):
                repo.mark_failed(
                    row["id"], ErrorCategory.CHECKSUM_MISMATCH, "audit mismatch"
                )
                mismatches += 1
    else:
        for row in rows:
            dest = _download_dest(job["source_root"], row["relative_path"])
            try:
                size, _ = _stat_source(dest)
            except OSError:
                repo.mark_failed(
                    row["id"], ErrorCategory.NOT_FOUND, "missing at audit"
                )
                mismatches += 1
                continue
            if size != row["size_bytes"]:
                repo.mark_failed(
                    row["id"], ErrorCategory.CHECKSUM_MISMATCH, "audit size mismatch"
                )
                mismatches += 1
    repo.record_event(
        job_id, "audit_finished", f"checked={len(rows)} mismatches={mismatches}"
    )


def run_job(
    db_path,
    job_id: int,
    ctx,
    *,
    options: EngineOptions | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> JobStatus:
    options = options or EngineOptions()
    rng = rng or random.Random()

    conn = connect(db_path)
    try:
        repo = JobRepository(conn)
        job = dict(repo.get_job(job_id))
        repo.start_job(job_id)
        repo.record_event(job_id, "run_started")
        repo.reset_stale_transfers(job_id)

        paused = False
        for _pass in range(2):  # second pass picks up files marked `changed`
            pending = [dict(r) for r in repo.iter_pending_files(job_id)]
            if _pass == 1:
                pending = [
                    r for r in pending if r["state"] == FileState.CHANGED.value
                ]
            if not pending:
                break
            with ThreadPoolExecutor(max_workers=options.file_workers) as pool:
                futures = [
                    pool.submit(
                        _process_file, db_path, ctx, job, row, options, sleep, rng
                    )
                    for row in pending
                ]
                for future in futures:
                    try:
                        future.result()
                    except JobPaused as exc:
                        repo.record_event(job_id, "run_paused", str(exc)[:200])
                        pool.shutdown(cancel_futures=True)
                        paused = True
                        break
            if paused:
                break

        if paused:
            repo.finish_job(job_id, JobStatus.PAUSED)
            repo.record_event(job_id, "run_finished", JobStatus.PAUSED.value)
            return JobStatus.PAUSED

        if options.audit:
            _audit(ctx, repo, job)

        status = repo.job_verdict(job_id)
        repo.finish_job(job_id, status)
        repo.record_event(job_id, "run_finished", status.value)
        return status
    finally:
        conn.close()
