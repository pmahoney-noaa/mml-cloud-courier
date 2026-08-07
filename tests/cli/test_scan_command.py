import csv

import pytest

from mml_cloud_courier.cli.scan_command import run_scan
from mml_cloud_courier.cli.__main__ import main
from mml_cloud_courier.core.models import FileState
from mml_cloud_courier.store.db import connect
from mml_cloud_courier.store.repository import JobRepository


@pytest.fixture
def tree(tmp_path):
    src = tmp_path / "src"
    (src / "run47").mkdir(parents=True)
    (src / "top.txt").write_bytes(b"a" * 10)
    (src / "run47" / "a.tif").write_bytes(b"b" * 20)
    return src


def test_scan_persists_a_manifest(tmp_path, tree):
    db = tmp_path / "jobs.db"
    outcome = run_scan(
        db_path=db,
        source_root=str(tree),
        dest_prefix="archive/run47",
        job_name="Run 47",
        follow_extended=False,
    )

    assert outcome.file_count == 2
    assert outcome.byte_count == 30
    assert outcome.errors == []

    conn = connect(db)
    repo = JobRepository(conn)
    rows = repo.get_files(outcome.job_id)
    assert sorted(r["object_name"] for r in rows) == [
        "archive/run47/run47/a.tif",
        "archive/run47/top.txt",
    ]
    assert all(r["state"] == FileState.PENDING.value for r in rows)
    conn.close()


def test_scan_records_start_and_finish_events(tmp_path, tree):
    db = tmp_path / "jobs.db"
    outcome = run_scan(
        db_path=db,
        source_root=str(tree),
        dest_prefix="",
        job_name="j",
        follow_extended=False,
    )
    conn = connect(db)
    kinds = [e["kind"] for e in JobRepository(conn).get_events(outcome.job_id)]
    assert kinds == ["scan_started", "scan_finished"]
    conn.close()


def test_scan_is_resumable_and_does_not_duplicate_rows(tmp_path, tree):
    db = tmp_path / "jobs.db"
    first = run_scan(
        db_path=db,
        source_root=str(tree),
        dest_prefix="",
        job_name="j",
        follow_extended=False,
    )
    second = run_scan(
        db_path=db,
        source_root=str(tree),
        dest_prefix="",
        job_name="j",
        follow_extended=False,
        job_id=first.job_id,
    )

    assert second.job_id == first.job_id
    conn = connect(db)
    assert len(JobRepository(conn).get_files(first.job_id)) == 2
    conn.close()


def test_scan_exports_csv(tmp_path, tree):
    db = tmp_path / "jobs.db"
    csv_path = tmp_path / "manifest.csv"
    run_scan(
        db_path=db,
        source_root=str(tree),
        dest_prefix="p",
        job_name="j",
        csv_path=csv_path,
        follow_extended=False,
    )

    with csv_path.open(newline="", encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))

    assert len(rows) == 2
    assert set(rows[0]) == {
        "relative_path",
        "object_name",
        "size_bytes",
        "mtime_ns",
        "method",
        "state",
    }


def test_scan_reports_a_missing_source_without_crashing(tmp_path):
    outcome = run_scan(
        db_path=tmp_path / "jobs.db",
        source_root=str(tmp_path / "nope"),
        dest_prefix="",
        job_name="j",
        follow_extended=False,
    )
    assert outcome.file_count == 0
    assert len(outcome.errors) == 1


def test_scan_errors_are_persisted_as_events(tmp_path):
    """IMPORTANT 4 regression: scan errors must be visible in the job's event
    log, not just in the in-memory ScanOutcome — a report generated later
    (or a resumed job) reads events, not the return value of this call.
    """
    db = tmp_path / "jobs.db"
    outcome = run_scan(
        db_path=db,
        source_root=str(tmp_path / "nope"),
        dest_prefix="",
        job_name="j",
        follow_extended=False,
    )
    conn = connect(db)
    events = JobRepository(conn).get_events(outcome.job_id)
    conn.close()

    scan_error_events = [e for e in events if e["kind"] == "scan_error"]
    assert len(scan_error_events) == 1
    assert outcome.errors[0].category.value in scan_error_events[0]["detail"]
    assert outcome.errors[0].message in scan_error_events[0]["detail"]


def test_main_returns_zero_on_success(tmp_path, tree, capsys):
    code = main(
        [
            "scan",
            "--db", str(tmp_path / "jobs.db"),
            "--source", str(tree),
            "--prefix", "archive",
            "--name", "Run 47",
            "--no-extended-paths",
        ]
    )
    assert code == 0
    assert "2 files" in capsys.readouterr().out


def test_main_returns_nonzero_when_the_scan_had_errors(tmp_path, capsys):
    code = main(
        [
            "scan",
            "--db", str(tmp_path / "jobs.db"),
            "--source", str(tmp_path / "nope"),
            "--prefix", "",
            "--name", "j",
            "--no-extended-paths",
        ]
    )
    assert code == 1
    assert "error" in capsys.readouterr().out.lower()


def test_run_scan_with_a_nonexistent_job_id_raises_lookup_error(tmp_path, tree):
    with pytest.raises(LookupError, match="no job with id 999"):
        run_scan(
            db_path=tmp_path / "jobs.db",
            source_root=str(tree),
            dest_prefix="",
            job_name="j",
            job_id=999,
            follow_extended=False,
        )


def test_main_with_a_nonexistent_job_id_returns_one_without_a_traceback(
    tmp_path, tree, capsys
):
    code = main(
        [
            "scan",
            "--db", str(tmp_path / "jobs.db"),
            "--source", str(tree),
            "--prefix", "",
            "--name", "j",
            "--job-id", "999",
            "--no-extended-paths",
        ]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "no job with id" in out
    assert "Traceback" not in out
