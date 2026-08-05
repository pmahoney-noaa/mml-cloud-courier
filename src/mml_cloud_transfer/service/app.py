"""The service REST API. Binds only through the host (127.0.0.1); every
route except /health requires the bearer token from the ACL-restricted
token file. Handlers are synchronous (FastAPI runs them on a threadpool)
and open a fresh SQLite connection per request — connections are
thread-bound and requests land on arbitrary pool threads."""

from __future__ import annotations

import importlib.metadata
import os
import secrets
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from mml_cloud_transfer.core.models import Direction, JobStatus
from mml_cloud_transfer.engine.report import write_report
from mml_cloud_transfer.service.config import ServiceConfig
from mml_cloud_transfer.service.controller import JobController
from mml_cloud_transfer.service.security import ensure_token
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

try:
    VERSION = importlib.metadata.version("mml-cloud-transfer")
except importlib.metadata.PackageNotFoundError:  # frozen/odd environments
    VERSION = "0"


class JobSubmission(BaseModel):
    name: str = Field(min_length=1)
    direction: Literal["upload", "download"]
    source_root: str = Field(min_length=1)
    dest_prefix: str = ""
    bucket: str = Field(min_length=1)
    credentials_path: str | None = None
    emulator_endpoint: str | None = None
    audit_hash: bool = False
    scheduled_start_at: str | None = None


def _normalize_schedule(text: str) -> str:
    """To the exact _now() format so SQL string comparison is ordering."""
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"scheduled_start_at is not ISO-8601: {text!r}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()  # naive input means local wall-clock time
    return parsed.astimezone(UTC).isoformat(timespec="seconds")


def _require_token(request: Request) -> None:
    header = request.headers.get("authorization", "")
    token = request.app.state.token
    if not header.startswith("Bearer ") or not secrets.compare_digest(
        header[len("Bearer "):], token
    ):
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")


def _row_dict(row) -> dict:
    return {key: row[key] for key in row.keys()}


