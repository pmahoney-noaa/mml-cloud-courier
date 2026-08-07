"""Phase 3's defining test: kill the service mid-transfer, restart it, and
the job completes with every checksum matching — with NO resume call.
The console host runs as a real subprocess so the kill is real process
death; auto-resume (default on) must do the rest."""

import json
import os
import subprocess
import sys
import time

import pytest
import requests

from mml_cloud_courier.core.hashing import hash_file
from mml_cloud_courier.core.models import JobStatus
from mml_cloud_courier.gcs.client import make_context
from mml_cloud_courier.gcs.objects import get_meta
from mml_cloud_courier.service.security import read_token
from mml_cloud_courier.store.db import connect

from tests.service.conftest import free_port


def _start_service(data_dir, port):
    return subprocess.Popen(
        [
            sys.executable, "-m", "mml_cloud_courier.service",
            "--data-dir", str(data_dir), "--port", str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_healthy(base_url, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if requests.get(f"{base_url}/health", timeout=1).ok:
                return
        except requests.ConnectionError:
            pass
        time.sleep(0.2)
    raise AssertionError("service did not become healthy in time")


@pytest.mark.emulator
def test_job_survives_service_kill(emulator, emulator_client, tmp_path):
    _, bucket = emulator_client
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "settings.json").write_text(json.dumps({
        "file_workers": 1,
        "size_policy": "65536,262144,262144",   # tiny thresholds: all 3 paths
        "poll_interval": 0.2,
    }), encoding="utf-8")

    src = tmp_path / "src"
    src.mkdir()
    for n in range(40):
        (src / f"small-{n:02d}.bin").write_bytes(os.urandom(8_192))
    (src / "medium.bin").write_bytes(os.urandom(200 * 1024))
    (src / "big.bin").write_bytes(os.urandom(600 * 1024))

    port = free_port()
    base = f"http://127.0.0.1:{port}"

    proc = _start_service(data_dir, port)
    try:
        _wait_healthy(base)
        headers = {
            "Authorization": f"Bearer {read_token(data_dir / 'api_token')}"
        }
        job_id = requests.post(f"{base}/jobs", json={
            "name": "overnight", "direction": "upload",
            "source_root": str(src), "dest_prefix": "night",
            "bucket": bucket, "emulator_endpoint": emulator.endpoint,
        }, headers=headers, timeout=30).json()["job_id"]

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = requests.get(
                f"{base}/jobs/{job_id}", headers=headers, timeout=5
            ).json()
            if job["progress"]["files_done"] >= 3:
                break
            time.sleep(0.2)
        else:
            pytest.fail("transfer made no visible progress within 60s")
    finally:
        proc.kill()          # real, unceremonious process death
        proc.wait(timeout=15)

    db = data_dir / "jobs.db"
    conn = connect(db)
    row = conn.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    done_before = conn.execute(
        "SELECT COUNT(*) AS n FROM job_files WHERE job_id = ? AND state IN ('verified', 'skipped')",
        (job_id,),
    ).fetchone()["n"]
    conn.close()
    assert row["status"] != JobStatus.COMPLETE.value
    assert done_before < 42, "kill happened too late to prove anything"

    # Restart. NO resume call: startup recovery must re-enqueue and the
    # worker must finish the job on its own.
    proc = _start_service(data_dir, port)
    try:
        _wait_healthy(base)
        headers = {
            "Authorization": f"Bearer {read_token(data_dir / 'api_token')}"
        }
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            job = requests.get(
                f"{base}/jobs/{job_id}", headers=headers, timeout=5
            ).json()
            if job["status"] in (
                JobStatus.COMPLETE.value, JobStatus.INCOMPLETE.value,
                JobStatus.PAUSED.value,
            ):
                break
            time.sleep(0.5)
        else:
            pytest.fail("job did not reach a terminal status after restart")
        assert job["status"] == JobStatus.COMPLETE.value

        # The worker commits COMPLETE before it finishes writing the report,
        # so wait for it here — while the service is still alive — rather
        # than asserting once after it's already been killed.
        report_html = data_dir / "reports" / f"job-{job_id}" / "report.html"
        report_deadline = time.monotonic() + 30
        while time.monotonic() < report_deadline and not report_html.exists():
            time.sleep(0.2)
        # Written by the worker after the terminal status commits — never by
        # a client call.
        assert report_html.exists()
    finally:
        proc.kill()
        proc.wait(timeout=15)

    conn = connect(db)
    rows = conn.execute(
        "SELECT state FROM job_files WHERE job_id = ?", (job_id,)
    ).fetchall()
    conn.close()
    assert len(rows) == 42
    assert {r["state"] for r in rows} <= {"verified", "skipped"}

    # Spot-check the sliced file end-to-end: remote CRC == fresh local hash.
    ctx = make_context(bucket, emulator_endpoint=emulator.endpoint)
    meta = get_meta(ctx, "night/big.bin")
    assert meta is not None
    assert meta.crc32c == hash_file(src / "big.bin").crc32c
