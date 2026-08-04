"""SQLite schema and migrations.

Enum values from ``core.models`` are stored as plain text. Every state
transition is committed, so a killed process loses at most the in-flight
chunk.
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE,
    project_id     TEXT NOT NULL,
    bucket         TEXT NOT NULL,
    auth_type      TEXT NOT NULL,
    credential_ref TEXT,
    default_prefix TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id                 INTEGER PRIMARY KEY,
    name               TEXT NOT NULL,
    direction          TEXT NOT NULL,
    profile_id         INTEGER REFERENCES profiles(id),
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

CREATE TABLE IF NOT EXISTS job_files (
    id                INTEGER PRIMARY KEY,
    job_id            INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    relative_path     TEXT NOT NULL,
    source_path       TEXT NOT NULL,
    object_name       TEXT NOT NULL,
    size_bytes        INTEGER NOT NULL,
    mtime_ns          INTEGER NOT NULL,
    method            TEXT NOT NULL,
    state             TEXT NOT NULL,
    local_crc32c      INTEGER,
    remote_crc32c     INTEGER,
    sha256            TEXT,
    generation        INTEGER,
    -- Destination generation captured at plan time; 0 means the object must
    -- not exist yet. Enforced via if_generation_match by the Plan 2 engine.
    precondition_generation INTEGER,
    bytes_transferred INTEGER NOT NULL DEFAULT 0,
    attempts          INTEGER NOT NULL DEFAULT 0,
    error_category    TEXT,
    error_message     TEXT,
    heartbeat_at      TEXT,
    started_at        TEXT,
    finished_at       TEXT,
    UNIQUE (job_id, relative_path)
);

CREATE INDEX IF NOT EXISTS idx_job_files_state ON job_files (job_id, state);

CREATE TABLE IF NOT EXISTS file_slices (
    id                INTEGER PRIMARY KEY,
    file_id           INTEGER NOT NULL REFERENCES job_files(id) ON DELETE CASCADE,
    slice_index       INTEGER NOT NULL,
    offset_bytes      INTEGER NOT NULL,
    length_bytes      INTEGER NOT NULL,
    state             TEXT NOT NULL,
    session_uri       TEXT,
    temp_object       TEXT,
    crc32c            INTEGER,
    bytes_transferred INTEGER NOT NULL DEFAULT 0,
    UNIQUE (file_id, slice_index)
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY,
    job_id  INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    file_id INTEGER,
    at      TEXT NOT NULL,
    kind    TEXT NOT NULL,
    detail  TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_job ON events (job_id, id);
"""


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Create or upgrade the schema. Safe to call on every connect."""
    conn.executescript(_DDL)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    conn.commit()
