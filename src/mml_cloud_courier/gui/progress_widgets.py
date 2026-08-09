"""Custom-painted pieces of the Progress tab. Every color is read from
theme.current() at paint time — nothing here caches a hex value."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from mml_cloud_courier.gui import theme
from mml_cloud_courier.gui.format import STATE_LABELS


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


STATE_ORDER = ("verified", "transferred", "transferring", "pending",
               "skipped", "changed", "failed", "quarantined")
STATE_TOKENS = {"verified": "accent", "transferred": "accent_2",
                "transferring": "accent_3", "pending": "track",
                "skipped": "skip", "changed": "warn",
                "failed": "danger", "quarantined": "danger"}


class _StackedStateBar(QWidget):
    """9px stacked bar, one segment per nonzero state in STATE_ORDER."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._counts: dict[str, int] = {}
        self.setMinimumHeight(9)
        self.setMaximumHeight(9)
        theme.notifier.changed.connect(self._on_theme)

    def _on_theme(self, _t) -> None:
        self.update()

    def set_counts(self, counts: dict[str, int]) -> None:
        self._counts = dict(counts)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        path = QPainterPath()
        path.addRoundedRect(rect, 3, 3)
        painter.setClipPath(path)
        painter.fillRect(rect, token_color("track"))
        total = sum(v for v in self._counts.values() if v > 0)
        if total:
            x = 0
            for state in STATE_ORDER:
                count = self._counts.get(state, 0)
                if count <= 0:
                    continue
                seg = round(rect.width() * count / total)
                painter.fillRect(x, 0, seg, rect.height(),
                                 token_color(STATE_TOKENS[state]))
                x += seg
        painter.end()


class StateBarCard(QWidget):
    """README screen 1 item 3 — the single most valuable addition: every
    file's state at once, so skipped is distinguishable from failed."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("surfaceCard")
        self.title = QLabel("EVERY FILE, BY STATE")
        self.title.setObjectName("sectionLabel")
        self.title.setFont(theme.mono_font(8.0))
        self.bar = _StackedStateBar()
        self._legend_layout = QHBoxLayout()
        self._legend_layout.setSpacing(18)
        self._legend: list[tuple[str, int]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 13, 15, 13)
        layout.setSpacing(9)
        layout.addWidget(self.title)
        layout.addWidget(self.bar)
        layout.addLayout(self._legend_layout)
        theme.notifier.changed.connect(self._on_theme)

    def _on_theme(self, _t) -> None:
        if hasattr(self, "_raw_counts"):
            self.set_counts(dict(self._raw_counts))

    def legend_labels(self) -> list[tuple[str, int]]:
        return list(self._legend)

    def set_counts(self, counts: dict[str, int]) -> None:
        self._raw_counts = dict(counts)
        self.bar.set_counts(counts)
        ordered = [(state, counts[state]) for state in STATE_ORDER
                   if counts.get(state, 0) > 0]
        ordered += [(state, count) for state, count in counts.items()
                    if state not in STATE_TOKENS and count > 0]
        self._legend = [(STATE_LABELS.get(state, state), count)
                        for state, count in ordered]
        while self._legend_layout.count():
            item = self._legend_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        t = theme.current()
        for (state, count), (label, _c) in zip(ordered, self._legend):
            swatch = QLabel()
            swatch.setFixedSize(8, 8)
            token = STATE_TOKENS.get(state, "skip")
            swatch.setStyleSheet(
                f"background: {getattr(t, token)}; border-radius: 2px;")
            entry_label = QLabel(f"{label}  ")
            count_label = QLabel(f"{count:,}")
            count_label.setFont(theme.mono_font(8.5, 500))
            cell = QHBoxLayout()
            cell.setSpacing(7)
            cell.addWidget(swatch)
            cell.addWidget(entry_label)
            cell.addWidget(count_label)
            holder = QWidget()
            holder.setLayout(cell)
            self._legend_layout.addWidget(holder)
        self._legend_layout.addStretch(1)
