"""The defining test of the whole design: kill a transfer mid-flight,
resume it, and end COMPLETE with everything verified. Runs the CLI as a
real subprocess so the kill is a real process death, not an exception."""

import os
import subprocess
import sys
import time
import uuid

import pytest

from mml_cloud_transfer.core.models import FileState, JobStatus
from mml_cloud_transfer.core.hashing import hash_file
from mml_cloud_transfer.gcs.client import make_context
from mml_cloud_transfer.gcs.objects import get_meta
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

POLICY = "65536,262144,262144"


def _cli(*args):
    return [sys.executable, "-m", "mml_cloud_transfer.cli", *args]


@pytest.fixture
def big_tree(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for n in range(40):
        (src / f"small-{n:02d}.bin").write_bytes(os.urandom(8_192))
    (src / "medium.bin").write_bytes(os.urandom(200 * 1024))
    (src / "big.bin").write_bytes(os.urandom(600 * 1024))
    return src


@pytest.mark.emulator
def test_kill_and_resume_reaches_complete(emulator, emulator_client, big_tree, tmp_path):
    _, bucket = emulator_client
    db = tmp_path / "jobs.db"

    proc = subprocess.Popen(
        _cli(
            "transfer", "--db", str(db), "--bucket", bucket, "--name", "overnight",
            "--source", str(big_tree), "--prefix", "night",
            "--size-policy", POLICY, "--workers", "1",
            "--emulator-endpoint", emulator.endpoint,
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Wait until real progress exists, then kill without ceremony.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if db.exists():
                conn = connect(db)
                verified = conn.execute(
                    "SELECT COUNT(*) AS n FROM job_files WHERE state = ?",
                    (FileState.VERIFIED.value,),
                ).fetchone()["n"]
                conn.close()
                if verified >= 3:
                    break
            time.sleep(0.2)
        else:
            pytest.fail("transfer made no visible progress within 60s")
    finally:
        proc.kill()
        proc.wait(timeout=15)

    conn = connect(db)
    repo = JobRepository(conn)
    job = conn.execute("SELECT * FROM jobs ORDER BY id").fetchone()
    counts = repo.count_by_state(job["id"])
    conn.close()
    assert job["status"] != JobStatus.COMPLETE.value
    assert counts.get(FileState.VERIFIED, 0) < 42, "kill happened too late to prove anything"

    result = subprocess.run(
        _cli(
            "resume", "--db", str(db), "--job-id", str(job["id"]),
            "--bucket", bucket, "--size-policy", POLICY,
            "--emulator-endpoint", emulator.endpoint,
        ),
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    conn = connect(db)
    repo = JobRepository(conn)
    assert repo.get_job(job["id"])["status"] == JobStatus.COMPLETE.value
    rows = repo.get_files(job["id"])
    conn.close()
    assert len(rows) == 42
    assert all(
        r["state"] in (FileState.VERIFIED.value, FileState.SKIPPED.value) for r in rows
    )

    # Spot-check the sliced file end-to-end: remote CRC equals a fresh local hash.
    ctx = make_context(bucket, emulator_endpoint=emulator.endpoint)
    meta = get_meta(ctx, "night/big.bin")
    assert meta is not None
    assert meta.crc32c == hash_file(big_tree / "big.bin").crc32c


@pytest.mark.real_bucket
def test_real_bucket_round_trip(tmp_path):
    bucket = os.environ.get("MMLCT_TEST_BUCKET")
    if not bucket:
        pytest.skip("set MMLCT_TEST_BUCKET (and ADC credentials) to run")

    run_prefix = f"mmlct-test/{uuid.uuid4().hex[:12]}"
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.bin").write_bytes(os.urandom(100 * 1024))
    (src / "b.bin").write_bytes(os.urandom(400 * 1024))
    db = tmp_path / "jobs.db"

    up = subprocess.run(
        _cli(
            "transfer", "--db", str(db), "--bucket", bucket, "--name", "real-up",
            "--source", str(src), "--prefix", run_prefix,
            "--size-policy", POLICY,
        ),
        capture_output=True, text=True, timeout=600,
    )
    assert up.returncode == 0, up.stdout + up.stderr

    dest = tmp_path / "restored"
    down = subprocess.run(
        _cli(
            "transfer", "--db", str(db), "--bucket", bucket, "--name", "real-down",
            "--direction", "download", "--source", str(dest), "--prefix", run_prefix,
            "--size-policy", POLICY,
        ),
        capture_output=True, text=True, timeout=600,
    )
    assert down.returncode == 0, down.stdout + down.stderr
    assert (dest / "a.bin").read_bytes() == (src / "a.bin").read_bytes()
    assert (dest / "b.bin").read_bytes() == (src / "b.bin").read_bytes()

    # Clean up the run's objects.
    from mml_cloud_transfer.gcs.objects import delete_object, list_prefix

    ctx = make_context(bucket)
    for meta in list_prefix(ctx, f"{run_prefix}/"):
        delete_object(ctx, meta.name)
