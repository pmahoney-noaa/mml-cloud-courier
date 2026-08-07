"""Main window. Task 18 builds the real rail/tabs; this shell exists so
the entry point and smoke fixtures have a stable import from day one."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QMainWindow

from mml_cloud_transfer.gui.session import ServiceSession


class MainWindow(QMainWindow):
    def __init__(self, session: ServiceSession):
        super().__init__()
        self.session = session
        self.setWindowTitle("MML Cloud Transfer")
        self.resize(1100, 700)
        text = session.error or f"Connected to {session.base_url}"
        label = QLabel(text)
        label.setWordWrap(True)
        self.setCentralWidget(label)

    def shutdown(self) -> None:
        """Stop background threads. The shell has none yet."""
