"""New Transfer: a single QDialog screen (direction well, side-by-side
source/connection fields, inline preview, More options disclosure) rather
than a page-by-page wizard. build_submission has one, direction-agnostic
shape to turn a WizardState into a POST /jobs body: source is always the
LOCAL folder, prefix always the REMOTE one, in both directions. The live
preview runs preview_scan on a plain daemon thread (via call_async, which
already spawns one) and streams partial totals back through a dedicated Qt
signal — Qt queues a cross-thread emission automatically, so the label
update is safe. A threading.Event cancels a still-running scan when the
source/direction/profile changes again or the dialog closes, so a slow tree
never keeps writing into a label that no longer means anything. Edits to the
source field are debounced 400ms before the preview restarts, so fast typing
doesn't launch a scan per keystroke.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import PurePath

from PySide6.QtCore import QDateTime, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mml_cloud_courier.cli.service_client import ServiceError
from mml_cloud_courier.core.models import PlannedFile
from mml_cloud_courier.core.paths import resolve_mapped_drive
from mml_cloud_courier.core.scanner import ScanTotals, iter_source
from mml_cloud_courier.gui.connection_dialogs import NewConnectionDialog
from mml_cloud_courier.gui.format import human_bytes
from mml_cloud_courier.gui.workers import call_async

_DUPLICATE = re.compile(r"\bjob (\d+) \(")


# ---------------------------------------------------------------------------
# Pure logic: state shape, submission body, duplicate parsing, scan preview.
# ---------------------------------------------------------------------------


@dataclass
class WizardState:
    direction: str = "upload"
    profile_name: str | None = None
    source: str = ""
    prefix: str = ""
    job_name: str = ""
    audit_hash: bool = False
    scheduled_at: str | None = None


def build_submission(state: WizardState, *, resolver=None) -> dict:
    kwargs = {"resolver": resolver} if resolver is not None else {}
    return {
        "name": state.job_name,
        "direction": state.direction,
        "source_root": resolve_mapped_drive(state.source, **kwargs),
        "dest_prefix": state.prefix,
        "profile": state.profile_name,
        "bucket": None,
        "credentials_path": None,
        "emulator_endpoint": None,
        "audit_hash": state.audit_hash,
        "scheduled_start_at": state.scheduled_at,
    }


def parse_duplicate_job_id(detail: str) -> int | None:
    match = _DUPLICATE.search(detail)
    return int(match.group(1)) if match else None


def preview_scan(root, *, emit, cancelled, batch: int = 250):
    files = byte_count = errors = 0
    for index, entry in enumerate(iter_source(root)):
        if cancelled():
            return None
        if isinstance(entry, PlannedFile):
            files += 1
            byte_count += entry.size_bytes
        else:
            errors += 1
        if (index + 1) % batch == 0:
            emit(files, byte_count, errors)
    emit(files, byte_count, errors)
    return ScanTotals(files, byte_count, errors)


# ---------------------------------------------------------------------------
# The wizard itself
# ---------------------------------------------------------------------------


class NewTransferWizard(QDialog):
    jobSubmitted = Signal(int)
    previewUpdated = Signal(int, int, int)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.state = WizardState()
        self.profile_id: int | None = None
        self._profiles: list[dict] = []
        self._name_touched = False
        self._scan_cancel = threading.Event()
        self.setWindowTitle("New transfer")

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(400)
        self._preview_timer.timeout.connect(self._restart_preview)

        self.previewUpdated.connect(self._on_preview_update)

        layout = QVBoxLayout(self)

        # -- direction well -------------------------------------------
        self.upload_button = QPushButton("Upload")
        self.download_button = QPushButton("Download")
        for button in (self.upload_button, self.download_button):
            button.setObjectName("segmentButton")
            button.setCheckable(True)
        self.upload_button.setChecked(True)

        self._direction_group = QButtonGroup(self)
        self._direction_group.setExclusive(True)
        self._direction_group.addButton(self.upload_button)
        self._direction_group.addButton(self.download_button)

        well = QWidget()
        well.setObjectName("segmentWell")
        well_layout = QHBoxLayout(well)
        well_layout.setContentsMargins(2, 2, 2, 2)
        well_layout.setSpacing(0)
        well_layout.addWidget(self.upload_button)
        well_layout.addWidget(self.download_button)
        layout.addWidget(well)

        self._direction_group.buttonToggled.connect(self._on_direction_toggled)

        # -- left column: local folder ---------------------------------
        self.source_label = QLabel()
        self.source_edit = QLineEdit()
        self.source_browse = QPushButton("Browse…")
        self.source_browse.clicked.connect(self._browse_source)
        self.mapped_label = QLabel("")
        self.mapped_label.setWordWrap(True)

        self.source_edit.textChanged.connect(self._update_mapped_label)
        self.source_edit.textChanged.connect(self._on_source_changed)

        source_row = QHBoxLayout()
        source_row.addWidget(self.source_edit, 1)
        source_row.addWidget(self.source_browse)

        left_column = QVBoxLayout()
        left_column.addWidget(self.source_label)
        left_column.addLayout(source_row)
        left_column.addWidget(self.mapped_label)

        # -- right column: connection + remote prefix -------------------
        self.profile_combo = QComboBox()
        self.refresh_button = QPushButton("Refresh")
        self.new_button = QPushButton("New connection…")
        self.refresh_button.clicked.connect(self._refresh)
        self.new_button.clicked.connect(self._open_new_connection)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)

        connection_row = QHBoxLayout()
        connection_row.addWidget(self.profile_combo, 1)
        connection_row.addWidget(self.refresh_button)
        connection_row.addWidget(self.new_button)

        self.prefix_label = QLabel()
        self.prefix_edit = QLineEdit()
        self.prefix_edit.textChanged.connect(self._on_prefix_changed)

        right_column = QVBoxLayout()
        right_column.addLayout(connection_row)
        right_column.addWidget(self.prefix_label)
        right_column.addWidget(self.prefix_edit)

        columns_row = QHBoxLayout()
        columns_row.addLayout(left_column, 1)
        columns_row.addLayout(right_column, 1)
        layout.addLayout(columns_row)

        self._update_direction_labels()

        # -- live preview -------------------------------------------------
        self.preview_label = QLabel("")
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label)

        # -- job name -------------------------------------------------
        self.name_edit = QLineEdit()
        self.name_edit.textEdited.connect(lambda _t: setattr(self, "_name_touched", True))
        form = QFormLayout()
        form.addRow("Job name:", self.name_edit)
        layout.addLayout(form)

        # -- more options disclosure --------------------------------------
        self.start_later_checkbox = QCheckBox("Start later")
        self.datetime_edit = QDateTimeEdit()
        self.datetime_edit.setCalendarPopup(True)
        now = QDateTime.currentDateTime()
        self.datetime_edit.setMinimumDateTime(now)
        self.datetime_edit.setDateTime(now)
        self.datetime_edit.setEnabled(False)
        self.start_later_checkbox.toggled.connect(self.datetime_edit.setEnabled)

        self.audit_checkbox = QCheckBox("Also compute SHA-256 audit hashes")
        self.audit_note = QLabel(
            "Roughly doubles local read time for files over 1 GB."
        )
        self.audit_note.setWordWrap(True)

        self.more_options_button = QToolButton()
        self.more_options_button.setText("More options")
        self.more_options_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.more_options_button.setCheckable(True)
        self.more_options_button.setArrowType(Qt.ArrowType.RightArrow)
        self.more_options_button.toggled.connect(self._on_more_options_toggled)

        self.more_options_widget = QWidget()
        more_layout = QVBoxLayout(self.more_options_widget)
        more_layout.setContentsMargins(0, 0, 0, 0)
        more_layout.addWidget(self.start_later_checkbox)
        more_layout.addWidget(self.datetime_edit)
        more_layout.addWidget(self.audit_checkbox)
        more_layout.addWidget(self.audit_note)
        self.more_options_widget.setVisible(False)

        layout.addWidget(self.more_options_button)
        layout.addWidget(self.more_options_widget)

        # -- status + bottom row --------------------------------------
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.cancel_button = QPushButton("Cancel")
        self.start_button = QPushButton("Start transfer")
        self.start_button.setObjectName("primaryButton")
        self.cancel_button.clicked.connect(self.reject)
        self.start_button.clicked.connect(self.accept_and_submit)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.start_button)
        layout.addLayout(button_row)

        # Loading on construction means the combo is already populating
        # the moment the dialog exists.
        self._refresh()

    # -- direction well -------------------------------------------------

    def _on_direction_toggled(self, button, checked: bool) -> None:
        if not checked:
            return
        self.state.direction = "upload" if button is self.upload_button else "download"
        self._update_direction_labels()
        self._preview_timer.start()

    def _update_direction_labels(self) -> None:
        upload = self.state.direction == "upload"
        self.source_label.setText(
            "Source folder (this computer):" if upload
            else "Destination folder (this computer):"
        )
        self.prefix_label.setText(
            "Destination prefix (in the bucket):" if upload
            else "Source prefix (in the bucket):"
        )

    def set_direction(self, direction: str) -> None:
        self.upload_button.setChecked(direction == "upload")
        self.download_button.setChecked(direction != "upload")

    # -- connection loading ----------------------------------------------

    def _refresh(self) -> None:
        self.status_label.setText("Loading connections…")
        call_async(self.client.list_profiles, parent=self,
                   on_done=self._loaded, on_failed=self._failed)

    def _loaded(self, profiles: list[dict]) -> None:
        self._profiles = profiles
        self.profile_combo.clear()
        for profile in profiles:
            target = f"gs://{profile['bucket']}/{profile['default_prefix']}".rstrip("/")
            self.profile_combo.addItem(f"{profile['name']} — {target}")
        self.status_label.setText(
            "" if profiles else "No connections yet — create one below."
        )

    def _failed(self, message: str) -> None:
        self.status_label.setText(message)

    def _open_new_connection(self) -> None:
        dialog = NewConnectionDialog(self.client, self)
        dialog.created.connect(lambda _result: self._refresh())
        dialog.exec()

    def _on_profile_changed(self, index: int) -> None:
        if 0 <= index < len(self._profiles):
            profile = self._profiles[index]
            self.state.profile_name = profile["name"]
            self.profile_id = profile["id"]
        else:
            self.state.profile_name = None
            self.profile_id = None
        self._preview_timer.start()

    def set_profile_by_name(self, name: str) -> bool:
        for index, profile in enumerate(self._profiles):
            if profile["name"] == name:
                self.profile_combo.setCurrentIndex(index)
                return True
        return False

    # -- local folder ------------------------------------------------

    def _browse_source(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a folder")
        if path:
            self.source_edit.setText(path)

    def _update_mapped_label(self, _text: str = "") -> None:
        path = self.source_edit.text()
        if not path:
            self.mapped_label.setText("")
            return
        normalized = path.replace("/", "\\")
        resolved = resolve_mapped_drive(path)
        if resolved != normalized:
            self.mapped_label.setText(
                f"{normalized} → {resolved}\n"
                "(the service cannot see your drive letters)"
            )
        else:
            self.mapped_label.setText("")

    def _on_source_changed(self, text: str) -> None:
        self.state.source = text.strip()
        if not self._name_touched:
            leaf = PurePath(self.state.source).name or self.state.source or "transfer"
            self.name_edit.setText(f"{leaf}-{date.today().isoformat()}")
        self._preview_timer.start()

    def _on_prefix_changed(self, text: str) -> None:
        self.state.prefix = text.strip()

    def set_source(self, path: str) -> None:
        self.source_edit.setText(path)

    def set_prefix(self, prefix: str) -> None:
        self.prefix_edit.setText(prefix)

    def set_job_name(self, name: str) -> None:
        self.name_edit.setText(name)
        self._name_touched = True

    # -- more options -----------------------------------------------------

    def _on_more_options_toggled(self, checked: bool) -> None:
        self.more_options_widget.setVisible(checked)
        self.more_options_button.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )

    # -- status -------------------------------------------------------

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def sync_state(self) -> None:
        """Fold widget values into self.state. Called both from
        accept_and_submit (normal Start flow) and directly by callers
        (e.g. tests) that want the state synced without submitting."""
        state = self.state
        state.job_name = self.name_edit.text().strip()
        state.audit_hash = self.audit_checkbox.isChecked()
        if self.start_later_checkbox.isChecked():
            state.scheduled_at = (
                self.datetime_edit.dateTime().toPython().astimezone()
                .isoformat(timespec="seconds")
            )
        else:
            state.scheduled_at = None

    # -- scan preview -----------------------------------------------------

    def cancel_preview(self) -> None:
        self._scan_cancel.set()
        self._preview_timer.stop()

    def _restart_preview(self) -> None:
        self._scan_cancel.set()
        cancel_event = threading.Event()
        self._scan_cancel = cancel_event
        state = self.state
        if not state.source and state.direction == "upload":
            self.preview_label.setText("")
            return

        def guarded_emit(files: int, byte_count: int, errors: int) -> None:
            if not cancel_event.is_set():
                self.previewUpdated.emit(files, byte_count, errors)

        if state.direction == "upload":
            self.preview_label.setText("Scanning…")
            call_async(
                lambda: preview_scan(
                    state.source, emit=guarded_emit, cancelled=cancel_event.is_set,
                ),
                parent=self, on_done=self._preview_finished,
                on_failed=self._preview_failed,
            )
        else:
            profile_id = self.profile_id
            if profile_id is None:
                self.preview_label.setText("")
                return
            self.preview_label.setText("Checking the bucket…")
            prefix = state.prefix or None
            call_async(
                lambda: self.client.preview_remote(profile_id, prefix),
                parent=self, on_done=self._remote_preview_done,
                on_failed=self._preview_failed,
            )

    def _on_preview_update(self, files: int, byte_count: int, errors: int) -> None:
        text = f"{files:,} files, {human_bytes(byte_count)} so far…"
        if errors:
            text += f" — {errors} unreadable"
        self.preview_label.setText(text)

    def _preview_finished(self, totals: ScanTotals | None) -> None:
        if totals is None:
            return  # cancelled: state has moved on, nothing to show
        text = f"{totals.file_count:,} files, {human_bytes(totals.byte_count)}"
        if totals.error_count:
            text += f" — {totals.error_count} unreadable"
        self.preview_label.setText(text)

    def _remote_preview_done(self, result: dict) -> None:
        objects = result["objects"]
        total_bytes = result["bytes"]
        lead = "at least " if result.get("truncated") else ""
        self.preview_label.setText(
            f"{lead}{objects:,} files, {lead}{human_bytes(total_bytes)}"
        )

    def _preview_failed(self, message: str) -> None:
        self.preview_label.setText(message)

    # -- finish -------------------------------------------------------

    def accept_and_submit(self) -> None:
        index = self.profile_combo.currentIndex()
        if index < 0 or index >= len(self._profiles):
            self.set_status("Select a connection to continue, or create one.")
            return
        if not self.source_edit.text().strip():
            self.set_status("Choose a local folder to continue.")
            return
        if not self.name_edit.text().strip():
            self.set_status("Give the job a name to continue.")
            return

        # Finish always supersedes an in-flight scan preview: whatever it
        # would eventually report is no longer relevant once submission is
        # under way. QDialog.accept()/reject() call hide(), not close(), so
        # closeEvent alone would miss this path.
        self.cancel_preview()
        self.sync_state()
        body = build_submission(self.state)
        self.set_status("Submitting…")

        def submit() -> dict:
            try:
                return {"ok": True, "result": self.client.submit_job(body)}
            except ServiceError as exc:
                return {"ok": False, "status_code": exc.status_code, "detail": exc.detail}

        call_async(submit, parent=self, on_done=self._submit_done,
                   on_failed=self.set_status)

    def _submit_done(self, outcome: dict) -> None:
        if outcome["ok"]:
            self.set_status("")
            self.jobSubmitted.emit(outcome["result"]["job_id"])
            super().accept()
            return

        status_code = outcome["status_code"]
        detail = outcome["detail"]
        if status_code == 409:
            job_id = parse_duplicate_job_id(detail)
            if job_id is not None:
                answer = QMessageBox.question(
                    self, "Resume job", f"Resume job #{job_id} instead?",
                )
                if answer == QMessageBox.StandardButton.Yes:
                    self._resume(job_id)
                    return
        self.set_status(detail)

    def _resume(self, job_id: int) -> None:
        call_async(lambda: self.client.resume(job_id), parent=self,
                   on_done=lambda _result: self._resumed(job_id),
                   on_failed=self.set_status)

    def _resumed(self, job_id: int) -> None:
        self.jobSubmitted.emit(job_id)
        super().accept()

    def reject(self) -> None:
        # Cancel button: same hide()-not-close() consideration as accept().
        self.cancel_preview()
        super().reject()

    def closeEvent(self, event) -> None:
        # Belt-and-braces for a direct window-manager close.
        self.cancel_preview()
        super().closeEvent(event)
