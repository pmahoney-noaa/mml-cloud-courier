"""Byte progress is derived from file_slices (per-slice, correct under
concurrency); job_files.bytes_transferred is no longer written by
heartbeat because concurrent slice callbacks made it flap."""

from mml_cloud_transfer.core.models import Direction, PlannedFile, SliceState
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


def _job(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    repo = JobRepository(conn)
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root="s", dest_prefix="p"
    )
    repo.add_planned_files(job_id, [
        PlannedFile("a.bin", "s/a.bin", 100, 1),
        PlannedFile("b.bin", "s/b.bin", 1000, 1),
        PlannedFile("c.bin", "s/c.bin", 500, 1),
    ])
    return conn, repo, job_id


def test_heartbeat_updates_timestamp_only(tmp_path):
    conn, repo, job_id = _job(tmp_path)
    file_id = repo.get_files(job_id)[0]["id"]
    repo.heartbeat(file_id)
    row = repo.get_file(file_id)
    conn.close()
    assert row["heartbeat_at"] is not None
    assert row["bytes_transferred"] == 0


def test_job_progress_sums_done_sizes_and_inflight_slices(tmp_path):
    conn, repo, job_id = _job(tmp_path)
    a, b, c = (r["id"] for r in repo.get_files(job_id))
    repo.mark_verified(a, local_crc32c=1, remote_crc32c=1, generation=1)
    repo.mark_transferring(b)
    repo.upsert_slice(b, 0, offset=0, length=600,
                      state=SliceState.UPLOADED, bytes_transferred=600)
    repo.upsert_slice(b, 1, offset=600, length=400,
                      state=SliceState.UPLOADING, bytes_transferred=150)
    progress = repo.job_progress(job_id)
    conn.close()
    assert progress.files_total == 3
    assert progress.files_done == 1
    assert progress.files_failed == 0
    assert progress.bytes_total == 1600
    assert progress.bytes_done == 100 + 600 + 150
    assert progress.state_counts["pending"] == 1


def test_job_progress_ignores_slices_of_files_not_transferring(tmp_path):
    conn, repo, job_id = _job(tmp_path)
    b = repo.get_files(job_id)[1]["id"]
    repo.upsert_slice(b, 0, offset=0, length=600,
                      state=SliceState.UPLOADED, bytes_transferred=600)
    progress = repo.job_progress(job_id)  # b is pending, not transferring
    conn.close()
    assert progress.bytes_done == 0
