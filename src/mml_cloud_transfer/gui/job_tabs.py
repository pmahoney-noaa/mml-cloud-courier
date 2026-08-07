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
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QProgressBar,
    QPushButton,
    QTableView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mml_cloud_transfer.gui.errors_model import ErrorGroup
from mml_cloud_transfer.gui.files_model import FileTableModel
from mml_cloud_transfer.gui.format import (
    STATE_LABELS,
    STATUS_LABELS,
    human_bytes,
    human_duration,
    human_rate,
)

_CATEGORY_ROLE = Qt.ItemDataRole.UserRole + 1
_FILLED_ROLE = Qt.ItemDataRole.UserRole + 2

_RESUME_VISIBLE = frozenset({"paused", "stalled", "incomplete", "cancelled"})
_REPORT_VISIBLE = frozenset({"complete", "paused", "stalled", "incomplete", "cancelled"})


def group_fill_rows(page: list[str], group_count: int) -> list[str]:
    """Rows to display for a lazily-filled error group.

    ``page`` is a single (already page-size-bounded) fetch of paths; the
    trailing "...and N more" row is sized from the group's already-known
    ``group_count`` rather than by fetching every remaining page, so
    expanding a large group costs exactly one round trip.
    """
    rows = list(page)
    remaining = group_count - len(page)
    if remaining > 0:
        rows.append(f"…and {remaining:,} more")
    return rows


class ProgressTab(QWidget):
    """Live view of one job: headline, byte/file progress, throughput,
    in-flight files, and a capped rolling event log."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.headline_label = QLabel("")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.counts_label = QLabel("")
        self.throughput_label = QLabel("")
        self.inflight_list = QListWidget()
        self.events_list = QListWidget()

        layout = QVBoxLayout(self)
        layout.addWidget(self.headline_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.counts_label)
        layout.addWidget(self.throughput_label)
        layout.addWidget(QLabel("In progress:"))
        layout.addWidget(self.inflight_list, 1)
        layout.addWidget(QLabel("Events:"))
        layout.addWidget(self.events_list, 1)

        self._events: list[str] = []
        self._last_bytes: int | None = None
        self._last_time: float | None = None
        self._rate: float | None = None

    def reset(self) -> None:
        """Clear per-job state (throughput EWMA, event log) before a new
        job's snapshots start arriving — otherwise a fast switch would
        blend two jobs' byte deltas into one bogus rate."""
        self._events = []
        self._last_bytes = None
        self._last_time = None
        self._rate = None
        self.headline_label.setText("")
        self.counts_label.setText("")
        self.throughput_label.setText("")
        self.progress_bar.setValue(0)
        self.inflight_list.clear()
        self.events_list.clear()

    def update_snapshot(self, snap: dict) -> None:
        status = snap.get("status", "")
        self.headline_label.setText(STATUS_LABELS.get(status, status))

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
        self.progress_bar.setValue(int(fraction * 100))

        self.counts_label.setText(
            f"{files_done:,} of {files_total:,} files — "
            f"{human_bytes(bytes_done)} of {human_bytes(bytes_total)}"
        )

        self._update_throughput(bytes_done)
        self._update_inflight(snap.get("transferring") or [])
        self._append_events(snap.get("events") or [])

    def _update_throughput(self, bytes_done: int) -> None:
        now = time.monotonic()
        if self._last_time is not None:
            delta_t = now - self._last_time
            if delta_t > 0:
                instant = (bytes_done - self._last_bytes) / delta_t
                self._rate = instant if self._rate is None else (
                    0.7 * self._rate + 0.3 * instant
                )
                self.throughput_label.setText(human_rate(max(self._rate, 0.0)))
        self._last_bytes = bytes_done
        self._last_time = now

    def _update_inflight(self, transferring: list[dict]) -> None:
        self.inflight_list.clear()
        for entry in transferring:
            relative_path = entry.get("relative_path", "")
            bytes_transferred = entry.get("bytes_transferred", 0)
            size_bytes = entry.get("size_bytes", 0)
            text = (
                f"{relative_path} — {human_bytes(bytes_transferred)}"
                f" of {human_bytes(size_bytes)}"
            )
            slices_total = entry.get("slices_total", 0)
            if entry.get("method") == "sliced" and slices_total > 0:
                slices_done = entry.get("slices_done", 0)
                current = min(slices_done + 1, slices_total)
                text += f" (slice {current} of {slices_total}, {slices_done} done)"
            self.inflight_list.addItem(text)

    def _append_events(self, new_events: list[dict]) -> None:
        if not new_events:
            return
        for event in new_events:
            self._events.append(
                f"{event.get('at', '')}  {event.get('kind', '')}: {event.get('detail', '')}"
            )
        self._events = self._events[-200:]
        self.events_list.clear()
        self.events_list.addItems(self._events)


