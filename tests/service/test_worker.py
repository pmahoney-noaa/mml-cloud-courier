"""Worker unit tests: every engine collaborator is injected, no network.
The injected run_job_fn writes DB state exactly as the real one would."""

import threading

import pytest
import requests

from mml_cloud_transfer.core.errors import ErrorCategory
from mml_cloud_transfer.core.models import Direction, FileState, JobStatus, PlannedFile
from mml_cloud_transfer.engine.joblock import JobAlreadyRunning
from mml_cloud_transfer.service.config import load_config
from mml_cloud_transfer.service.controller import JobController
from mml_cloud_transfer.service.worker import QueueWorker
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


@pytest.fixture
def config(tmp_path):
    return load_config(tmp_path / "data")


def _submit(config, *, name="j", direction=Direction.UPLOAD,
            scheduled=None, planned=True):
    conn = connect(config.db_path)
    try:
        repo = JobRepository(conn)
        profile_id = repo.get_or_create_profile(
            bucket="b", auth_type="adc", credential_ref=None
        )
        job_id = repo.create_job(
            name=name, direction=direction, source_root="s", dest_prefix="p",
            profile_id=profile_id, scheduled_start_at=scheduled,
        )
        if planned:
            repo.add_planned_files(job_id, [PlannedFile("a.bin", "s/a.bin", 3, 1)])
    finally:
        conn.close()
    return job_id


def _status(config, job_id):
    conn = connect(config.db_path)
    try:
        return JobRepository(conn).get_job(job_id)["status"]
    finally:
        conn.close()


def _worker(config, controller=None, **kw):
    controller = controller or JobController()
    kw.setdefault("make_context_fn", lambda bucket, **k: object())
    kw.setdefault("write_report_fn", lambda *a, **k: None)
    # A never-started job is always rescanned for safety (see worker.py); the
    # unit tests below don't exercise scanning, so keep it a no-op unless a
    # test overrides it with its own spy.
    kw.setdefault("run_scan_fn", lambda **kwargs: None)
    kw.setdefault("scan_remote_fn", lambda *args, **kwargs: None)
    return QueueWorker(config, controller, **kw), controller


def test_fifo_pickup_and_schedule_eligibility(config):
    first = _submit(config, name="first")
    second = _submit(config, name="later", scheduled="2100-01-01T00:00:00+00:00")
    ran = []

    def fake_run_job(db_path, job_id, ctx, *, options):
        ran.append(job_id)
        conn = connect(db_path)
        try:
            JobRepository(conn).finish_job(job_id, JobStatus.COMPLETE)
        finally:
            conn.close()
        return JobStatus.COMPLETE

    worker, _ = _worker(config, run_job_fn=fake_run_job)
    assert worker.run_once() is True
    assert worker.run_once() is False        # the year-2100 job is not eligible
    assert ran == [first]
    assert _status(config, second) == JobStatus.PENDING.value


def test_never_started_upload_is_scanned_first(config):
    _submit(config, planned=False)
    order = []

    def fake_scan(**kwargs):
        order.append(("scan", kwargs["job_id"]))

    def fake_run_job(db_path, job_id, ctx, *, options):
        order.append(("run", job_id))
        conn = connect(db_path)
        try:
            JobRepository(conn).finish_job(job_id, JobStatus.COMPLETE)
        finally:
            conn.close()
        return JobStatus.COMPLETE

    worker, _ = _worker(config, run_scan_fn=fake_scan, run_job_fn=fake_run_job)
    worker.run_once()
    assert [kind for kind, _ in order] == ["scan", "run"]


def test_planned_but_never_started_upload_is_rescanned_for_safety(config):
    """A crash mid-scan can leave a PARTIAL manifest with planned_files > 0.
    Rescanning must not be skipped just because a manifest already exists —
    only `started_at` tells us the job has genuinely begun transferring."""
    _submit(config, planned=True)
    order = []

    def fake_scan(**kwargs):
        order.append(("scan", kwargs["job_id"]))

    def fake_run_job(db_path, job_id, ctx, *, options):
        order.append(("run", job_id))
        conn = connect(db_path)
        try:
            JobRepository(conn).finish_job(job_id, JobStatus.COMPLETE)
        finally:
            conn.close()
        return JobStatus.COMPLETE

    worker, _ = _worker(config, run_scan_fn=fake_scan, run_job_fn=fake_run_job)
    worker.run_once()
    assert [kind for kind, _ in order] == ["scan", "run"]


