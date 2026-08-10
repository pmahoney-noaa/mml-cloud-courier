import csv
import json

import pytest

from mml_cloud_courier.core.errors import ErrorCategory
from mml_cloud_courier.core.hashing import crc32c_to_base64
from mml_cloud_courier.core.models import Direction, JobStatus
from mml_cloud_courier.engine.report import write_report
from mml_cloud_courier.store.db import connect
from mml_cloud_courier.store.repository import JobRepository

from tests.store.test_repository import make_files


@pytest.fixture
def finished_job(tmp_path):
    """A job with 2 verified files, 1 skipped, and 2 failures in one category."""
    db = tmp_path / "jobs.db"
    conn = connect(db)
    repo = JobRepository(conn)
    job_id = repo.create_job(
        name="Run 47", direction=Direction.UPLOAD,
        source_root=r"\\?\C:\data\run47", dest_prefix="archive/run47",
    )
    repo.add_planned_files(job_id, make_files(5, size=1000))
    rows = repo.get_files(job_id)
    repo.mark_verified(rows[0]["id"], local_crc32c=11, remote_crc32c=11, generation=1)
    repo.mark_verified(
        rows[1]["id"], local_crc32c=22, remote_crc32c=22, generation=2, sha256="ab" * 32
    )
    repo.mark_skipped(rows[2]["id"])
    repo.mark_failed(rows[3]["id"], ErrorCategory.FILE_LOCKED, "in use by EDITOR.EXE")
    repo.mark_failed(rows[4]["id"], ErrorCategory.FILE_LOCKED, "in use by EDITOR.EXE")
    repo.start_job(job_id)
    repo.finish_job(job_id, JobStatus.INCOMPLETE)
    conn.close()
    return db, job_id


def test_summary_json_carries_verdict_counts_and_identity(finished_job, tmp_path):
    db, job_id = finished_job
    paths = write_report(db, job_id, tmp_path / "out", bucket="mml-archive")

    summary = json.loads(paths.summary_json.read_text(encoding="utf-8"))
    assert summary["job_id"] == job_id
    assert summary["name"] == "Run 47"
    assert summary["direction"] == "upload"
    assert summary["bucket"] == "mml-archive"
    assert summary["dest_prefix"] == "archive/run47"
    assert summary["verdict"] == "INCOMPLETE"
    assert summary["counts"] == {"verified": 2, "skipped": 1, "failed": 2}
    assert summary["planned_files"] == 5
    assert summary["planned_bytes"] == 5000
    assert summary["errors_by_category"] == {"file_locked": 2}
    # The stored \\?\ prefix never reaches the report.
    assert summary["source_root"] == "C:\\data\\run47"


def test_manifest_csv_has_one_row_per_file_with_base64_crcs(finished_job, tmp_path):
    db, job_id = finished_job
    paths = write_report(db, job_id, tmp_path / "out")

    with paths.manifest_csv.open(newline="", encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))
    assert len(rows) == 5
    by_path = {r["relative_path"]: r for r in rows}
    verified = by_path["run47/file0.tif"]
    assert verified["state"] == "verified"
    assert verified["local_crc32c"] == crc32c_to_base64(11)
    assert verified["remote_crc32c"] == crc32c_to_base64(11)
    failed = by_path["run47/file3.tif"]
    assert failed["error_category"] == "file_locked"
    assert failed["local_crc32c"] == ""


def test_html_report_is_self_contained_and_groups_failures(finished_job, tmp_path):
    db, job_id = finished_job
    paths = write_report(db, job_id, tmp_path / "out")

    html = paths.report_html.read_text(encoding="utf-8")
    assert "INCOMPLETE" in html
    assert "file_locked" in html
    assert "in use by EDITOR.EXE" in html
    assert "run47/file3.tif" in html
    # Self-contained: no external references of any kind.
    assert "http://" not in html and "https://" not in html
    assert "<script src" not in html and "<link" not in html


def test_complete_job_reports_complete(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    repo = JobRepository(conn)
    job_id = repo.create_job(
        name="ok", direction=Direction.UPLOAD, source_root=r"C:\x", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1))
    repo.mark_verified(
        repo.get_files(job_id)[0]["id"], local_crc32c=1, remote_crc32c=1, generation=1
    )
    repo.finish_job(job_id, JobStatus.COMPLETE)
    conn.close()

    paths = write_report(db, job_id, tmp_path / "out")
    summary = json.loads(paths.summary_json.read_text(encoding="utf-8"))
    assert summary["verdict"] == "COMPLETE"
    assert "COMPLETE" in paths.report_html.read_text(encoding="utf-8")


