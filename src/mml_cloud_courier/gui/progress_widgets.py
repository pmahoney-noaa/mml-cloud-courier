"""Custom-painted pieces of the Progress tab. Every color is read from
theme.current() at paint time — nothing here caches a hex value."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from mml_cloud_courier.gui import theme


def token_color(token: str) -> QColor:
    return theme._qcolor(getattr(theme.current(), token))


class SegmentedBar(QWidget):
    """The 8px two-segment progress bar: verified in accent, in-flight in
    accent_2, on a track background. Radius 4."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.verified = 0.0
        self.inflight = 0.0
        self.setMinimumHeight(8)
        self.setMaximumHeight(8)
        theme.notifier.changed.connect(self._on_theme)   # bound: auto-disconnects

    def _on_theme(self, _t) -> None:
        self.update()

    def set_fractions(self, verified: float, inflight: float) -> None:
        self.verified = min(1.0, max(0.0, verified))
        self.inflight = min(1.0 - self.verified, max(0.0, inflight))
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        path = QPainterPath()
        path.addRoundedRect(rect, 4, 4)
        painter.setClipPath(path)
        painter.fillRect(rect, token_color("track"))
        width = rect.width()
        verified_width = round(width * self.verified)
        inflight_width = round(width * self.inflight)
        if verified_width:
            painter.fillRect(0, 0, verified_width, rect.height(), token_color("accent"))
        if inflight_width:
            painter.fillRect(verified_width, 0, inflight_width, rect.height(),
                             token_color("accent_2"))
        painter.end()
