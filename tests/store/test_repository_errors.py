"""Grouped-error queries and the category filter behind the GUI Errors tab."""

from mml_cloud_courier.core.errors import ErrorCategory
from mml_cloud_courier.core.models import Direction, PlannedFile
from mml_cloud_courier.store.db import connect
from mml_cloud_courier.store.repository import JobRepository


def _seeded_repo(tmp_path):
    conn = connect(tmp_path / "j.db")
    repo = JobRepository(conn)
    job_id = repo.create_job(name="j", direction=Direction.UPLOAD,
                             source_root="C:\\d", dest_prefix="")
    repo.add_planned_files(job_id, [
        PlannedFile(f"f{i}.bin", f"C:\\d\\f{i}.bin", 10, 1) for i in range(5)
    ])
    files = repo.get_files(job_id)
    repo.mark_failed(files[0]["id"], ErrorCategory.PERMISSION_DENIED, "denied")
    repo.mark_failed(files[1]["id"], ErrorCategory.PERMISSION_DENIED, "denied")
    repo.mark_failed(files[2]["id"], ErrorCategory.FILE_LOCKED, "locked")
    repo.quarantine(files[2]["id"])          # locked file gave up
    repo.mark_verified(files[3]["id"], local_crc32c=0, remote_crc32c=0, generation=1)
    return conn, repo, job_id, files


def test_error_groups_counts_failed_and_quarantined_by_category(tmp_path):
    conn, repo, job_id, _ = _seeded_repo(tmp_path)
    groups = repo.error_groups(job_id)
    assert [(g["category"], g["count"], g["quarantined"]) for g in groups] == [
        ("permission_denied", 2, 0), ("file_locked", 1, 1),
    ]
    conn.close()


def test_category_filter_only_returns_failed_and_quarantined_rows(tmp_path):
    conn, repo, job_id, files = _seeded_repo(tmp_path)
    # give the verified file a stale category, as a retried-then-ok file has
    conn.execute("UPDATE job_files SET error_category = 'permission_denied'"
                 " WHERE id = ?", (files[3]["id"],))
    rows = repo.get_files_page(job_id, category="permission_denied")
    assert sorted(r["relative_path"] for r in rows) == ["f0.bin", "f1.bin"]
    conn.close()


def test_retry_files_revives_failed_and_quarantined_with_fresh_attempts(tmp_path):
    conn, repo, job_id, files = _seeded_repo(tmp_path)
    count = repo.retry_files(job_id, "file_locked")
    assert count == 1
    row = repo.get_file(files[2]["id"])
    assert (row["state"], row["attempts"], row["error_category"]) == ("pending", 0, None)
    # the other category was untouched
    assert repo.get_file(files[0]["id"])["state"] == "failed"
    conn.close()


def test_exclude_files_quarantines_failed_rows_and_keeps_the_error(tmp_path):
    conn, repo, job_id, files = _seeded_repo(tmp_path)
    count = repo.exclude_files(job_id, "permission_denied")
    assert count == 2
    row = repo.get_file(files[0]["id"])
    assert row["state"] == "quarantined"
    assert row["error_category"] == "permission_denied"  # cause stays visible
    conn.close()