def test_summary_and_html_surface_scan_errors(tmp_path):
    """IMPORTANT 4 regression: scan errors recorded as events must show up in
    both summary.json and report.html, or a job can look COMPLETE while
    files were silently unplanned.
    """
    db = tmp_path / "jobs.db"
    conn = connect(db)
    repo = JobRepository(conn)
    job_id = repo.create_job(
        name="partial-scan", direction=Direction.UPLOAD,
        source_root=r"C:\x", dest_prefix="",
    )
    repo.add_planned_files(job_id, make_files(1))
    repo.mark_verified(
        repo.get_files(job_id)[0]["id"], local_crc32c=1, remote_crc32c=1, generation=1
    )
    repo.record_event(job_id, "scan_error", "[permission_denied] denied (C:\\locked)")
    repo.record_event(job_id, "scan_error", "[not_found] vanished (C:\\gone)")
    repo.finish_job(job_id, JobStatus.COMPLETE)
    conn.close()

    paths = write_report(db, job_id, tmp_path / "out")

    summary = json.loads(paths.summary_json.read_text(encoding="utf-8"))
    assert summary["scan_errors"] == 2

    html = paths.report_html.read_text(encoding="utf-8")
    assert "Scan errors (2)" in html
    assert "permission_denied" in html
    assert "C:\\locked" in html


def test_write_report_leaves_no_tmp_files_behind(finished_job, tmp_path):
    """IMPORTANT 4 regression: each of the three files is written to a
    sibling temp path and atomically replaced into place so a concurrent
    writer (worker auto-report vs. POST /report) can't interleave/truncate
    the other's output. No .tmp artifact should survive a normal write."""
    db, job_id = finished_job
    out_dir = tmp_path / "out"
    write_report(db, job_id, out_dir, bucket="mml-archive")
    assert list(out_dir.glob("*.tmp")) == []


def test_summary_scan_errors_is_zero_when_none_recorded(finished_job, tmp_path):
    db, job_id = finished_job
    paths = write_report(db, job_id, tmp_path / "out")

    summary = json.loads(paths.summary_json.read_text(encoding="utf-8"))
    assert summary["scan_errors"] == 0
    assert "Scan errors" not in paths.report_html.read_text(encoding="utf-8")


def test_failure_groups_are_capped_at_fifty_with_overflow_line(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    repo = JobRepository(conn)
    job_id = repo.create_job(
        name="big-fail", direction=Direction.UPLOAD,
        source_root=r"C:\x", dest_prefix="",
    )
    repo.add_planned_files(job_id, make_files(60, size=10))
    for row in repo.get_files(job_id):
        repo.mark_failed(row["id"], ErrorCategory.NETWORK, f"drop {row['id']}")
    repo.finish_job(job_id, JobStatus.INCOMPLETE)
    conn.close()

    paths = write_report(db, job_id, tmp_path / "out")
    html = paths.report_html.read_text(encoding="utf-8")
    assert "network (60)" in html
    assert html.count("<li>") == 50
    assert "… and 10 more." in html


def test_html_report_lists_every_file_with_checksums(finished_job, tmp_path):
    db, job_id = finished_job
    paths = write_report(db, job_id, tmp_path / "report")
    html_text = paths.report_html.read_text(encoding="utf-8")
    assert "Files (5)" in html_text
    assert "file0.tif" in html_text and "file4.tif" in html_text
    assert crc32c_to_base64(11) in html_text          # verified file's crc
    assert "ab" * 32 in html_text                     # the one sha256
    assert "in use by EDITOR.EXE" in html_text        # detail column
    assert "1.0 KB" in html_text                      # human-readable size
    # self-containment invariant must survive the new table
    assert "http://" not in html_text and "https://" not in html_text


def test_html_files_table_caps_and_points_at_manifest(finished_job, tmp_path, monkeypatch):
    import mml_cloud_courier.engine.report as report_module
    monkeypatch.setattr(report_module, "_MAX_FILES_SHOWN", 3)
    db, job_id = finished_job
    paths = write_report(db, job_id, tmp_path / "report")
    html_text = paths.report_html.read_text(encoding="utf-8")
    # Extract just the Files table section to test capping logic
    files_section = html_text[html_text.find("<h2>Files"):html_text.find("</html>")]
    assert "Files (5)" in html_text
    assert "file2.tif" in files_section
    assert "file3.tif" not in files_section and "file4.tif" not in files_section
    assert "and 2 more" in files_section
    assert "manifest.csv" in html_text
