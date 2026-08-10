"""REST surface. No worker thread: lifecycle actions on a *running* job are
tested by arming the controller directly, exactly as the worker does."""

import pytest
from fastapi.testclient import TestClient

from mml_cloud_courier.core.errors import ErrorCategory
from mml_cloud_courier.core.models import Direction, JobStatus, PlannedFile
from mml_cloud_courier.service.app import create_app
from mml_cloud_courier.service.config import load_config
from mml_cloud_courier.service.controller import JobController
from mml_cloud_courier.service.security import read_token
from mml_cloud_courier.store.db import connect
from mml_cloud_courier.store.repository import JobRepository


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


def test_submit_rejects_a_relative_source_root(api, tmp_path):
    """IMPORTANT 6 regression: a relative path resolves against the
    service's own CWD (System32 when installed), not the caller's — must
    be rejected outright rather than silently escalated against."""
    client, _, _ = api
    response = client.post("/jobs", json={
        "name": "j", "direction": "upload",
        "source_root": "relative/dir", "bucket": "b",
    })
    assert response.status_code == 400
    assert "absolute path" in response.json()["detail"]


def test_submit_rejects_a_raw_device_path(api, tmp_path):
    client, _, _ = api
    response = client.post("/jobs", json={
        "name": "j", "direction": "download",
        "source_root": r"\\.\PhysicalDrive0", "bucket": "b",
    })
    assert response.status_code == 400
    assert "absolute path" in response.json()["detail"]


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
    # Phase 5 gate feedback: the event timeline must record why the job is
    # queued, so the GUI's events pane can remind the user.
    job_id = response.json()["job_id"]
    events = client.get(f"/jobs/{job_id}/events").json()
    scheduled_events = [e for e in events if e["kind"] == "scheduled"]
    assert len(scheduled_events) == 1
    assert "waiting until 2027-01-01T07:30:00+00:00" in scheduled_events[0]["detail"]
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


def test_pause_on_a_claimed_pending_job_goes_through_the_controller(api, tmp_path):
    """IMPORTANT 2 regression: a pause can land in the worker's window
    between _pick (job still PENDING in the DB) and job_started (controller
    marks it active). Once the controller has claimed the job, pause must
    route through request()/the stop event rather than flip the row
    directly — a direct flip here would be silently overwritten once
    run_job/run_scan starts."""
    client, config, controller = api
    job_id = _submit(client, tmp_path)
    stop = controller.job_started(job_id)
    assert client.post(f"/jobs/{job_id}/pause").json()["status"] == "stopping"
    assert stop.is_set()
    assert controller.job_finished() == "pause"


def test_cancel_on_a_claimed_pending_job_goes_through_the_controller(api, tmp_path):
    client, config, controller = api
    job_id = _submit(client, tmp_path)
    stop = controller.job_started(job_id)
    assert client.post(f"/jobs/{job_id}/cancel").json()["status"] == "stopping"
    assert stop.is_set()
    assert controller.job_finished() == "cancel"


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


def test_bridge_config_submission_shape_is_pinned(api, tmp_path):
    """Carried-over item 4: the live install submits with bucket + ADC and
    no profile field. That shape must keep working verbatim — DPAPI
    profiles replace it eventually; they do not break it."""
    client, config, _ = api
    job_id = _submit(client, tmp_path)  # bucket-only payload, as before
    conn = connect(config.db_path)
    try:
        repo = JobRepository(conn)
        job = repo.get_job(job_id)
        profile = repo.get_profile(job["profile_id"])
        assert profile["auth_type"] == "adc"
        assert profile["bucket"] == "b"
    finally:
        conn.close()


def _free_drive_letter() -> str:
    import os
    import string

    for letter in reversed(string.ascii_uppercase):
        if not os.path.exists(f"{letter}:\\"):
            return letter
    pytest.skip("every drive letter exists on this machine")


def test_unreachable_drive_letter_explains_mapped_drives(api):
    """A drive that does not exist for the service is (almost always) the
    user's mapped drive. The error must teach, not just refuse."""
    client, _, _ = api
    response = client.post("/jobs", json={
        "name": "j", "direction": "upload",
        "source_root": f"{_free_drive_letter()}:\\imaging\\run47", "bucket": "b",
    })
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "running as" in detail
    assert "mapped drive" in detail


def test_unreachable_unc_share_names_the_service_identity(api, monkeypatch):
    """UNC checks against a dead host can hang for many seconds — the
    filesystem answer is stubbed; the MESSAGE is what this test pins."""
    import os

    client, _, _ = api
    real_isdir = os.path.isdir
    monkeypatch.setattr(
        os.path, "isdir",
        lambda p: False if "unreachable-host" in str(p) else real_isdir(p),
    )
    response = client.post("/jobs", json={
        "name": "j", "direction": "upload",
        "source_root": r"\\unreachable-host\share\data", "bucket": "b",
    })
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "running as" in detail
    assert "VPN" in detail