def test_cancel_intent_lands_after_the_stopped_run(config):
    job_id = _submit(config)

    def fake_run_job(db_path, job_id, ctx, *, options):
        worker_controller.request(job_id, "cancel")   # user clicks mid-run
        assert options.should_stop()                  # wired to the stop event
        conn = connect(db_path)
        try:
            repo = JobRepository(conn)
            repo.set_job_status(job_id, JobStatus.PENDING)  # engine stop path
        finally:
            conn.close()
        return JobStatus.PENDING

    worker, worker_controller = _worker(config, run_job_fn=fake_run_job)
    worker.run_once()
    assert _status(config, job_id) == JobStatus.CANCELLED.value


def test_pause_intent_lands_after_the_stopped_run(config):
    job_id = _submit(config)

    def fake_run_job(db_path, job_id, ctx, *, options):
        worker_controller.request(job_id, "pause")
        conn = connect(db_path)
        try:
            JobRepository(conn).set_job_status(job_id, JobStatus.PENDING)
        finally:
            conn.close()
        return JobStatus.PENDING

    worker, worker_controller = _worker(config, run_job_fn=fake_run_job)
    worker.run_once()
    assert _status(config, job_id) == JobStatus.PAUSED.value


def test_pending_job_flipped_between_pickup_and_start_is_never_run(config):
    """IMPORTANT 2 regression: a pause/cancel can land in the window between
    _pick (job read as PENDING) and job_started (controller marks it
    active) — the app flips the row directly in that case. `_handle` must
    notice the flip and refuse to run/scan a job that is no longer PENDING,
    or the direct flip would be silently overwritten."""
    job_id = _submit(config)
    conn = connect(config.db_path)
    try:
        repo = JobRepository(conn)
        job = dict(repo.get_job(job_id))
        profile = dict(repo.get_profile(job["profile_id"]))
        repo.set_job_status(job_id, JobStatus.PAUSED)  # the race: flipped here
    finally:
        conn.close()

    run_job_calls = []

    def fake_run_job(db_path, job_id, ctx, *, options):
        run_job_calls.append(job_id)
        return JobStatus.COMPLETE

    worker, _ = _worker(config, run_job_fn=fake_run_job)
    worker._handle(job, profile, threading.Event())

    assert run_job_calls == []
    assert _status(config, job_id) == JobStatus.PAUSED.value


def test_intent_never_downgrades_a_finished_job(config):
    job_id = _submit(config)

    def fake_run_job(db_path, job_id, ctx, *, options):
        worker_controller.request(job_id, "cancel")   # arrives too late
        conn = connect(db_path)
        try:
            JobRepository(conn).finish_job(job_id, JobStatus.COMPLETE)
        finally:
            conn.close()
        return JobStatus.COMPLETE

    worker, worker_controller = _worker(config, run_job_fn=fake_run_job)
    worker.run_once()
    assert _status(config, job_id) == JobStatus.COMPLETE.value


def test_report_written_for_finished_runs(config):
    _submit(config)
    reported = []

    def fake_run_job(db_path, job_id, ctx, *, options):
        conn = connect(db_path)
        try:
            JobRepository(conn).finish_job(job_id, JobStatus.INCOMPLETE)
        finally:
            conn.close()
        return JobStatus.INCOMPLETE

    worker, _ = _worker(
        config, run_job_fn=fake_run_job,
        write_report_fn=lambda db, job_id, out, bucket=None: reported.append(job_id),
    )
    worker.run_once()
    assert len(reported) == 1


def test_worker_crash_pauses_the_job_not_the_service(config):
    job_id = _submit(config)

    def exploding_context(bucket, **kw):
        raise RuntimeError("boom")

    worker, _ = _worker(config, make_context_fn=exploding_context)
    assert worker.run_once() is True        # handled: did work, didn't raise
    assert _status(config, job_id) == JobStatus.PAUSED.value
    conn = connect(config.db_path)
    kinds = [e["kind"] for e in JobRepository(conn).events_after(job_id, 0)]
    conn.close()
    assert "worker_error" in kinds


