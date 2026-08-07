"""The service REST API. Binds only through the host (127.0.0.1); every
route except /health requires the bearer token from the ACL-restricted
token file. Handlers are synchronous (FastAPI runs them on a threadpool)
and open a fresh SQLite connection per request — connections are
thread-bound and requests land on arbitrary pool threads."""

from __future__ import annotations

import getpass
import importlib.metadata
import os
import secrets
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from mml_cloud_transfer.auth.context import context_for_profile
from mml_cloud_transfer.auth.credential_store import CredentialStore
from mml_cloud_transfer.auth.preflight import run_preflight
from mml_cloud_transfer.core.errors import ErrorCategory, classify, describe
from mml_cloud_transfer.core.models import Direction, JobStatus
from mml_cloud_transfer.core.paths import display_path, extended_path, is_unc
from mml_cloud_transfer.engine.report import write_report
from mml_cloud_transfer.gcs.client import make_context
from mml_cloud_transfer.service.config import ServiceConfig
from mml_cloud_transfer.service.controller import JobController
from mml_cloud_transfer.service.security import ensure_token
from mml_cloud_transfer.service.sse import progress_events
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository, ProfileInUse

try:
    VERSION = importlib.metadata.version("mml-cloud-transfer")
except importlib.metadata.PackageNotFoundError:  # frozen/odd environments
    VERSION = "0"


class JobSubmission(BaseModel):
    name: str = Field(min_length=1)
    direction: Literal["upload", "download"]
    source_root: str = Field(min_length=1)
    dest_prefix: str = ""
    profile: str | None = None
    bucket: str | None = Field(default=None, min_length=1)
    credentials_path: str | None = None
    emulator_endpoint: str | None = None
    audit_hash: bool = False
    scheduled_start_at: str | None = None


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1)
    bucket: str = Field(min_length=1)
    auth_type: Literal["service_account_key", "oauth_user"]
    credential: dict
    project_id: str = ""
    default_prefix: str = ""
    emulator_endpoint: str | None = None  # tests only, like JobSubmission's


class ProfileCheck(BaseModel):
    direction: Literal["upload", "download"] | None = None
    prefix: str | None = None


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
        header[len("Bearer "):].encode("utf-8"), token.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")


def _row_dict(row) -> dict:
    return {key: row[key] for key in row.keys()}


def _service_identity() -> str:
    try:
        domain = os.environ.get("USERDOMAIN", "")
        user = getpass.getuser()
        return f"{domain}\\{user}" if domain else user
    except Exception:  # session-0 oddities must not break an error message
        return "the service account"


def _unreachable_detail(root: str) -> str:
    where = display_path(root)
    base = f"{where} is not reachable by the service (running as {_service_identity()})"
    if is_unc(root):
        return base + (
            ": check the share path and that this account has been granted"
            " access; folders on a per-user VPN are not visible to the service"
        )
    drive = root[:2] + "\\" if len(root) >= 2 and root[1] == ":" else None
    if drive is not None and not os.path.exists(drive):
        return base + (
            f": drive {root[:2]} does not exist for the service — if it is a"
            " mapped drive, use the full \\\\server\\share form (the CLI and"
            " GUI resolve mapped drives automatically)"
        )
    return f"source folder not found or not readable by the service account: {where}"


