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

Two distinct failure modes live here, and conflating them is worse than
either alone: failing to even OPEN the lock file (permissions, a read-only
attribute, a scanner/backup agent transiently holding it) is not the same
as genuine contention for the lock itself, and a false "already running"
on an unattended overnight job silently loses the run. `JobLockUnavailable`
and `JobAlreadyRunning` share a base, `JobLockError`, so callers that only
care "did the lock stop me" can catch one type, while callers that care
"why" can tell the two apart.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from mml_cloud_transfer.core.paths import display_path, extended_path


class JobLockError(RuntimeError):
    """Base for anything that stops `job_run_lock` from being held for the
    duration of a run. Catch this to mean "this run did not get the lock,
    for any reason"; catch the subclasses to tell the reasons apart."""


class JobAlreadyRunning(JobLockError):
    """Raised when a job's run lock is already held by another process (or
    the lock file can be opened but not byte-range-locked at all, e.g. on a
    filesystem/redirector without lock support — see the message for why
    this can't be told apart from genuine contention)."""


class JobLockUnavailable(JobLockError):
    """Raised when the lock FILE itself cannot be opened — permissions, the
    read-only attribute, a directory sitting at the lock path — as opposed
    to `JobAlreadyRunning`, which means the file opened fine but the lock
    on it is held elsewhere. Distinct from contention on purpose: this is
    often a permanent, fixable condition (clear a read-only attribute) or a
    short-lived one (an AV/backup agent's own hold on the file), never "an
    operator needs to go find and kill another run of this job"."""


# Retries only the open() of the lock file, not the byte-range lock itself —
# genuine contention (another process already holds the lock) must still
# fail immediately; see job_run_lock's docstring. These delays absorb a
# transient AV on-demand scan or a backup/EDR agent's own brief, exclusive
# hold on the file (milliseconds to seconds) without waiting out anything
# that won't resolve on its own, like a permanent read-only attribute.
_OPEN_RETRY_DELAYS: tuple[float, ...] = (0.1, 0.2, 0.4, 0.8, 1.6)  # ~3s total


def _os_error_detail(exc: OSError) -> str:
    """`str(exc)` on an OSError from `open()` embeds the raw path it was
    given — which, here, is the `\\\\?\\`-prefixed extended-length form
    (see M6). The caller already names the lock path itself through
    `display_path`; this pulls out just errno/strerror so that internal
    form never rides along a second time inside the OS's own message."""
    if exc.strerror is not None and exc.errno is not None:
        return f"[Errno {exc.errno}] {exc.strerror}"
    return str(exc)


@contextmanager
def job_run_lock(
    db_path,
    job_id: int,
    *,
    open_retry_delays: tuple[float, ...] = _OPEN_RETRY_DELAYS,
) -> Iterator[None]:
    """Hold an exclusive, per-job OS file lock for the duration of the
    `with` block.

    Raises `JobLockUnavailable` if the lock FILE itself can never be
    opened (after brief retries — see below), and `JobAlreadyRunning` if
    the file opens but the byte-range lock on it is held elsewhere (or
    can't be taken at all, which looks identical to the OS — see that
    exception's docstring).

    `job_id` is coerced with `int()`: sqlite treats job id `3` and `3.0`
    as the same row, so an uncoerced float would silently name a
    *different* lock file than the id it supposedly locks. `int()` also
    rejects non-numeric junk outright.

    The lock file lives alongside the database, named
    ``f"{db_path.name}.job-{job_id}.lock"`` — so two different job ids on
    the same database do not contend, but two processes racing the same
    job id do. It is opened through `core.paths.extended_path`, matching
    how the rest of this codebase touches the filesystem, so the lock
    itself never re-introduces the 260-character path limit this product
    otherwise avoids; human-facing messages go through `display_path` so
    the `\\\\?\\` form never reaches an operator. Uses the same
    non-blocking byte-range lock (``msvcrt.locking(..., LK_NBLCK, 1)``) as
    the service's own instance lock in `service/host.py`; see that
    module's `_acquire_instance_lock` for the sibling implementation this
    mirrors.

    Opening the lock file is retried briefly (`open_retry_delays`, default
    ~3s total) because a transient holder — an on-demand AV scan or a
    backup/EDR agent sweeping the data directory — can make `open()` raise
    `PermissionError` for a moment that has nothing to do with another run
    of this job. This retry is for OPENING the file only: once open, the
    byte-range lock attempt itself is a single non-blocking call and never
    waits — contention must fail fast, since the caller (an operator
    retrying a job, or the service picking up a job) needs a fast, honest
    answer, not a hang.

    The lock file itself is never deleted: deleting it would race any other
    process currently waiting to acquire it (it could recreate and lock a
    *different* file than the one the waiter has open). A leftover empty
    lock file is harmless — it is reused, not recreated, on every future
    acquisition.
    """
    import msvcrt

    job_id = int(job_id)

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = db_path.parent / f"{db_path.name}.job-{job_id}.lock"
    lock_path_ext = extended_path(str(lock_path))
    lock_display = display_path(lock_path_ext)

    lock_file = None
    open_exc: OSError | None = None
    for delay in (0.0, *open_retry_delays):
        if delay:
            time.sleep(delay)
        try:
            lock_file = open(lock_path_ext, "a")
            break
        except OSError as exc:
            open_exc = exc
    if lock_file is None:
        raise JobLockUnavailable(
            f"cannot open the run lock for job {job_id} at {lock_display}:"
            f" {_os_error_detail(open_exc)}. If the file is read-only, clear"
            " it (attrib -R); if a scanner or backup agent is holding it,"
            " retry in a moment."
        ) from open_exc

    try:
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        lock_file.close()
        raise JobAlreadyRunning(
            f"could not acquire the run lock for job {job_id}"
            f" (lock: {lock_display}): {_os_error_detail(exc)}. Another"
            " process is probably running this job — if none is, the lock"
            " file may be unusable (read-only, or on a filesystem without"
            " byte-range locking); delete it, or move the database off the"
            " share."
        ) from exc

    try:
        yield
    finally:
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        lock_file.close()
