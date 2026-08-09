"""The four job tabs: Progress, Files, Errors, Summary.

Each tab is total over its input: a live SSE snapshot and a plain
``ApiClient.get_job`` payload differ slightly (the latter carries no
``transferring`` key), and MainWindow feeds both shapes through the same
``update_snapshot``/``update_job`` methods, so every lookup here defaults
missing keys rather than raising.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from mml_cloud_courier.gui import theme
from mml_cloud_courier.gui.files_model import FileTableModel
from mml_cloud_courier.gui.format import (
    STATE_LABELS,
    STATUS_LABELS,
    human_bytes,
    human_duration,
    human_rate,
)
from mml_cloud_courier.gui.progress_widgets import (
    EVENT_ROLE,
    INFLIGHT_ROLE,
    EventsDelegate,
    InflightDelegate,
    SegmentedBar,
    StateBarCard,
    inflight_detail_text,
)

_RESUME_VISIBLE = frozenset({"paused", "stalled", "incomplete", "cancelled"})
_REPORT_VISIBLE = frozenset({"complete", "paused", "stalled", "incomplete", "cancelled"})


class ProgressTab(QWidget):
    """Live view of one job: headline, byte/file progress, throughput,
    in-flight files, and a capped rolling event log."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.headline_name = QLabel("")
        font = self.headline_name.font()
        font.setPointSizeF(13.5)
        font.setWeight(QFont.Weight(600))
        self.headline_name.setFont(font)
        self.headline_route = QLabel("")
        self.headline_route.setFont(theme.mono_font(8.5))
        self.percent_label = QLabel("")
        self.percent_label.setFont(theme.mono_font(19.5, 600))
        self.percent_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)

        name_column = QVBoxLayout()
        name_column.setSpacing(2)
        name_column.addWidget(self.headline_name)
        name_column.addWidget(self.headline_route)
        headline_row = QHBoxLayout()
        headline_row.addLayout(name_column, 1)
        headline_row.addWidget(self.percent_label)

        self.bar = SegmentedBar()
        self.counts_label = QLabel("")
        self.counts_label.setFont(theme.mono_font(9))
        self.rate_label = QLabel("")
        self.rate_label.setFont(theme.mono_font(9, 500))
        self.eta_label = QLabel("")
        self.eta_label.setFont(theme.mono_font(9))
        under_bar = QHBoxLayout()
        under_bar.setSpacing(16)
        under_bar.addWidget(self.counts_label)
        under_bar.addStretch(1)
        under_bar.addWidget(self.rate_label)
        under_bar.addWidget(self.eta_label)

        self.state_card = StateBarCard()

        self.headline_label = self.headline_name   # back-compat alias; remove in Task 9

        self.inflight_title = QLabel("IN PROGRESS")
        self.inflight_title.setObjectName("sectionLabel")
        self.inflight_title.setFont(theme.mono_font(8.0))
        self.inflight_list = QListWidget()
        self.inflight_list.setTextElideMode(Qt.TextElideMode.ElideLeft)
        self.inflight_list.setFont(theme.mono_font(8.5))
        self.inflight_list.setItemDelegate(InflightDelegate(self.inflight_list))
        inflight_card = QWidget()
        inflight_card.setObjectName("surfaceCard")
        inflight_layout = QVBoxLayout(inflight_card)
        inflight_layout.setContentsMargins(15, 13, 15, 13)
        inflight_layout.setSpacing(9)
        inflight_layout.addWidget(self.inflight_title)
        inflight_layout.addWidget(self.inflight_list, 1)

        self.events_title = QLabel("EVENTS")
        self.events_title.setObjectName("sectionLabel")
        self.events_title.setFont(theme.mono_font(8.0))
        self.events_list = QListWidget()
        self.events_list.setTextElideMode(Qt.TextElideMode.ElideLeft)
        self.events_list.setFont(theme.mono_font(8.5))
        self.events_list.setItemDelegate(EventsDelegate(self.events_list))
        events_card = QWidget()
        events_card.setObjectName("surfaceCard")
        events_layout = QVBoxLayout(events_card)
        events_layout.setContentsMargins(15, 13, 15, 13)
        events_layout.setSpacing(9)
        events_layout.addWidget(self.events_title)
        events_layout.addWidget(self.events_list, 1)

        cards_row = QHBoxLayout()
        cards_row.addWidget(inflight_card, 115)
        cards_row.addWidget(events_card, 100)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 20, 18)
        layout.setSpacing(15)
        layout.addLayout(headline_row)
        layout.addWidget(self.bar)
        layout.addLayout(under_bar)
        layout.addWidget(self.state_card)
        layout.addLayout(cards_row, 1)

        self._events: list[str] = []
        self._event_tuples: list[tuple[str, str, str]] = []
        self._last_bytes: int | None = None
        self._last_time: float | None = None
        self._rate: float | None = None

        theme.notifier.changed.connect(self._on_theme_repaint)   # bound: auto-disconnects

    def reset(self) -> None:
        """Clear per-job state (throughput EWMA, event log) before a new
        job's snapshots start arriving — otherwise a fast switch would
        blend two jobs' byte deltas into one bogus rate."""
        self._events = []
        self._event_tuples = []
        self._last_bytes = None
        self._last_time = None
        self._rate = None
        self.headline_name.setText("")
        self.headline_route.setText("")
        self.percent_label.setText("")
        self.counts_label.setText("")
        self.rate_label.setText("")
        self.eta_label.setText("")
        self.bar.set_fractions(0.0, 0.0)
        self.state_card.set_counts({})
        self.inflight_title.setText("IN PROGRESS")
        self.inflight_list.clear()
        self.events_list.clear()

    def update_snapshot(self, snap: dict) -> None:
        name = snap.get("name")
        if name is not None:
            self.headline_name.setText(name)

        direction = snap.get("direction")
        source_root = snap.get("source_root")
        dest_prefix = snap.get("dest_prefix")
        if direction is not None and source_root is not None and dest_prefix is not None:
            self.headline_route.setText(
                f"{direction.title()} · {source_root} → {dest_prefix}"
            )

        progress = snap.get("progress") or {}
        files_total = progress.get("files_total", 0)
        files_done = progress.get("files_done", 0)
        bytes_total = progress.get("bytes_total", 0)
        bytes_done = progress.get("bytes_done", 0)

        if bytes_total:
            fraction = min(1.0, bytes_done / bytes_total)
        elif files_total:
            fraction = min(1.0, files_done / files_total)
        else:
            fraction = 0.0
        self.percent_label.setText(f"{int(fraction * 100)}%")

        self.counts_label.setText(
            f"{files_done:,} of {files_total:,} files — "
            f"{human_bytes(bytes_done)} of {human_bytes(bytes_total)}"
        )

        self._update_throughput(bytes_done, bytes_total)

        transferring = snap.get("transferring") or []
        inflight_fraction = (
            sum(e.get("bytes_transferred", 0) for e in transferring) / bytes_total
            if bytes_total else 0
        )
        self.bar.set_fractions(fraction, inflight_fraction)

        self.state_card.set_counts(progress.get("state_counts") or {})

        self._update_inflight(transferring)
        self._append_events(snap.get("events") or [])

    def _update_throughput(self, bytes_done: int, bytes_total: int) -> None:
        now = time.monotonic()
        if self._last_time is not None:
            delta_t = now - self._last_time
            if delta_t > 0:
                instant = (bytes_done - self._last_bytes) / delta_t
                self._rate = instant if self._rate is None else (
                    0.7 * self._rate + 0.3 * instant
                )
                self.rate_label.setText(human_rate(max(self._rate, 0.0)))
        self._last_bytes = bytes_done
        self._last_time = now

        if self._rate and self._rate > 0 and bytes_total and bytes_done <= bytes_total:
            self.eta_label.setText(human_duration((bytes_total - bytes_done) / self._rate))
        else:
            self.eta_label.setText("")

    def _update_inflight(self, transferring: list[dict]) -> None:
        self.inflight_title.setText(f"IN PROGRESS — {len(transferring)} FILES")
        self.inflight_list.clear()
        for entry in transferring:
            relative_path = entry.get("relative_path", "")
            # Display text is an accessible fallback; InflightDelegate paints
            # the real row from INFLIGHT_ROLE.
            text = f"{relative_path} — {inflight_detail_text(entry)}"
            item = QListWidgetItem(text)
            item.setData(INFLIGHT_ROLE, entry)
            self.inflight_list.addItem(item)

    def _append_events(self, new_events: list[dict]) -> None:
        if not new_events:
            return
        for event in new_events:
            at, kind, detail = event.get("at", ""), event.get("kind", ""), event.get("detail", "")
            self._events.append(f"{at}  {kind}: {detail}")
            self._event_tuples.append((at, kind, detail))
        self._events = self._events[-200:]
        self._event_tuples = self._event_tuples[-200:]
        self.events_list.clear()
        for text, tup in zip(self._events, self._event_tuples):
            # Display text is an accessible fallback; EventsDelegate paints
            # the real row from EVENT_ROLE.
            item = QListWidgetItem(text)
            item.setData(EVENT_ROLE, tup)
            self.events_list.addItem(item)

    def _on_theme_repaint(self, _t) -> None:
        self.inflight_list.viewport().update()
        self.events_list.viewport().update()


