"""The FIFO job worker: one job at a time, scan-if-needed, run, report.

The worker owns no transfer logic — it wires engine.runner.run_job to the
queue, the controller, and the report writer. Task 9 adds startup recovery
and the stalled slow-retry loop.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from mml_cloud_transfer.cli.scan_command import run_scan
from mml_cloud_transfer.core.errors import ErrorCategory, classify
from mml_cloud_transfer.core.models import Direction, JobStatus
from mml_cloud_transfer.engine.report import write_report
from mml_cloud_transfer.engine.runner import EngineOptions, run_job, scan_remote
from mml_cloud_transfer.gcs.client import make_context
from mml_cloud_transfer.gcs.objects import get_meta
from mml_cloud_transfer.service.config import ServiceConfig
from mml_cloud_transfer.service.controller import JobController
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

_log = logging.getLogger(__name__)

_INTENTS = {
    "pause": (JobStatus.PAUSED, "paused_by_user"),
    "cancel": (JobStatus.CANCELLED, "cancelled_by_user"),
}


def _now() -> str:
    """Must match store.repository._now — schedules compare as strings."""
    return datetime.now(UTC).isoformat(timespec="seconds")


class QueueWorker:
    def __init__(
        self,
        config: ServiceConfig,
        controller: JobController,
        *,
        make_context_fn=make_context,
        run_job_fn=run_job,
        run_scan_fn=run_scan,
        scan_remote_fn=scan_remote,
        write_report_fn=write_report,
        get_meta_fn=get_meta,
        sleep=time.sleep,
        now=_now,
    ) -> None:
        self._config = config
        self._controller = controller
        self._make_context = make_context_fn
        self._run_job = run_job_fn
        self._run_scan = run_scan_fn
        self._scan_remote = scan_remote_fn
        self._write_report = write_report_fn
        self._get_meta = get_meta_fn
        self._sleep = sleep
        self._now = now

    # ---- startup recovery -------------------------------------------------

    def startup_recovery(self) -> None:
        """Jobs left running/stalled/scanning by a dead service become
        pending (or paused when auto-resume is off). Zero staleness on the
        heartbeat reset is correct here and only here: at startup, no
        transfer can possibly be in flight.

        SCANNING is recovered too (a controller amendment to the original
        plan): a service killed mid-scan leaves the job stuck there, and
        the queue only ever selects PENDING jobs, so without this it would
        sit unpicked forever. This is safe because of the Task 8
        rescan-for-safety rule: any job with started_at IS NULL is rescanned
        at next pickup, and add_planned_files is INSERT OR IGNORE, so
        re-walking the partial manifest is idempotent.
        """
        conn = connect(self._config.db_path)
        try:
            repo = JobRepository(conn)
            for status in (JobStatus.RUNNING, JobStatus.STALLED, JobStatus.SCANNING):
                for job in repo.jobs_with_status(status):
                    repo.reset_stale_transfers(job["id"], stale_after_seconds=0)
                    if self._config.auto_resume_on_startup:
                        repo.set_job_status(job["id"], JobStatus.PENDING)
                        repo.record_event(
                            job["id"], "recovered_at_startup", "auto-resume"
                        )
                    else:
                        repo.set_job_status(job["id"], JobStatus.PAUSED)
                        repo.record_event(
                            job["id"], "recovered_at_startup",
                            "paused (auto-resume off)",
                        )
        finally:
            conn.close()

    # ---- loop -----------------------------------------------------------

    def run_forever(self) -> None:
        """Loop `run_once()` until stopped. The survivability boundary: a
        queue-level failure (e.g. a transient sqlite3.OperationalError from
        `_pick`/`_apply_intent`) must not end the worker thread for the rest
        of the service's life, so it is logged and the loop continues."""
        while not self._controller.service_stop.is_set():
            try:
                worked = self.run_once()
            except Exception:
                _log.exception("queue worker iteration failed")
                worked = False
            if not worked:
                self._sleep(self._config.poll_interval)

    def run_once(self) -> bool:
        """Pick up and fully handle at most one job.

        Exceptions inside job handling are contained per-job (see
        `_handle`, which pauses the job and records a `worker_error` event
        instead of raising). Queue-level errors — opening the DB, picking
        the next eligible job, applying a pause/cancel intent — propagate
        to the caller; `run_forever` is the survivability boundary that
        contains those.
        """
        picked = self._pick()
        if picked is None:
            return False
        job, profile = picked
        stop_event = self._controller.job_started(job["id"])
        try:
            self._handle(job, profile, stop_event)
        finally:
            intent = self._controller.job_finished()
            self._apply_intent(job["id"], intent)
        return True

    # ---- steps ----------------------------------------------------------

    def _pick(self):
        conn = connect(self._config.db_path)
        try:
            repo = JobRepository(conn)
            job = repo.next_eligible_job(self._now())
            if job is None:
                return None
            if job["profile_id"] is None:
                repo.set_job_status(job["id"], JobStatus.PAUSED)
                repo.record_event(
                    job["id"], "worker_error", "job has no connection profile"
                )
                return dict(job), None
            return dict(job), dict(repo.get_profile(job["profile_id"]))
        finally:
            conn.close()

    def _handle(self, job, profile, stop_event) -> None:
        job_id = job["id"]
        # A pause/cancel can land in the window between _pick (job read as
        # PENDING) and this call (controller.job_started marks it active).
        # If the app flipped the row in that window, the flip must win —
        # re-read before doing anything the flip would otherwise be
        # silently overwritten by.
        conn = connect(self._config.db_path)
        try:
            if JobRepository(conn).get_job(job_id)["status"] != JobStatus.PENDING.value:
                return  # flipped (paused/cancelled) between pickup and start
        finally:
            conn.close()
        if stop_event.is_set() or self._controller.service_stop.is_set():
            return
        if profile is None:
            return  # _pick already paused it
        try:
            ctx = self._context(profile)
            # Deliberately NOT `planned_files == 0 and started_at is None`:
            # a scan interrupted mid-flight (service killed while SCANNING)
            # can leave a PARTIAL manifest with planned_files > 0, and that
            # condition would then skip the rescan — letting the job reach a
            # COMPLETE verdict over a partial manifest. A job that has never
            # started transferring is rescanned even if a manifest already
            # exists; add_planned_files is INSERT OR IGNORE, so rescanning
            # is idempotent. In the normal path this costs nothing: jobs are
            # scanned at pickup anyway, and run_job stamps started_at
            # immediately, so resumes never rescan.
            if job["started_at"] is None:
                if Direction(job["direction"]) is Direction.UPLOAD:
                    self._run_scan(
                        db_path=self._config.db_path,
                        source_root=job["source_root"],
                        dest_prefix=job["dest_prefix"],
                        job_name=job["name"],
                        job_id=job_id,
                        policy=self._config.size_policy,
                    )
                else:
                    self._scan_remote(
                        ctx, self._config.db_path, job_id,
                        policy=self._config.size_policy,
                    )
            if stop_event.is_set() or self._controller.service_stop.is_set():
                return
            status = self._run_job(
                self._config.db_path, job_id, ctx,
                options=self._options(stop_event),
            )
        except Exception as exc:  # a worker crash must not kill the service
            self._record_failure(job_id, exc)
            return

        # Outside the guard above on purpose: by this point run_job has
        # already committed a real COMPLETE/INCOMPLETE/PAUSED outcome. A
        # report-write failure (disk full, permissions) here must not be
        # allowed to fall into _record_failure and downgrade that verified
        # outcome to PAUSED — the job would silently lose a real result and
        # never get auto-retried. Surface it as an event only.
        #
        # The stalled check sits between the run and the report: a run that
        # ended INCOMPLETE on sustained network failure is parked (not
        # reported — it isn't done, it will re-run once the bucket answers).
        if (
            status is JobStatus.INCOMPLETE
            and self._network_failed(job_id)
            and not self._probe(ctx)
        ):
            self._stall(job_id, ctx, stop_event)
            return

        if status in (JobStatus.COMPLETE, JobStatus.INCOMPLETE, JobStatus.PAUSED):
            try:
                self._report(job_id, profile)
            except Exception as exc:
                self._record_report_failure(job_id, exc)

    def _context(self, profile):
        auth_type = profile["auth_type"]
        if auth_type == "emulator":
            return self._make_context(
                profile["bucket"], emulator_endpoint=profile["credential_ref"]
            )
        if auth_type == "key_file":
            return self._make_context(
                profile["bucket"], credentials_path=profile["credential_ref"]
            )
        return self._make_context(profile["bucket"])

    def _options(self, stop_event) -> EngineOptions:
        return EngineOptions(
            policy=self._config.size_policy,
            file_workers=self._config.file_workers,
            should_stop=lambda: (
                stop_event.is_set() or self._controller.service_stop.is_set()
            ),
        )

    def _report(self, job_id: int, profile) -> None:
        self._write_report(
            self._config.db_path, job_id,
            self._config.reports_dir / f"job-{job_id}",
            bucket=profile["bucket"],
        )

    # ---- stalled ----------------------------------------------------------

    def _network_failed(self, job_id: int) -> bool:
        conn = connect(self._config.db_path)
        try:
            repo = JobRepository(conn)
            return repo.count_failures(job_id, ErrorCategory.NETWORK) > 0
        finally:
            conn.close()

    def _probe(self, ctx) -> bool:
        """Can we reach the bucket at all? A 404 (get_meta -> None) is a
        SUCCESSFUL probe — the server answered. Only network-class failures
        mean unreachable; e.g. a credential failure is 'reachable', so the
        re-run escalates it properly (the job pauses)."""
        try:
            self._get_meta(ctx, "mmlct-connectivity-probe")
        except Exception as exc:
            return classify(exc).category is not ErrorCategory.NETWORK
        return True

    def _stall(self, job_id: int, ctx, stop_event) -> None:
        conn = connect(self._config.db_path)
        try:
            repo = JobRepository(conn)
            repo.set_job_status(job_id, JobStatus.STALLED)
            repo.record_event(
                job_id, "job_stalled",
                "sustained network failure; probing on slow cadence",
            )
        finally:
            conn.close()
        while not (stop_event.is_set() or self._controller.service_stop.is_set()):
            self._stall_wait(stop_event)
            if stop_event.is_set() or self._controller.service_stop.is_set():
                return  # intent (if any) is applied by run_once's finally
            conn = connect(self._config.db_path)
            try:
                repo = JobRepository(conn)
                if repo.get_job(job_id)["status"] != JobStatus.STALLED.value:
                    return  # paused/cancelled externally while we slept
                if self._probe(ctx):
                    repo.set_job_status(job_id, JobStatus.PENDING)
                    repo.record_event(
                        job_id, "job_unstalled", "network is back; re-queued"
                    )
                    return
            finally:
                conn.close()

    def _stall_wait(self, stop_event) -> None:
        """Sleep stall_probe_interval in small steps so a stop request
        interrupts within ~a second instead of a full probe interval."""
        waited = 0.0
        step = min(1.0, self._config.stall_probe_interval)
        while waited < self._config.stall_probe_interval:
            if stop_event.is_set() or self._controller.service_stop.is_set():
                return
            self._sleep(step)
            waited += step

    def _apply_intent(self, job_id: int, intent: str | None) -> None:
        if intent not in _INTENTS:
            return
        target, event = _INTENTS[intent]
        conn = connect(self._config.db_path)
        try:
            repo = JobRepository(conn)
            # Only a run that actually stopped (engine put it back to
            # `pending`, or Task 9 left it `stalled`) takes the intent; a
            # run that finished first must keep its real outcome.
            if repo.get_job(job_id)["status"] in (
                JobStatus.PENDING.value, JobStatus.STALLED.value
            ):
                repo.set_job_status(job_id, target)
                repo.record_event(job_id, event)
        finally:
            conn.close()

    def _record_failure(self, job_id: int, exc: Exception) -> None:
        conn = connect(self._config.db_path)
        try:
            repo = JobRepository(conn)
            repo.set_job_status(job_id, JobStatus.PAUSED)  # needs attention
            repo.record_event(job_id, "worker_error", str(exc)[:500])
        finally:
            conn.close()

    def _record_report_failure(self, job_id: int, exc: Exception) -> None:
        """No status change: the run's outcome already committed and is
        real. Only the report artifact failed to write."""
        conn = connect(self._config.db_path)
        try:
            JobRepository(conn).record_event(job_id, "report_error", str(exc)[:500])
        finally:
            conn.close()
