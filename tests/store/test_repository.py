import pytest

from mml_cloud_transfer.core.errors import ErrorCategory
from mml_cloud_transfer.core.models import (
    Direction,
    FileState,
    JobStatus,
    PlannedFile,
    TransferMethod,
)
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

GIB = 1024**3


def make_files(n: int, size: int = 100) -> list[PlannedFile]:
    return [
        PlannedFile(
            relative_path=f"run47/file{i}.tif",
            source_path=rf"C:\data\run47\file{i}.tif",
            size_bytes=size,
            mtime_ns=1_700_000_000_000_000_000 + i,
        )
        for i in range(n)
    ]


@pytest.fixture
def repo(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    yield JobRepository(conn)
    conn.close()


def test_create_job_returns_an_id_and_pending_status(repo):
    job_id = repo.create_job(
        name="Run 47",
        direction=Direction.UPLOAD,
        source_root=r"\\nas01\imaging\run47",
        dest_prefix="archive/run47",
    )
    job = repo.get_job(job_id)
    assert job["status"] == JobStatus.PENDING.value
    assert job["dest_prefix"] == "archive/run47"


def test_add_planned_files_sets_object_name_and_method(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix="archive"
    )
    repo.add_planned_files(job_id, make_files(1))
    row = repo.get_files(job_id)[0]
    assert row["object_name"] == "archive/run47/file0.tif"
    assert row["method"] == TransferMethod.SINGLE_SHOT.value
    assert row["state"] == FileState.PENDING.value


def test_large_files_are_planned_as_sliced(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1, size=40 * GIB))
    assert repo.get_files(job_id)[0]["method"] == TransferMethod.SLICED.value


def test_add_planned_files_updates_job_totals(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(5, size=200))
    job = repo.get_job(job_id)
    assert job["planned_files"] == 5
    assert job["planned_bytes"] == 1000


def test_add_planned_files_is_idempotent_for_the_same_path(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(3))
    repo.add_planned_files(job_id, make_files(3))
    assert len(repo.get_files(job_id)) == 3
    assert repo.get_job(job_id)["planned_files"] == 3


def test_iter_pending_files_skips_finished_work(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(4))
    files = repo.get_files(job_id)
    repo.mark_verified(files[0]["id"], local_crc32c=1, remote_crc32c=1, generation=7)
    repo.mark_skipped(files[1]["id"])

    remaining = [f["relative_path"] for f in repo.iter_pending_files(job_id)]
    assert remaining == ["run47/file2.tif", "run47/file3.tif"]


def test_mark_verified_records_checksums_and_generation(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1))
    file_id = repo.get_files(job_id)[0]["id"]
    repo.mark_verified(file_id, local_crc32c=42, remote_crc32c=42, generation=99, sha256="ab")

    row = repo.get_files(job_id)[0]
    assert row["state"] == FileState.VERIFIED.value
    assert row["local_crc32c"] == 42
    assert row["remote_crc32c"] == 42
    assert row["generation"] == 99
    assert row["sha256"] == "ab"
    assert row["finished_at"] is not None


def test_mark_failed_increments_attempts_and_stores_the_category(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1))
    file_id = repo.get_files(job_id)[0]["id"]

    repo.mark_failed(file_id, ErrorCategory.FILE_LOCKED, "in use")
    repo.mark_failed(file_id, ErrorCategory.FILE_LOCKED, "in use")

    row = repo.get_files(job_id)[0]
    assert row["attempts"] == 2
    assert row["error_category"] == ErrorCategory.FILE_LOCKED.value
    assert row["state"] == FileState.FAILED.value


def test_failed_files_are_retried_on_the_next_run(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1))
    file_id = repo.get_files(job_id)[0]["id"]
    repo.mark_failed(file_id, ErrorCategory.NETWORK, "dropped")

    assert [f["id"] for f in repo.iter_pending_files(job_id)] == [file_id]


def test_quarantined_files_are_not_retried(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1))
    file_id = repo.get_files(job_id)[0]["id"]
    repo.quarantine(file_id)

    assert list(repo.iter_pending_files(job_id)) == []


def test_reset_stale_transfers_recovers_from_a_crash(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(2))
    files = repo.get_files(job_id)
    repo.mark_transferring(files[0]["id"])
    repo.mark_transferring(files[1]["id"])
    repo.mark_verified(files[1]["id"], local_crc32c=1, remote_crc32c=1, generation=1)

    # Simulate the service dying: file 0 is stuck in 'transferring'.
    recovered = repo.reset_stale_transfers(job_id, stale_after_seconds=0)

    assert recovered == 1
    assert repo.get_files(job_id)[0]["state"] == FileState.PENDING.value
    assert repo.get_files(job_id)[1]["state"] == FileState.VERIFIED.value


def test_count_by_state(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(3))
    files = repo.get_files(job_id)
    repo.mark_verified(files[0]["id"], local_crc32c=1, remote_crc32c=1, generation=1)
    repo.mark_failed(files[1]["id"], ErrorCategory.NETWORK, "x")

    counts = repo.count_by_state(job_id)
    assert counts[FileState.VERIFIED] == 1
    assert counts[FileState.FAILED] == 1
    assert counts[FileState.PENDING] == 1


def test_verdict_is_incomplete_until_every_file_succeeds(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(2))
    files = repo.get_files(job_id)

    assert repo.job_verdict(job_id) is JobStatus.INCOMPLETE
    repo.mark_verified(files[0]["id"], local_crc32c=1, remote_crc32c=1, generation=1)
    assert repo.job_verdict(job_id) is JobStatus.INCOMPLETE
    repo.mark_skipped(files[1]["id"])
    assert repo.job_verdict(job_id) is JobStatus.COMPLETE


