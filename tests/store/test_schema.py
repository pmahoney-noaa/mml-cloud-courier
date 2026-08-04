import sqlite3

import pytest

from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.schema import SCHEMA_VERSION, apply_migrations

EXPECTED_TABLES = {
    "schema_version",
    "profiles",
    "jobs",
    "job_files",
    "file_slices",
    "events",
}


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "jobs.db")
    yield connection
    connection.close()


def test_all_tables_exist(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    assert EXPECTED_TABLES <= {r["name"] for r in rows}


def test_schema_version_is_recorded(conn):
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert row["version"] == SCHEMA_VERSION


def test_wal_mode_is_enabled(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_foreign_keys_are_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO job_files (job_id, relative_path, source_path, object_name,"
            " size_bytes, mtime_ns, method, state)"
            " VALUES (999, 'a', 'a', 'a', 0, 0, 'single_shot', 'pending')"
        )
        conn.commit()


def test_rows_are_dict_like(conn):
    conn.execute(
        "INSERT INTO jobs (name, direction, source_root, dest_prefix, status, created_at)"
        " VALUES ('j', 'upload', 'C:/x', 'p', 'pending', '2026-08-04T00:00:00Z')"
    )
    row = conn.execute("SELECT name, direction FROM jobs").fetchone()
    assert row["name"] == "j"
    assert row["direction"] == "upload"


def test_migrations_are_idempotent(tmp_path):
    path = tmp_path / "jobs.db"
    first = connect(path)
    first.execute(
        "INSERT INTO jobs (name, direction, source_root, dest_prefix, status, created_at)"
        " VALUES ('j', 'upload', 'C:/x', 'p', 'pending', '2026-08-04T00:00:00Z')"
    )
    first.commit()
    first.close()

    second = connect(path)
    apply_migrations(second)
    assert second.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 1
    assert second.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()["n"] == 1
    second.close()


def test_duplicate_relative_path_in_one_job_is_rejected(conn):
    conn.execute(
        "INSERT INTO jobs (id, name, direction, source_root, dest_prefix, status, created_at)"
        " VALUES (1, 'j', 'upload', 'C:/x', 'p', 'pending', '2026-08-04T00:00:00Z')"
    )
    insert = (
        "INSERT INTO job_files (job_id, relative_path, source_path, object_name,"
        " size_bytes, mtime_ns, method, state)"
        " VALUES (1, 'a.tif', 'C:/x/a.tif', 'p/a.tif', 1, 1, 'single_shot', 'pending')"
    )
    conn.execute(insert)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert)
