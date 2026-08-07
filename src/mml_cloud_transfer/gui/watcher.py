"""Watch a job the way the service actually behaves.

Two lessons from the Phase 3 gate are load-bearing here: (1) the SSE
stream dies with the service while the job survives — so transport loss
means back off, poll /health, re-attach, and let the fresh stream
re-render everything from the service DB; (2) the stream's terminal
INCOMPLETE tick can precede a STALLED retry (see service/sse.py), so a
closed stream is a hint, and get_job is the truth about "settled".
"""

from __future__ import annotations

import time
from collections.abc import Callable

import requests

from mml_cloud_transfer.cli.service_client import ServiceError

SETTLED = frozenset({"complete", "incomplete", "paused", "cancelled"})
NOTEWORTHY = frozenset({"complete", "incomplete", "stalled"})
_BACKOFF = (1, 2, 4, 8, 15)
_TRANSPORT_ERRORS = (requests.exceptions.RequestException, ValueError)
# ValueError: a snapshot line truncated by a dying service breaks json.loads


def _wait_for_service(client, stop, sleep, on_state) -> bool:
    on_state("reconnecting")
    attempt = 0
    while not stop():
        sleep(_BACKOFF[min(attempt, len(_BACKOFF) - 1)])
        attempt += 1
        try:
            client.health()
            return True
        except requests.exceptions.RequestException:
            continue
    return False


def watch_job(
    client,
    job_id: int,
    *,
    stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
    on_snapshot: Callable[[dict], None] = lambda snap: None,
    on_state: Callable[[str], None] = lambda state: None,
) -> dict | None:
    while not stop():
        try:
            on_state("streaming")
            for snapshot in client.stream(job_id):
                on_snapshot(snapshot)
                if stop():
                    return None
            job = client.get_job(job_id)
            if job["status"] in SETTLED:
                return {key: job[key] for key in job}
            on_state("waiting")   # e.g. INCOMPLETE tick, then STALLED retry
            sleep(2.0)
        except ServiceError as exc:
            if exc.status_code in (401, 403, 404):
                on_state("failed")
                return None
            if not _wait_for_service(client, stop, sleep, on_state):
                return None
        except _TRANSPORT_ERRORS:
            if not _wait_for_service(client, stop, sleep, on_state):
                return None
    return None


def poll_loop(
    client,
    *,
    stop: Callable[[], bool],
    sleep: Callable[[float], None] = time.sleep,
    interval: float = 2.0,
    on_jobs: Callable[[list], None],
    on_down: Callable[[str], None],
) -> None:
    while not stop():
        try:
            on_jobs(client.list_jobs())
        except (requests.exceptions.RequestException, ServiceError, ValueError) as exc:
            on_down(str(exc))
        sleep(interval)


def detect_transitions(
    before: dict[int, str], after: dict[int, str]
) -> list[tuple[int, str, str]]:
    """Status changes worth a tray balloon. First sight of a job is not a
    transition — reopening the GUI must not replay a night of balloons."""
    out = []
    for job_id in sorted(after):
        old, new = before.get(job_id), after[job_id]
        if old is not None and old != new and new in NOTEWORTHY:
            out.append((job_id, old, new))
    return out
