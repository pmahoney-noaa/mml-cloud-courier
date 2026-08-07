import pytest

pytest.importorskip("PySide6")

import requests

from mml_cloud_transfer.cli.service_client import ServiceError
from mml_cloud_transfer.gui.watcher import detect_transitions, watch_job


class FakeClient:
    """Scripted transport. stream_steps: each is {'yields': [...]} with an
    optional 'raises'; get_job pops job_statuses; health pops health_steps
    (an Exception instance raises)."""

    def __init__(self, stream_steps, job_statuses=(), health_steps=()):
        self.stream_steps = list(stream_steps)
        self.job_statuses = list(job_statuses)
        self.health_steps = list(health_steps)

    def stream(self, job_id):
        step = self.stream_steps.pop(0)
        yield from step.get("yields", [])
        if "raises" in step:
            raise step["raises"]

    def get_job(self, job_id):
        return {"id": job_id, "status": self.job_statuses.pop(0)}

    def health(self):
        step = self.health_steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def test_watcher_survives_a_service_restart():
    tick1 = {"status": "running", "progress": {}, "events": [], "transferring": []}
    tick2 = {"status": "complete", "progress": {}, "events": [], "transferring": []}
    client = FakeClient(
        stream_steps=[
            {"yields": [tick1], "raises": requests.exceptions.ConnectionError()},
            {"yields": [tick2]},
        ],
        job_statuses=["complete"],
        health_steps=[requests.exceptions.ConnectionError(), {"status": "ok"}],
    )
    snapshots, states, sleeps = [], [], []

    final = watch_job(client, 1, sleep=sleeps.append,
                      on_snapshot=snapshots.append, on_state=states.append)

    assert [s["status"] for s in snapshots] == ["running", "complete"]
    assert "reconnecting" in states
    assert final["status"] == "complete"
    assert sleeps[:2] == [1, 2]          # backoff, not hammering


def test_watcher_keeps_watching_when_the_job_moves_on():
    incomplete = {"status": "incomplete", "progress": {}, "events": [], "transferring": []}
    complete = {"status": "complete", "progress": {}, "events": [], "transferring": []}
    client = FakeClient(
        stream_steps=[{"yields": [incomplete]}, {"yields": [complete]}],
        job_statuses=["stalled", "complete"],   # moved on, then settled
    )
    states = []
    final = watch_job(client, 1, sleep=lambda s: None, on_state=states.append)
    assert final["status"] == "complete"
    assert "waiting" in states


def test_watcher_gives_up_on_auth_or_missing_job():
    client = FakeClient(stream_steps=[{"raises": ServiceError(404, "no job")}])
    states = []
    assert watch_job(client, 9, sleep=lambda s: None, on_state=states.append) is None
    assert "failed" in states


def test_watcher_stops_when_asked():
    tick = {"status": "running", "progress": {}, "events": [], "transferring": []}
    client = FakeClient(stream_steps=[{"yields": [tick] * 100}])
    seen = []

    def on_snapshot(snap):
        seen.append(snap)

    assert watch_job(client, 1, stop=lambda: len(seen) >= 1,
                     sleep=lambda s: None, on_snapshot=on_snapshot) is None
    assert len(seen) == 1


def test_detect_transitions_flags_only_noteworthy_changes():
    before = {1: "running", 2: "running", 3: "pending"}
    after = {1: "complete", 2: "running", 3: "running", 4: "incomplete"}
    assert detect_transitions(before, after) == [(1, "running", "complete")]
