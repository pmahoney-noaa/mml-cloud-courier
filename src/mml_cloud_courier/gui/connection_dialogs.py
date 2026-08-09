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
import re
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mml_cloud_courier.auth.oauth_flow import load_client_config, run_login
from mml_cloud_courier.gui.connection_widgets import ConnectionCard
from mml_cloud_courier.gui.format import split_service_error
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
        self.key_button = QPushButton("Choose a key file")
        self.key_button.setWhatsThis(COPY_CHOOSE_KEY)
        self.key_button.clicked.connect(self._choose_key)
        # The key path is the one COPY_CHOOSE_KEY itself calls out as
        # "recommended for unattended, recurring transfers" -- the create
        # action a new connection should default toward.
        self.key_button.setObjectName("primaryButton")

        signin_label = QLabel(COPY_CHOOSE_SIGNIN)
        signin_label.setWordWrap(True)
        self.signin_button = QPushButton("Sign in with Google")
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
    """Status-card manager per the committed handoff. Per-card actions —
    no selection-driven button bar. New connection is the one filled control."""

    showJobsForProfile = Signal(int, str)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.setWindowTitle("Connections")
        self.setFixedWidth(640)
        self.setMinimumHeight(420)
        self._profiles: list[dict] = []
        self.cards: list[ConnectionCard] = []

        header = QWidget()
        header.setObjectName("connHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 20, 20, 15)
        header_layout.setSpacing(15)
        title_column = QVBoxLayout()
        title_column.setSpacing(7)
        title = QLabel("Connections")
        title.setObjectName("connTitle")
        intro = QLabel("Each connection is a bucket and a credential the"
                       " service keeps and uses on its own. Transfers pick"
                       " one by name.")
        intro.setObjectName("connIntro")
        intro.setWordWrap(True)
        title_column.addWidget(title)
        title_column.addWidget(intro)
        header_layout.addLayout(title_column, 1)
        self.new_button = QPushButton("New connection")
        self.new_button.setObjectName("primaryButton")
        self.new_button.setAutoDefault(False)
        header_layout.addWidget(self.new_button,
                                alignment=Qt.AlignmentFlag.AlignTop)

        self._cards_host = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_host)
        self._cards_layout.setContentsMargins(20, 15, 20, 15)
        self._cards_layout.setSpacing(11)
        self.empty_label = QLabel("No connections yet.")
        self.empty_label.setObjectName("connMuted")
        self.empty_label.hide()
        self.error_label = QLabel("")
        self.error_label.setObjectName("connDangerLine")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        self._cards_layout.addWidget(self.empty_label)
        self._cards_layout.addWidget(self.error_label)
        self._cards_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setObjectName("connScroll")
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._cards_host)

        footer = QWidget()
        footer.setObjectName("connFooter")
        footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 13, 20, 13)
        note = QLabel("Checking re-runs the same probe used when the"
                      " connection was created.")
        note.setObjectName("helperText")
        note.setWordWrap(True)
        footer_layout.addWidget(note, 1)
        self.close_button = QPushButton("Close")
        self.close_button.setAutoDefault(False)
        footer_layout.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(scroll, 1)
        layout.addWidget(footer)

        self.new_button.clicked.connect(self._new_connection)
        self.close_button.clicked.connect(self.close)
        self.resize(640, 560)
        self.refresh()

    def refresh(self) -> None:
        call_async(self.client.list_profiles, parent=self,
                   on_done=self._profiles_loaded, on_failed=self._list_failed)

    def _profiles_loaded(self, profiles):
        self._profiles = profiles
        for card in self.cards:
            card.setParent(None)
            card.deleteLater()
        self.cards = []
        self.error_label.hide()
        self.empty_label.setVisible(not profiles)
        for profile in profiles:
            card = ConnectionCard(profile)
            card.check_clicked.connect(
                lambda c=card: self._check_card(c))
            card.remove_confirmed.connect(
                lambda c=card: self._delete_card(c))
            card.show_jobs_clicked.connect(
                lambda c=card: self._show_jobs(c))
            # insert above the trailing stretch
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
            self.cards.append(card)

    def _list_failed(self, message):
        _code, detail = split_service_error(message)
        self.error_label.setText(detail)
        self.error_label.show()

    def _check_card(self, card) -> None:
        card.set_checking()
        call_async(lambda: self.client.check_profile(card.profile["id"]),
                   parent=self,
                   on_done=lambda result, c=card: c.show_check_summary(
                       result["summary"]),
                   on_failed=lambda message, c=card: c.show_error_line(
                       split_service_error(message)[1]))

    def _delete_card(self, card) -> None:
        call_async(lambda: self.client.delete_profile(card.profile["id"]),
                   parent=self,
                   on_done=lambda _r: self.refresh(),
                   on_failed=lambda message, c=card: self._delete_failed(c, message))

    def _delete_failed(self, card, message) -> None:
        code, detail = split_service_error(message)
        match = re.search(r"used by (\d+) job", detail)
        if code == 409 and match:
            card.show_refusal(int(match.group(1)))
        else:
            card.reset_region()
            card.show_error_line(detail)

    def _show_jobs(self, card) -> None:
        self.showJobsForProfile.emit(card.profile["id"], card.profile["name"])
        self.accept()

    def _new_connection(self):
        dialog = NewConnectionDialog(self.client, self)
        dialog.created.connect(lambda _result: self.refresh())
        dialog.exec()
