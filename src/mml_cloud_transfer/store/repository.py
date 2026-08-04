"""All job and file state transitions live here.

Resume is not a special mode: it is simply ``iter_pending_files`` returning
everything that is not yet verified, skipped, or quarantined.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime

from mml_cloud_transfer.core.errors import ErrorCategory
from mml_cloud_transfer.core.models import (
    Direction,
    FileState,
    JobStatus,
    PlannedFile,
)
from mml_cloud_transfer.core.paths import to_object_name
from mml_cloud_transfer.core.slicing import choose_method

#: States that will never be retried by a resume.
_NOT_RETRIED = (
    FileState.VERIFIED.value,
    FileState.SKIPPED.value,
    FileState.QUARANTINED.value,
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class JobRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ---- jobs -----------------------------------------------------------

    def create_job(
        self,
        *,
        name: str,
        direction: Direction,
        source_root: str,
        dest_prefix: str,
        profile_id: int | None = None,
        audit_hash: bool = False,
        scheduled_start_at: str | None = None,
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO jobs (name, direction, profile_id, source_root, dest_prefix,"
            " status, audit_hash, scheduled_start_at, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                direction.value,
                profile_id,
                source_root,
                dest_prefix,
                JobStatus.PENDING.value,
                int(audit_hash),
                scheduled_start_at,
                _now(),
            ),
        )
        return int(cursor.lastrowid)

    def get_job(self, job_id: int) -> sqlite3.Row:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise LookupError(f"no job with id {job_id}")
        return row

    def set_job_status(self, job_id: int, status: JobStatus) -> None:
        self._conn.execute(
            "UPDATE jobs SET status = ? WHERE id = ?", (status.value, job_id)
        )

    # ---- planning -------------------------------------------------------

    def add_planned_files(self, job_id: int, files: Iterable[PlannedFile]) -> int:
        """Insert planned files, ignoring any already present. Returns rows added."""
        job = self.get_job(job_id)
        prefix = job["dest_prefix"]

        rows = [
            (
                job_id,
                f.relative_path,
                f.source_path,
                to_object_name(prefix, f.relative_path),
                f.size_bytes,
                f.mtime_ns,
                choose_method(f.size_bytes).value,
                FileState.PENDING.value,
            )
            for f in files
        ]

        self._conn.execute("BEGIN")
        try:
            before = self._conn.total_changes
            self._conn.executemany(
                "INSERT OR IGNORE INTO job_files (job_id, relative_path, source_path,"
                " object_name, size_bytes, mtime_ns, method, state)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            added = self._conn.total_changes - before
            self._conn.execute(
                "UPDATE jobs SET"
                " planned_files = (SELECT COUNT(*) FROM job_files WHERE job_id = ?),"
                " planned_bytes = (SELECT COALESCE(SUM(size_bytes), 0) FROM job_files"
                "                  WHERE job_id = ?)"
                " WHERE id = ?",
                (job_id, job_id, job_id),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return added

    def get_files(self, job_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM job_files WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()

    def iter_pending_files(self, job_id: int) -> Iterator[sqlite3.Row]:
        placeholders = ", ".join("?" for _ in _NOT_RETRIED)
        yield from self._conn.execute(
            f"SELECT * FROM job_files WHERE job_id = ?"
            f" AND state NOT IN ({placeholders}) ORDER BY id",
            (job_id, *_NOT_RETRIED),
        )

    # ---- file state transitions ----------------------------------------

    def mark_transferring(self, file_id: int) -> None:
        now = _now()
        self._conn.execute(
            "UPDATE job_files SET state = ?, heartbeat_at = ?,"
            " started_at = COALESCE(started_at, ?) WHERE id = ?",
            (FileState.TRANSFERRING.value, now, now, file_id),
        )

    def heartbeat(self, file_id: int, bytes_transferred: int) -> None:
        self._conn.execute(
            "UPDATE job_files SET heartbeat_at = ?, bytes_transferred = ? WHERE id = ?",
            (_now(), bytes_transferred, file_id),
        )

    def mark_verified(
        self,
        file_id: int,
        *,
        local_crc32c: int,
        remote_crc32c: int,
        generation: int,
        sha256: str | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE job_files SET state = ?, local_crc32c = ?, remote_crc32c = ?,"
            " generation = ?, sha256 = ?, error_category = NULL, error_message = NULL,"
            " heartbeat_at = NULL, finished_at = ? WHERE id = ?",
            (
                FileState.VERIFIED.value,
                local_crc32c,
                remote_crc32c,
                generation,
                sha256,
                _now(),
                file_id,
            ),
        )

    def mark_skipped(self, file_id: int) -> None:
        self._conn.execute(
            "UPDATE job_files SET state = ?, heartbeat_at = NULL, finished_at = ?"
            " WHERE id = ?",
            (FileState.SKIPPED.value, _now(), file_id),
        )

    def mark_failed(self, file_id: int, category: ErrorCategory, message: str) -> None:
        self._conn.execute(
            "UPDATE job_files SET state = ?, attempts = attempts + 1,"
            " error_category = ?, error_message = ?, heartbeat_at = NULL WHERE id = ?",
            (FileState.FAILED.value, category.value, message, file_id),
        )

    def mark_changed(self, file_id: int, size_bytes: int, mtime_ns: int) -> None:
        self._conn.execute(
            "UPDATE job_files SET state = ?, size_bytes = ?, mtime_ns = ?,"
            " local_crc32c = NULL, bytes_transferred = 0, heartbeat_at = NULL"
            " WHERE id = ?",
            (FileState.CHANGED.value, size_bytes, mtime_ns, file_id),
        )

    def quarantine(self, file_id: int) -> None:
        self._conn.execute(
            "UPDATE job_files SET state = ?, heartbeat_at = NULL, finished_at = ?"
            " WHERE id = ?",
            (FileState.QUARANTINED.value, _now(), file_id),
        )

    def reset_stale_transfers(self, job_id: int, *, stale_after_seconds: int = 300) -> int:
        """Return files stranded in 'transferring' by a crash to 'pending'."""
        cursor = self._conn.execute(
            "UPDATE job_files SET state = ?, heartbeat_at = NULL, bytes_transferred = 0"
            " WHERE job_id = ? AND state = ?"
            " AND (heartbeat_at IS NULL"
            "      OR (julianday('now') - julianday(heartbeat_at)) * 86400.0 >= ?)",
            (
                FileState.PENDING.value,
                job_id,
                FileState.TRANSFERRING.value,
                stale_after_seconds,
            ),
        )
        return cursor.rowcount

    # ---- reporting ------------------------------------------------------

    def count_by_state(self, job_id: int) -> dict[FileState, int]:
        rows = self._conn.execute(
            "SELECT state, COUNT(*) AS n FROM job_files WHERE job_id = ? GROUP BY state",
            (job_id,),
        ).fetchall()
        return {FileState(r["state"]): r["n"] for r in rows}

    def job_verdict(self, job_id: int) -> JobStatus:
        """COMPLETE only when every planned file is verified or skipped.

        Checks file states only. The final job status additionally requires
        the Layer 3 completeness audit (Plan 2) to reconcile.
        """
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM job_files WHERE job_id = ? AND state NOT IN (?, ?)",
            (job_id, FileState.VERIFIED.value, FileState.SKIPPED.value),
        ).fetchone()
        return JobStatus.COMPLETE if row["n"] == 0 else JobStatus.INCOMPLETE

    # ---- events ---------------------------------------------------------

    def record_event(
        self, job_id: int, kind: str, detail: str | None = None, file_id: int | None = None
    ) -> None:
        self._conn.execute(
            "INSERT INTO events (job_id, file_id, at, kind, detail) VALUES (?, ?, ?, ?, ?)",
            (job_id, file_id, _now(), kind, detail),
        )

    def get_events(self, job_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM events WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()
