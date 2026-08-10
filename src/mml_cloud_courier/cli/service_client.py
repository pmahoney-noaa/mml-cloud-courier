"""HTTP client for the service API — the CLI's transport when --service-url
is given, and the GUI's transport in Plan 5. SSE parsing is hand-rolled:
the stream carries one event type with a single data: line per event."""

from __future__ import annotations

import json
from collections.abc import Iterator

import requests


class ServiceError(Exception):
    """The API said no: carries the HTTP status and the server's detail."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class ApiClient:
    def __init__(
        self, base_url: str, token: str, *, session: requests.Session | None = None
    ):
        self._base = base_url.rstrip("/")
        self._session = session if session is not None else requests.Session()
        self._session.headers["Authorization"] = f"Bearer {token}"

    def _check(self, response):
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", "")
            except ValueError:
                detail = getattr(response, "text", "")
            raise ServiceError(response.status_code, str(detail))
        return response.json()

    def health(self) -> dict:
        return self._check(self._session.get(f"{self._base}/health", timeout=10))

    def submit_job(self, payload: dict) -> dict:
        """Full creation response: job_id, scheduled_start_at, profile_id,
        preflight_summary. (Returned an int before Phase 5.)"""
        response = self._session.post(f"{self._base}/jobs", json=payload, timeout=120)
        return self._check(response)

    def preview_remote(self, profile_id: int, prefix: str | None = None) -> dict:
        return self._check(self._session.post(
            f"{self._base}/profiles/{profile_id}/preview",
            json={"prefix": prefix}, timeout=300,
        ))

    def list_jobs(self, include_archived: bool = False) -> list[dict]:
        params = {"include_archived": "true"} if include_archived else None
        return self._check(
            self._session.get(f"{self._base}/jobs", params=params, timeout=30)
        )

    def get_job(self, job_id: int) -> dict:
        return self._check(
            self._session.get(f"{self._base}/jobs/{job_id}", timeout=30)
        )

    def pause(self, job_id: int) -> dict:
        return self._check(
            self._session.post(f"{self._base}/jobs/{job_id}/pause", timeout=30)
        )

    def archive_job(self, job_id: int) -> dict:
        return self._check(
            self._session.post(f"{self._base}/jobs/{job_id}/archive", timeout=30)
        )

    def unarchive_job(self, job_id: int) -> dict:
        return self._check(
            self._session.post(f"{self._base}/jobs/{job_id}/unarchive", timeout=30)
        )

    def resume(self, job_id: int) -> dict:
        return self._check(
            self._session.post(f"{self._base}/jobs/{job_id}/resume", timeout=30)
        )

    def cancel(self, job_id: int) -> dict:
        return self._check(
            self._session.post(f"{self._base}/jobs/{job_id}/cancel", timeout=30)
        )

    def report(self, job_id: int) -> dict:
        return self._check(
            self._session.post(f"{self._base}/jobs/{job_id}/report", timeout=600)
        )

    def files(
        self, job_id: int, *, state: str | None = None,
        category: str | None = None, limit: int = 500, offset: int = 0,
    ) -> list[dict]:
        params: dict = {"limit": limit, "offset": offset}
        if state is not None:
            params["state"] = state
        if category is not None:
            params["category"] = category
        return self._check(self._session.get(
            f"{self._base}/jobs/{job_id}/files", params=params, timeout=30
        ))

    def errors(self, job_id: int) -> list[dict]:
        return self._check(
            self._session.get(f"{self._base}/jobs/{job_id}/errors", timeout=30)
        )

    def retry_errors(self, job_id: int, category: str) -> dict:
        return self._check(self._session.post(
            f"{self._base}/jobs/{job_id}/errors/{category}/retry", timeout=30))

    def exclude_errors(self, job_id: int, category: str) -> dict:
        return self._check(self._session.post(
            f"{self._base}/jobs/{job_id}/errors/{category}/exclude", timeout=30))

    def events(self, job_id: int, after_id: int = 0) -> list[dict]:
        return self._check(
            self._session.get(
                f"{self._base}/jobs/{job_id}/events",
                params={"after_id": after_id}, timeout=30,
            )
        )

    def stream(self, job_id: int) -> Iterator[dict]:
        """Yield each SSE progress payload until the server closes the
        stream (which it does after a terminal tick)."""
        response = self._session.get(
            f"{self._base}/jobs/{job_id}/stream", stream=True, timeout=(10, 65)
        )
        if response.status_code >= 400:
            raise ServiceError(response.status_code, "stream refused")
        for line in response.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                yield json.loads(line[len("data:"):].strip())

    def create_profile(self, payload: dict) -> dict:
        return self._check(
            self._session.post(f"{self._base}/profiles", json=payload, timeout=120)
        )

    def list_profiles(self) -> list[dict]:
        return self._check(self._session.get(f"{self._base}/profiles", timeout=30))

    def check_profile(
        self, profile_id: int, *, direction: str | None = None, prefix: str | None = None
    ) -> dict:
        return self._check(
            self._session.post(
                f"{self._base}/profiles/{profile_id}/check",
                json={"direction": direction, "prefix": prefix}, timeout=120,
            )
        )

    def delete_profile(self, profile_id: int) -> dict:
        return self._check(
            self._session.delete(f"{self._base}/profiles/{profile_id}", timeout=30)
        )

    def get_settings(self) -> dict:
        return self._check(self._session.get(f"{self._base}/settings", timeout=30))

    def put_settings(self, payload: dict) -> dict:
        return self._check(
            self._session.put(f"{self._base}/settings", json=payload, timeout=30)
        )
