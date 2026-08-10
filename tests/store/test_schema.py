import sqlite3

import pytest

from mml_cloud_courier.store.db import connect
from mml_cloud_courier.store.schema import SCHEMA_VERSION, apply_migrations

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


def test_fresh_database_is_version_3_with_validated_at_and_archived_at(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 3
        columns = {r[1] for r in conn.execute("PRAGMA table_info(profiles)")}
        assert "validated_at" in columns
        columns = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
        assert "archived_at" in columns
    finally:
        conn.close()


def test_a_v1_database_is_migrated_in_place(tmp_path):
    """Build a database exactly as schema v1 wrote it (no validated_at,
    version=1), then connect(): the column appears, the version bumps,
    and existing rows survive."""

    db = tmp_path / "jobs.db"
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version (version) VALUES (1);
        CREATE TABLE profiles (
            id             INTEGER PRIMARY KEY,
            name           TEXT NOT NULL UNIQUE,
            project_id     TEXT NOT NULL,
            bucket         TEXT NOT NULL,
            auth_type      TEXT NOT NULL,
            credential_ref TEXT,
            default_prefix TEXT NOT NULL DEFAULT '',
            created_at     TEXT NOT NULL
        );
        INSERT INTO profiles (name, project_id, bucket, auth_type, created_at)
        VALUES ('legacy', '', 'b', 'adc', '2026-08-05T00:00:00+00:00');
        """
    )
    raw.commit()
    raw.close()

    conn = connect(db)
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 3
        row = conn.execute("SELECT * FROM profiles WHERE name = 'legacy'").fetchone()
        assert row["validated_at"] is None  # new column, old row intact
    finally:
        conn.close()


def test_an_interrupted_migration_recovers_on_the_next_connect(tmp_path):
    """Killed between the ALTER and the version bump, a v1 database has
    the column but still says version 1. The next connect() must finish
    the migration, not die on 'duplicate column name'."""

    db = tmp_path / "jobs.db"
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version (version) VALUES (1);
        CREATE TABLE profiles (
            id             INTEGER PRIMARY KEY,
            name           TEXT NOT NULL UNIQUE,
            project_id     TEXT NOT NULL,
            bucket         TEXT NOT NULL,
            auth_type      TEXT NOT NULL,
            credential_ref TEXT,
            default_prefix TEXT NOT NULL DEFAULT '',
            created_at     TEXT NOT NULL
        );
        """
    )
    raw.execute("ALTER TABLE profiles ADD COLUMN validated_at TEXT")  # crash point: version bump never ran
    raw.commit()
    raw.close()

    conn = connect(db)
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 3
    finally:
        conn.close()


def test_a_v2_database_gains_archived_at_in_place(tmp_path):
    """Build a database exactly as schema v2 wrote it (no jobs.archived_at,
    version=2), then connect(): the column appears, the version bumps, and
    existing rows survive with archived_at NULL."""
    db = tmp_path / "jobs.db"
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version (version) VALUES (2);
        CREATE TABLE jobs (
            id                 INTEGER PRIMARY KEY,
            name               TEXT NOT NULL,
            direction          TEXT NOT NULL,
            profile_id         INTEGER,
            source_root        TEXT NOT NULL,
            dest_prefix        TEXT NOT NULL,
            status             TEXT NOT NULL,
            audit_hash         INTEGER NOT NULL DEFAULT 0,
            scheduled_start_at TEXT,
            created_at         TEXT NOT NULL,
            started_at         TEXT,
            finished_at        TEXT,
            planned_files      INTEGER NOT NULL DEFAULT 0,
            planned_bytes      INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO jobs (name, direction, source_root, dest_prefix, status, created_at)
        VALUES ('legacy', 'upload', 'C:\\d', 'p', 'complete', '2026-08-09T00:00:00+00:00');
        """
    )
    raw.commit()
    raw.close()

    conn = connect(db)
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 3
        row = conn.execute("SELECT * FROM jobs WHERE name = 'legacy'").fetchone()
        assert row["archived_at"] is None       # new column, old row intact
    finally:
        conn.close()


def test_an_interrupted_v3_migration_recovers_on_the_next_connect(tmp_path):
    """Simulate a crash between the ALTER and the version bump: the column
    exists but the version still says 2. connect() must not re-ALTER (which
    would raise) and must catch the version up."""
    db = tmp_path / "jobs.db"
    conn = connect(db)          # fresh v3
    conn.execute("UPDATE schema_version SET version = 2")
    conn.commit()
    conn.close()

    conn = connect(db)
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 3
    finally:
        conn.close()