def test_an_empty_job_is_complete(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    assert repo.job_verdict(job_id) is JobStatus.COMPLETE


def test_events_are_appended_in_order(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.record_event(job_id, "scan_started", "root=C:/data")
    repo.record_event(job_id, "scan_finished", "files=3")

    kinds = [e["kind"] for e in repo.get_events(job_id)]
    assert kinds == ["scan_started", "scan_finished"]


def test_iter_pending_files_is_safe_to_mutate_during_iteration(repo):
    """iter_pending_files must materialize rows up front.

    SQLite documents that a SELECT interleaved with writes to the same table
    on the same connection may skip or repeat rows. The docstring invites
    exactly this pattern (transfer, then mark_verified, in the same loop),
    so it must be safe.
    """
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(10))
    expected_ids = sorted(f["id"] for f in repo.get_files(job_id))

    seen_ids = []
    for row in repo.iter_pending_files(job_id):
        seen_ids.append(row["id"])
        repo.mark_verified(row["id"], local_crc32c=1, remote_crc32c=1, generation=1)

    assert sorted(seen_ids) == expected_ids
    assert len(seen_ids) == len(set(seen_ids))


def test_get_job_raises_lookup_error_for_an_unknown_id(repo):
    with pytest.raises(LookupError, match="no job with id 999"):
        repo.get_job(999)


def test_heartbeat_updates_timestamp_and_bytes_transferred(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1))
    file_id = repo.get_files(job_id)[0]["id"]
    repo.mark_transferring(file_id)

    repo.heartbeat(file_id, 12345)

    row = repo.get_files(job_id)[0]
    assert row["heartbeat_at"] is not None
    assert row["bytes_transferred"] == 12345


def test_reset_stale_transfers_leaves_a_fresh_heartbeat_alone(repo):
    """Negative case: a file transferring with a recent heartbeat is not stale."""
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1))
    file_id = repo.get_files(job_id)[0]["id"]
    repo.mark_transferring(file_id)
    repo.heartbeat(file_id, 10)

    recovered = repo.reset_stale_transfers(job_id, stale_after_seconds=300)

    assert recovered == 0
    assert repo.get_files(job_id)[0]["state"] == FileState.TRANSFERRING.value


def test_mark_changed_resets_transfer_progress_and_is_retried(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1, size=100))
    file_id = repo.get_files(job_id)[0]["id"]
    repo.mark_transferring(file_id)
    repo.heartbeat(file_id, 50)

    repo.mark_changed(file_id, 200, 1_800_000_000_000_000_000)

    row = repo.get_files(job_id)[0]
    assert row["state"] == FileState.CHANGED.value
    assert row["size_bytes"] == 200
    assert row["mtime_ns"] == 1_800_000_000_000_000_000
    assert row["local_crc32c"] is None
    assert row["bytes_transferred"] == 0
    assert [f["id"] for f in repo.iter_pending_files(job_id)] == [file_id]


def test_mark_skipped_persists_proven_checksums_and_clears_errors(repo):
    """IMPORTANT 3 regression: a skip is a proven match (dest already has the
    right size+CRC), so the proven values must be recorded, not discarded —
    and any stale error from an earlier failed attempt must be cleared, same
    as mark_verified does.
    """
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1))
    file_id = repo.get_files(job_id)[0]["id"]
    repo.mark_failed(file_id, ErrorCategory.NETWORK, "earlier drop")

    repo.mark_skipped(
        file_id, local_crc32c=42, remote_crc32c=42, generation=99, sha256="ab"
    )

    row = repo.get_files(job_id)[0]
    assert row["state"] == FileState.SKIPPED.value
    assert row["local_crc32c"] == 42
    assert row["remote_crc32c"] == 42
    assert row["generation"] == 99
    assert row["sha256"] == "ab"
    assert row["error_category"] is None
    assert row["error_message"] is None
    assert row["finished_at"] is not None


def test_mark_skipped_with_no_values_stays_backward_compatible(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1))
    file_id = repo.get_files(job_id)[0]["id"]

    repo.mark_skipped(file_id)

    row = repo.get_files(job_id)[0]
    assert row["state"] == FileState.SKIPPED.value
    assert row["local_crc32c"] is None
    assert row["remote_crc32c"] is None
    assert row["generation"] is None
    assert row["sha256"] is None


def test_state_survives_reopening_the_database(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    repo = JobRepository(conn)
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(2))
    repo.mark_verified(
        repo.get_files(job_id)[0]["id"], local_crc32c=5, remote_crc32c=5, generation=1
    )
    conn.close()

    reopened = connect(tmp_path / "jobs.db")
    repo2 = JobRepository(reopened)
    assert repo2.count_by_state(job_id)[FileState.VERIFIED] == 1
    assert len(list(repo2.iter_pending_files(job_id))) == 1
    reopened.close()


def test_add_planned_files_honours_a_size_policy(repo):
    from mml_cloud_transfer.core.slicing import SizePolicy

    tiny = SizePolicy(
        single_shot_max=10,
        resumable_max=50,
        min_slice=50,
        max_components=32,
    )
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1, size=100), policy=tiny)
    assert repo.get_files(job_id)[0]["method"] == TransferMethod.SLICED.value


def test_set_audit_hash_flips_the_flag(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    repo = JobRepository(conn)
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root="s", dest_prefix=""
    )
    assert repo.get_job(job_id)["audit_hash"] == 0
    repo.set_audit_hash(job_id, True)
    assert repo.get_job(job_id)["audit_hash"] == 1
    repo.set_audit_hash(job_id, False)
    assert repo.get_job(job_id)["audit_hash"] == 0
    with pytest.raises(LookupError):
        repo.set_audit_hash(999, True)
    conn.close()