def test_lock_contention_leaves_job_pending_for_retry(config):
    """I3: a held run lock (I1's transient scanner/backup window, or the
    operator's own `mmlct resume` finishing in 20 minutes) must not pause
    the job the way a genuine crash does — next_eligible_job never
    re-picks a PAUSED job without a human, turning a transient conflict
    into a permanent stop. It must stay PENDING and be retried on the
    worker's next poll."""
    job_id = _submit(config)
    calls = []

    def fake_run_job(db_path, job_id, ctx, *, options):
        calls.append(job_id)
        if len(calls) == 1:
            raise JobAlreadyRunning(
                f"could not acquire the run lock for job {job_id}"
            )
        conn = connect(db_path)
        try:
            JobRepository(conn).finish_job(job_id, JobStatus.COMPLETE)
        finally:
            conn.close()
        return JobStatus.COMPLETE

    worker, _ = _worker(config, run_job_fn=fake_run_job)

    assert worker.run_once() is True             # handled: did work, didn't raise
    assert _status(config, job_id) == JobStatus.PENDING.value      # NOT paused
    conn = connect(config.db_path)
    kinds = [e["kind"] for e in JobRepository(conn).events_after(job_id, 0)]
    conn.close()
    assert "job_lock_contended" in kinds
    assert "worker_error" not in kinds

    # Retried on the very next poll, exactly like any other PENDING job.
    assert worker.run_once() is True
    assert calls == [job_id, job_id]
    assert _status(config, job_id) == JobStatus.COMPLETE.value


def test_report_failure_does_not_mask_a_real_outcome(config):
    """A run that genuinely finished INCOMPLETE must keep that verdict even
    if writing the report artifact afterward blows up (disk full, etc.) —
    the outcome already committed and must not be downgraded to PAUSED."""
    job_id = _submit(config)

    def fake_run_job(db_path, job_id, ctx, *, options):
        conn = connect(db_path)
        try:
            JobRepository(conn).finish_job(job_id, JobStatus.INCOMPLETE)
        finally:
            conn.close()
        return JobStatus.INCOMPLETE

    def exploding_report(*a, **k):
        raise RuntimeError("disk full")

    worker, _ = _worker(
        config, run_job_fn=fake_run_job, write_report_fn=exploding_report
    )
    assert worker.run_once() is True
    assert _status(config, job_id) == JobStatus.INCOMPLETE.value
    conn = connect(config.db_path)
    kinds = [e["kind"] for e in JobRepository(conn).events_after(job_id, 0)]
    conn.close()
    assert "report_error" in kinds


def test_run_forever_survives_a_crashing_iteration(config):
    """A transient queue-level failure (e.g. sqlite3.OperationalError from
    _pick/_apply_intent) must not end the worker thread for good — the loop
    logs it and keeps going."""
    worker, controller = _worker(config, sleep=lambda seconds: None)
    calls = []

    def flaky_run_once():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        controller.service_stop.set()
        return False

    worker.run_once = flaky_run_once
    worker.run_forever()          # would hang or raise if the guard were missing
    assert len(calls) == 2


def _fail_network(config, job_id):
    conn = connect(config.db_path)
    try:
        repo = JobRepository(conn)
        file_id = repo.get_files(job_id)[0]["id"]
        repo.mark_failed(file_id, ErrorCategory.NETWORK, "conn reset")
    finally:
        conn.close()


def test_startup_recovery_resumes_interrupted_jobs(config):
    job_id = _submit(config)
    conn = connect(config.db_path)
    repo = JobRepository(conn)
    repo.set_job_status(job_id, JobStatus.RUNNING)
    file_id = repo.get_files(job_id)[0]["id"]
    repo.mark_transferring(file_id)          # fresh heartbeat — a crash relic
    conn.close()

    worker, _ = _worker(config)
    worker.startup_recovery()

    conn = connect(config.db_path)
    repo = JobRepository(conn)
    job = repo.get_job(job_id)
    state = repo.get_files(job_id)[0]["state"]
    kinds = [e["kind"] for e in repo.events_after(job_id, 0)]
    conn.close()
    assert job["status"] == JobStatus.PENDING.value
    assert state == FileState.PENDING.value  # 0s staleness beat the heartbeat
    assert "recovered_at_startup" in kinds


def test_startup_recovery_resumes_interrupted_scanning_jobs(config):
    """A service killed mid-scan leaves the job SCANNING. The queue only
    ever picks up PENDING jobs, so without recovering SCANNING too, such a
    job would sit unpicked forever."""
    job_id = _submit(config)
    conn = connect(config.db_path)
    JobRepository(conn).set_job_status(job_id, JobStatus.SCANNING)
    conn.close()

    worker, _ = _worker(config)
    worker.startup_recovery()

    conn = connect(config.db_path)
    repo = JobRepository(conn)
    job = repo.get_job(job_id)
    kinds = [e["kind"] for e in repo.events_after(job_id, 0)]
    conn.close()
    assert job["status"] == JobStatus.PENDING.value
    assert "recovered_at_startup" in kinds


