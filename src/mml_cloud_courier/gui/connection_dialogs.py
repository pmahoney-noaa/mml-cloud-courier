"""Connection setup: both credential paths, service checked before anything
credential-shaped happens.

GUI half of carried-over item 3 — NewConnectionDialog runs client.health()
via call_async on open and disables both credential-path buttons until it
succeeds, so no browser can open (and no key file can be read) against an
unreachable service. The copy strings are gate-findings-bound (Finding 1:
recommend least-privilege service-account keys; Finding 2: disclose the
OAuth 'testing'-status 7-day expiry honestly) — tests assert on their exact
phrases, so they are not to be reworded.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from mml_cloud_courier.auth.oauth_flow import load_client_config, run_login
from mml_cloud_courier.gui.workers import call_async

COPY_CHOOSE_KEY = (
    "Service account key — recommended for unattended, recurring transfers."
    " Ask your administrator for a key with least-privilege access: object"
    " access to this one bucket, nothing more."
)
COPY_CHOOSE_SIGNIN = (
    "Google sign-in — good for interactive or short-lived use. Transfers"
    " keep running after you sign out, but the sign-in itself can expire"
    " and need repeating — for apps registered in Google's 'testing' status"
    " it stops working after about 7 days. For a connection that must run"
    " unattended for months, prefer a service account key."
)
COPY_DELETE_ORIGINAL = (
    "The service now holds an encrypted copy of this key. You may delete"
    " the original file."
)
COPY_SERVICE_FIRST = (
    "The transfer service is not reachable, so a connection cannot be"
    " validated or saved. Start the service from the main window, then try"
    " again."
)


def load_key_file(path: str) -> dict:
    """Read a downloaded credential JSON and reject anything that is not a
    service-account key, naming the actual type so the mistake (e.g. an
    OAuth client_secret file dropped in the wrong dialog) is obvious."""
    key = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    actual = key.get("type")
    if actual != "service_account":
        raise ValueError(
            f"{path} is not a service-account key (type={actual!r};"
            " expected 'service_account')"
        )
    return key


def key_profile_payload(*, name: str, bucket: str, prefix: str, project: str,
                         key: dict) -> dict:
    return {
        "name": name,
        "bucket": bucket,
        "auth_type": "service_account_key",
        "credential": key,
        "project_id": project or key.get("project_id", ""),
        "default_prefix": prefix,
    }


def oauth_profile_payload(*, name: str, bucket: str, prefix: str, project: str,
                           credential: dict) -> dict:
    return {
        "name": name,
        "bucket": bucket,
        "auth_type": "oauth_user",
        "credential": credential,
        "project_id": project,
        "default_prefix": prefix,
    }


class NewConnectionDialog(QDialog):
    """Name/bucket/prefix/project fields plus the two credential paths.
    Nothing credential-shaped (browsing for a key, opening a sign-in
    browser tab) is reachable until the service has answered /health."""

    created = Signal(dict)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.setWindowTitle("New connection")

        self.name_edit = QLineEdit()
        self.bucket_edit = QLineEdit()
        self.prefix_edit = QLineEdit()
        self.project_edit = QLineEdit()

        form = QFormLayout()
        form.addRow("Name:", self.name_edit)
        form.addRow("Bucket:", self.bucket_edit)
        form.addRow("Prefix (optional):", self.prefix_edit)
        form.addRow("Project ID (optional):", self.project_edit)

        key_label = QLabel(COPY_CHOOSE_KEY)
        key_label.setWordWrap(True)
        self.key_button = QPushButton("Choose a key file…")
        self.key_button.setWhatsThis(COPY_CHOOSE_KEY)
        self.key_button.clicked.connect(self._choose_key)
        # The key path is the one COPY_CHOOSE_KEY itself calls out as
        # "recommended for unattended, recurring transfers" -- the create
        # action a new connection should default toward.
        self.key_button.setObjectName("primaryButton")

        signin_label = QLabel(COPY_CHOOSE_SIGNIN)
        signin_label.setWordWrap(True)
        self.signin_button = QPushButton("Sign in with Google…")
        self.signin_button.setWhatsThis(COPY_CHOOSE_SIGNIN)
        self.signin_button.clicked.connect(self._choose_signin)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(11)
        layout.addLayout(form)
        layout.addWidget(key_label)
        layout.addWidget(self.key_button)
        layout.addWidget(signin_label)
        layout.addWidget(self.signin_button)
        layout.addWidget(self.status_label)

        self.key_button.setEnabled(False)
        self.signin_button.setEnabled(False)
        self.status_label.setText("Checking the transfer service…")
        call_async(self.client.health, parent=self,
                   on_done=self._service_ok, on_failed=self._service_down)

    def _service_ok(self, _result):
        self.key_button.setEnabled(True)
        self.signin_button.setEnabled(True)
        self.status_label.setText("")

    def _service_down(self, _message):
        self.key_button.setEnabled(False)
        self.signin_button.setEnabled(False)
        self.status_label.setText(COPY_SERVICE_FIRST)

    def _set_paths_enabled(self, enabled: bool) -> None:
        self.key_button.setEnabled(enabled)
        self.signin_button.setEnabled(enabled)

    def _fields(self) -> tuple[str, str, str, str] | None:
        name = self.name_edit.text().strip()
        bucket = self.bucket_edit.text().strip()
        if not name or not bucket:
            self.status_label.setText("Name and bucket are required.")
            return None
        return name, bucket, self.prefix_edit.text().strip(), self.project_edit.text().strip()

    # -- service-account key path -------------------------------------

    def _choose_key(self):
        fields = self._fields()
        if fields is None:
            return
        path, _filter = QFileDialog.getOpenFileName(
            self, "Choose a service account key",
            filter="OAuth/service-account JSON (*.json)",
        )
        if not path:
            return
        try:
            key = load_key_file(path)
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return
        name, bucket, prefix, project = fields
        payload = key_profile_payload(
            name=name, bucket=bucket, prefix=prefix, project=project, key=key
        )
        self.status_label.setText("Validating the connection against the bucket…")
        self._set_paths_enabled(False)
        call_async(lambda: self.client.create_profile(payload), parent=self,
                   on_done=self._profile_created, on_failed=self._flow_failed)

    def _profile_created(self, result):
        self._set_paths_enabled(True)
        self.status_label.setText(result["summary"] + "\n\n" + COPY_DELETE_ORIGINAL)
        self.created.emit(result)

    # -- Google sign-in path --------------------------------------------

    def _choose_signin(self):
        fields = self._fields()
        if fields is None:
            return
        source = os.environ.get("MMLCC_OAUTH_CLIENT")
        if not source:
            path, _filter = QFileDialog.getOpenFileName(
                self, "Choose the OAuth client configuration",
                filter="OAuth client JSON (*.json)",
            )
            if not path:
                return
            source = path
        try:
            config = load_client_config(source)
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return
        self._pending_fields = fields
        self.status_label.setText("A browser window will open for Google sign-in…")
        self._set_paths_enabled(False)
        call_async(lambda: run_login(config, timeout_seconds=300), parent=self,
                   on_done=self._signed_in, on_failed=self._flow_failed)

    def _signed_in(self, credential):
        name, bucket, prefix, project = self._pending_fields
        payload = oauth_profile_payload(
            name=name, bucket=bucket, prefix=prefix, project=project,
            credential=credential,
        )
        self.status_label.setText("Validating the connection against the bucket…")
        call_async(lambda: self.client.create_profile(payload), parent=self,
                   on_done=self._profile_created, on_failed=self._flow_failed)

    # -- shared -----------------------------------------------------------

    def _flow_failed(self, message):
        self._set_paths_enabled(True)
        self.status_label.setText(message)


class ConnectionsDialog(QDialog):
    """Profiles list plus New / Check / Remove / Close."""

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.setWindowTitle("Connections")
        self._profiles: list[dict] = []

        self.list_widget = QListWidget()
        self.new_button = QPushButton("New…")
        # The one clear primary action in this dialog: everything else
        # (Check/Remove) acts on a selection, New… is the only action that
        # doesn't need one.
        self.new_button.setObjectName("primaryButton")
        self.check_button = QPushButton("Check")
        self.remove_button = QPushButton("Remove")
        self.close_button = QPushButton("Close")
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        buttons = QHBoxLayout()
        buttons.setSpacing(11)
        buttons.addWidget(self.new_button)
        buttons.addWidget(self.check_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(11)
        layout.addWidget(self.list_widget)
        layout.addWidget(self.status_label)
        layout.addLayout(buttons)

        self.new_button.clicked.connect(self._new_connection)
        self.check_button.clicked.connect(self._check_selected)
        self.remove_button.clicked.connect(self._remove_selected)
        self.close_button.clicked.connect(self.close)

        self.refresh()

    def refresh(self) -> None:
        call_async(self.client.list_profiles, parent=self,
                   on_done=self._profiles_loaded, on_failed=self._show_error)

    def _profiles_loaded(self, profiles):
        self._profiles = profiles
        self.list_widget.clear()
        for profile in profiles:
            target = f"gs://{profile['bucket']}/{profile['default_prefix']}".rstrip("/")
            checked = profile["validated_at"] or "never"
            self.list_widget.addItem(
                f"{profile['name']} — {target} [{profile['auth_type']}]"
                f" last check: {checked}"
            )

    def _selected_profile(self) -> dict | None:
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self._profiles):
            return None
        return self._profiles[row]

    def _new_connection(self):
        dialog = NewConnectionDialog(self.client, self)
        dialog.created.connect(lambda _result: self.refresh())
        dialog.exec()

    def _check_selected(self):
        profile = self._selected_profile()
        if profile is None:
            return
        self.status_label.setText("Checking…")
        call_async(lambda: self.client.check_profile(profile["id"]), parent=self,
                   on_done=self._check_done, on_failed=self._show_error)

    def _check_done(self, result):
        self.status_label.setText(result["summary"])

    def _remove_selected(self):
        profile = self._selected_profile()
        if profile is None:
            return
        answer = QMessageBox.question(
            self, "Remove connection",
            f"Remove the connection {profile['name']!r}? This cannot be undone.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        call_async(lambda: self.client.delete_profile(profile["id"]), parent=self,
                   on_done=self._removed, on_failed=self._show_error)

    def _removed(self, _result):
        self.status_label.setText("Connection removed.")
        self.refresh()

    def _show_error(self, message):
        self.status_label.setText(message)
