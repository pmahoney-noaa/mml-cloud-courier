"""The integration hub: grouped rail, four job tabs, toolbar, and the
service-down banner. Everything built in Tasks 10-17 is wired together
here — the rail drives selection, selection drives a JobWatcher plus an
immediate get_job render, and every mutating action (pause/resume/cancel,
error retry/exclude, report) round-trips through call_async and then
refreshes what it touched.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

import mml_cloud_courier
from mml_cloud_courier.gui import theme
from mml_cloud_courier.gui.connection_dialogs import ConnectionsDialog
from mml_cloud_courier.gui.errors_model import (
    build_error_groups,
    fetch_group_page,
    fetch_group_paths,
)
from mml_cloud_courier.gui.errors_view import ErrorsTab, needs_you_count
from mml_cloud_courier.gui.first_run import FirstRunScreen
from mml_cloud_courier.gui.job_tabs import FilesTab, ProgressTab, SummaryTab
from mml_cloud_courier.gui.jobs_model import (
    JOB_ID_ROLE,
    RAIL_GROUPS,
    STATUS_ROLE,
    build_rail_model,
    rail_job_ids as _rail_job_ids,
    sync_rail,
)
from mml_cloud_courier.gui.rail_delegate import RailDelegate
from mml_cloud_courier.gui.service_control import start_service_elevated
from mml_cloud_courier.gui.session import ServiceSession, discover_session
from mml_cloud_courier.gui.settings_dialog import SettingsDialog
from mml_cloud_courier.gui.status_pill import StatusPill
from mml_cloud_courier.gui.tray import TrayController
from mml_cloud_courier.gui.wizard import NewTransferWizard
from mml_cloud_courier.gui.workers import JobWatcher, JobsPoller, call_async

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
        self.setWindowTitle("MML Cloud Courier")
        self.resize(1100, 700)

        self._selected_job_id: int | None = None
        self._selected_status: str | None = None
        self._pending_select: int | None = None
        self._last_statuses: dict[int, str] = {}
        self._last_jobs: list[dict] = []
        self.poller: JobsPoller | None = None
        self.watcher: JobWatcher | None = None
        self.rail_model = None
        self.rail_view: QTreeView | None = None
        self._tray: TrayController | None = None
        self._theme_changed_slot = None

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
        # _update_action_states (called from _build_toolbar) reads
        # _no_connections, so it must exist before _build_toolbar() runs.
        self._no_connections = False
        self.setAcceptDrops(True)
        self.banner = QWidget()
        self.banner.setObjectName("serviceBanner")
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(6, 6, 6, 6)
        self.banner_label = QLabel(BANNER_TEXT)
        self.banner_label.setWordWrap(True)
        banner_layout.addWidget(self.banner_label, 1)
        self.banner_start_button = QPushButton("Start the service")
        self.banner_start_button.clicked.connect(self._on_start_service)
        banner_layout.addWidget(self.banner_start_button)
        self.banner.hide()

        self.rail_model = build_rail_model()
        self.rail_view = QTreeView()
        self.rail_view.setObjectName("railView")
        self.rail_view.setModel(self.rail_model)
        self.rail_view.setHeaderHidden(True)
        self.rail_view.setItemDelegate(RailDelegate(self.rail_view))
        self.rail_view.setFixedWidth(262)
        self.rail_view.expandAll()
        completed_index = self.rail_model.index(RAIL_GROUPS.index("completed"), 0)
        self.rail_view.collapse(completed_index)   # once, at startup only
        self.rail_view.selectionModel().currentChanged.connect(
            self._on_rail_current_changed
        )
        # theme.notifier is a module-level singleton that outlives this window,
        # so a bound-and-tracked slot (disconnected in shutdown()) is used
        # instead of an anonymous lambda -- Qt won't auto-drop the connection
        # when rail_view's C++ object is deleted, and a live test suite creates
        # and destroys many MainWindows against the same notifier.
        self._theme_changed_slot = lambda _t: self.rail_view.viewport().update()
        theme.notifier.changed.connect(self._theme_changed_slot)

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

        self._first_run = FirstRunScreen(
            on_add_connection=self._open_connections,
            on_open_guide=self._open_setup_guide,
        )
        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(splitter)
        self._content_stack.addWidget(self._first_run)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.banner)
        layout.addWidget(self._content_stack)
        self.setCentralWidget(central)

        self._build_toolbar()
        self.statusBar()

        self._tray = TrayController(self)

        self.watcher = JobWatcher(self)
        self.watcher.snapshot.connect(self._on_watcher_snapshot)
        self.watcher.state.connect(self._on_watcher_state)
        self.watcher.settled.connect(self._on_watcher_settled)

        self.poller = JobsPoller(self)
        self.poller.jobs.connect(self._on_jobs)
        self.poller.down.connect(self._on_down)
        self.poller.start(self.client, interval=self._poll_interval)

        call_async(self.client.list_profiles, parent=self, on_done=self._on_profiles)

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)

        self.new_transfer_button = QPushButton("New transfer")
        self.new_transfer_button.setObjectName("primaryButton")
        self.new_transfer_button.clicked.connect(self._open_new_transfer)
        toolbar.addWidget(self.new_transfer_button)

        well = QWidget()
        well.setObjectName("segmentWell")
        well_layout = QHBoxLayout(well)
        well_layout.setContentsMargins(2, 2, 2, 2)
        well_layout.setSpacing(0)
        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.cancel_button = QPushButton("Cancel")
        for button, slot in ((self.pause_button, self._on_pause),
                             (self.resume_button, self._on_resume),
                             (self.cancel_button, self._on_cancel)):
            button.setObjectName("segmentButton")
            button.clicked.connect(slot)
            well_layout.addWidget(button)
        toolbar.addWidget(well)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        self.pill = StatusPill()
        toolbar.addWidget(self.pill)

        for text, slot in (("Connections", self._open_connections),
                           ("Settings", self._open_settings)):
            button = QPushButton(text)
            button.setObjectName("textButton")
            button.clicked.connect(slot)
            toolbar.addWidget(button)

        self._service_up = True
        self._update_action_states()

    def _update_action_states(self) -> None:
        status = self._selected_status
        up = self._service_up
        self.new_transfer_button.setEnabled(up and not self._no_connections)
        self.pause_button.setEnabled(up and status in _PAUSABLE)
        self.resume_button.setEnabled(up and status in _RESUMABLE)
        self.cancel_button.setEnabled(up and status in _CANCELLABLE)

    def _update_first_run(self) -> None:
        show_first_run = self._no_connections and not self._last_jobs
        self._content_stack.setCurrentWidget(
            self._first_run if show_first_run else self._content_stack.widget(0)
        )

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
        self.summary_tab.set_causes(None, None)   # drop the previous job's causes
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
        self._last_jobs = jobs
        self._service_up = True
        self.banner.hide()
        self.pill.set_state("noconn" if self._no_connections else "ok")
        self._update_action_states()
        if self._tray is not None:
            self._tray.notify_transitions(self._last_statuses, jobs)
        self._last_statuses = {job["id"]: job["status"] for job in jobs}
        sync_rail(self.rail_model, jobs, service_up=self._service_up)
        self._update_first_run()
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
        was_up = self._service_up
        self._service_up = False
        self.pill.set_state("down")
        self._update_action_states()
        self.banner.show()
        # The poller calls this on every failed tick, not just the up->down
        # transition. sync_rail destroys and recreates rows, which wipes the
        # rail's selection -- so only re-sync (and reselect) on the actual
        # transition; a repeat failed tick would otherwise clear the user's
        # selection on every miss.
        if was_up:
            sync_rail(self.rail_model, self._last_jobs, service_up=False)
            if self._selected_job_id is not None:
                index = self._find_rail_index(self._selected_job_id)
                if index is not None:
                    self.rail_view.setCurrentIndex(index)

    def _on_profiles(self, profiles: list) -> None:
        self._no_connections = not profiles
        if self._service_up:
            self.pill.set_state("noconn" if self._no_connections else "ok")
        self._update_action_states()
        self._update_first_run()

    def _on_start_service(self) -> None:
        if start_service_elevated():
            self.statusBar().showMessage("Start requested — waiting for the service…")
        else:
            QMessageBox.warning(
                self, "Couldn't start the service",
                "Windows refused or the prompt was cancelled. Ask your"
                " administrator to start the 'MML Cloud Courier' service.",
            )

    def closeEvent(self, event) -> None:
        if self._tray is not None and self._tray.handle_close(event):
            return
        super().closeEvent(event)

    # -- drop-a-folder-to-start-a-transfer -----------------------------

    def dragEnterEvent(self, event) -> None:
        # A drop must never open a dead-end wizard: mirrors the same gate
        # that dims the New transfer button.
        if not self._service_up or self._no_connections:
            event.ignore()
            return
        urls = event.mimeData().urls()
        if len(urls) != 1 or not urls[0].isLocalFile():
            event.ignore()
            return
        if not Path(urls[0].toLocalFile()).is_dir():
            event.ignore()
            return
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        # toLocalFile() yields "/"-separated paths even on Windows; round
        # -trip through Path so prefill_source lands in native form, same
        # as anything typed into the source field by hand.
        path = str(Path(event.mimeData().urls()[0].toLocalFile()))
        self._open_new_transfer(prefill_source=path)
        event.acceptProposedAction()

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
        if self._theme_changed_slot is not None:
            theme.notifier.changed.disconnect(self._theme_changed_slot)
            self._theme_changed_slot = None
        if self._tray is not None:
            self._tray.shutdown()

    # -- job rendering --------------------------------------------------

    def _render_job(self, job: dict) -> None:
        self._selected_status = job.get("status", self._selected_status)
        self._update_action_states()
        snap = {**job, "transferring": job.get("transferring", [])}
        self.progress_tab.update_snapshot(snap)
        self.summary_tab.update_job(job)
        self.files_tab.set_total(job.get("planned_files"))
        self.errors_tab.set_files_total((job.get("progress") or {}).get("files_total"))

    def _render_summary_only(self, job: dict) -> None:
        self._selected_status = job.get("status", self._selected_status)
        self._update_action_states()
        self.summary_tab.update_job(job)

    def _render_errors(self, raw: list[dict]) -> None:
        groups = build_error_groups(raw)
        self.errors_tab.load_groups(groups)
        if groups:
            self.summary_tab.set_causes(len(groups), needs_you_count(groups))
        else:
            self.summary_tab.set_causes(None, None)

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

    def _open_new_transfer(self, *, prefill_source: str | None = None) -> None:
        wizard = NewTransferWizard(self.client, self)
        if prefill_source:
            wizard.set_source(prefill_source)
        wizard.jobSubmitted.connect(self._on_job_submitted)
        wizard.show()

    def _on_job_submitted(self, job_id: int) -> None:
        self._pending_select = job_id
        self._poke_rail()

    def _open_connections(self) -> None:
        ConnectionsDialog(self.client, self).exec()
        call_async(self.client.list_profiles, parent=self, on_done=self._on_profiles)

    def _open_settings(self) -> None:
        SettingsDialog(self.client, self).exec()

    def _open_setup_guide(self) -> None:
        guide_path = (
            Path(mml_cloud_courier.__file__).resolve().parents[2] / "docs" / "gui.md"
        )
        if guide_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(guide_path)))
        else:
            QDesktopServices.openUrl(QUrl(
                "https://github.com/pmahoney-noaa/mml-cloud-courier/blob/master/docs/gui.md"
            ))

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
        # Deliberately bounded to a single page: ErrorsTab.load_groups calls
        # this once per always-expanded group, synchronously on the Qt
        # thread, so an unbounded multi-page walk (fetch_group_paths'
        # cap=20000, up to 40 sequential GETs) would jam the UI. One
        # localhost page is the documented FilesTab trade-off; the
        # "...and N more" trailer is sized from the group's already-known
        # count instead (see errors_view.group_fill_rows).
        job_id = self._selected_job_id
        if job_id is None:
            return []
        return fetch_group_page(self.client, job_id, category)

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
