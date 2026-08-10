"""Queue, profile, paging, and event-cursor queries the service layer runs."""

import pytest

from mml_cloud_courier.core.errors import ErrorCategory
from mml_cloud_courier.core.models import Direction, FileState, JobStatus, PlannedFile
from mml_cloud_courier.store.db import connect
from mml_cloud_courier.store.repository import JobRepository


@pytest.fixture
def repo(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    yield JobRepository(conn)
    conn.close()


def _job(repo, *, scheduled=None, status=None, name="j"):
    job_id = repo.create_job(
        name=name, direction=Direction.UPLOAD, source_root="s",
        dest_prefix="", scheduled_start_at=scheduled,
    )
    if status is not None:
        repo.set_job_status(job_id, status)
    return job_id


def test_next_eligible_is_fifo_by_id(repo):
    first = _job(repo)
    _job(repo)
    assert repo.next_eligible_job("2026-08-05T12:00:00+00:00")["id"] == first


def test_scheduled_jobs_wait_for_their_time(repo):
    job_id = _job(repo, scheduled="2026-08-05T22:00:00+00:00")
    assert repo.next_eligible_job("2026-08-05T21:59:59+00:00") is None
    assert repo.next_eligible_job("2026-08-05T22:00:00+00:00")["id"] == job_id


def test_missed_windows_are_eligible_not_skipped(repo):
    # The service was down at the scheduled time; at next start "now" is
    # simply later, so the job runs instead of silently disappearing.
    job_id = _job(repo, scheduled="2026-08-01T03:00:00+00:00")
    assert repo.next_eligible_job("2026-08-05T09:00:00+00:00")["id"] == job_id


def test_non_pending_jobs_are_not_picked(repo):
    _job(repo, status=JobStatus.PAUSED)
    _job(repo, status=JobStatus.COMPLETE)
    _job(repo, status=JobStatus.CANCELLED)
    assert repo.next_eligible_job("2026-08-05T12:00:00+00:00") is None


def test_jobs_with_status_and_list_jobs(repo):
    running = _job(repo, status=JobStatus.RUNNING)
    _job(repo)
    assert [j["id"] for j in repo.jobs_with_status(JobStatus.RUNNING)] == [running]
    assert len(repo.list_jobs()) == 2


def test_get_or_create_profile_is_null_safe_on_credential_ref(repo):
    a = repo.get_or_create_profile(bucket="b", auth_type="adc", credential_ref=None)
    b = repo.get_or_create_profile(bucket="b", auth_type="adc", credential_ref=None)
    c = repo.get_or_create_profile(
        bucket="b", auth_type="key_file", credential_ref="k.json"
    )
    assert a == b
    assert a != c
    assert repo.get_profile(c)["credential_ref"] == "k.json"
    with pytest.raises(LookupError):
        repo.get_profile(999)


def test_files_page_state_filter_and_failure_counts(repo):
    job_id = _job(repo)
    repo.add_planned_files(
        job_id, [PlannedFile(f"f{i:02d}", "s", 1, 1) for i in range(7)]
    )
    page = repo.get_files_page(job_id, limit=3, offset=3)
    assert [r["relative_path"] for r in page] == ["f03", "f04", "f05"]
    first = repo.get_files(job_id)[0]["id"]
    repo.mark_failed(first, ErrorCategory.NETWORK, "boom")
    failed = repo.get_files_page(job_id, state=FileState.FAILED.value, limit=10)
    assert [r["id"] for r in failed] == [first]
    assert repo.count_failures(job_id, ErrorCategory.NETWORK) == 1
    assert repo.count_failures(job_id, ErrorCategory.QUOTA) == 0


def test_events_after_cursor(repo):
    job_id = _job(repo)
    repo.record_event(job_id, "one")
    repo.record_event(job_id, "two")
    all_events = repo.events_after(job_id, 0)
    assert [e["kind"] for e in all_events] == ["one", "two"]
    after = repo.events_after(job_id, all_events[0]["id"])
    assert [e["kind"] for e in after] == ["two"]


def test_archive_only_terminal_completed_group_statuses(repo):
    from mml_cloud_courier.store.repository import JobNotArchivable
    job_id = _job(repo)
    for status in (JobStatus.PENDING, JobStatus.RUNNING, JobStatus.PAUSED,
                   JobStatus.STALLED, JobStatus.INCOMPLETE):
        repo.set_job_status(job_id, status)
        with pytest.raises(JobNotArchivable, match="only complete or cancelled"):
            repo.archive_job(job_id)
    repo.set_job_status(job_id, JobStatus.COMPLETE)
    repo.archive_job(job_id)
    assert repo.get_job(job_id)["archived_at"] is not None


def test_archive_is_idempotent_and_preserves_the_first_stamp(repo):
    job_id = _job(repo)
    repo.set_job_status(job_id, JobStatus.CANCELLED)
    repo.archive_job(job_id)
    first = repo.get_job(job_id)["archived_at"]
    repo.archive_job(job_id)
    assert repo.get_job(job_id)["archived_at"] == first


def test_unarchive_restores_and_is_idempotent(repo):
    job_id = _job(repo)
    repo.set_job_status(job_id, JobStatus.COMPLETE)
    repo.archive_job(job_id)
    repo.unarchive_job(job_id)
    assert repo.get_job(job_id)["archived_at"] is None
    repo.unarchive_job(job_id)                   # no-op, no raise
    with pytest.raises(LookupError):
        repo.archive_job(9999)
    with pytest.raises(LookupError):
        repo.unarchive_job(9999)


def test_list_jobs_excludes_archived_by_default(repo):
    keep = _job(repo)
    hide = _job(repo, name="hidden")
    repo.set_job_status(hide, JobStatus.COMPLETE)
    repo.archive_job(hide)
    assert [row["id"] for row in repo.list_jobs()] == [keep]
    assert [row["id"] for row in repo.list_jobs(include_archived=True)] == [keep, hide]
