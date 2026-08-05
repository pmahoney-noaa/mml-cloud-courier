"""The generator is tested directly (injected sleep, capped ticks); one
endpoint test proves the HTTP wrapper streams and terminates."""

import json

import pytest
from fastapi.testclient import TestClient

from mml_cloud_transfer.core.models import Direction, JobStatus
from mml_cloud_transfer.service.app import create_app
from mml_cloud_transfer.service.config import load_config
from mml_cloud_transfer.service.controller import JobController
from mml_cloud_transfer.service.security import read_token
from mml_cloud_transfer.service.sse import format_sse, progress_events
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


@pytest.fixture
def db_with_job(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    repo = JobRepository(conn)
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root="s", dest_prefix=""
    )
    repo.record_event(job_id, "job_submitted")
    yield db, conn, repo, job_id
    conn.close()


def _parse(event_text):
    lines = event_text.strip().splitlines()
    assert lines[0] == "event: progress"
    return json.loads(lines[1][len("data: "):])


def test_stream_ticks_then_terminates_on_terminal_status(db_with_job):
    db, conn, repo, job_id = db_with_job
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        if len(slept) == 2:                      # finish during the 2nd wait
            repo.set_job_status(job_id, JobStatus.COMPLETE)

    events = [
        _parse(e) for e in progress_events(db, job_id, interval=0.5, sleep=sleep)
    ]
    assert events[0]["status"] == "pending"
    assert events[-1]["status"] == "complete"    # final tick emitted, then closed
    assert all(s == 0.5 for s in slept)


def test_events_are_cursored_not_repeated(db_with_job):
    db, conn, repo, job_id = db_with_job

    def sleep(seconds):
        repo.record_event(job_id, "tick")
        if sleep.calls == 1:
            repo.set_job_status(job_id, JobStatus.CANCELLED)
        sleep.calls += 1

    sleep.calls = 0
    ticks = [
        _parse(e) for e in progress_events(db, job_id, interval=0.5, sleep=sleep)
    ]
    seen = [event["id"] for tick in ticks for event in tick["events"]]
    assert seen == sorted(set(seen)), "an event id was repeated across ticks"
    assert ticks[0]["events"][0]["kind"] == "job_submitted"


def test_max_ticks_caps_a_never_ending_job(db_with_job):
    db, conn, repo, job_id = db_with_job
    ticks = list(progress_events(db, job_id, interval=0.0, max_ticks=3, sleep=lambda s: None))
    assert len(ticks) == 3


def test_stream_endpoint_serves_one_terminal_event(tmp_path):
    config = load_config(tmp_path / "data")
    app = create_app(config, JobController())
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {read_token(config.token_path)}"})

    conn = connect(config.db_path)
    repo = JobRepository(conn)
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root="s", dest_prefix=""
    )
    repo.set_job_status(job_id, JobStatus.COMPLETE)
    conn.close()

    assert client.get("/jobs/999/stream").status_code == 404
    with client.stream("GET", f"/jobs/{job_id}/stream") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())     # terminates: job is terminal
    assert "event: progress" in body
    assert '"status": "complete"' in body or '"status":"complete"' in body
