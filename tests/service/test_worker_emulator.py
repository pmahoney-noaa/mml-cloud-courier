"""run_once end-to-end: a real tiny upload through the real engine."""

import os

import pytest

from mml_cloud_courier.core.models import Direction, JobStatus
from mml_cloud_courier.service.config import load_config
from mml_cloud_courier.service.controller import JobController
from mml_cloud_courier.service.worker import QueueWorker
from mml_cloud_courier.store.db import connect
from mml_cloud_courier.store.repository import JobRepository


@pytest.mark.emulator
def test_run_once_completes_a_real_upload(emulator, emulator_client, tmp_path):
    _, bucket = emulator_client
    config = load_config(tmp_path / "data")
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.bin").write_bytes(os.urandom(4096))
    (src / "b.bin").write_bytes(os.urandom(2048))

    conn = connect(config.db_path)
    repo = JobRepository(conn)
    profile_id = repo.get_or_create_profile(
        bucket=bucket, auth_type="emulator", credential_ref=emulator.endpoint
    )
    job_id = repo.create_job(
        name="e2e", direction=Direction.UPLOAD, source_root=str(src),
        dest_prefix="night", profile_id=profile_id,
    )
    conn.close()

    worker = QueueWorker(config, JobController())
    assert worker.run_once() is True

    conn = connect(config.db_path)
    repo = JobRepository(conn)
    job = repo.get_job(job_id)
    states = {r["state"] for r in repo.get_files(job_id)}
    conn.close()
    assert job["status"] == JobStatus.COMPLETE.value
    assert states == {"verified"}
    assert (config.reports_dir / f"job-{job_id}" / "report.html").exists()
