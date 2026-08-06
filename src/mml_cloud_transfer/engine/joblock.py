"""Per-job run lock: stops two concurrent `run_job` invocations for the
same job from both claiming and driving the same files.

Why an OS file lock rather than a database row or a PID file: this product
expects a killed transfer as a normal, unremarkable event — an operator
kills a transfer they believe has hung, a service process is terminated,
a machine loses power mid-job — and multi-terabyte jobs can run unattended
for days, so there is often nobody around to notice a crash and clean up
after it. A DB-row lock or a PID file would need exactly that cleanup: the
row/file survives the crash and has to be recognized as stale and removed
before the job can run again. An OS-held byte-range lock has no such gap.
The lock lives on a file handle that only the operating system can release,
and it does release it — automatically, unconditionally — the instant the
holding process exits, for any reason, including SIGKILL, a Windows
`taskkill /F`, or a crash. So the lock can never be stranded by the very
failure mode this tool is built to survive: the next `run_job` for that
job simply acquires it, no recovery step required.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class JobAlreadyRunning(RuntimeError):
    """Raised when a job's run lock is already held by another process."""


@contextmanager
def job_run_lock(db_path, job_id: int) -> Iterator[None]:
    """Hold an exclusive, per-job OS file lock for the duration of the
    `with` block; raise `JobAlreadyRunning` if another process already
    holds it for this job.

    The lock file lives alongside the database, named
    ``f"{db_path.name}.job-{job_id}.lock"`` — so two different job ids on
    the same database do not contend, but two processes racing the same
    job id do. Uses the same non-blocking byte-range lock
    (``msvcrt.locking(..., LK_NBLCK, 1)``) as the service's own instance
    lock in `service/host.py`; see that module's `_acquire_instance_lock`
    for the sibling implementation this mirrors.

    The lock file itself is never deleted: deleting it would race any other
    process currently waiting to acquire it (it could recreate and lock a
    *different* file than the one the waiter has open). A leftover empty
    lock file is harmless — it is reused, not recreated, on every future
    acquisition.
    """
    import msvcrt

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = db_path.parent / f"{db_path.name}.job-{job_id}.lock"

    lock_file = open(lock_path, "a")
    try:
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        lock_file.close()
        raise JobAlreadyRunning(
            f"job {job_id} is already running in another process"
            f" (lock: {lock_path}). If that process is gone, retry."
        ) from exc

    try:
        yield
    finally:
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        lock_file.close()