def create_app(
    config: ServiceConfig,
    controller: JobController,
    *,
    preflight_fn=run_preflight,
) -> FastAPI:
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

    def _profile_dict(row) -> dict:
        data = _row_dict(row)
        data.pop("credential_ref", None)  # an internal filename, not API surface
        return data

    def _profile_or_404(repo: JobRepository, profile_id: int):
        try:
            return repo.get_profile(profile_id)
        except LookupError:
            raise HTTPException(
                status_code=404, detail=f"no profile with id {profile_id}"
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
        root = submission.source_root
        if not os.path.isabs(root) or root.startswith("\\\\.\\"):
            raise HTTPException(status_code=400, detail=(
                "source_root must be an absolute path (drive or UNC),"
                f" not {root!r}"
            ))
        if submission.direction == "upload":
            if not os.path.isdir(extended_path(root)):
                raise HTTPException(status_code=400, detail=_unreachable_detail(root))
        else:
            # Spec: reachability is tested at creation, under the service's
            # own identity. Creating the destination folder IS that test —
            # via the extended path so >260-char trees work.
            try:
                os.makedirs(extended_path(root), exist_ok=True)
            except OSError as exc:
                raise HTTPException(status_code=400, detail=(
                    f"{_unreachable_detail(root)} ({exc.strerror or exc})"
                )) from exc

        scheduled = (
            _normalize_schedule(submission.scheduled_start_at)
            if submission.scheduled_start_at else None
        )

        if (submission.profile is None) == (submission.bucket is None):
            raise HTTPException(
                status_code=422,
                detail="provide exactly one of 'profile' or 'bucket'",
            )

        preflight_summary = None
        if submission.profile is not None:
            conn, repo = _open()
            try:
                row = repo.find_profile_by_name(submission.profile)
            finally:
                conn.close()
            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"no profile named {submission.profile!r}",
                )
            profile_id = int(row["id"])
            bucket = row["bucket"]
            dest_prefix = submission.dest_prefix or row["default_prefix"]
            # The spec's per-profile preflight, appropriate to the job
            # direction, at the one moment a human is present to see it.
            try:
                ctx = context_for_profile(
                    row, CredentialStore(config.credentials_dir)
                )
            except Exception as exc:
                reason = str(exc) if isinstance(exc, ValueError) else classify(exc).message
                raise HTTPException(status_code=400, detail=(
                    f"stored credential could not be loaded: {reason}"
                )) from exc
            result = preflight_fn(ctx, dest_prefix)
            if not result.ok_for(Direction(submission.direction)):
                raise HTTPException(status_code=400, detail=result.summary())
            preflight_summary = result.summary()
            conn, repo = _open()
            try:
                repo.set_profile_validated(profile_id)
            finally:
                conn.close()
        else:
            profile_id = None
            bucket = submission.bucket
            dest_prefix = submission.dest_prefix

        conn, repo = _open()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                duplicate = repo.find_active_duplicate(
                    source_root=submission.source_root,
                    dest_prefix=dest_prefix, bucket=bucket,
                )
                if duplicate is not None:
                    raise HTTPException(status_code=409, detail=(
                        f"job {duplicate['id']} ({duplicate['status']}) already"
                        f" transfers this source to"
                        f" gs://{bucket}/{dest_prefix or ''} — resume it"
                        f" (mmlct resume --job-id {duplicate['id']}) or cancel"
                        " it instead of creating a second writer"
                    ))
                if profile_id is None:
                    if submission.emulator_endpoint:
                        auth_type, credential_ref = "emulator", submission.emulator_endpoint
                    elif submission.credentials_path:
                        auth_type, credential_ref = "key_file", submission.credentials_path
                    else:
                        auth_type, credential_ref = "adc", None
                    profile_id = repo.get_or_create_profile(
                        bucket=bucket, auth_type=auth_type,
                        credential_ref=credential_ref,
                    )
                job_id = repo.create_job(
                    name=submission.name,
                    direction=Direction(submission.direction),
                    source_root=submission.source_root,
                    dest_prefix=dest_prefix,
                    profile_id=profile_id,
                    audit_hash=submission.audit_hash,
                    scheduled_start_at=scheduled,
                )
                repo.record_event(
                    job_id, "job_submitted", f"direction={submission.direction}"
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
        return {
            "job_id": job_id,
            "scheduled_start_at": scheduled,
            "profile_id": profile_id,
            "preflight_summary": preflight_summary,
        }

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
        job_id: int, state: str | None = None, category: str | None = None,
        limit: int = 500, offset: int = 0,
    ) -> list[dict]:
        limit = max(1, min(limit, 5000))
        conn, repo = _open()
        try:
            _job_or_404(repo, job_id)
            return [
                _row_dict(r)
                for r in repo.get_files_page(
                    job_id, state=state, category=category, limit=limit, offset=offset
                )
            ]
        finally:
            conn.close()

    @router.get("/jobs/{job_id}/errors")
    def get_error_groups(job_id: int) -> list[dict]:
        conn, repo = _open()
        try:
            _job_or_404(repo, job_id)
            groups = repo.error_groups(job_id)
        finally:
            conn.close()
        out = []
        for group in groups:
            try:
                info = describe(ErrorCategory(group["category"]))
            except ValueError:  # a category this build no longer knows
                info = describe(ErrorCategory.UNKNOWN)
            out.append({**group, "message": info.message, "action": info.action})
        return out

    _ERROR_ACTION_BLOCKED = (
        JobStatus.RUNNING.value, JobStatus.SCANNING.value, JobStatus.STALLED.value,
    )

    def _error_action(job_id: int, category: str, action) -> dict:
        if category not in {c.value for c in ErrorCategory}:
            raise HTTPException(status_code=422,
                                detail=f"unknown error category {category!r}")
        conn, repo = _open()
        try:
            job = _job_or_404(repo, job_id)
            if job["status"] in _ERROR_ACTION_BLOCKED:
                raise HTTPException(status_code=409, detail=(
                    f"cannot modify files while the job is {job['status']} —"
                    " pause it first"
                ))
            # Guard against TOCTOU race with worker job pickup: a PENDING job
            # can be claimed by the worker (marked active in the controller)
            # before we run the UPDATE, allowing the worker to process a
            # stale snapshot. The tiny residual window between _pick and
            # job_started is accepted (same as pause/cancel do).
            if controller.active_job_id == job_id:
                raise HTTPException(status_code=409, detail=(
                    "cannot modify files while the job is being processed —"
                    " pause it first"
                ))
            count, kind = action(repo)
            if count:
                repo.record_event(job_id, kind, f"{category}: {count} file(s)")
        finally:
            conn.close()
        return {"count": count}

    @router.post("/jobs/{job_id}/errors/{category}/retry")
    def retry_error_group(job_id: int, category: str) -> dict:
        return _error_action(job_id, category, lambda repo: (
            repo.retry_files(job_id, category), "files_retried"))

    @router.post("/jobs/{job_id}/errors/{category}/exclude")
    def exclude_error_group(job_id: int, category: str) -> dict:
        return _error_action(job_id, category, lambda repo: (
            repo.exclude_files(job_id, category), "files_excluded"))

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
                # A pause can land in the worker's window between _pick and
                # job_started — try the controller first so a job that has
                # already been claimed stops cleanly instead of racing a
                # direct DB flip the worker would silently overwrite.
                if controller.request(job_id, "pause"):
                    return {"status": "stopping"}
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
            if status in (
                JobStatus.PENDING.value, JobStatus.PAUSED.value,
                JobStatus.STALLED.value,
            ):
                # Same race as pause: try the controller first (covers a
                # STALLED job the worker still owns, and a PENDING job
                # claimed but not yet marked active) before flipping the
                # row directly.
                if controller.request(job_id, "cancel"):
                    return {"status": "stopping"}
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

    @router.get("/jobs/{job_id}/stream")
    def stream_job(job_id: int) -> StreamingResponse:
        conn, repo = _open()
        try:
            _job_or_404(repo, job_id)
        finally:
            conn.close()
        return StreamingResponse(
            progress_events(config.db_path, job_id, interval=config.sse_interval),
            media_type="text/event-stream",
        )

    @router.post("/profiles", status_code=201)
    def create_profile(body: ProfileCreate) -> dict:
        expected = (
            "service_account" if body.auth_type == "service_account_key"
            else "authorized_user"
        )
        if body.emulator_endpoint is None and body.credential.get("type") != expected:
            raise HTTPException(status_code=422, detail=(
                f"credential JSON has type {body.credential.get('type')!r};"
                f" a {body.auth_type} profile needs {expected!r}"
            ))

        # Validate by real operations against the target bucket BEFORE
        # anything is stored (spec: a wrong key or missing IAM grant
        # surfaces during setup rather than overnight).
        try:
            if body.emulator_endpoint:
                ctx = make_context(
                    body.bucket, emulator_endpoint=body.emulator_endpoint
                )
            else:
                ctx = make_context(
                    body.bucket,
                    credentials_info=body.credential,
                    project=body.project_id or None,
                )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=(
                f"credential rejected before reaching the bucket:"
                f" {classify(exc).message}"
            )) from exc
        result = preflight_fn(ctx, body.default_prefix)
        if not (result.can_list and result.can_read):
            raise HTTPException(status_code=400, detail=result.summary())

        if body.emulator_endpoint:
            auth_type, credential_ref, store = "emulator", body.emulator_endpoint, None
        else:
            store = CredentialStore(config.credentials_dir)
            auth_type, credential_ref = body.auth_type, store.save(body.credential)

        conn, repo = _open()
        try:
            try:
                profile_id = repo.create_profile(
                    name=body.name, bucket=body.bucket, auth_type=auth_type,
                    credential_ref=credential_ref, project_id=body.project_id,
                    default_prefix=body.default_prefix,
                )
            except sqlite3.IntegrityError:
                if store is not None:
                    store.delete(credential_ref)
                raise HTTPException(status_code=409, detail=(
                    f"a profile named {body.name!r} already exists"
                )) from None
            repo.set_profile_validated(profile_id)
            row = repo.get_profile(profile_id)
        finally:
            conn.close()
        return {
            **_profile_dict(row),
            "preflight": asdict(result),
            "summary": result.summary(),
        }

    @router.get("/profiles")
    def list_profiles() -> list[dict]:
        conn, repo = _open()
        try:
            return [_profile_dict(row) for row in repo.list_profiles()]
        finally:
            conn.close()

    @router.post("/profiles/{profile_id}/check")
    def check_profile(profile_id: int, body: ProfileCheck) -> dict:
        conn, repo = _open()
        try:
            row = _profile_or_404(repo, profile_id)
        finally:
            conn.close()
        try:
            ctx = context_for_profile(row, CredentialStore(config.credentials_dir))
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ValueError) else classify(exc).message
            raise HTTPException(status_code=400, detail=(
                f"stored credential could not be loaded: {reason}"
            )) from exc
        prefix = body.prefix if body.prefix is not None else row["default_prefix"]
        result = preflight_fn(ctx, prefix)
        if body.direction is not None:
            ok = result.ok_for(Direction(body.direction))
        else:
            ok = result.can_list and result.can_read
        if ok:
            conn, repo = _open()
            try:
                repo.set_profile_validated(profile_id)
            finally:
                conn.close()
        return {"ok": ok, "summary": result.summary(), "preflight": asdict(result)}

    @router.delete("/profiles/{profile_id}")
    def delete_profile(profile_id: int) -> dict:
        conn, repo = _open()
        try:
            row = _profile_or_404(repo, profile_id)
            try:
                repo.delete_profile(profile_id)
            except ProfileInUse as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
        finally:
            conn.close()
        # Blob removal AFTER the row is gone: an in-use profile must keep
        # its credential. delete() is idempotent, so a crash between the
        # two leaves only an orphaned encrypted blob, never a broken profile.
        if row["auth_type"] in ("service_account_key", "oauth_user") and row["credential_ref"]:
            CredentialStore(config.credentials_dir).delete(row["credential_ref"])
        return {"deleted": profile_id}

    app.include_router(router)
    return app
