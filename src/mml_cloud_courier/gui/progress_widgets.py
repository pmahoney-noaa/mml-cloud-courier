"""Custom-painted pieces of the Progress tab. Every color is read from
theme.current() at paint time — nothing here caches a hex value."""
from __future__ import annotations

from PySide6.QtCore import Qt, QRect, QSize
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from mml_cloud_courier.gui import theme
from mml_cloud_courier.gui.format import STATE_LABELS, human_bytes


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
# Bar-fill swatch/segment colors -- these include trough-ish tones ("track",
# "skip") that read fine as a small filled rectangle but are far too low
# contrast to use as body text.
STATE_TOKENS = {"verified": "accent", "transferred": "accent_2",
                "transferring": "accent_3", "pending": "track",
                "skipped": "skip", "changed": "warn",
                "failed": "danger", "quarantined": "danger"}
# Text-appropriate colors for the same states (Files tab STATE column):
# always a "_text"/"muted" foreground token, never a trough/fill tone.
STATE_TEXT_TOKENS = {"verified": "accent_text", "transferred": "accent_2",
                     "transferring": "accent_2", "pending": "muted",
                     "skipped": "muted", "changed": "warn_text",
                     "failed": "danger_text", "quarantined": "danger_text"}


class _StackedStateBar(QWidget):
    """9px stacked bar, one segment per nonzero state in STATE_ORDER."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._counts: dict[str, int] = {}
        self._total: int | None = None
        self.setMinimumHeight(9)
        self.setMaximumHeight(9)
        theme.notifier.changed.connect(self._on_theme)

    def _on_theme(self, _t) -> None:
        self.update()

    def set_counts(self, counts: dict[str, int], total: int | None = None) -> None:
        self._counts = dict(counts)
        self._total = total
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        path = QPainterPath()
        path.addRoundedRect(rect, 3, 3)
        painter.setClipPath(path)
        painter.fillRect(rect, token_color("track"))
        total = self._total if self._total is not None else sum(
            v for v in self._counts.values() if v > 0
        )
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
        # Custom QWidget subclasses don't paint QSS backgrounds/borders
        # unless this attribute is set -- without it the card is invisible
        # (transparent) and just shows whatever is behind it.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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
            # A theme swap replays the SAME counts to repaint the legend
            # swatches' inline colors -- it must bypass set_counts' own
            # unchanged-counts guard below, which exists to skip this exact
            # rebuild when nothing (not even the theme) has changed.
            self.set_counts(dict(self._raw_counts), force=True)

    def legend_labels(self) -> list[tuple[str, int]]:
        return list(self._legend)

    def set_counts(self, counts: dict[str, int], *, force: bool = False) -> None:
        # A live job streams an SSE snapshot several times a second; most
        # ticks report the exact same state_counts. Rebuilding the legend
        # row widgets on every one of those no-op ticks is pure churn.
        if not force and dict(counts) == getattr(self, "_raw_counts", None):
            return
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


INFLIGHT_ROLE = Qt.ItemDataRole.UserRole + 1
EVENT_ROLE = Qt.ItemDataRole.UserRole + 1

# kind -> token. The original three (verified/failed/retry) are generic
# example names kept for compatibility; everything else below is the real
# vocabulary found via `git grep -nE 'kind=|"kind"' -- service engine` and
# cross-referencing every repo.record_event(...) call site in engine/runner.py,
# service/app.py, service/worker.py, cli/scan_command.py, store/repository.py.
_EVENT_KIND_TOKENS = {
    "verified": "accent_text", "failed": "danger", "retry": "warn",
    # success/verified-ish
    "job_unstalled": "accent_text",
    # failed/error-ish
    "scan_error": "danger", "audit_error": "danger", "worker_error": "danger",
    "report_error": "danger", "quarantined": "danger",
    # retry/pause/stall-ish
    "files_retried": "warn", "paused_by_user": "warn", "run_paused": "warn",
    "job_stalled": "warn", "job_lock_contended": "warn", "source_changed": "warn",
    # start/resume/progress/info-ish
    "scan_started": "accent_2", "run_started": "accent_2",
    "resumed_by_user": "accent_2", "recovered_at_startup": "accent_2",
    "job_submitted": "accent_2", "scheduled": "accent_2",
    "run_stopped": "accent_2", "run_finished": "accent_2",
    "scan_finished": "accent_2", "audit_finished": "accent_2",
    # anything else -- neutral user actions with no clean bucket
    "cancelled_by_user": "muted", "files_excluded": "muted",
}


def event_kind_token(kind: str) -> str:
    return _EVENT_KIND_TOKENS.get(kind, "muted")


def inflight_detail_text(entry: dict) -> str:
    text = (f"{human_bytes(entry.get('bytes_transferred', 0))} of "
            f"{human_bytes(entry.get('size_bytes', 0))}")
    slices_total = entry.get("slices_total", 0)
    if entry.get("method") == "sliced" and slices_total > 0:
        slices_done = entry.get("slices_done", 0)
        current = min(slices_done + 1, slices_total)
        text += f" · slice {current} of {slices_total}, {slices_done} done"
    return text


class InflightDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), 40)

    def paint(self, painter, option, index) -> None:
        entry = index.data(INFLIGHT_ROLE) or {}
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(8, 6, -8, -6)
        painter.setFont(theme.mono_font(8.5))
        metrics = painter.fontMetrics()
        detail = inflight_detail_text(entry)
        detail_width = metrics.horizontalAdvance(detail)
        painter.setPen(token_color("ink"))
        path_width = max(0, rect.width() - detail_width - 12)
        path_text = metrics.elidedText(entry.get("relative_path", ""),
                                       Qt.TextElideMode.ElideLeft,
                                       path_width)
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, path_text)
        painter.setPen(token_color("faint"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, detail)
        size = entry.get("size_bytes", 0)
        fraction = (entry.get("bytes_transferred", 0) / size) if size else 0.0
        bar = QRect(rect.left(), rect.bottom() - 4, rect.width(), 4)
        painter.fillRect(bar, token_color("track"))
        painter.fillRect(QRect(bar.left(), bar.top(),
                               round(bar.width() * min(1.0, fraction)), 4),
                         token_color("accent_2"))
        painter.restore()


class EventsDelegate(QStyledItemDelegate):
    KIND_COLUMN = 52

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), 22)

    def paint(self, painter, option, index) -> None:
        at, kind, detail = index.data(EVENT_ROLE) or ("", "", "")
        painter.save()
        rect = option.rect.adjusted(8, 3, -8, -3)
        painter.setFont(theme.mono_font(8.0))
        metrics = painter.fontMetrics()
        if at == "date":
            # A date-separator row (see job_tabs._append_events): the
            # "kind" slot carries the display date text instead of a kind
            # string, and "detail" is unused.
            painter.setPen(token_color("faint"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, kind)
            text_width = metrics.horizontalAdvance(kind)
            line_y = rect.top() + rect.height() // 2
            line_left = rect.left() + text_width + 8
            line_right = rect.right()
            if line_right > line_left:
                painter.fillRect(QRect(line_left, line_y, line_right - line_left, 1),
                                 token_color("line"))
            painter.restore()
            return
        time_width = metrics.horizontalAdvance(at) + 10
        painter.setPen(token_color("faint"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, at)
        painter.setPen(token_color(event_kind_token(kind)))
        kind_rect = QRect(rect.left() + time_width, rect.top(), self.KIND_COLUMN, rect.height())
        painter.drawText(kind_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(kind, Qt.TextElideMode.ElideRight, self.KIND_COLUMN))
        painter.setPen(token_color("muted"))
        detail_width = max(0, rect.right() - kind_rect.right() - 8)
        detail_rect = QRect(kind_rect.right() + 8, rect.top(),
                            detail_width, rect.height())
        painter.drawText(detail_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(detail, Qt.TextElideMode.ElideRight, detail_rect.width()))
        painter.restore()
