"""Qt signal bridges over daemon threads.

Plain daemon threading.Thread, NOT QThread: a blocked HTTP read cannot
then hang process exit, and Qt delivers cross-thread signal emissions to
the receiver's loop via queued connections. Callers must parent every
Bridge (a collected bridge drops its emission silently).
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from mml_cloud_transfer.gui.watcher import poll_loop, watch_job


class Bridge(QObject):
    done = Signal(object)
    failed = Signal(str)


def call_async(fn: Callable, *, parent, on_done=None, on_failed=None) -> Bridge:
    bridge = Bridge(parent)
    if on_done is not None:
        bridge.done.connect(on_done)
    if on_failed is not None:
        bridge.failed.connect(on_failed)

    def runner():
        try:
            result = fn()
        except Exception as exc:
            bridge.failed.emit(str(exc))
            return
        bridge.done.emit(result)

    threading.Thread(target=runner, daemon=True, name="mmlct-gui-call").start()
    return bridge


class JobWatcher(QObject):
    snapshot = Signal(dict)
    state = Signal(str)
    settled = Signal(object)   # final job dict, or None

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, client, job_id: int) -> None:
        self.stop()
        self._stop = threading.Event()
        stop_event = self._stop

        def runner():
            final = watch_job(
                client, job_id, stop=stop_event.is_set,
                on_snapshot=self.snapshot.emit, on_state=self.state.emit,
            )
            if not stop_event.is_set():
                self.settled.emit(final)

        self._thread = threading.Thread(
            target=runner, daemon=True, name=f"mmlct-gui-watch-{job_id}"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


class JobsPoller(QObject):
    jobs = Signal(list)
    down = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = threading.Event()

    def start(self, client, interval: float = 2.0) -> None:
        self.stop()
        self._stop = threading.Event()
        stop_event = self._stop
        threading.Thread(
            target=lambda: poll_loop(
                client, stop=stop_event.is_set, interval=interval,
                on_jobs=self.jobs.emit, on_down=self.down.emit,
            ),
            daemon=True, name="mmlct-gui-poll",
        ).start()

    def stop(self) -> None:
        self._stop.set()
