"""All job and file state transitions live here.

Resume is not a special mode: it is simply ``iter_pending_files`` returning
everything that is not yet verified, skipped, or quarantined.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

from mml_cloud_transfer.core.errors import ErrorCategory
from mml_cloud_transfer.core.models import (
    Direction,
    FileState,
    JobStatus,
    PlannedFile,
    SliceState,
)
from mml_cloud_transfer.core.paths import canonical_source_key, to_object_name
from mml_cloud_transfer.core.slicing import SizePolicy, choose_method

#: States that will never be retried by a resume.
_NOT_RETRIED = (
    FileState.VERIFIED.value,
    FileState.SKIPPED.value,
    FileState.QUARANTINED.value,
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ProfileInUse(Exception):
    """Deleting a profile that jobs still reference."""


@dataclass(frozen=True, slots=True)
class JobProgress:
    files_total: int
    files_done: int      # verified + skipped
    files_failed: int    # failed + quarantined
    bytes_total: int
    bytes_done: int      # sizes of done files + in-flight slice bytes
    state_counts: dict[str, int]


class JobRepository:
    #: Statuses that can still write to (or be resumed onto) a destination.
    _ACTIVE_STATUSES = (
        JobStatus.PENDING.value, JobStatus.SCANNING.value,
        JobStatus.RUNNING.value, JobStatus.PAUSED.value,
        JobStatus.STALLED.value, JobStatus.INCOMPLETE.value,
    )

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ---- jobs -----------------------------------------------------------

    def find_active_duplicate(
        self, *, source_root: str, dest_prefix: str, bucket: str
    ) -> sqlite3.Row | None:
        """A not-finished job already targeting this destination, or None.

        Two writers composing to one destination corrupt each other's slice
        temps and precondition bookkeeping (release-gate follow-up 3). The
        candidate set is filtered in SQL (destination prefix + active
        status); source equality needs canonical_source_key, so it runs in
        Python over the handful of active rows. A job with no profile row
        (direct-CLI history) has an unknowable bucket and matches
        conservatively — blocking is safe, double-writing is not."""
        placeholders = ", ".join("?" for _ in self._ACTIVE_STATUSES)
        rows = self._conn.execute(
            f"SELECT j.*, p.bucket AS profile_bucket FROM jobs j"
            f" LEFT JOIN profiles p ON p.id = j.profile_id"
            f" WHERE j.dest_prefix = ? AND j.status IN ({placeholders})",
            (dest_prefix, *self._ACTIVE_STATUSES),
        ).fetchall()
        wanted = canonical_source_key(source_root)
        for row in rows:
            if row["profile_bucket"] not in (None, bucket):
                continue
            if canonical_source_key(row["source_root"]) == wanted:
                return row
        return None

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

    def start_job(self, job_id: int) -> None:
        now = _now()
        self._conn.execute(
            "UPDATE jobs SET status = ?, started_at = COALESCE(started_at, ?)"
            " WHERE id = ?",
            (JobStatus.RUNNING.value, now, job_id),
        )

    def finish_job(self, job_id: int, status: JobStatus) -> None:
        self._conn.execute(
            "UPDATE jobs SET status = ?, finished_at = ? WHERE id = ?",
            (status.value, _now(), job_id),
        )

    def get_file(self, file_id: int) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM job_files WHERE id = ?", (file_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"no file with id {file_id}")
        return row

    def set_precondition(self, file_id: int, generation: int) -> None:
        self._conn.execute(
            "UPDATE job_files SET precondition_generation = ? WHERE id = ?",
            (generation, file_id),
        )

    def set_audit_hash(self, job_id: int, enabled: bool) -> None:
        self.get_job(job_id)  # LookupError on a bogus id, matching get_precondition
        self._conn.execute(
            "UPDATE jobs SET audit_hash = ? WHERE id = ?", (int(enabled), job_id)
        )

    def get_precondition(self, file_id: int) -> int | None:
        row = self._conn.execute(
            "SELECT precondition_generation FROM job_files WHERE id = ?", (file_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"no file with id {file_id}")
        value = row["precondition_generation"]
        return None if value is None else int(value)

    def list_jobs(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()

    def jobs_with_status(self, status: JobStatus) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY id", (status.value,)
        ).fetchall()

    def next_eligible_job(self, now: str) -> sqlite3.Row | None:
        """FIFO by id among pending jobs whose scheduled start has passed.

        String comparison is correct only because both sides use the
        _now() format (UTC ISO-8601, seconds, +00:00). The API layer owns
        normalizing user-supplied schedules into that format. A missed
        window needs no special case: its schedule is simply <= now.
        """
        return self._conn.execute(
            "SELECT * FROM jobs WHERE status = ?"
            " AND (scheduled_start_at IS NULL OR scheduled_start_at <= ?)"
            " ORDER BY id LIMIT 1",
            (JobStatus.PENDING.value, now),
        ).fetchone()

    # ---- planning -------------------------------------------------------

    def add_planned_files(
        self,
        job_id: int,
        files: Iterable[PlannedFile],
        *,
        policy: SizePolicy | None = None,
    ) -> int:
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
                choose_method(f.size_bytes, policy=policy).value,
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
        """Yield every file not yet verified, skipped, or quarantined.

        Rows are fetched eagerly (not streamed from a live cursor): SQLite
        documents that a SELECT interleaved with writes to the same table on
        the same connection may skip or repeat rows, and callers are expected
        to write to this same row (e.g. via ``mark_verified``) while
        iterating. A million-row list is an acceptable cost for that safety.
        """
        placeholders = ", ".join("?" for _ in _NOT_RETRIED)
        rows = self._conn.execute(
            f"SELECT * FROM job_files WHERE job_id = ?"
            f" AND state NOT IN ({placeholders}) ORDER BY id",
            (job_id, *_NOT_RETRIED),
        ).fetchall()
        yield from rows

    # ---- file state transitions ----------------------------------------

    def mark_transferring(self, file_id: int) -> None:
        now = _now()
        self._conn.execute(
            "UPDATE job_files SET state = ?, heartbeat_at = ?,"
            " started_at = COALESCE(started_at, ?) WHERE id = ?",
            (FileState.TRANSFERRING.value, now, now, file_id),
        )

    def heartbeat(self, file_id: int) -> None:
        """Refresh the staleness timestamp. Byte progress lives in
        file_slices — a whole-file number written from concurrent slice
        callbacks flaps, so it is derived (job_progress), not stored."""
        self._conn.execute(
            "UPDATE job_files SET heartbeat_at = ? WHERE id = ?", (_now(), file_id)
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

    def mark_skipped(
        self,
        file_id: int,
        *,
        local_crc32c: int | None = None,
        remote_crc32c: int | None = None,
        generation: int | None = None,
        sha256: str | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE job_files SET state = ?, local_crc32c = ?, remote_crc32c = ?,"
            " generation = ?, sha256 = ?, error_category = NULL, error_message = NULL,"
            " heartbeat_at = NULL, finished_at = ? WHERE id = ?",
            (
                FileState.SKIPPED.value,
                local_crc32c,
                remote_crc32c,
                generation,
                sha256,
                _now(),
                file_id,
            ),
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
        # A same-size in-place rewrite must not let a later resume reuse
        # content-A slice temp objects/CRCs recorded before this change was
        # detected — otherwise Layer 2 can verify a chimera of old and new
        # bytes. See CRITICAL 1 in the final-review report.
        self._conn.execute("DELETE FROM file_slices WHERE file_id = ?", (file_id,))

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

    def job_progress(self, job_id: int) -> JobProgress:
        job = self.get_job(job_id)
        counts = {
            r["state"]: r["n"]
            for r in self._conn.execute(
                "SELECT state, COUNT(*) AS n FROM job_files"
                " WHERE job_id = ? GROUP BY state",
                (job_id,),
            )
        }
        done_bytes = self._conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) AS b FROM job_files"
            " WHERE job_id = ? AND state IN (?, ?)",
            (job_id, FileState.VERIFIED.value, FileState.SKIPPED.value),
        ).fetchone()["b"]
        inflight = self._conn.execute(
            "SELECT COALESCE(SUM(s.bytes_transferred), 0) AS b FROM file_slices s"
            " JOIN job_files f ON f.id = s.file_id"
            " WHERE f.job_id = ? AND f.state = ?",
            (job_id, FileState.TRANSFERRING.value),
        ).fetchone()["b"]
        return JobProgress(
            files_total=job["planned_files"],
            files_done=counts.get(FileState.VERIFIED.value, 0)
            + counts.get(FileState.SKIPPED.value, 0),
            files_failed=counts.get(FileState.FAILED.value, 0)
            + counts.get(FileState.QUARANTINED.value, 0),
            bytes_total=job["planned_bytes"],
            bytes_done=done_bytes + inflight,
            state_counts=counts,
        )

    def get_files_page(
        self, job_id: int, *, state: str | None = None,
        limit: int = 500, offset: int = 0,
    ) -> list[sqlite3.Row]:
        if state is None:
            return self._conn.execute(
                "SELECT * FROM job_files WHERE job_id = ?"
                " ORDER BY id LIMIT ? OFFSET ?",
                (job_id, limit, offset),
            ).fetchall()
        return self._conn.execute(
            "SELECT * FROM job_files WHERE job_id = ? AND state = ?"
            " ORDER BY id LIMIT ? OFFSET ?",
            (job_id, state, limit, offset),
        ).fetchall()

    def count_failures(self, job_id: int, category: ErrorCategory) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) AS n FROM job_files WHERE job_id = ?"
            " AND state = ? AND error_category = ?",
            (job_id, FileState.FAILED.value, category.value),
        ).fetchone()["n"]

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

    def events_after(self, job_id: int, after_id: int = 0) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM events WHERE job_id = ? AND id > ? ORDER BY id",
            (job_id, after_id),
        ).fetchall()

    # ---- slices ---------------------------------------------------------

    def upsert_slice(
        self,
        file_id: int,
        slice_index: int,
        *,
        offset: int,
        length: int,
        session_uri: str | None = None,
        crc32c: int | None = None,
        state: SliceState = SliceState.PENDING,
        bytes_transferred: int = 0,
    ) -> None:
        self._conn.execute(
            "INSERT INTO file_slices (file_id, slice_index, offset_bytes,"
            " length_bytes, state, session_uri, crc32c, bytes_transferred)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (file_id, slice_index) DO UPDATE SET"
            " offset_bytes = excluded.offset_bytes,"
            " length_bytes = excluded.length_bytes,"
            " state = excluded.state,"
            " session_uri = excluded.session_uri,"
            " crc32c = excluded.crc32c,"
            " bytes_transferred = excluded.bytes_transferred",
            (
                file_id,
                slice_index,
                offset,
                length,
                state.value,
                session_uri,
                crc32c,
                bytes_transferred,
            ),
        )

    def get_slices(self, file_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM file_slices WHERE file_id = ? ORDER BY slice_index",
            (file_id,),
        ).fetchall()

    def clear_slices(self, file_id: int) -> None:
        self._conn.execute("DELETE FROM file_slices WHERE file_id = ?", (file_id,))

    # ---- profiles -------------------------------------------------------

    def create_profile(
        self, *, name: str, bucket: str, auth_type: str,
        credential_ref: str | None = None, project_id: str = "",
        default_prefix: str = "",
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO profiles (name, project_id, bucket, auth_type,"
            " credential_ref, default_prefix, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, project_id, bucket, auth_type, credential_ref,
             default_prefix, _now()),
        )
        return int(cursor.lastrowid)

    def get_profile(self, profile_id: int) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"no profile with id {profile_id}")
        return row

    def find_profile(
        self, *, bucket: str, auth_type: str, credential_ref: str | None
    ) -> sqlite3.Row | None:
        # "IS ?" is SQLite's NULL-safe equality, so an ADC profile
        # (credential_ref NULL) is found rather than duplicated.
        return self._conn.execute(
            "SELECT * FROM profiles WHERE bucket = ? AND auth_type = ?"
            " AND credential_ref IS ? ORDER BY id LIMIT 1",
            (bucket, auth_type, credential_ref),
        ).fetchone()

    def get_or_create_profile(
        self, *, bucket: str, auth_type: str, credential_ref: str | None = None
    ) -> int:
        """Find-or-create without COUNT-based naming: the UNIQUE(name)
        index is the arbiter, so two connections racing here converge on
        one row instead of crashing (Plan 3 deferred fix). A name taken by
        a *different* credential gets the next suffix; a name taken by our
        twin is found on the next loop."""
        base = f"{bucket} [{auth_type}]"
        for attempt in range(1, 101):
            row = self.find_profile(
                bucket=bucket, auth_type=auth_type, credential_ref=credential_ref
            )
            if row is not None:
                return int(row["id"])
            candidate = base if attempt == 1 else f"{base} ({attempt})"
            try:
                return self.create_profile(
                    name=candidate, bucket=bucket, auth_type=auth_type,
                    credential_ref=credential_ref,
                )
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError(f"could not allocate a profile name from {base!r}")

    def find_profile_by_name(self, name: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM profiles WHERE name = ?", (name,)
        ).fetchone()

    def list_profiles(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM profiles ORDER BY id").fetchall()

    def set_profile_validated(self, profile_id: int) -> None:
        self.get_profile(profile_id)  # LookupError on a bogus id
        self._conn.execute(
            "UPDATE profiles SET validated_at = ? WHERE id = ?",
            (_now(), profile_id),
        )

    def delete_profile(self, profile_id: int) -> None:
        """Refuses while jobs reference the profile: jobs keep it for
        report/bucket lookups, and the FK would reject the delete anyway —
        this just says so in words."""
        self.get_profile(profile_id)  # LookupError on a bogus id
        used = self._conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE profile_id = ?", (profile_id,)
        ).fetchone()["n"]
        if used:
            raise ProfileInUse(
                f"profile {profile_id} is used by {used} job(s) and cannot be"
                " deleted while they exist"
            )
        self._conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
