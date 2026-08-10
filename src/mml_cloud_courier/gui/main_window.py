"""The integration hub: grouped rail, four job tabs, toolbar, and the
service-down banner. Everything built in Tasks 10-17 is wired together
here — the rail drives selection, selection drives a JobWatcher plus an
immediate get_job render, and every mutating action (pause/resume/cancel,
error retry/exclude, report) round-trips through call_async and then
refreshes what it touched.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
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
        self._show_archived = False
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
        # The branch column/indentation was the residual left offset the
        # rule-to-the-right-edge/left-alignment pass (wave 1, items B/C)
        # couldn't reach -- it's QTreeView chrome, not delegate paint space.
        # Flattening it means expand/collapse arrows are gone, so clicks on
        # a group header must toggle expansion themselves (below); the
        # nonzero count next to each header is the remaining affordance.
        self.rail_view.setRootIsDecorated(False)
        self.rail_view.setIndentation(0)
        self.rail_view.expandAll()
        completed_index = self.rail_model.index(RAIL_GROUPS.index("completed"), 0)
        self.rail_view.collapse(completed_index)   # once, at startup only
        archived_index = self.rail_model.index(RAIL_GROUPS.index("archived"), 0)
        self.rail_view.setRowHidden(
            archived_index.row(), archived_index.parent(), True)
        self.rail_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.rail_view.customContextMenuRequested.connect(self._show_rail_menu)
        self.rail_view.selectionModel().currentChanged.connect(
            self._on_rail_current_changed
        )
        self.rail_view.clicked.connect(self._on_rail_clicked)
        # theme.notifier is a module-level singleton that outlives this window,
        # so a bound-and-tracked slot (disconnected in shutdown()) is used
        # instead of an anonymous lambda -- Qt won't auto-drop the connection
        # when rail_view's C++ object is deleted, and a live test suite creates
        # and destroys many MainWindows against the same notifier.
        self._theme_changed_slot = lambda _t: self.rail_view.viewport().update()
        theme.notifier.changed.connect(self._theme_changed_slot)

        self._profile_filter: int | None = None
        self.filter_bar = QWidget()
        self.filter_bar.setObjectName("connFilterBar")
        self.filter_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        filter_layout = QHBoxLayout(self.filter_bar)
        filter_layout.setContentsMargins(11, 6, 11, 6)
        filter_layout.setSpacing(9)
        self.filter_label = QLabel("")
        self.filter_label.setWordWrap(True)
        filter_layout.addWidget(self.filter_label, 1)
        self.show_all_button = QPushButton("Show all")
        self.show_all_button.setObjectName("textButton")
        self.show_all_button.clicked.connect(self.clear_profile_filter)
        filter_layout.addWidget(self.show_all_button)
        self.filter_bar.hide()

        rail_column = QWidget()
        rail_column_layout = QVBoxLayout(rail_column)
        rail_column_layout.setContentsMargins(0, 0, 0, 0)
        rail_column_layout.setSpacing(0)
        rail_column_layout.addWidget(self.filter_bar)
        rail_column_layout.addWidget(self.rail_view, 1)
        rail_column.setFixedWidth(262)

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
            on_archive=self._on_archive_from_summary,
        )

        self.tabs = QTabWidget()
        self.tabs.addTab(self.progress_tab, "Progress")
        self.tabs.addTab(self.files_tab, "Files")
        self.tabs.addTab(self.errors_tab, "Errors")
        self.tabs.addTab(self.summary_tab, "Summary")

        splitter = QSplitter()
        splitter.addWidget(rail_column)
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
        self.poller.start(
            self.client, interval=self._poll_interval,
            fetch=lambda: self.client.list_jobs(
                include_archived=self._show_archived))

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

    def _on_rail_clicked(self, index) -> None:
        # No branch arrows (flattened indentation): a click on a group
        # header toggles it directly. Job rows keep going through
        # currentChanged/selection as before -- this only fires for the
        # no-JOB_ID_ROLE header rows.
        if index.data(JOB_ID_ROLE) is None:
            self.rail_view.setExpanded(index, not self.rail_view.isExpanded(index))

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

    def _sync_rail_preserving_expansion(self, jobs: list[dict], *, service_up: bool) -> None:
        """sync_rail, then restore each group's expand state and only
        touch the visible selection if a rebuild actually happened.

        QTreeView.setCurrentIndex auto-expands the selection's ancestors
        to make it visible -- with every job living in Completed, that
        silently reopened a Completed the user had just collapsed on the
        very next poll tick, even though nothing about the jobs changed.
        sync_rail returning False (no-op: same signature as last time)
        is what lets a real collapse stick; when it DOES rebuild, the
        expand states captured before the rebuild are reapplied after.

        Two different intents then compete for the same reselect, and
        they must NOT be treated the same:
        - `_selected_job_id` (passive): a poll tick just re-affirming the
          job the user already has open. Re-shown only if its group is
          still expanded once expansion is restored -- honoring a
          deliberate collapse of the group holding the selection.
          `_selected_job_id` keeps tracking the job either way; only the
          rail's visual current-row is skipped, not the tabs, which stay
          driven by `_selected_job_id` independently of rail_view's
          current index.
        - `_pending_select` (active): the job the user just explicitly
          submitted via the wizard. That must win over a collapsed group
          rather than silently vanish -- the group is force-expanded and
          the job force-selected, same as if the user had clicked it.
        """
        pending = self._pending_select
        if (pending is not None and self._profile_filter is not None
                and any(job["id"] == pending for job in self._last_jobs)
                and not any(job["id"] == pending for job in jobs)):
            # The just-submitted job exists but the profile filter hides
            # it. An explicit submission wins over view state -- the same
            # doctrine as the force-expand below -- so drop the filter;
            # clear_profile_filter re-syncs with the full list and this
            # method's pending branch then selects the job. This has to
            # run before sync_rail below: the filtered `jobs` this method
            # was called with may be identical to what the rail already
            # shows (the new job lives outside the filter), which makes
            # sync_rail a no-op that returns before ever reaching the
            # pending/target/index logic further down.
            self.clear_profile_filter()
            return
        expanded_before = {
            group: self.rail_view.isExpanded(self.rail_model.index(i, 0))
            for i, group in enumerate(RAIL_GROUPS)
        }
        rebuilt = sync_rail(self.rail_model, jobs, service_up=service_up)
        if not rebuilt:
            return
        for i, group in enumerate(RAIL_GROUPS):
            self.rail_view.setExpanded(self.rail_model.index(i, 0), expanded_before[group])

        target = pending if pending is not None else self._selected_job_id
        if target is None:
            return
        index = self._find_rail_index(target)
        if index is None:
            return
        if pending is not None:
            self.rail_view.expand(index.parent())
            self.rail_view.setCurrentIndex(index)
            self._pending_select = None
        elif self.rail_view.isExpanded(index.parent()):
            self.rail_view.setCurrentIndex(index)

    def _on_jobs(self, jobs: list[dict]) -> None:
        self._last_jobs = jobs
        self._service_up = True
        self.banner.hide()
        self.pill.set_state("noconn" if self._no_connections else "ok")
        self._update_action_states()
        if self._tray is not None:
            self._tray.notify_transitions(self._last_statuses, jobs)
        self._last_statuses = {job["id"]: job["status"] for job in jobs}
        self._sync_rail_preserving_expansion(self._filtered_jobs(jobs), service_up=self._service_up)
        self._update_first_run()

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
            self._sync_rail_preserving_expansion(
                self._filtered_jobs(self._last_jobs), service_up=False)

    # -- rail profile filter ------------------------------------------

    def _filtered_jobs(self, jobs: list[dict]) -> list[dict]:
        if self._profile_filter is None:
            return jobs
        return [job for job in jobs
                if job.get("profile_id") == self._profile_filter]

    def _clear_selection_and_tabs(self) -> None:
        """The full deselection reset: mirrors what selecting a different
        job would tear down, so no tab keeps rendering a job the rail no
        longer shows."""
        self.rail_view.selectionModel().clearSelection()
        self._selected_job_id = None
        self._selected_status = None
        self._update_action_states()
        self.watcher.stop()
        self.progress_tab.reset()
        self.errors_tab.load_groups([])
        self.summary_tab.set_causes(None, None)

    def show_jobs_for_profile(self, profile_id: int, name: str) -> None:
        self._profile_filter = profile_id
        self.filter_label.setText(f'Showing jobs using "{name}"')
        self.filter_bar.show()
        filtered_ids = {job["id"] for job in self._filtered_jobs(self._last_jobs)}
        if self._selected_job_id is not None and self._selected_job_id not in filtered_ids:
            self._clear_selection_and_tabs()
        self._sync_rail_preserving_expansion(
            self._filtered_jobs(self._last_jobs), service_up=self._service_up)

    def clear_profile_filter(self) -> None:
        self._profile_filter = None
        self.filter_bar.hide()
        self._sync_rail_preserving_expansion(
            self._last_jobs, service_up=self._service_up)

    # -- archive ------------------------------------------------------

    def _set_show_archived(self, on: bool) -> None:
        self._show_archived = on
        archived_index = self.rail_model.index(RAIL_GROUPS.index("archived"), 0)
        self.rail_view.setRowHidden(
            archived_index.row(), archived_index.parent(), not on)
        if on:
            self.rail_view.collapse(archived_index)    # starts shelved, like Completed
        elif self._selected_job_id is not None:
            selected = next((job for job in self._last_jobs
                             if job["id"] == self._selected_job_id), None)
            if selected is not None and selected.get("archived_at"):
                self._clear_selection_and_tabs()
        self._poke_rail()

    def _rail_menu_spec(self, index) -> list[tuple[str, str, bool]]:
        """(kind, label, checked) triples for the rail context menu at
        `index` -- pure composition so tests can assert it without popping
        a QMenu."""
        spec: list[tuple[str, str, bool]] = []
        job_id = index.data(JOB_ID_ROLE)
        if job_id is not None:
            job = next((j for j in self._last_jobs if j["id"] == job_id), None)
            if job is not None:
                if job.get("archived_at"):
                    spec.append(("unarchive", "Unarchive job", False))
                elif job.get("status") in ("complete", "cancelled"):
                    spec.append(("archive", "Archive job", False))
        spec.append(("toggle_archived", "Show archived", self._show_archived))
        return spec

    def _show_rail_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu
        index = self.rail_view.indexAt(pos)
        job_id = index.data(JOB_ID_ROLE)
        menu = QMenu(self.rail_view)
        for kind, label, checked in self._rail_menu_spec(index):
            action = menu.addAction(label)
            if kind == "toggle_archived":
                action.setCheckable(True)
                action.setChecked(checked)
                action.triggered.connect(
                    lambda on, self=self: self._set_show_archived(on))
            elif kind == "archive":
                action.triggered.connect(
                    lambda _c=False, j=job_id: self._archive_job(j))
            elif kind == "unarchive":
                action.triggered.connect(
                    lambda _c=False, j=job_id: self._unarchive_job(j))
        menu.exec(self.rail_view.viewport().mapToGlobal(pos))

    def _archive_job(self, job_id: int) -> None:
        call_async(lambda: self.client.archive_job(job_id), parent=self,
                   on_done=lambda _r, j=job_id: self._archived(j),
                   on_failed=self._status_message)

    def _archived(self, job_id: int) -> None:
        if job_id == self._selected_job_id:
            self._clear_selection_and_tabs()
        self._poke_rail()

    def _unarchive_job(self, job_id: int) -> None:
        call_async(lambda: self.client.unarchive_job(job_id), parent=self,
                   on_done=lambda _r: self._poke_rail(),
                   on_failed=self._status_message)

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
        call_async(
            lambda: self.client.list_jobs(include_archived=self._show_archived),
            parent=self, on_done=self._on_jobs)

    def _status_message(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)

    # -- watcher signals --------------------------------------------------

    def _on_watcher_snapshot(self, snap: dict) -> None:
        self.progress_tab.update_snapshot(snap)
        self.files_tab.maybe_auto_refresh(snap.get("progress"))
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
        self.files_tab.maybe_auto_refresh(final.get("progress"))
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
        dialog = ConnectionsDialog(self.client, self)
        dialog.showJobsForProfile.connect(self.show_jobs_for_profile)
        dialog.exec()
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

    def _on_archive_from_summary(self) -> None:
        if self._selected_job_id is not None:
            self._archive_job(self._selected_job_id)
