"""Orchestration tests — no network. Transfer functions are stubbed at the
runner's module level; a temp SQLite DB and real source files are used so
state transitions are exercised for real."""

import pytest

import mml_cloud_transfer.engine.runner as runner
from mml_cloud_transfer.core.errors import ErrorCategory
from mml_cloud_transfer.core.models import Direction, FileState, JobStatus, SliceState
from mml_cloud_transfer.core.retry import RetrySchedule
from mml_cloud_transfer.engine.runner import EngineOptions, run_job
from mml_cloud_transfer.gcs.objects import ObjectMeta
from mml_cloud_transfer.gcs.uploader import UploadResult
from mml_cloud_transfer.cli.scan_command import run_scan
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


class FakeApiError(Exception):
    def __init__(self, code):
        super().__init__(f"api {code}")
        self.code = code


def verified(size):
    return UploadResult(
        state="verified", local_crc32c=1, remote_crc32c=1,
        generation=7, sha256=None, bytes_sent=size,
    )


@pytest.fixture
def job(tmp_path):
    """A scanned upload job over two small real files."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.bin").write_bytes(b"a" * 100)
    (src / "b.bin").write_bytes(b"b" * 200)
    db = tmp_path / "jobs.db"
    outcome = run_scan(
        db_path=db, source_root=str(src), dest_prefix="p", job_name="j",
        follow_extended=False,
    )
    return db, outcome.job_id


def opts(**kw):
    kw.setdefault("file_workers", 1)
    kw.setdefault("retry", RetrySchedule(max_attempts=3, base_delay=0.01))
    kw.setdefault("audit", False)
    return EngineOptions(**kw)


def files_by_state(db, job_id):
    conn = connect(db)
    rows = JobRepository(conn).get_files(job_id)
    conn.close()
    return {r["relative_path"]: r["state"] for r in rows}


def test_happy_path_reaches_complete(job, monkeypatch):
    db, job_id = job
    monkeypatch.setattr(runner, "upload_single_shot", lambda *a, **k: verified(100))
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)

    status = run_job(db, job_id, ctx=None, options=opts())
    assert status is JobStatus.COMPLETE
    assert set(files_by_state(db, job_id).values()) == {FileState.VERIFIED.value}


def test_transient_errors_retry_with_sleeps(job, monkeypatch):
    db, job_id = job
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise FakeApiError(503)
        return verified(100)

    slept = []
    monkeypatch.setattr(runner, "upload_single_shot", flaky)
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    run_job(db, job_id, ctx=None, options=opts(), sleep=slept.append)
    assert calls["n"] >= 3
    assert len(slept) == 2  # two retries for the first file, none for the second


def test_non_transient_errors_fail_without_retry(job, monkeypatch):
    db, job_id = job
    slept = []
    monkeypatch.setattr(
        runner, "upload_single_shot",
        lambda *a, **k: (_ for _ in ()).throw(FakeApiError(404)),
    )
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    status = run_job(db, job_id, ctx=None, options=opts(), sleep=slept.append)
    assert status is JobStatus.INCOMPLETE
    assert slept == []
    assert set(files_by_state(db, job_id).values()) == {FileState.FAILED.value}


def test_credential_errors_pause_the_job(job, monkeypatch):
    db, job_id = job
    monkeypatch.setattr(
        runner, "upload_single_shot",
        lambda *a, **k: (_ for _ in ()).throw(FakeApiError(403)),
    )
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    status = run_job(db, job_id, ctx=None, options=opts())
    assert status is JobStatus.PAUSED
    states = files_by_state(db, job_id)
    # The pause fires on the first credential failure; whether the second
    # file was attempted before the pool cancelled is a race, so assert
    # only that nothing progressed past FAILED/PENDING.
    assert FileState.FAILED.value in states.values()
    assert set(states.values()) <= {FileState.FAILED.value, FileState.PENDING.value}


def test_cumulative_attempts_reach_quarantine(job, monkeypatch):
    db, job_id = job
    conn = connect(db)
    repo = JobRepository(conn)
    first = repo.get_files(job_id)[0]["id"]
    for _ in range(14):
        repo.mark_failed(first, ErrorCategory.NETWORK, "past runs")
    conn.close()

    monkeypatch.setattr(
        runner, "upload_single_shot",
        lambda *a, **k: (_ for _ in ()).throw(FakeApiError(404)),
    )
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    run_job(db, job_id, ctx=None, options=opts())
    states = files_by_state(db, job_id)
    assert FileState.QUARANTINED.value in states.values()


def test_credential_error_pauses_even_a_nearly_quarantined_file(job, monkeypatch):
    db, job_id = job
    conn = connect(db)
    repo = JobRepository(conn)
    first = repo.get_files(job_id)[0]["id"]
    for _ in range(14):
        repo.mark_failed(first, ErrorCategory.NETWORK, "past runs")
    conn.close()

    monkeypatch.setattr(
        runner, "upload_single_shot",
        lambda *a, **k: (_ for _ in ()).throw(FakeApiError(403)),
    )
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    status = run_job(db, job_id, ctx=None, options=opts())
    assert status is JobStatus.PAUSED
    conn = connect(db)
    row = JobRepository(conn).get_file(first)
    conn.close()
    assert row["state"] == FileState.FAILED.value  # failed, NOT quarantined


def test_changed_source_is_requeued_once_then_transferred(job, monkeypatch, tmp_path):
    db, job_id = job
    # Grow a.bin after the scan: first pass must mark it changed with fresh
    # metadata, the second pass must transfer it.
    (tmp_path / "src" / "a.bin").write_bytes(b"a" * 150)

    sent = []
    monkeypatch.setattr(
        runner, "upload_single_shot",
        lambda ctx, path, name, **k: sent.append(name) or verified(100),
    )
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    status = run_job(db, job_id, ctx=None, options=opts())
    assert status is JobStatus.COMPLETE
    assert sorted(sent) == ["p/a.bin", "p/b.bin"]

    conn = connect(db)
    row = [r for r in JobRepository(conn).get_files(job_id) if r["relative_path"] == "a.bin"][0]
    conn.close()
    assert row["size_bytes"] == 150
    assert row["state"] == FileState.VERIFIED.value


def test_changed_source_clears_stale_slices_before_retransfer(job, monkeypatch, tmp_path):
    """CRITICAL 1 regression: slices recorded against the OLD content must be
    gone before the retry transfers the new content, or resume could reuse a
    stale temp object / CRC and Layer 2 would verify a chimera object.
    """
    db, job_id = job
    conn = connect(db)
    repo = JobRepository(conn)
    file_id = [
        r for r in repo.get_files(job_id) if r["relative_path"] == "a.bin"
    ][0]["id"]
    # Slices left behind from an earlier attempt against the file's old content.
    repo.upsert_slice(
        file_id, 0, offset=0, length=100, crc32c=999, state=SliceState.UPLOADED
    )
    conn.close()

    # In-place rewrite: same-ish size, different bytes.
    (tmp_path / "src" / "a.bin").write_bytes(b"a" * 150)

    seen_slices_at_transfer = {}

    def capture(ctx, path, name, **k):
        if name == "p/a.bin":
            conn2 = connect(db)
            seen_slices_at_transfer["a.bin"] = JobRepository(conn2).get_slices(file_id)
            conn2.close()
            return verified(150)
        return verified(100)

    monkeypatch.setattr(runner, "upload_single_shot", capture)
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    status = run_job(db, job_id, ctx=None, options=opts())

    assert status is JobStatus.COMPLETE
    assert seen_slices_at_transfer["a.bin"] == []

    conn = connect(db)
    row = JobRepository(conn).get_file(file_id)
    conn.close()
    assert row["state"] == FileState.VERIFIED.value
    assert row["size_bytes"] == 150


def test_checksum_mismatch_clears_stale_slices(job, monkeypatch):
    """IMPORTANT 5 regression: a checksum-mismatch failure must clear slice
    state, or the next attempt starts from a sticky, already-broken state
    until the file is quarantined many attempts later.
    """
    from mml_cloud_transfer.gcs.uploader import ChecksumMismatch

    db, job_id = job
    conn = connect(db)
    repo = JobRepository(conn)
    file_id = [
        r for r in repo.get_files(job_id) if r["relative_path"] == "a.bin"
    ][0]["id"]
    repo.upsert_slice(
        file_id, 0, offset=0, length=100, crc32c=1, state=SliceState.UPLOADED
    )
    conn.close()

    monkeypatch.setattr(
        runner, "upload_single_shot",
        lambda *a, **k: (_ for _ in ()).throw(ChecksumMismatch("mismatch")),
    )
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    run_job(db, job_id, ctx=None, options=opts())

    conn = connect(db)
    slices = JobRepository(conn).get_slices(file_id)
    row = JobRepository(conn).get_file(file_id)
    conn.close()
    assert slices == []
    assert row["state"] == FileState.FAILED.value
    assert row["error_category"] == ErrorCategory.CHECKSUM_MISMATCH.value


def test_error_messages_are_scrubbed_of_url_query_strings(job, monkeypatch):
    """MINOR 7 regression: resumable session URIs carry bearer tokens in
    their query string — they must never reach the database or the report.
    """
    db, job_id = job
    monkeypatch.setattr(
        runner, "upload_single_shot",
        lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("PUT https://host/upload?upload_id=SECRET123 failed")
        ),
    )
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    run_job(db, job_id, ctx=None, options=opts())

    conn = connect(db)
    rows = JobRepository(conn).get_files(job_id)
    conn.close()
    messages = [r["error_message"] for r in rows if r["error_message"]]
    assert messages, "expected at least one failed file with an error message"
    for message in messages:
        assert "SECRET123" not in message
        assert "?..." in message


def test_source_changing_twice_fails_as_source_changed(job, monkeypatch, tmp_path):
    db, job_id = job
    stats = {"calls": 0}
    real_stat = runner._stat_source

    def restless(path):
        if path.endswith("a.bin"):
            stats["calls"] += 1
            return (100 + stats["calls"], 1_700_000_000_000_000_000 + stats["calls"])
        return real_stat(path)

    monkeypatch.setattr(runner, "_stat_source", restless)
    monkeypatch.setattr(runner, "upload_single_shot", lambda *a, **k: verified(100))
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    status = run_job(db, job_id, ctx=None, options=opts())
    assert status is JobStatus.INCOMPLETE

    conn = connect(db)
    row = [r for r in JobRepository(conn).get_files(job_id) if r["relative_path"] == "a.bin"][0]
    conn.close()
    assert row["state"] == FileState.FAILED.value
    assert row["error_category"] == ErrorCategory.SOURCE_CHANGED.value


def test_missing_source_fails_as_not_found(job, monkeypatch, tmp_path):
    db, job_id = job
    (tmp_path / "src" / "a.bin").unlink()
    monkeypatch.setattr(runner, "upload_single_shot", lambda *a, **k: verified(200))
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    run_job(db, job_id, ctx=None, options=opts())
    states = files_by_state(db, job_id)
    assert states["a.bin"] == FileState.FAILED.value
    assert states["b.bin"] == FileState.VERIFIED.value


def test_audit_catches_an_object_missing_from_the_listing(job, monkeypatch):
    db, job_id = job
    monkeypatch.setattr(runner, "upload_single_shot", lambda *a, **k: verified(100))
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    # Listing knows only one of the two objects, with matching size/crc.
    monkeypatch.setattr(
        runner, "list_prefix",
        lambda ctx, prefix: iter(
            [ObjectMeta(name="p/a.bin", size=100, crc32c=1, generation=7)]
        ),
    )
    status = run_job(db, job_id, ctx=None, options=opts(audit=True))
    assert status is JobStatus.INCOMPLETE
    states = files_by_state(db, job_id)
    assert states["b.bin"] == FileState.FAILED.value
    assert states["a.bin"] == FileState.VERIFIED.value


def test_precondition_is_captured_before_the_first_attempt(job, monkeypatch):
    db, job_id = job
    seen = {}

    def capture(ctx, path, name, *, precondition_generation, **k):
        seen[name] = precondition_generation
        return verified(100)

    monkeypatch.setattr(runner, "upload_single_shot", capture)
    monkeypatch.setattr(
        runner, "get_meta",
        lambda ctx, name: (
            ObjectMeta(name=name, size=5, crc32c=9, generation=42)
            if name == "p/a.bin" else None
        ),
    )
    run_job(db, job_id, ctx=None, options=opts())
    assert seen == {"p/a.bin": 42, "p/b.bin": 0}


def test_paused_run_still_records_run_finished(job, monkeypatch):
    db, job_id = job
    monkeypatch.setattr(
        runner, "upload_single_shot",
        lambda *a, **k: (_ for _ in ()).throw(FakeApiError(403)),
    )
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    run_job(db, job_id, ctx=None, options=opts())
    conn = connect(db)
    pairs = [(e["kind"], e["detail"]) for e in JobRepository(conn).get_events(job_id)]
    conn.close()
    assert ("run_finished", JobStatus.PAUSED.value) in pairs
