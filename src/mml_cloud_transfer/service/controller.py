"""Shared state between the API (which receives pause/cancel requests) and
the worker (which owns the running job). One job runs at a time, so one
active id, one stop event, one pending intent."""

from __future__ import annotations

import threading


class JobController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_job_id: int | None = None
        self._intent: str | None = None
        self._stop_event = threading.Event()
        self.service_stop = threading.Event()

    def job_started(self, job_id: int) -> threading.Event:
        """Arm a fresh stop event for this job; returns it for the run."""
        with self._lock:
            self._active_job_id = job_id
            self._intent = None
            self._stop_event = threading.Event()
            return self._stop_event

    def job_finished(self) -> str | None:
        """Clear the active job; return the pending intent, if any."""
        with self._lock:
            intent = self._intent
            self._intent = None
            self._active_job_id = None
            return intent

    def request(self, job_id: int, intent: str) -> bool:
        """Ask the active job to stop with this intent. False if not active."""
        with self._lock:
            if self._active_job_id != job_id:
                return False
            self._intent = intent
            self._stop_event.set()
            return True

    @property
    def active_job_id(self) -> int | None:
        with self._lock:
            return self._active_job_id