def create_app(config: ServiceConfig, controller: JobController) -> FastAPI:
    app = FastAPI(title="MML Cloud Transfer", version=VERSION)
    app.state.config = config
    app.state.controller = controller
    app.state.token = ensure_token(config.token_path)

    router = APIRouter(dependencies=[Depends(_require_token)])

    def _open():
        conn = connect(config.db_path)
        return conn, JobRepository(conn)

    def _job_or_404(repo: JobRepository, job_id: int):
        try:
            return repo.get_job(job_id)
        except LookupError:
            raise HTTPException(
                status_code=404, detail=f"no job with id {job_id}"
            ) from None

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": VERSION,
            "active_job_id": controller.active_job_id,
        }

    @router.post("/jobs", status_code=201)
    def submit_job(submission: JobSubmission) -> dict:
        if submission.direction == "upload":
            if not os.path.isdir(submission.source_root):
                raise HTTPException(status_code=400, detail=(
                    "source folder not found or not readable by the service"
                    f" account: {submission.source_root}"
                ))
        else:
            # Spec: reachability is tested at creation, under the service's
            # own identity. Creating the destination folder IS that test.
            try:
                os.makedirs(submission.source_root, exist_ok=True)
            except OSError as exc:
                raise HTTPException(status_code=400, detail=(
                    f"destination folder cannot be created by the service: {exc}"
                )) from exc

        scheduled = (
            _normalize_schedule(submission.scheduled_start_at)
            if submission.scheduled_start_at else None
        )
        if submission.emulator_endpoint:
            auth_type, credential_ref = "emulator", submission.emulator_endpoint
        elif submission.credentials_path:
            auth_type, credential_ref = "key_file", submission.credentials_path
        else:
            auth_type, credential_ref = "adc", None

        conn, repo = _open()
        try:
            profile_id = repo.get_or_create_profile(
                bucket=submission.bucket, auth_type=auth_type,
                credential_ref=credential_ref,
            )
            job_id = repo.create_job(
                name=submission.name,
                direction=Direction(submission.direction),
                source_root=submission.source_root,
                dest_prefix=submission.dest_prefix,
                profile_id=profile_id,
                audit_hash=submission.audit_hash,
                scheduled_start_at=scheduled,
            )
            repo.record_event(
                job_id, "job_submitted", f"direction={submission.direction}"
            )
        finally:
            conn.close()
        return {"job_id": job_id, "scheduled_start_at": scheduled}

    @router.get("/jobs")
    def list_jobs() -> list[dict]:
        conn, repo = _open()
        try:
            return [_row_dict(row) for row in repo.list_jobs()]
        finally:
            conn.close()

    @router.get("/jobs/{job_id}")
    def get_job(job_id: int) -> dict:
        conn, repo = _open()
        try:
            job = _job_or_404(repo, job_id)
            return {**_row_dict(job), "progress": asdict(repo.job_progress(job_id))}
        finally:
            conn.close()

    @router.get("/jobs/{job_id}/files")
    def get_files(
        job_id: int, state: str | None = None, limit: int = 500, offset: int = 0
    ) -> list[dict]:
        limit = max(1, min(limit, 5000))
        conn, repo = _open()
        try:
            _job_or_404(repo, job_id)
            return [
                _row_dict(r)
                for r in repo.get_files_page(
                    job_id, state=state, limit=limit, offset=offset
                )
            ]
        finally:
            conn.close()

    @router.get("/jobs/{job_id}/events")
    def get_events(job_id: int, after_id: int = 0) -> list[dict]:
        conn, repo = _open()
        try:
            _job_or_404(repo, job_id)
            return [_row_dict(e) for e in repo.events_after(job_id, after_id)]
        finally:
            conn.close()

    @router.post("/jobs/{job_id}/pause")
    def pause_job(job_id: int) -> dict:
        conn, repo = _open()
        try:
            job = _job_or_404(repo, job_id)
            status = job["status"]
            if status in (JobStatus.RUNNING.value, JobStatus.SCANNING.value):
                if controller.request(job_id, "pause"):
                    return {"status": "stopping"}
                raise HTTPException(status_code=409, detail=(
                    "job is marked running or scanning but nothing is active;"
                    " restart the service to recover it"
                ))
            if status == JobStatus.STALLED.value:
                # Mid-stall the worker still owns the job; otherwise flip
                # the row directly and the stall loop notices and exits.
                if controller.request(job_id, "pause"):
                    return {"status": "stopping"}
                repo.set_job_status(job_id, JobStatus.PAUSED)
                repo.record_event(job_id, "paused_by_user")
                return {"status": JobStatus.PAUSED.value}
            if status == JobStatus.PENDING.value:
                repo.set_job_status(job_id, JobStatus.PAUSED)
                repo.record_event(job_id, "paused_by_user")
                return {"status": JobStatus.PAUSED.value}
            raise HTTPException(
                status_code=409, detail=f"cannot pause a {status} job"
            )
        finally:
            conn.close()

    @router.post("/jobs/{job_id}/resume")
    def resume_job(job_id: int) -> dict:
        resumable = {
            JobStatus.PAUSED.value, JobStatus.STALLED.value,
            JobStatus.INCOMPLETE.value, JobStatus.CANCELLED.value,
        }
        conn, repo = _open()
        try:
            job = _job_or_404(repo, job_id)
            if job["status"] not in resumable:
                raise HTTPException(
                    status_code=409, detail=f"cannot resume a {job['status']} job"
                )
            repo.set_job_status(job_id, JobStatus.PENDING)
            repo.record_event(job_id, "resumed_by_user")
            return {"status": JobStatus.PENDING.value}
        finally:
            conn.close()

    @router.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: int) -> dict:
        conn, repo = _open()
        try:
            job = _job_or_404(repo, job_id)
            status = job["status"]
            if status in (JobStatus.RUNNING.value, JobStatus.SCANNING.value):
                if controller.request(job_id, "cancel"):
                    return {"status": "stopping"}
                raise HTTPException(status_code=409, detail=(
                    "job is marked running or scanning but nothing is active;"
                    " restart the service to recover it"
                ))
            if status == JobStatus.STALLED.value and controller.request(
                job_id, "cancel"
            ):
                return {"status": "stopping"}
            if status in (
                JobStatus.PENDING.value, JobStatus.PAUSED.value,
                JobStatus.STALLED.value,
            ):
                repo.set_job_status(job_id, JobStatus.CANCELLED)
                repo.record_event(job_id, "cancelled_by_user")
                return {"status": JobStatus.CANCELLED.value}
            raise HTTPException(
                status_code=409, detail=f"cannot cancel a {status} job"
            )
        finally:
            conn.close()

    @router.post("/jobs/{job_id}/report")
    def make_report(job_id: int) -> dict:
        conn, repo = _open()
        try:
            job = _job_or_404(repo, job_id)
            bucket = None
            if job["profile_id"] is not None:
                bucket = repo.get_profile(job["profile_id"])["bucket"]
        finally:
            conn.close()
        paths = write_report(
            config.db_path, job_id,
            config.reports_dir / f"job-{job_id}", bucket=bucket,
        )
        return {
            "summary_json": str(paths.summary_json),
            "manifest_csv": str(paths.manifest_csv),
            "report_html": str(paths.report_html),
        }

    app.include_router(router)
    return app