def test_startup_recovery_honours_auto_resume_off_for_scanning_jobs(config):
    (config.data_dir).mkdir(parents=True, exist_ok=True)
    import json as jsonlib
    config.settings_path.write_text(
        jsonlib.dumps({"auto_resume_on_startup": False}), encoding="utf-8"
    )
    from mml_cloud_transfer.service.config import load_config as reload
    config = reload(config.data_dir)
    job_id = _submit(config)
    conn = connect(config.db_path)
    JobRepository(conn).set_job_status(job_id, JobStatus.SCANNING)
    conn.close()

    worker, _ = _worker(config)
    worker.startup_recovery()
    assert _status(config, job_id) == JobStatus.PAUSED.value


def test_startup_recovery_honours_auto_resume_off(config, tmp_path):
    import json as jsonlib
    (config.data_dir).mkdir(parents=True, exist_ok=True)
    config.settings_path.write_text(
        jsonlib.dumps({"auto_resume_on_startup": False}), encoding="utf-8"
    )
    from mml_cloud_transfer.service.config import load_config as reload
    config = reload(config.data_dir)
    job_id = _submit(config)
    conn = connect(config.db_path)
    JobRepository(conn).set_job_status(job_id, JobStatus.RUNNING)
    conn.close()

    worker, _ = _worker(config)
    worker.startup_recovery()
    assert _status(config, job_id) == JobStatus.PAUSED.value


def test_incomplete_with_unreachable_network_stalls_then_requeues(config):
    job_id = _submit(config)
    probes = {"n": 0}
    slept = []

    def fake_run_job(db_path, job_id, ctx, *, options):
        _fail_network(config, job_id)
        conn = connect(db_path)
        try:
            JobRepository(conn).finish_job(job_id, JobStatus.INCOMPLETE)
        finally:
            conn.close()
        return JobStatus.INCOMPLETE

    def fake_get_meta(ctx, name):
        probes["n"] += 1
        if probes["n"] <= 2:
            # A REAL transport exception type — never builtin ConnectionResetError.
            raise requests.exceptions.ConnectionError("network is down")
        return None                       # 404 == server answered == reachable

    worker, _ = _worker(
        config, run_job_fn=fake_run_job, get_meta_fn=fake_get_meta,
        sleep=slept.append,
    )
    worker.run_once()

    conn = connect(config.db_path)
    repo = JobRepository(conn)
    job = repo.get_job(job_id)
    kinds = [e["kind"] for e in repo.events_after(job_id, 0)]
    conn.close()
    assert job["status"] == JobStatus.PENDING.value   # re-queued, ready to re-run
    assert "job_stalled" in kinds
    assert "job_unstalled" in kinds
    # _stall_wait sleeps in <=1s steps so a stop request can interrupt it
    # quickly; here nothing interrupts, so each of the two full waits (one
    # before each probe: the first still-down probe, then the successful
    # one) accumulates to exactly stall_probe_interval.
    assert slept and all(s <= 1.0 for s in slept)
    assert sum(slept) == 2 * config.stall_probe_interval


def test_incomplete_with_reachable_network_does_not_stall(config):
    job_id = _submit(config)
    reported = []

    def fake_run_job(db_path, job_id, ctx, *, options):
        _fail_network(config, job_id)
        conn = connect(db_path)
        try:
            JobRepository(conn).finish_job(job_id, JobStatus.INCOMPLETE)
        finally:
            conn.close()
        return JobStatus.INCOMPLETE

    worker, _ = _worker(
        config, run_job_fn=fake_run_job,
        get_meta_fn=lambda ctx, name: None,           # probe succeeds
        write_report_fn=lambda db, job_id, out, bucket=None: reported.append(job_id),
    )
    worker.run_once()
    assert _status(config, job_id) == JobStatus.INCOMPLETE.value
    assert reported == [job_id]


def test_cancel_during_stall_wins(config):
    job_id = _submit(config)

    def fake_run_job(db_path, job_id, ctx, *, options):
        _fail_network(config, job_id)
        conn = connect(db_path)
        try:
            JobRepository(conn).finish_job(job_id, JobStatus.INCOMPLETE)
        finally:
            conn.close()
        return JobStatus.INCOMPLETE

    def sleeping_cancel(seconds):
        worker_controller.request(job_id, "cancel")   # user cancels mid-stall

    worker, worker_controller = _worker(
        config, run_job_fn=fake_run_job,
        get_meta_fn=lambda ctx, name: (_ for _ in ()).throw(
            requests.exceptions.ConnectionError("still down")
        ),
        sleep=sleeping_cancel,
    )
    worker.run_once()
    assert _status(config, job_id) == JobStatus.CANCELLED.value


def test_stall_wait_interrupts_on_service_stop(config):
    slept = []
    worker, controller = _worker(config, sleep=slept.append)
    controller.service_stop.set()
    worker._stall_wait(threading.Event())
    assert slept == []
