"""System tray: close-to-tray, balloon notifications for job transitions,
and quick actions when the main window is out of sight.

Some test sessions (and stripped-down desktops) have no tray at all —
QSystemTrayIcon.isSystemTrayAvailable() is the ground truth, checked once
at construction. When it says no, the controller disables itself: no icon
is created, handle_close() returns False so the window really closes, and
notify_transitions() is a no-op.
"""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from mml_cloud_courier.gui import theme
from mml_cloud_courier.gui.icons import app_icon
from mml_cloud_courier.gui.watcher import detect_transitions

_MESSAGES = {
    "complete": (
        "Transfer complete",
        "'{name}' finished — all files verified.",
    ),
    "incomplete": (
        "Transfer needs attention",
        "'{name}' finished with problems. Open MML Cloud Courier to see"
        " what and why.",
    ),
    "stalled": (
        "Transfer stalled",
        "'{name}' hit a network problem. It keeps retrying automatically.",
    ),
}

STILL_RUNNING_MESSAGE = (
    "MML Cloud Courier is still running here. Transfers continue in the"
    " background."
)


def notification_for(job: dict, new_status: str) -> tuple[str, str]:
    title, body_template = _MESSAGES[new_status]
    return title, body_template.format(name=job.get("name", ""))


class TrayController:
    def __init__(self, window, parent=None):
        self._window = window
        self._balloon_shown = False
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        self.icon = None
        self.menu = None
        self._theme_refresh_slot = None
        if not self.available:
            return

        self.icon = QSystemTrayIcon(app_icon(), parent)
        self.icon.setToolTip("MML Cloud Courier")

        self._theme_refresh_slot = lambda _t: self._refresh_icon(_t)
        theme.notifier.changed.connect(self._theme_refresh_slot)

        self.menu = QMenu()
        open_action = QAction("Open", self.menu)
        open_action.triggered.connect(self._open_window)
        self.menu.addAction(open_action)

        new_transfer_action = QAction("New Transfer", self.menu)
        new_transfer_action.triggered.connect(self._new_transfer)
        self.menu.addAction(new_transfer_action)

        self.menu.addSeparator()

        quit_action = QAction("Quit", self.menu)
        quit_action.triggered.connect(self.quit)
        self.menu.addAction(quit_action)

        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._on_activated)
        self.icon.show()

    def _open_window(self) -> None:
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    def _new_transfer(self) -> None:
        self._open_window()
        self._window._open_new_transfer()

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._open_window()

    def _refresh_icon(self, _t) -> None:
        if self.icon is not None:
            self.icon.setIcon(app_icon())

    def shutdown(self) -> None:
        if self._theme_refresh_slot is not None:
            theme.notifier.changed.disconnect(self._theme_refresh_slot)
            self._theme_refresh_slot = None

    def handle_close(self, event) -> bool:
        """Called from MainWindow.closeEvent. Returns True when the tray
        consumed the close (window hidden, not destroyed); False when
        there is no tray to hide to, so the caller must let it close."""
        if not self.available:
            return False
        self._window.hide()
        event.ignore()
        if not self._balloon_shown:
            self._balloon_shown = True
            self.icon.showMessage(
                "MML Cloud Courier", STILL_RUNNING_MESSAGE,
                QSystemTrayIcon.MessageIcon.Information,
            )
        return True

    def notify_transitions(
        self, jobs_before: dict[int, str], jobs_now: list[dict]
    ) -> None:
        if not self.available:
            return
        after = {job["id"]: job["status"] for job in jobs_now}
        by_id = {job["id"]: job for job in jobs_now}
        for job_id, _old, new_status in detect_transitions(jobs_before, after):
            title, body = notification_for(by_id[job_id], new_status)
            self.icon.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information)

    def quit(self) -> None:
        self._window.shutdown()
        QApplication.quit()
