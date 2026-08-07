"""New Transfer wizard: direction -> connection -> folders -> options/review.

The four QWizardPage subclasses only ever mutate the wizard's WizardState
(source is always the LOCAL folder, prefix always the REMOTE one, in both
directions) so build_submission has one, direction-agnostic shape to turn
into a POST /jobs body. The scan preview on the last page runs preview_scan
on a plain daemon thread (via call_async, which already spawns one) and
streams partial totals back through a dedicated Qt signal — Qt queues a
cross-thread emission automatically, so the label update is safe. A
threading.Event cancels a still-running scan when the user leaves the page
or closes the wizard, so a slow tree never keeps writing into a label that
no longer means anything.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import PurePath

from PySide6.QtCore import QDateTime, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from mml_cloud_transfer.cli.service_client import ServiceError
from mml_cloud_transfer.core.models import PlannedFile
from mml_cloud_transfer.core.paths import resolve_mapped_drive
from mml_cloud_transfer.core.scanner import ScanTotals, iter_source
from mml_cloud_transfer.gui.connection_dialogs import NewConnectionDialog
from mml_cloud_transfer.gui.format import human_bytes
from mml_cloud_transfer.gui.workers import call_async

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
# Wizard pages
# ---------------------------------------------------------------------------


class DirectionPage(QWizardPage):
    def __init__(self, wizard: "NewTransferWizard"):
        super().__init__()
        self._wizard = wizard
        self.setTitle("Direction")

        self.upload_radio = QRadioButton("Upload")
        self.download_radio = QRadioButton("Download")
        self.upload_radio.setChecked(True)

        upload_note = QLabel(
            "Copy files from this computer up to the cloud bucket."
        )
        upload_note.setWordWrap(True)
        download_note = QLabel(
            "Copy files from the cloud bucket down to a folder on this computer."
        )
        download_note.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(self.upload_radio)
        layout.addWidget(upload_note)
        layout.addWidget(self.download_radio)
        layout.addWidget(download_note)
        layout.addStretch(1)

    def set_direction(self, direction: str) -> None:
        self.upload_radio.setChecked(direction == "upload")
        self.download_radio.setChecked(direction != "upload")

    def validatePage(self) -> bool:
        self._wizard.state.direction = (
            "upload" if self.upload_radio.isChecked() else "download"
        )
        return True


class ConnectionPage(QWizardPage):
    def __init__(self, wizard: "NewTransferWizard"):
        super().__init__()
        self._wizard = wizard
        self.setTitle("Connection")
        self._profiles: list[dict] = []

        self.profile_combo = QComboBox()
        self.refresh_button = QPushButton("Refresh")
        self.new_button = QPushButton("New connection…")
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        self.refresh_button.clicked.connect(self._refresh)
        self.new_button.clicked.connect(self._open_new_connection)

        row = QHBoxLayout()
        row.addWidget(self.profile_combo, 1)
        row.addWidget(self.refresh_button)
        row.addWidget(self.new_button)

        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.status_label)

        # Loading on construction (in addition to every initializePage) means
        # the combo is already populating the moment the wizard exists.
        self._refresh()

    def initializePage(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        self.status_label.setText("Loading connections…")
        call_async(self._wizard.client.list_profiles, parent=self,
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
        dialog = NewConnectionDialog(self._wizard.client, self)
        dialog.created.connect(lambda _result: self._refresh())
        dialog.exec()

    def validatePage(self) -> bool:
        index = self.profile_combo.currentIndex()
        if index < 0 or index >= len(self._profiles):
            self.status_label.setText(
                "Select a connection to continue, or create one."
            )
            return False
        profile = self._profiles[index]
        self._wizard.state.profile_name = profile["name"]
        self._wizard.profile_id = profile["id"]
        return True


class FoldersPage(QWizardPage):
    def __init__(self, wizard: "NewTransferWizard"):
        super().__init__()
        self._wizard = wizard
        self.setTitle("Source and destination")

        self.source_label = QLabel()
        self.source_edit = QLineEdit()
        self.source_browse = QPushButton("Browse…")
        self.source_browse.clicked.connect(self._browse_source)
        self.mapped_label = QLabel("")
        self.mapped_label.setWordWrap(True)

        self.prefix_label = QLabel()
        self.prefix_edit = QLineEdit()

        self.source_edit.textChanged.connect(self._update_mapped_label)

        source_row = QHBoxLayout()
        source_row.addWidget(self.source_edit, 1)
        source_row.addWidget(self.source_browse)

        layout = QVBoxLayout(self)
        layout.addWidget(self.source_label)
        layout.addLayout(source_row)
        layout.addWidget(self.mapped_label)
        layout.addWidget(self.prefix_label)
        layout.addWidget(self.prefix_edit)
        layout.addStretch(1)

    def initializePage(self) -> None:
        upload = self._wizard.state.direction == "upload"
        self.source_label.setText(
            "Source folder (this computer):" if upload
            else "Destination folder (this computer):"
        )
        self.prefix_label.setText(
            "Destination prefix (in the bucket):" if upload
            else "Source prefix (in the bucket):"
        )

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

    def validatePage(self) -> bool:
        state = self._wizard.state
        state.source = self.source_edit.text().strip()
        state.prefix = self.prefix_edit.text().strip()
        if not state.source:
            self.mapped_label.setText("Choose a local folder to continue.")
            return False
        return True


class OptionsPage(QWizardPage):
    def __init__(self, wizard: "NewTransferWizard"):
        super().__init__()
        self._wizard = wizard
        self.setTitle("Options and review")
        self._scan_cancel = threading.Event()

        self.name_edit = QLineEdit()
        form = QFormLayout()
        form.addRow("Job name:", self.name_edit)

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

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.preview_label = QLabel("")
        self.preview_label.setWordWrap(True)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.start_later_checkbox)
        layout.addWidget(self.datetime_edit)
        layout.addWidget(self.audit_checkbox)
        layout.addWidget(self.audit_note)
        layout.addWidget(QLabel("Review:"))
        layout.addWidget(self.summary_label)
        layout.addWidget(self.preview_label)
        layout.addWidget(self.status_label)

        wizard.previewUpdated.connect(self._on_preview_update)

    def initializePage(self) -> None:
        state = self._wizard.state
        if not self.name_edit.text():
            leaf = PurePath(state.source).name or state.source or "transfer"
            self.name_edit.setText(f"{leaf}-{date.today().isoformat()}")
        self.status_label.setText("")
        self._update_summary()
        self._start_preview()

    def cleanupPage(self) -> None:
        # Leaving via Back: stop a still-running scan, it no longer applies.
        self._scan_cancel.set()

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def sync_state(self) -> None:
        """Fold widget values into wizard.state. Called both from
        validatePage (normal Next/Finish navigation) and directly from
        accept_and_submit (programmatic use, e.g. tests, which may not have
        driven navigation through this page at all)."""
        state = self._wizard.state
        state.job_name = self.name_edit.text().strip()
        state.audit_hash = self.audit_checkbox.isChecked()
        if self.start_later_checkbox.isChecked():
            state.scheduled_at = (
                self.datetime_edit.dateTime().toPython().astimezone()
                .isoformat(timespec="seconds")
            )
        else:
            state.scheduled_at = None
        self._update_summary()

    def validatePage(self) -> bool:
        self.sync_state()
        if not self._wizard.state.job_name:
            self.status_label.setText("Give the job a name to continue.")
            return False
        return True

    def _update_summary(self) -> None:
        state = self._wizard.state
        parts = [
            state.direction.title(),
            f"connection: {state.profile_name or '(none)'}",
            f"local folder: {state.source or '(none)'}",
            f"bucket prefix: {state.prefix or '(root)'}",
        ]
        if state.audit_hash:
            parts.append("SHA-256 audit hashes")
        if state.scheduled_at:
            parts.append(f"starts: {state.scheduled_at}")
        self.summary_label.setText(" | ".join(parts))

    # -- scan preview -----------------------------------------------------

    def cancel_preview(self) -> None:
        self._scan_cancel.set()

    def _start_preview(self) -> None:
        self._scan_cancel.set()
        cancel_event = threading.Event()
        self._scan_cancel = cancel_event
        state = self._wizard.state
        if not state.source and state.direction == "upload":
            self.preview_label.setText("")
            return

        def guarded_emit(files: int, byte_count: int, errors: int) -> None:
            if not cancel_event.is_set():
                self._wizard.previewUpdated.emit(files, byte_count, errors)

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
            profile_id = self._wizard.profile_id
            if profile_id is None:
                self.preview_label.setText("")
                return
            self.preview_label.setText("Checking the bucket…")
            prefix = state.prefix or None
            call_async(
                lambda: self._wizard.client.preview_remote(profile_id, prefix),
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
            return  # cancelled: the page has moved on, nothing to show
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


# ---------------------------------------------------------------------------
# The wizard itself
# ---------------------------------------------------------------------------


class NewTransferWizard(QWizard):
    jobSubmitted = Signal(int)
    previewUpdated = Signal(int, int, int)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.state = WizardState()
        self.profile_id: int | None = None
        self.setWindowTitle("New transfer")
        # Windows' default Aero wizard style paints the page area white while
        # dark mode keeps the palette's white text — white on white. Classic
        # style draws pages on the normal themed background instead.
        self.setWizardStyle(QWizard.WizardStyle.ClassicStyle)

        self.direction_page = DirectionPage(self)
        self.connection_page = ConnectionPage(self)
        self.folders_page = FoldersPage(self)
        self.options_page = OptionsPage(self)

        self.addPage(self.direction_page)
        self.addPage(self.connection_page)
        self.addPage(self.folders_page)
        self.addPage(self.options_page)

        # Thin delegation so callers (tests included) can reach the one
        # widget the interface calls out by name without reaching through
        # the page.
        self.profile_combo = self.connection_page.profile_combo

        # QWizard's page flow (currentId(), next(), validatePage() dispatch)
        # is normally started by show()/exec(); restart() does the same
        # setup without opening a window, so next() works for callers (this
        # module's tests included) that drive the wizard programmatically.
        self.restart()

    # -- programmatic setters, thin one-liners over the real widgets ------

    def set_direction(self, direction: str) -> None:
        self.direction_page.set_direction(direction)

    def set_source(self, path: str) -> None:
        self.folders_page.source_edit.setText(path)

    def set_prefix(self, prefix: str) -> None:
        self.folders_page.prefix_edit.setText(prefix)

    def set_job_name(self, name: str) -> None:
        self.options_page.name_edit.setText(name)

    # -- finish -------------------------------------------------------

    def accept(self) -> None:
        self.accept_and_submit()

    def accept_and_submit(self) -> None:
        # Finish always supersedes an in-flight scan preview: whatever it
        # would eventually report is no longer relevant once submission is
        # under way. QDialog.accept()/reject() call hide(), not close(), so
        # closeEvent alone would miss this path.
        self.options_page.cancel_preview()
        self.options_page.sync_state()
        body = build_submission(self.state)
        self.options_page.set_status("Submitting…")

        def submit() -> dict:
            try:
                return {"ok": True, "result": self.client.submit_job(body)}
            except ServiceError as exc:
                return {"ok": False, "status_code": exc.status_code, "detail": exc.detail}

        call_async(submit, parent=self, on_done=self._submit_done,
                   on_failed=self.options_page.set_status)

    def _submit_done(self, outcome: dict) -> None:
        if outcome["ok"]:
            self.options_page.set_status("")
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
        self.options_page.set_status(detail)

    def _resume(self, job_id: int) -> None:
        call_async(lambda: self.client.resume(job_id), parent=self,
                   on_done=lambda _result: self._resumed(job_id),
                   on_failed=self.options_page.set_status)

    def _resumed(self, job_id: int) -> None:
        self.jobSubmitted.emit(job_id)
        super().accept()

    def reject(self) -> None:
        # Cancel button: same hide()-not-close() consideration as accept().
        self.options_page.cancel_preview()
        super().reject()

    def closeEvent(self, event) -> None:
        # Belt-and-braces for a direct window-manager close.
        self.options_page.cancel_preview()
        super().closeEvent(event)