def test_deep_download_destination_is_created_via_extended_path(api, tmp_path):
    """Spec: \\\\?\\-prefixed paths so >260-char destinations do not fail."""
    client, _, _ = api
    deep = tmp_path
    for i in range(12):
        deep = deep / ("d" * 24)
    assert len(str(deep)) > 260
    response = client.post("/jobs", json={
        "name": "j", "direction": "download",
        "source_root": str(deep), "bucket": "b",
    })
    assert response.status_code == 201, response.text
    import os
    from mml_cloud_courier.core.paths import extended_path
    assert os.path.isdir(extended_path(str(deep)))


def _seed_error_job(config) -> int:
    conn = connect(config.db_path)
    repo = JobRepository(conn)
    job_id = repo.create_job(name="e", direction=Direction.UPLOAD,
                             source_root="C:\\d", dest_prefix="")
    repo.add_planned_files(job_id, [
        PlannedFile(f"f{i}.bin", f"C:\\d\\f{i}.bin", 10, 1) for i in range(3)
    ])
    files = repo.get_files(job_id)
    repo.mark_failed(files[0]["id"], ErrorCategory.PERMISSION_DENIED, "denied")
    repo.mark_failed(files[1]["id"], ErrorCategory.PERMISSION_DENIED, "denied")
    conn.close()
    return job_id


def test_errors_route_groups_by_cause_with_plain_language(api):
    client, config, _ = api
    job_id = _seed_error_job(config)
    response = client.get(f"/jobs/{job_id}/errors")
    assert response.status_code == 200
    top = response.json()[0]
    assert top["category"] == "permission_denied"
    assert top["count"] == 2
    assert "denied" in top["message"].lower()
    assert top["action"]


def test_error_group_actions_round_trip_and_guard_running_jobs(api):
    client, config, _ = api
    job_id = _seed_error_job(config)          # 2 permission_denied failures

    assert client.post(f"/jobs/{job_id}/errors/nonsense/retry").status_code == 422

    response = client.post(f"/jobs/{job_id}/errors/permission_denied/retry")
    assert response.status_code == 200 and response.json()["count"] == 2

    conn = connect(config.db_path)
    JobRepository(conn).set_job_status(job_id, JobStatus.RUNNING)
    conn.close()
    response = client.post(f"/jobs/{job_id}/errors/permission_denied/exclude")
    assert response.status_code == 409
    assert "pause" in response.json()["detail"]


def test_error_actions_guard_against_controller_claimed_pending_job(api):
    """Error actions must not race with worker pickup. A PENDING job claimed
    by the controller (between _pick and job_started) must refuse mutations."""
    client, config, controller = api
    job_id = _seed_error_job(config)

    # Simulate worker claiming the job
    stop = controller.job_started(job_id)

    # Both retry and exclude must refuse while controller owns it
    response = client.post(f"/jobs/{job_id}/errors/permission_denied/retry")
    assert response.status_code == 409
    assert "pause" in response.json()["detail"]

    response = client.post(f"/jobs/{job_id}/errors/permission_denied/exclude")
    assert response.status_code == 409
    assert "pause" in response.json()["detail"]

    # Verify failed file rows are unchanged (no silent mutations)
    conn = connect(config.db_path)
    all_files = JobRepository(conn).get_files(job_id)
    conn.close()
    failed_files = [f for f in all_files if f["state"] == "failed"]
    assert len(failed_files) == 2
    assert all(f["error_category"] == "permission_denied" for f in failed_files)

    controller.job_finished()


def test_archive_lifecycle(api, tmp_path):
    client, _, _ = api
    job_id = _submit(client, tmp_path)
    # queued job: not archivable
    response = client.post(f"/jobs/{job_id}/archive")
    assert response.status_code == 409
    assert "only complete or cancelled jobs can" in response.json()["detail"]
    assert f"job {job_id} is pending" in response.json()["detail"]
    # cancel it -> archivable
    assert client.post(f"/jobs/{job_id}/cancel").status_code == 200
    assert client.post(f"/jobs/{job_id}/archive").json() == {"archived": job_id}
    # hidden from the default list, present with include_archived
    assert [j["id"] for j in client.get("/jobs").json()] == []
    listed = client.get("/jobs", params={"include_archived": "true"}).json()
    assert [j["id"] for j in listed] == [job_id]
    assert listed[0]["archived_at"] is not None
    # unarchive restores it
    assert client.post(f"/jobs/{job_id}/unarchive").json() == {"unarchived": job_id}
    assert [j["id"] for j in client.get("/jobs").json()] == [job_id]
    # 404s
    assert client.post("/jobs/999/archive").status_code == 404
    assert client.post("/jobs/999/unarchive").status_code == 404
