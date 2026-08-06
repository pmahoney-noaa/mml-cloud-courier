"""Per-job run lock. No bucket/emulator needed anywhere in this file:
`job_run_lock` failing to acquire raises before any GCS or even any DB
access happens, so these tests exercise the lock (and its wiring into
`run_job`) entirely offline."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from mml_cloud_transfer.core.models import JobStatus
from mml_cloud_transfer.engine.joblock import JobAlreadyRunning, job_run_lock
from mml_cloud_transfer.engine.runner import run_job


def test_second_acquisition_in_same_process_raises(tmp_path):
    """msvcrt.locking is a mandatory, OS-enforced byte-range lock — it
    contends even against a second file handle opened by the *same*
    process, not just a second process. Verified directly against this
    interpreter before relying on it here (see report); this test pins
    that behavior against regression."""
    db = tmp_path / "jobs.db"
    with job_run_lock(db, 7):
        with pytest.raises(JobAlreadyRunning, match=r"job 7 is already running"):
            with job_run_lock(db, 7):
                pass


def test_different_job_ids_do_not_contend(tmp_path):
    db = tmp_path / "jobs.db"
    with job_run_lock(db, 7):
        with job_run_lock(db, 8):
            pass  # no contention: different job id, same database


def test_lock_is_released_on_exit(tmp_path):
    db = tmp_path / "jobs.db"
    with job_run_lock(db, 7):
        pass
    with job_run_lock(db, 7):  # second acquisition succeeds now
        pass


def test_error_message_names_job_and_lock_path(tmp_path):
    db = tmp_path / "jobs.db"
    lock_path = db.parent / f"{db.name}.job-7.lock"
    with job_run_lock(db, 7):
        with pytest.raises(JobAlreadyRunning) as exc_info:
            with job_run_lock(db, 7):
                pass
    message = str(exc_info.value)
    assert "job 7" in message
    assert str(lock_path) in message
    assert "retry" in message.lower()


_HOLD_SCRIPT = """
import sys, time
from mml_cloud_transfer.engine.joblock import job_run_lock
with job_run_lock(sys.argv[1], int(sys.argv[2])):
    print("locked", flush=True)
    time.sleep(60)
"""


def test_subprocess_holding_lock_blocks_this_process_and_releases_on_kill(tmp_path):
    """The case that actually matters: a *different* process (a hung
    transfer the operator wants to retry) holds the lock, and killing it
    — the normal way this product's transfers die — makes the lock
    acquirable again with no cleanup step."""
    db = tmp_path / "jobs.db"
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLD_SCRIPT, str(db), "7"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 15
        line = ""
        while time.monotonic() < deadline:
            line = proc.stdout.readline().strip()
            if line:
                break
        assert line == "locked", f"subprocess did not report holding the lock: {line!r}"

        with pytest.raises(JobAlreadyRunning):
            with job_run_lock(db, 7):
                pass
    finally:
        proc.kill()
        proc.wait(timeout=15)

    # The OS released the lock the instant the process died — no stale
    # lock file, no cleanup step, no retry-until-timeout in production.
    # A short retry loop here just absorbs the tail of process teardown.
    deadline = time.monotonic() + 10
    while True:
        try:
            with job_run_lock(db, 7):
                break
        except JobAlreadyRunning:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)


def test_run_job_raises_job_already_running_when_lock_is_held(tmp_path):
    """run_job must take the lock before touching job state (start_job /
    reset_stale_transfers) — a held lock must fail run_job outright, and
    fail before any DB mutation. ctx=None here is fine: the lock check
    happens before ctx is ever used."""
    db = tmp_path / "jobs.db"
    with job_run_lock(db, 7):
        with pytest.raises(JobAlreadyRunning):
            run_job(db, 7, ctx=None)


def test_run_job_succeeds_normally_once_no_one_holds_the_lock(tmp_path):
    """Sanity check that the lock wiring doesn't break the ordinary path:
    an empty (no planned files) job still reaches a verdict. audit=False
    because the (empty-manifest) audit step still calls list_prefix(ctx,
    ...) unconditionally, which would need a real ctx."""
    from mml_cloud_transfer.core.models import Direction
    from mml_cloud_transfer.engine.runner import EngineOptions
    from mml_cloud_transfer.store.db import connect
    from mml_cloud_transfer.store.repository import JobRepository

    db = tmp_path / "jobs.db"
    conn = connect(db)
    try:
        job_id = JobRepository(conn).create_job(
            name="empty", direction=Direction.UPLOAD,
            source_root=str(tmp_path), dest_prefix="p",
        )
    finally:
        conn.close()

    status = run_job(db, job_id, ctx=None, options=EngineOptions(audit=False))
    assert status is JobStatus.COMPLETE  # zero planned files verdicts COMPLETE
