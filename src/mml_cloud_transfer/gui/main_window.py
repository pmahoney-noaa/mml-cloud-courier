"""The integration hub: grouped rail, four job tabs, toolbar, and the
service-down banner. Everything built in Tasks 10-17 is wired together
here — the rail drives selection, selection drives a JobWatcher plus an
immediate get_job render, and every mutating action (pause/resume/cancel,
error retry/exclude, report) round-trips through call_async and then
refreshes what it touched.
"""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from mml_cloud_transfer.gui.connection_dialogs import ConnectionsDialog
from mml_cloud_transfer.gui.errors_model import build_error_groups, fetch_group_paths
from mml_cloud_transfer.gui.job_tabs import ErrorsTab, FilesTab, ProgressTab, SummaryTab
from mml_cloud_transfer.gui.jobs_model import (
    JOB_ID_ROLE,
    RAIL_GROUPS,
    STATUS_ROLE,
    build_rail_model,
    rail_job_ids as _rail_job_ids,
    sync_rail,
)
from mml_cloud_transfer.gui.session import ServiceSession, discover_session
from mml_cloud_transfer.gui.settings_dialog import SettingsDialog
from mml_cloud_transfer.gui.wizard import NewTransferWizard
from mml_cloud_transfer.gui.workers import JobWatcher, JobsPoller, call_async

BANNER_TEXT = (
    "The transfer service is not running. Transfers cannot start or report"
    " progress until it is."
)

_PAUSABLE = frozenset({"running", "scanning", "stalled", "pending"})
_RESUMABLE = frozenset({"paused", "stalled", "incomplete", "cancelled"})
_CANCELLABLE = frozenset({"pending", "running", "scanning", "paused", "stalled"})