class FilesTab(QWidget):
    """A virtualized file table for the selected job, filterable by state."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.state_combo = QComboBox()
        self.state_combo.addItem("All states", None)
        for state, label in STATE_LABELS.items():
            self.state_combo.addItem(label, state)

        self.table = QTableView()
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.hide()

        layout = QVBoxLayout(self)
        layout.addWidget(self.state_combo)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.error_label)

        self.state_combo.currentIndexChanged.connect(self._on_filter_changed)

        self._model: FileTableModel | None = None

    def attach(self, fetcher: Callable[..., list[dict]]) -> None:
        """Bind a new per-job fetcher and load its first page."""
        self._model = FileTableModel(fetcher)
        self.table.setModel(self._model)
        self.refresh()

    def refresh(self) -> None:
        if self._model is None:
            return
        state = self.state_combo.currentData()
        self._model.set_filter(state=state)
        self._show_error()

    def _on_filter_changed(self, _index: int) -> None:
        self.refresh()

    def _show_error(self) -> None:
        error = self._model.last_error if self._model is not None else None
        if error:
            self.error_label.setText(error)
            self.error_label.show()
        else:
            self.error_label.hide()


class ErrorsTab(QWidget):
    """Errors grouped by cause. Children (file paths) are lazily fetched
    the first time a group is expanded, so opening the tab never pays for
    paths the user never looks at."""

    def __init__(self, *, on_retry, on_exclude, on_copy, on_expand=None, parent=None):
        super().__init__(parent)
        self._on_retry = on_retry
        self._on_exclude = on_exclude
        self._on_copy = on_copy
        self._on_expand = on_expand
        self._groups: list[ErrorGroup] = []

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.currentItemChanged.connect(self._on_current_changed)

        # The taxonomy's suggested action for the selected group — the
        # spec's "what to do about it" line, shown right above the buttons
        # that act on it.
        self.action_label = QLabel("")
        self.action_label.setWordWrap(True)

        self.retry_button = QPushButton("Retry these files")
        self.exclude_button = QPushButton("Stop retrying these files")
        self.copy_button = QPushButton("Copy file list")
        self.retry_button.clicked.connect(self._retry_clicked)
        self.exclude_button.clicked.connect(self._exclude_clicked)
        self.copy_button.clicked.connect(self._copy_clicked)

        buttons = QHBoxLayout()
        buttons.addWidget(self.retry_button)
        buttons.addWidget(self.exclude_button)
        buttons.addWidget(self.copy_button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.action_label)
        layout.addLayout(buttons)

    def load_groups(self, groups: list[ErrorGroup]) -> None:
        self._groups = list(groups)
        self.tree.clear()
        self.action_label.setText("")
        for group in self._groups:
            item = QTreeWidgetItem([group.label])
            item.setData(0, _CATEGORY_ROLE, group.category)
            item.setData(0, _FILLED_ROLE, False)
            item.setToolTip(0, group.action)
            item.addChild(QTreeWidgetItem(["Loading…"]))   # placeholder for the arrow
            self.tree.addTopLevelItem(item)
        if self._groups:
            # Auto-select the first group: single-cause jobs (the common
            # case) show their guidance without a click, and the action
            # buttons have a target immediately.
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def _on_current_changed(self, current, _previous) -> None:
        item = current
        while item is not None and item.parent() is not None:
            item = item.parent()
        text = ""
        if item is not None:
            category = item.data(0, _CATEGORY_ROLE)
            for group in self._groups:
                if group.category == category:
                    text = group.action
                    break
        self.action_label.setText(text)

    # -- test/UI hooks ------------------------------------------------

    def group_count(self) -> int:
        return len(self._groups)

    def group_label(self, i: int) -> str:
        return self._groups[i].label

    def selected_category(self) -> str | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        if item.parent() is not None:
            item = item.parent()
        return item.data(0, _CATEGORY_ROLE)

    # -- lazy fill ------------------------------------------------------

    def _group_count(self, category: str) -> int:
        for group in self._groups:
            if group.category == category:
                return group.count
        return 0

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        if item.parent() is not None or item.data(0, _FILLED_ROLE):
            return
        item.setData(0, _FILLED_ROLE, True)
        category = item.data(0, _CATEGORY_ROLE)
        try:
            page = self._on_expand(category) if self._on_expand is not None else []
            rows = group_fill_rows(page, self._group_count(category))
        except Exception as exc:
            # Leave the group re-expandable (clear _FILLED_ROLE) so the user
            # can retry instead of being stuck at "Loading..." forever.
            item.setData(0, _FILLED_ROLE, False)
            item.takeChildren()
            item.addChild(QTreeWidgetItem([f"Failed to load: {exc}"]))
            return
        item.takeChildren()
        for row in rows:
            item.addChild(QTreeWidgetItem([row]))

    # -- buttons ----------------------------------------------------------

    def _retry_clicked(self) -> None:
        category = self.selected_category()
        if category is not None:
            self._on_retry(category)

    def _exclude_clicked(self) -> None:
        category = self.selected_category()
        if category is not None:
            self._on_exclude(category)

    def _copy_clicked(self) -> None:
        category = self.selected_category()
        if category is not None:
            self._on_copy(category)


class SummaryTab(QWidget):
    """The verdict, the state breakdown, and the two follow-up actions
    that only make sense once a job has stopped moving on its own."""

    def __init__(self, *, on_open_report, on_resume, parent=None):
        super().__init__(parent)
        self._on_open_report = on_open_report
        self._on_resume = on_resume

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

    def update_job(self, job: dict) -> None:
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


def _verdict_style(status: str) -> str:
    # Text color pinned alongside each background: under Windows dark mode
    # the palette text is white, unreadable on these pastel fills.
    if status == "complete":
        return "background-color: #dff0d8; color: #3c763d; padding: 6px;"
    if status == "incomplete":
        return "background-color: #fcf8e3; color: #8a6d3b; padding: 6px;"
    return "padding: 6px;"


def _duration_text(started: str | None, finished: str | None) -> str:
    if started and finished:
        delta = (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds()
        return f"Started {started} — duration {human_duration(delta)}"
    if started:
        return f"Started {started}"
    return ""
