import pytest

from mml_cloud_transfer.core.models import Direction, JobStatus, SliceState
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

from tests.store.test_repository import make_files


@pytest.fixture
def repo(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    yield JobRepository(conn)
    conn.close()


@pytest.fixture
def file_id(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1))
    return repo.get_files(job_id)[0]["id"]


def test_start_job_sets_running_and_preserves_the_first_start(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.start_job(job_id)
    first = repo.get_job(job_id)
    assert first["status"] == JobStatus.RUNNING.value
    assert first["started_at"] is not None

    repo.start_job(job_id)  # resume: started_at must not move
    assert repo.get_job(job_id)["started_at"] == first["started_at"]


def test_finish_job_records_status_and_finished_at(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.finish_job(job_id, JobStatus.INCOMPLETE)
    job = repo.get_job(job_id)
    assert job["status"] == JobStatus.INCOMPLETE.value
    assert job["finished_at"] is not None


def test_get_file_fetches_one_row_or_raises(repo, file_id):
    assert repo.get_file(file_id)["id"] == file_id
    with pytest.raises(LookupError):
        repo.get_file(99999)


def test_precondition_round_trip(repo, file_id):
    assert repo.get_precondition(file_id) is None
    repo.set_precondition(file_id, 0)
    assert repo.get_precondition(file_id) == 0
    repo.set_precondition(file_id, 12345)
    assert repo.get_precondition(file_id) == 12345


def test_upsert_slice_inserts_then_updates(repo, file_id):
    repo.upsert_slice(file_id, 0, offset=0, length=100)
    repo.upsert_slice(file_id, 1, offset=100, length=100)
    repo.upsert_slice(
        file_id, 0, offset=0, length=100,
        session_uri="http://s/u", state=SliceState.UPLOADING, bytes_transferred=40,
    )

    rows = repo.get_slices(file_id)
    assert [r["slice_index"] for r in rows] == [0, 1]
    assert rows[0]["session_uri"] == "http://s/u"
    assert rows[0]["state"] == SliceState.UPLOADING.value
    assert rows[0]["bytes_transferred"] == 40
    assert rows[1]["state"] == SliceState.PENDING.value


def test_slice_crc_survives_a_reopen(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    repo = JobRepository(conn)
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1))
    fid = repo.get_files(job_id)[0]["id"]
    repo.upsert_slice(fid, 3, offset=300, length=100, crc32c=42, state=SliceState.UPLOADED)
    conn.close()

    conn2 = connect(tmp_path / "jobs.db")
    rows = JobRepository(conn2).get_slices(fid)
    assert rows[0]["crc32c"] == 42
    assert rows[0]["state"] == SliceState.UPLOADED.value
    conn2.close()


def test_clear_slices_removes_all_rows(repo, file_id):
    repo.upsert_slice(file_id, 0, offset=0, length=10)
    repo.upsert_slice(file_id, 1, offset=10, length=10)
    repo.clear_slices(file_id)
    assert repo.get_slices(file_id) == []


def test_mark_changed_clears_stale_slices(repo, file_id):
    """CRITICAL 1 regression: a same-size in-place rewrite must not let resume
    reuse content-A slice temp objects recorded before the change was seen.
    """
    from mml_cloud_transfer.core.models import FileState

    repo.upsert_slice(file_id, 0, offset=0, length=10, crc32c=111, state=SliceState.UPLOADED)
    repo.upsert_slice(file_id, 1, offset=10, length=10, crc32c=222, state=SliceState.UPLOADED)

    repo.mark_changed(file_id, 999, 1_800_000_000_000_000_000)

    assert repo.get_slices(file_id) == []
    assert repo.get_file(file_id)["state"] == FileState.CHANGED.value
