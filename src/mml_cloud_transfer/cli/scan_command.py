"""The scan half of a transfer job: build the manifest before any bytes move."""

from __future__ import annotations

import csv
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mml_cloud_transfer.core.errors import ScanError
from mml_cloud_transfer.core.models import Direction, JobStatus, PlannedFile
from mml_cloud_transfer.core.paths import default_drive_resolver, resolve_mapped_drive
from mml_cloud_transfer.core.scanner import iter_source
from mml_cloud_transfer.core.slicing import SizePolicy
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

_BATCH_SIZE = 5000

CSV_COLUMNS = [
    "relative_path",
    "object_name",
    "size_bytes",
    "mtime_ns",
    "method",
    "state",
]


@dataclass(slots=True)
class ScanOutcome:
    job_id: int
    file_count: int
    byte_count: int
    errors: list[ScanError] = field(default_factory=list)


def run_scan(
    *,
    db_path: str | os.PathLike[str],
    source_root: str,
    dest_prefix: str,
    job_name: str,
    job_id: int | None = None,
    csv_path: str | os.PathLike[str] | None = None,
    resolver: Callable[[str], str | None] = default_drive_resolver,
    follow_extended: bool = True,
    policy: SizePolicy | None = None,
) -> ScanOutcome:
    """Scan ``source_root`` into a job manifest, creating the job if needed."""
    root = resolve_mapped_drive(source_root, resolver)

    conn = connect(db_path)
    try:
        repo = JobRepository(conn)
        if job_id is None:
            job_id = repo.create_job(
                name=job_name,
                direction=Direction.UPLOAD,
                source_root=root,
                dest_prefix=dest_prefix,
            )
        else:
            # Fail fast with a clean LookupError rather than letting a bogus
            # id silently no-op set_job_status and then crash record_event
            # on the job_files/events foreign key.
            repo.get_job(job_id)

        repo.set_job_status(job_id, JobStatus.SCANNING)
        repo.record_event(job_id, "scan_started", f"root={root}")

        errors: list[ScanError] = []
        file_count = 0
        byte_count = 0
        batch: list[PlannedFile] = []

        for entry in iter_source(root, follow_extended=follow_extended):
            if isinstance(entry, ScanError):
                errors.append(entry)
                continue
            batch.append(entry)
            file_count += 1
            byte_count += entry.size_bytes
            if len(batch) >= _BATCH_SIZE:
                repo.add_planned_files(job_id, batch, policy=policy)
                batch.clear()

        if batch:
            repo.add_planned_files(job_id, batch, policy=policy)

        repo.record_event(
            job_id, "scan_finished", f"files={file_count} bytes={byte_count} errors={len(errors)}"
        )
        repo.set_job_status(job_id, JobStatus.PENDING)

        if csv_path is not None:
            _write_csv(repo, job_id, csv_path)

        return ScanOutcome(
            job_id=job_id, file_count=file_count, byte_count=byte_count, errors=errors
        )
    finally:
        conn.close()


def _write_csv(repo: JobRepository, job_id: int, csv_path: str | os.PathLike[str]) -> None:
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in repo.get_files(job_id):
            writer.writerow({column: row[column] for column in CSV_COLUMNS})
