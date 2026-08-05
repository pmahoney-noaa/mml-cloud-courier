"""One process, three threads: uvicorn, the queue worker, and the caller.

The Windows Service wrapper and `python -m mml_cloud_transfer.service`
both drive ServiceHost; tests run it in-process on an ephemeral port.
"""

from __future__ import annotations

import logging
import threading
import time

import uvicorn

from mml_cloud_transfer.service.app import create_app
from mml_cloud_transfer.service.config import ServiceConfig
from mml_cloud_transfer.service.controller import JobController
from mml_cloud_transfer.service.worker import QueueWorker

_log = logging.getLogger(__name__)


class ServiceHost:
    def __init__(self, config: ServiceConfig) -> None:
        self.config = config
        self.controller = JobController()
        self.app = create_app(config, self.controller)
        self.worker = QueueWorker(config, self.controller)
        self._server = uvicorn.Server(
            uvicorn.Config(
                self.app, host=config.host, port=config.port, log_level="warning"
            )
        )
        self.threads: list[threading.Thread] = []
        self._lock_file = None

    def _acquire_instance_lock(self) -> None:
        """One service instance per data directory. The byte-range lock (not
        file existence) is the lock, so the OS releases it on process death
        — a crashed service never leaves a stale lock behind."""
        import msvcrt
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock_file = open(self.config.data_dir / "service.lock", "a")
        try:
            msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            self._lock_file.close()
            self._lock_file = None
            raise RuntimeError(
                f"another MML Cloud Transfer service already owns"
                f" {self.config.data_dir} — refusing to start a second instance"
            ) from exc

    def start(self) -> None:
        """Recovery first, then worker and API threads. Non-blocking."""
        self._acquire_instance_lock()
        self.worker.startup_recovery()
        self.threads = [
            threading.Thread(
                target=self.worker.run_forever, name="mmlct-worker", daemon=True
            ),
            threading.Thread(
                target=self._server.run, name="mmlct-api", daemon=True
            ),
        ]
        for thread in self.threads:
            thread.start()

    def wait_ready(self, timeout: float = 15.0) -> None:
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"API server did not start on {self.config.base_url}"
                    " (port in use?)"
                )
            time.sleep(0.05)

    def stop(self, timeout: float = 30.0) -> bool:
        """Graceful: the running job winds down via should_stop within one
        chunk and lands on the recovery path. Safe to call twice.

        An incomplete stop is survivable — the threads are daemons and
        startup recovery self-heals on the next launch — but the caller is
        told the truth rather than being left to assume a clean shutdown.
        Returns True if every thread joined within `timeout`, else False.
        """
        self.controller.service_stop.set()
        self._server.should_exit = True
        for thread in self.threads:
            thread.join(timeout=timeout)
        stragglers = [t.name for t in self.threads if t.is_alive()]
        if self._lock_file is not None:
            import msvcrt
            try:
                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            self._lock_file.close()
            self._lock_file = None
        if stragglers:
            _log.warning(
                "service stop incomplete: %s still alive after %.0fs",
                ", ".join(stragglers), timeout,
            )
            return False
        return True


def run_console(config: ServiceConfig) -> None:
    host = ServiceHost(config)
    host.start()
    host.wait_ready()
    print(f"MML Cloud Transfer service on {config.base_url} (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        host.stop()
