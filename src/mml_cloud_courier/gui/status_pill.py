"""The toolbar's persistent truth pill (Recommendation 2): one element that
always answers whether closing the window is safe."""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

PILL_TEXT = {
    "ok": "Service running — transfers continue if you close this window",
    "down": "Service stopped — nothing is moving",
    "noconn": "Service running — no connection set up yet",
}


class StatusPill(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusPill")
        self.dot = QFrame()
        self.dot.setObjectName("pillDot")
        self.dot.setFixedSize(6, 6)
        self.label = QLabel()
        self.label.setObjectName("pillLabel")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 5, 11, 5)
        layout.setSpacing(6)
        layout.addWidget(self.dot)
        layout.addWidget(self.label)
        self._state = None
        self.set_state("ok")

    @property
    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        if state not in PILL_TEXT:
            return
        if state == self._state:
            return
        self._state = state
        self.label.setText(PILL_TEXT[state])
        # "noconn" keeps the ok (accent) tones; only "down" flips to danger.
        self.setProperty("pillState", state)
        # QStyleSheetStyle caches descendant rules per-child, so re-polishing
        # only `self` leaves pillDot/pillLabel stuck on the previous state's
        # colors -- every widget whose stylesheet rule keys off pillState
        # must be unpolished and repolished.
        for w in (self, self.dot, self.label):
            self.style().unpolish(w)
            self.style().polish(w)
