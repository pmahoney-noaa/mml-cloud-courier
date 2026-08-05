"""Server-Sent Events: one progress snapshot per tick, ~2/second.

Ticks continue while the client stays connected and the job is live; the
tick that shows a terminal status is emitted and then the stream closes.
`stalled` is deliberately NOT terminal so a watcher sees recovery happen.
The generator owns one read-only SQLite connection for its lifetime (ticks
run sequentially but may execute on different worker threads) and closes
it on the way out — including when the client disconnects and the generator
is garbage-collected mid-yield.

A subtlety this creates: the worker always writes the terminal INCOMPLETE
status before it decides (separately, afterward) whether sustained network
failure means the job should be parked STALLED. An in-flight watcher — one
that was already connected when the run ended — therefore receives the
INCOMPLETE terminal tick and the stream closes, even though the job goes on
to STALLED (and gets retried) a moment later. A watcher that connects late,
after the job is already STALLED, does see `stalled` ticks correctly (it
just never sees them as terminal). The CLI compensates for the in-flight
case by re-checking the job's status once the stream closes and resuming
the watch if it moved on (see cli/transfer_command.py's
`_watch_until_settled`).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict

from mml_cloud_transfer.core.models import JobStatus
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

TERMINAL_STREAM_STATUSES = frozenset({
    JobStatus.COMPLETE.value,
    JobStatus.INCOMPLETE.value,
    JobStatus.PAUSED.value,
    JobStatus.CANCELLED.value,
})


def snapshot(repo: JobRepository, job_id: int, after_event_id: int) -> dict:
    job = repo.get_job(job_id)
    return {
        "job_id": job_id,
        "status": job["status"],
        "progress": asdict(repo.job_progress(job_id)),
        "events": [
            {"id": e["id"], "at": e["at"], "kind": e["kind"], "detail": e["detail"]}
            for e in repo.events_after(job_id, after_event_id)
        ],
    }


def format_sse(data: dict) -> str:
    return f"event: progress\ndata: {json.dumps(data)}\n\n"


def progress_events(
    db_path,
    job_id: int,
    *,
    interval: float = 0.5,
    max_ticks: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[str]:
    # Starlette's iterate_in_threadpool may dispatch each next() call onto a
    # different worker thread, but ticks are strictly sequential — the connection
    # is never used by two threads concurrently, so check_same_thread=False is safe.
    conn = connect(db_path, check_same_thread=False)
    try:
        repo = JobRepository(conn)
        last_event_id = 0
        ticks = 0
        while True:
            data = snapshot(repo, job_id, last_event_id)
            if data["events"]:
                last_event_id = data["events"][-1]["id"]
            yield format_sse(data)
            ticks += 1
            if data["status"] in TERMINAL_STREAM_STATUSES:
                return
            if max_ticks is not None and ticks >= max_ticks:
                return
            sleep(interval)
    finally:
        conn.close()