class FilesTab(QWidget):
    """A virtualized file table for the selected job, filterable by state."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.state_combo = QComboBox()
        self.state_combo.addItem("All states", None)
        for state, label in STATE_LABELS.items():
            self.state_combo.addItem(label, state)

        self.header_label = QLabel("")
        self.header_label.setObjectName("filesHeader")
        self.header_label.setFont(theme.mono_font(8.5))

        header_row = QHBoxLayout()
        header_row.addWidget(self.state_combo)
        header_row.addStretch(1)
        header_row.addWidget(self.header_label)

        self.table = QTableView()
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setTextElideMode(Qt.TextElideMode.ElideLeft)
        self.table.setAlternatingRowColors(True)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.hide()

        layout = QVBoxLayout(self)
        layout.addLayout(header_row)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.error_label)

        self.state_combo.currentIndexChanged.connect(self._on_filter_changed)

        self._model: FileTableModel | None = None
        self._total: int | None = None

    def attach(self, fetcher: Callable[..., list[dict]]) -> None:
        """Bind a new per-job fetcher and load its first page."""
        self._total = None   # previous job's total must not bleed into this one
        self._model = FileTableModel(fetcher)
        self.table.setModel(self._model)
        self._model.rowsInserted.connect(self._update_header)
        self._model.modelReset.connect(self._update_header)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)   # PATH
        header.resizeSection(1, 88)                                      # SIZE
        header.resizeSection(2, 204)   # STATE — hard requirement: "Excluded after
                                       # repeated failures" must render in full
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)   # DETAIL

        self.refresh()

    def set_total(self, total: int | None) -> None:
        self._total = total
        self._update_header()

    def refresh(self) -> None:
        if self._model is None:
            return
        state = self.state_combo.currentData()
        self._model.set_filter(state=state)
        self._show_error()
        self._update_header()

    def _on_filter_changed(self, _index: int) -> None:
        self.refresh()

    def _update_header(self) -> None:
        loaded = self._model.rowCount() if self._model is not None else 0
        filtered = self.state_combo.currentData() is not None
        if self._total is None or filtered:
            self.header_label.setText(f"showing 1–{loaded:,}")
        else:
            self.header_label.setText(f"{self._total:,} files · showing 1–{loaded:,}")

    def _show_error(self) -> None:
        error = self._model.last_error if self._model is not None else None
        if error:
            self.error_label.setText(error)
            self.error_label.show()
        else:
            self.error_label.hide()


class SummaryTab(QWidget):
    """The verdict, the state breakdown, and the two follow-up actions
    that only make sense once a job has stopped moving on its own."""

    def __init__(self, *, on_open_report, on_resume, parent=None):
        super().__init__(parent)
        self._on_open_report = on_open_report
        self._on_resume = on_resume
        self._last_job: dict | None = None

        self.verdict_label = QLabel("")
        self.verdict_label.setWordWrap(True)

        self._counts_form = QFormLayout()
        counts_widget = QWidget()
        counts_widget.setLayout(self._counts_form)

        self.totals_label = QLabel("")
        self.duration_label = QLabel("")

        self.report_button = QPushButton("Open report")
        self.resume_button = QPushButton("Resume remaining")
        self.report_button.clicked.connect(lambda: self._on_open_report())
        self.resume_button.clicked.connect(lambda: self._on_resume())
        self.report_button.hide()
        self.resume_button.hide()

        buttons = QHBoxLayout()
        buttons.addWidget(self.report_button)
        buttons.addWidget(self.resume_button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.verdict_label)
        layout.addWidget(counts_widget)
        layout.addWidget(self.totals_label)
        layout.addWidget(self.duration_label)
        layout.addLayout(buttons)
        layout.addStretch(1)

        theme.notifier.changed.connect(self._on_theme_changed)

    def update_job(self, job: dict) -> None:
        self._last_job = job
        status = job.get("status", "")
        self.verdict_label.setText(STATUS_LABELS.get(status, status))
        self.verdict_label.setStyleSheet(_verdict_style(status))

        while self._counts_form.rowCount():
            self._counts_form.removeRow(0)
        progress = job.get("progress") or {}
        for state, count in (progress.get("state_counts") or {}).items():
            self._counts_form.addRow(STATE_LABELS.get(state, state), QLabel(f"{count:,}"))

        files_total = progress.get("files_total", 0)
        bytes_total = progress.get("bytes_total", 0)
        self.totals_label.setText(f"{files_total:,} files, {human_bytes(bytes_total)} total")
        self.duration_label.setText(_duration_text(job.get("started_at"), job.get("finished_at")))

        self.report_button.setVisible(status in _REPORT_VISIBLE)
        self.resume_button.setVisible(status in _RESUME_VISIBLE)

    def _on_theme_changed(self, _t) -> None:
        if self._last_job is not None:
            self.update_job(self._last_job)


def _verdict_style(status: str) -> str:
    # Text color pinned alongside each background: under Windows dark mode
    # the palette text is white, unreadable on these pastel fills.
    t = theme.current()
    if status == "complete":
        return f"background-color: {t.accent_soft}; color: {t.accent_text}; padding: 6px;"
    if status == "incomplete":
        return f"background-color: {t.danger_soft}; color: {t.danger_text}; padding: 6px;"
    return f"padding: 6px; color: {t.muted};"


def _duration_text(started: str | None, finished: str | None) -> str:
    if started and finished:
        delta = (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds()
        return f"Started {started} — duration {human_duration(delta)}"
    if started:
        return f"Started {started}"
    return ""
