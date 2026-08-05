"""REST surface. No worker thread: lifecycle actions on a *running* job are
tested by arming the controller directly, exactly as the worker does."""

import pytest
from fastapi.testclient import TestClient

from mml_cloud_transfer.core.models import JobStatus
from mml_cloud_transfer.service.app import create_app
from mml_cloud_transfer.service.config import load_config
from mml_cloud_transfer.service.controller import JobController
from mml_cloud_transfer.service.security import read_token
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


@pytest.fixture
def api(tmp_path):
    config = load_config(tmp_path / "data")
    controller = JobController()
    app = create_app(config, controller)
    client = TestClient(app)
    client.headers.update(
        {"Authorization": f"Bearer {read_token(config.token_path)}"}
    )
    return client, config, controller


def _submit(client, tmp_path, **overrides):
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    payload = {
        "name": "j", "direction": "upload", "source_root": str(src),
        "dest_prefix": "p", "bucket": "b",
    }
    payload.update(overrides)
    response = client.post("/jobs", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["job_id"]


def test_routes_require_the_bearer_token(api):
    client, config, _ = api
    bare = TestClient(client.app)
    assert bare.get("/health").status_code == 200          # health is open
    assert bare.get("/jobs").status_code == 401
    assert bare.post("/jobs", json={}).status_code == 401
    wrong = TestClient(client.app)
    wrong.headers.update({"Authorization": "Bearer nope"})
    assert wrong.get("/jobs").status_code == 401


def test_submit_upload_validates_the_source_folder(api, tmp_path):
    client, _, _ = api
    response = client.post("/jobs", json={
        "name": "j", "direction": "upload",
        "source_root": str(tmp_path / "missing"), "bucket": "b",
    })
    assert response.status_code == 400
    assert "not found" in response.json()["detail"]


def test_submit_creates_job_and_profile(api, tmp_path):
    client, config, _ = api
    job_id = _submit(client, tmp_path, emulator_endpoint="http://127.0.0.1:1")
    conn = connect(config.db_path)
    repo = JobRepository(conn)
    job = repo.get_job(job_id)
    profile = repo.get_profile(job["profile_id"])
    conn.close()
    assert job["status"] == JobStatus.PENDING.value
    assert profile["auth_type"] == "emulator"
    assert profile["credential_ref"] == "http://127.0.0.1:1"
    detail = client.get(f"/jobs/{job_id}").json()
    assert detail["status"] == "pending"
    assert detail["progress"]["files_total"] == 0
    assert client.get("/jobs").json()[0]["id"] == job_id


def test_schedule_is_normalized_to_utc_seconds(api, tmp_path):
    client, _, _ = api
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    response = client.post("/jobs", json={
        "name": "j", "direction": "upload", "source_root": str(src),
        "bucket": "b", "scheduled_start_at": "2027-01-01T09:30:00+02:00",
    })
    assert response.status_code == 201
    assert response.json()["scheduled_start_at"] == "2027-01-01T07:30:00+00:00"
    bad = client.post("/jobs", json={
        "name": "j", "direction": "upload", "source_root": str(src),
        "bucket": "b", "scheduled_start_at": "tonight",
    })
    assert bad.status_code == 422


def test_pause_resume_cancel_on_queued_jobs(api, tmp_path):
    client, _, _ = api
    job_id = _submit(client, tmp_path)
    assert client.post(f"/jobs/{job_id}/pause").json()["status"] == "paused"
    assert client.post(f"/jobs/{job_id}/resume").json()["status"] == "pending"
    assert client.post(f"/jobs/{job_id}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/jobs/{job_id}/resume").json()["status"] == "pending"
    assert client.post(f"/jobs/{job_id}/resume").status_code == 409
    assert client.post("/jobs/999/pause").status_code == 404


def test_lifecycle_on_a_running_job_goes_through_the_controller(api, tmp_path):
    client, config, controller = api
    job_id = _submit(client, tmp_path)
    conn = connect(config.db_path)
    JobRepository(conn).set_job_status(job_id, JobStatus.RUNNING)
    conn.close()
    stop = controller.job_started(job_id)
    assert client.post(f"/jobs/{job_id}/pause").json()["status"] == "stopping"
    assert stop.is_set()
    assert controller.job_finished() == "pause"


def test_lifecycle_on_a_scanning_job_goes_through_the_controller(api, tmp_path):
    """The worker arms the controller before running the scan, so a job
    stuck in a slow scan must be pauseable/cancellable the same way a
    running job is (Task 6 review finding)."""
    client, config, controller = api
    job_id = _submit(client, tmp_path)
    conn = connect(config.db_path)
    JobRepository(conn).set_job_status(job_id, JobStatus.SCANNING)
    conn.close()
    stop = controller.job_started(job_id)
    assert client.post(f"/jobs/{job_id}/pause").json()["status"] == "stopping"
    assert stop.is_set()
    assert controller.job_finished() == "pause"


def test_pause_and_cancel_on_a_scanning_job_with_nothing_active_conflict(api, tmp_path):
    client, config, _ = api
    job_id = _submit(client, tmp_path)
    conn = connect(config.db_path)
    JobRepository(conn).set_job_status(job_id, JobStatus.SCANNING)
    conn.close()
    assert client.post(f"/jobs/{job_id}/pause").status_code == 409
    assert client.post(f"/jobs/{job_id}/cancel").status_code == 409


def test_report_endpoint_writes_the_three_files(api, tmp_path):
    client, _, _ = api
    job_id = _submit(client, tmp_path)
    paths = client.post(f"/jobs/{job_id}/report").json()
    from pathlib import Path
    assert Path(paths["report_html"]).exists()
    assert Path(paths["summary_json"]).exists()
    assert Path(paths["manifest_csv"]).exists()