class MainWindow(QMainWindow):
    def __init__(self, session: ServiceSession, *, poll_interval: float = 2.0):
        super().__init__()
        self.session = session
        self.client = session.client
        self._poll_interval = poll_interval
        self.setWindowTitle("MML Cloud Transfer")
        self.resize(1100, 700)

        self._selected_job_id: int | None = None
        self._selected_status: str | None = None
        self._pending_select: int | None = None
        self.poller: JobsPoller | None = None
        self.watcher: JobWatcher | None = None
        self.rail_model = None
        self.rail_view: QTreeView | None = None

        if self.client is None:
            self._build_error_ui(session)
        else:
            self._build_full_ui()

    # -- no-token error state ------------------------------------------

    def _build_error_ui(self, session: ServiceSession) -> None:
        container = QWidget()
        layout = QVBoxLayout(container)
        label = QLabel(session.error or "Cannot reach the transfer service.")
        label.setWordWrap(True)
        retry = QPushButton("Retry")
        retry.clicked.connect(self._retry_discover)
        layout.addWidget(label)
        layout.addWidget(retry)
        layout.addStretch(1)
        self.setCentralWidget(container)

    def _retry_discover(self) -> None:
        session = discover_session()
        self.session = session
        self.client = session.client
        if session.client is None:
            self._build_error_ui(session)
        else:
            self._build_full_ui()

    # -- normal UI --------------------------------------------------------

    def _build_full_ui(self) -> None:
        self.banner = QLabel(BANNER_TEXT)
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("background-color: #f2dede; padding: 6px;")
        self.banner.hide()

        self.rail_model = build_rail_model()
        self.rail_view = QTreeView()
        self.rail_view.setModel(self.rail_model)
        self.rail_view.setHeaderHidden(True)
        self.rail_view.expandAll()
        completed_index = self.rail_model.index(RAIL_GROUPS.index("completed"), 0)
        self.rail_view.collapse(completed_index)   # once, at startup only
        self.rail_view.selectionModel().currentChanged.connect(
            self._on_rail_current_changed
        )

        self.progress_tab = ProgressTab()
        self.files_tab = FilesTab()
        self.errors_tab = ErrorsTab(
            on_retry=self._on_retry_errors,
            on_exclude=self._on_exclude_errors,
            on_copy=self._on_copy_errors,
            on_expand=self._on_expand_error_group,
        )
        self.summary_tab = SummaryTab(
            on_open_report=self._on_open_report,
            on_resume=self._on_resume_from_summary,
        )

        self.tabs = QTabWidget()
        self.tabs.addTab(self.progress_tab, "Progress")
        self.tabs.addTab(self.files_tab, "Files")
        self.tabs.addTab(self.errors_tab, "Errors")
        self.tabs.addTab(self.summary_tab, "Summary")

        splitter = QSplitter()
        splitter.addWidget(self.rail_view)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.banner)
        layout.addWidget(splitter)
        self.setCentralWidget(central)

        self._build_toolbar()
        self.statusBar()

        self.watcher = JobWatcher(self)
        self.watcher.snapshot.connect(self._on_watcher_snapshot)
        self.watcher.state.connect(self._on_watcher_state)
        self.watcher.settled.connect(self._on_watcher_settled)

        self.poller = JobsPoller(self)
        self.poller.jobs.connect(self._on_jobs)
        self.poller.down.connect(self._on_down)
        self.poller.start(self.client, interval=self._poll_interval)

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Main")

        self.new_transfer_action = QAction("New Transfer", self)
        self.new_transfer_action.triggered.connect(self._open_new_transfer)
        toolbar.addAction(self.new_transfer_action)

        toolbar.addSeparator()

        self.pause_action = QAction("Pause", self)
        self.pause_action.triggered.connect(self._on_pause)
        toolbar.addAction(self.pause_action)

        self.resume_action = QAction("Resume", self)
        self.resume_action.triggered.connect(self._on_resume)
        toolbar.addAction(self.resume_action)

        self.cancel_action = QAction("Cancel", self)
        self.cancel_action.triggered.connect(self._on_cancel)
        toolbar.addAction(self.cancel_action)

        toolbar.addSeparator()

        connections_action = QAction("Connections…", self)
        connections_action.triggered.connect(self._open_connections)
        toolbar.addAction(connections_action)

        settings_action = QAction("Settings…", self)
        settings_action.triggered.connect(self._open_settings)
        toolbar.addAction(settings_action)

        self._update_action_states()

    def _update_action_states(self) -> None:
        status = self._selected_status
        self.pause_action.setEnabled(status in _PAUSABLE)
        self.resume_action.setEnabled(status in _RESUMABLE)
        self.cancel_action.setEnabled(status in _CANCELLABLE)

    # -- rail: selection, sync, reselect -----------------------------

    def _on_rail_current_changed(self, current, _previous) -> None:
        job_id = current.data(JOB_ID_ROLE)
        if job_id is None:               # a group header, not a job row
            return
        if job_id == self._selected_job_id:
            return
        self._selected_job_id = job_id
        self._selected_status = current.data(STATUS_ROLE)
        self._update_action_states()

        self.progress_tab.reset()
        self.errors_tab.load_groups([])
        self.files_tab.attach(lambda **kw: self.client.files(job_id, **kw))
        self.watcher.start(self.client, job_id)

        call_async(lambda: self.client.errors(job_id), parent=self,
                   on_done=self._render_errors, on_failed=self._status_message)
        call_async(lambda: self.client.get_job(job_id), parent=self,
                   on_done=self._render_job, on_failed=self._status_message)

    def _find_rail_index(self, job_id: int):
        for row in range(self.rail_model.rowCount()):
            parent_item = self.rail_model.item(row)
            for child_row in range(parent_item.rowCount()):
                child = parent_item.child(child_row)
                if child.data(JOB_ID_ROLE) == job_id:
                    return self.rail_model.indexFromItem(child)
        return None

    def _on_jobs(self, jobs: list[dict]) -> None:
        self.banner.hide()
        sync_rail(self.rail_model, jobs)
        target = self._pending_select or self._selected_job_id
        if target is None:
            return
        index = self._find_rail_index(target)
        if index is None:
            return
        self.rail_view.setCurrentIndex(index)
        if self._pending_select is not None:
            self._pending_select = None

    def _on_down(self, _message: str) -> None:
        self.banner.show()

    # -- test/UI hooks ------------------------------------------------

    def rail_job_ids(self) -> list[int]:
        if self.rail_model is None:
            return []
        return _rail_job_ids(self.rail_model)

    def select_job(self, job_id: int) -> None:
        index = self._find_rail_index(job_id) if self.rail_model is not None else None
        if index is not None:
            self.rail_view.setCurrentIndex(index)

    @property
    def selected_job_id(self) -> int | None:
        return self._selected_job_id

    def shutdown(self) -> None:
        if self.poller is not None:
            self.poller.stop()
        if self.watcher is not None:
            self.watcher.stop()

    # -- job rendering --------------------------------------------------

    def _render_job(self, job: dict) -> None:
        self._selected_status = job.get("status", self._selected_status)
        self._update_action_states()
        snap = {**job, "transferring": job.get("transferring", [])}
        self.progress_tab.update_snapshot(snap)
        self.summary_tab.update_job(job)

    def _render_summary_only(self, job: dict) -> None:
        self._selected_status = job.get("status", self._selected_status)
        self._update_action_states()
        self.summary_tab.update_job(job)

    def _render_errors(self, raw: list[dict]) -> None:
        self.errors_tab.load_groups(build_error_groups(raw))

    def _refresh_selected_job(self) -> None:
        job_id = self._selected_job_id
        if job_id is None:
            return
        call_async(lambda: self.client.get_job(job_id), parent=self,
                   on_done=self._render_job, on_failed=self._status_message)
        call_async(lambda: self.client.errors(job_id), parent=self,
                   on_done=self._render_errors, on_failed=self._status_message)
        self.files_tab.refresh()
        self._poke_rail()

    def _poke_rail(self) -> None:
        call_async(self.client.list_jobs, parent=self, on_done=self._on_jobs)

    def _status_message(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)

    # -- watcher signals --------------------------------------------------

    def _on_watcher_snapshot(self, snap: dict) -> None:
        self.progress_tab.update_snapshot(snap)
        if snap.get("events"):
            job_id = self._selected_job_id
            if job_id is not None:
                call_async(lambda: self.client.errors(job_id), parent=self,
                           on_done=self._render_errors)
                call_async(lambda: self.client.get_job(job_id), parent=self,
                           on_done=self._render_summary_only)

    def _on_watcher_state(self, state: str) -> None:
        if state == "streaming":
            self.statusBar().clearMessage()
        elif state == "reconnecting":
            self.statusBar().showMessage("Connection to the service lost — reconnecting…")
        elif state == "waiting":
            self.statusBar().showMessage("The service is still retrying this job…")

    def _on_watcher_settled(self, final) -> None:
        if final is None:
            return
        self._render_job(final)
        self._poke_rail()

    # -- toolbar actions --------------------------------------------------

    def _open_new_transfer(self) -> None:
        wizard = NewTransferWizard(self.client, self)
        wizard.jobSubmitted.connect(self._on_job_submitted)
        wizard.show()

    def _on_job_submitted(self, job_id: int) -> None:
        self._pending_select = job_id
        self._poke_rail()

    def _open_connections(self) -> None:
        ConnectionsDialog(self.client, self).exec()

    def _open_settings(self) -> None:
        SettingsDialog(self.client, self).exec()

    def _run_job_action(self, method) -> None:
        job_id = self._selected_job_id
        if job_id is None:
            return
        call_async(lambda: method(job_id), parent=self,
                   on_done=lambda _r: self._on_job_action_done(job_id),
                   on_failed=self._status_message)

    def _on_job_action_done(self, job_id: int) -> None:
        if job_id == self._selected_job_id:
            self.watcher.start(self.client, job_id)
            self._refresh_selected_job()
        else:
            self._poke_rail()

    def _on_pause(self) -> None:
        self._run_job_action(self.client.pause)

    def _on_resume(self) -> None:
        self._run_job_action(self.client.resume)

    def _on_cancel(self) -> None:
        self._run_job_action(self.client.cancel)

    # -- errors tab callbacks -----------------------------------------

    def _on_expand_error_group(self, category: str) -> list[str]:
        job_id = self._selected_job_id
        if job_id is None:
            return []
        return fetch_group_paths(self.client, job_id, category)

    def _on_retry_errors(self, category: str) -> None:
        job_id = self._selected_job_id
        if job_id is None:
            return
        should_resume = self._selected_status in ("paused", "incomplete", "cancelled")

        def do() -> dict:
            result = self.client.retry_errors(job_id, category)
            if should_resume:
                self.client.resume(job_id)
            return result

        call_async(do, parent=self,
                   on_done=lambda result: self._retry_done(job_id, result),
                   on_failed=self._status_message)

    def _retry_done(self, job_id: int, result: dict) -> None:
        self.watcher.start(self.client, job_id)
        self._refresh_selected_job()
        self.statusBar().showMessage(f"Retrying {result['count']} file(s)", 5000)

    def _on_exclude_errors(self, category: str) -> None:
        job_id = self._selected_job_id
        if job_id is None:
            return
        answer = QMessageBox.question(
            self, "Stop retrying these files?",
            "They stay recorded as excluded and the job will finish as INCOMPLETE.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        call_async(lambda: self.client.exclude_errors(job_id, category), parent=self,
                   on_done=lambda _r: self._refresh_selected_job(),
                   on_failed=self._status_message)

    def _on_copy_errors(self, category: str) -> None:
        job_id = self._selected_job_id
        if job_id is None:
            return
        call_async(lambda: fetch_group_paths(self.client, job_id, category), parent=self,
                   on_done=self._copy_done, on_failed=self._status_message)

    def _copy_done(self, paths: list[str]) -> None:
        QGuiApplication.clipboard().setText("\n".join(paths))
        self.statusBar().showMessage(f"Copied {len(paths):,} file path(s)", 5000)

    # -- summary tab callbacks -----------------------------------------

    def _on_open_report(self) -> None:
        job_id = self._selected_job_id
        if job_id is None:
            return
        call_async(lambda: self.client.report(job_id), parent=self,
                   on_done=self._report_ready, on_failed=self._status_message)

    def _report_ready(self, result: dict) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(result["report_html"]))

    def _on_resume_from_summary(self) -> None:
        job_id = self._selected_job_id
        if job_id is None:
            return
        call_async(lambda: self.client.resume(job_id), parent=self,
                   on_done=lambda _r: self._on_job_action_done(job_id),
                   on_failed=self._status_message)
