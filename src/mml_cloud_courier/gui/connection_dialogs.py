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
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mml_cloud_courier.auth.oauth_flow import load_client_config, run_login
from mml_cloud_courier.gui.connection_widgets import (
    ConnectionCard, Dot, Pill, ProbeList, RingSpinner, SectionLabel,
    StepRail, _BigCircle, repolish,
)
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
    """Three-step stepper: Where → Credential → Verify. Nothing
    credential-shaped (browsing for a key, opening a sign-in browser tab) is
    reachable until the service has answered /health — the gate covers step 2
    as a whole. The copy strings are gate-findings-bound; tests assert on
    their phrases, so they are not to be reworded."""

    created = Signal(dict)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.setWindowTitle("New connection")
        self.setFixedWidth(600)
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            self.setMaximumHeight(screen.availableGeometry().height() - 80)
        self._step = 1
        self._phase = "idle"
        self._health_ok = False
        self._credential: dict | None = None
        self._key_path: str | None = None
        self._login_generation = 0

        header = QWidget()
        header.setObjectName("connHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 18, 20, 15)
        header_layout.setSpacing(14)
        title = QLabel("New connection")
        title.setObjectName("connTitle")
        self.step_rail = StepRail()
        header_layout.addWidget(title)
        header_layout.addWidget(self.step_rail)

        self._stack = QStackedWidget()
        self.page_where = self._build_page_where()
        self.page_credential = self._build_page_credential()
        self._stack.addWidget(self.page_where)
        self._stack.addWidget(self.page_credential)
        self.page_signin = self._build_page_signin()
        self._stack.addWidget(self.page_signin)
        self.page_validating = self._build_page_validating()
        self.page_verified = self._build_page_verified()
        self.page_failed = self._build_page_failed()
        for page in (self.page_validating, self.page_verified, self.page_failed):
            self._stack.addWidget(page)

        footer = QWidget()
        footer.setObjectName("connFooter")
        footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 13, 20, 13)
        footer_layout.setSpacing(9)
        self.back_button = QPushButton("Back")
        self.back_button.setAutoDefault(False)
        self.back_button.clicked.connect(self._on_back)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        self.next_button = QPushButton("Next: credential")
        self.next_button.setObjectName("primaryButton")
        self.next_button.clicked.connect(lambda: self._go_to_step(2))
        self.done_button = QPushButton("Done")
        self.done_button.setObjectName("primaryButton")
        self.done_button.clicked.connect(self.accept)
        self.retry_button = QPushButton("Try another credential")
        self.retry_button.setObjectName("primaryButton")
        self.retry_button.clicked.connect(lambda: self._go_to_step(2))
        self.done_button.hide()
        self.retry_button.hide()
        footer_layout.addWidget(self.back_button)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.cancel_button)
        footer_layout.addWidget(self.next_button)
        footer_layout.addWidget(self.done_button)
        footer_layout.addWidget(self.retry_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self._stack, 1)
        layout.addWidget(footer)

        # gate: both credential paths dead until /health answers (synchronous,
        # before the check is even dispatched)
        self._set_paths_enabled(False)
        call_async(self.client.health, parent=self,
                   on_done=self._service_ok, on_failed=self._service_down)
        self._go_to_step(1)

    def _show_page(self, page) -> None:
        # Only the current page drives the dialog's height ("600 x natural
        # height" per state): inactive pages get an Ignored vertical policy
        # so the stack's sizeHint tracks the visible step, then the dialog
        # re-fits.
        for i in range(self._stack.count()):
            widget = self._stack.widget(i)
            vertical = (QSizePolicy.Policy.Preferred if widget is page
                        else QSizePolicy.Policy.Ignored)
            widget.setSizePolicy(QSizePolicy.Policy.Preferred, vertical)
        self._stack.setCurrentWidget(page)
        self.layout().activate()
        self.adjustSize()

    # -- pages -----------------------------------------------------------

    def _build_page_where(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(15)
        layout.addWidget(SectionLabel("Where the data goes"))

        def field(label_text, optional=False):
            row = QHBoxLayout()
            row.setSpacing(7)
            label = QLabel(label_text)
            label.setObjectName("connBody")
            row.addWidget(label)
            if optional:
                marker = QLabel("optional")
                marker.setObjectName("helperText")
                row.addWidget(marker)
            row.addStretch(1)
            return row

        self.name_edit = QLineEdit()
        self.name_error = QLabel("")
        self.name_error.setObjectName("connDangerLine")
        self.name_error.setWordWrap(True)
        self.name_error.hide()
        name_helper = QLabel("How this appears when you start a transfer.")
        self.bucket_edit = QLineEdit()
        bucket_helper = QLabel(
            "The bucket your administrator set up for this lab.")
        self.prefix_edit = QLineEdit()
        prefix_helper = QLabel(
            "A folder inside the bucket that transfers start from. Leave it"
            " blank to start at the root.")
        self.project_edit = QLineEdit()
        self.project_edit.setPlaceholderText("Taken from the key file")
        project_helper = QLabel(
            "Only needed when the credential does not name a project.")
        for helper in (name_helper, bucket_helper, prefix_helper,
                       project_helper):
            helper.setObjectName("helperText")
            helper.setWordWrap(True)

        # bucket field: static gs:// prefix inside the field frame
        bucket_frame = QWidget()
        bucket_frame.setObjectName("connField")
        bucket_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bucket_row = QHBoxLayout(bucket_frame)
        bucket_row.setContentsMargins(11, 8, 11, 8)
        bucket_row.setSpacing(2)
        gs_label = QLabel("gs://")
        gs_label.setObjectName("connFieldPrefix")
        bucket_row.addWidget(gs_label)
        bucket_row.addWidget(self.bucket_edit, 1)

        layout.addLayout(field("Name"))
        layout.addWidget(self.name_edit)
        layout.addWidget(self.name_error)
        layout.addWidget(name_helper)
        layout.addLayout(field("Bucket"))
        layout.addWidget(bucket_frame)
        layout.addWidget(bucket_helper)
        layout.addLayout(field("Default prefix", optional=True))
        layout.addWidget(self.prefix_edit)
        layout.addWidget(prefix_helper)
        layout.addLayout(field("Project ID", optional=True))
        layout.addWidget(self.project_edit)
        layout.addWidget(project_helper)
        layout.addStretch(1)

        self.name_edit.textChanged.connect(self._update_next)
        self.bucket_edit.textChanged.connect(self._update_next)
        return page

    def _build_page_credential(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(11)

        self.gate_banner = QWidget()
        self.gate_banner.setObjectName("connNotice")
        self.gate_banner.setProperty("tone", "danger")
        self.gate_banner.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True)
        gate_layout = QVBoxLayout(self.gate_banner)
        gate_layout.setContentsMargins(15, 13, 15, 13)
        gate_layout.setSpacing(9)
        gate_row = QHBoxLayout()
        gate_row.setSpacing(9)
        gate_row.addWidget(Dot(tone="danger"),
                           alignment=Qt.AlignmentFlag.AlignTop)
        self.status_label = QLabel("Checking the transfer service…")
        self.status_label.setObjectName("connNoticeText")
        self.status_label.setProperty("tone", "danger")
        self.status_label.setWordWrap(True)
        gate_row.addWidget(self.status_label, 1)
        gate_layout.addLayout(gate_row)
        gate_buttons = QHBoxLayout()
        gate_buttons.setSpacing(9)
        self.check_again_button = QPushButton("Check again")
        self.check_again_button.setObjectName("dangerButton")
        self.check_again_button.clicked.connect(self._check_again)
        self.open_main_button = QPushButton("Open the main window")
        self.open_main_button.setObjectName("dangerOutline")
        self.open_main_button.clicked.connect(self._open_main_window)
        for button in (self.check_again_button, self.open_main_button):
            button.setAutoDefault(False)
        gate_buttons.addWidget(self.check_again_button)
        gate_buttons.addWidget(self.open_main_button)
        gate_buttons.addStretch(1)
        gate_layout.addLayout(gate_buttons)
        self.gate_banner.hide()
        layout.addWidget(self.gate_banner)

        layout.addWidget(SectionLabel("How the service signs in"))

        # Card A — service account key (recommended)
        self.card_key = QWidget()
        self.card_key.setObjectName("connCard")
        self.card_key.setProperty("accent", "true")
        self.card_key.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        key_layout = QVBoxLayout(self.card_key)
        key_layout.setContentsMargins(17, 15, 17, 15)
        key_layout.setSpacing(9)
        key_head = QHBoxLayout()
        key_head.setSpacing(9)
        key_heading = QLabel("Service account key")
        key_heading.setObjectName("connCardHeading")
        key_head.addWidget(key_heading)
        self.key_pill = Pill("Recommended", tone="accent")
        key_head.addWidget(self.key_pill)
        key_head.addStretch(1)
        key_layout.addLayout(key_head)
        key_body = QLabel(COPY_CHOOSE_KEY)
        key_body.setObjectName("connBody")
        key_body.setWordWrap(True)
        key_layout.addWidget(key_body)
        self.key_error_block = QWidget()
        self.key_error_block.setObjectName("connNotice")
        self.key_error_block.setProperty("tone", "danger")
        self.key_error_block.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True)
        key_error_layout = QVBoxLayout(self.key_error_block)
        key_error_layout.setContentsMargins(13, 11, 13, 11)
        key_error_layout.setSpacing(7)
        self.key_error_mono = QLabel("")
        self.key_error_mono.setObjectName("connDangerMono")
        self.key_error_mono.setWordWrap(True)
        self.key_error_plain = QLabel(
            "That file is an OAuth client configuration, not a key. Use it"
            " under Google sign-in below, or ask your administrator for a"
            " service-account key.")
        self.key_error_plain.setObjectName("connBody")
        self.key_error_plain.setWordWrap(True)
        key_error_layout.addWidget(self.key_error_mono)
        key_error_layout.addWidget(self.key_error_plain)
        self.key_error_block.hide()
        key_layout.addWidget(self.key_error_block)
        key_action = QHBoxLayout()
        key_action.setSpacing(9)
        self.key_button = QPushButton("Choose a key file…")
        self.key_button.setObjectName("primaryButton")
        self.key_button.setAutoDefault(False)
        self.key_button.clicked.connect(self._choose_key)
        key_note = QLabel("A .json file your administrator sends you.")
        key_note.setObjectName("helperText")
        key_action.addWidget(self.key_button)
        key_action.addWidget(key_note)
        key_action.addStretch(1)
        key_layout.addLayout(key_action)
        layout.addWidget(self.card_key)

        # Card B — Google sign-in
        self.card_signin = QWidget()
        self.card_signin.setObjectName("connCard")
        self.card_signin.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True)
        signin_layout = QVBoxLayout(self.card_signin)
        signin_layout.setContentsMargins(17, 15, 17, 15)
        signin_layout.setSpacing(9)
        signin_head = QHBoxLayout()
        signin_head.setSpacing(9)
        signin_heading = QLabel("Google sign-in")
        signin_heading.setObjectName("connCardHeading")
        signin_head.addWidget(signin_heading)
        self.signin_pill = Pill("Can expire in ~7 days", tone="warn")
        signin_head.addWidget(self.signin_pill)
        signin_head.addStretch(1)
        signin_layout.addLayout(signin_head)
        signin_body = QLabel(COPY_CHOOSE_SIGNIN)
        signin_body.setObjectName("connBody")
        signin_body.setWordWrap(True)
        signin_layout.addWidget(signin_body)
        self.signin_error_label = QLabel("")
        self.signin_error_label.setObjectName("connDangerLine")
        self.signin_error_label.setWordWrap(True)
        self.signin_error_label.hide()
        signin_layout.addWidget(self.signin_error_label)
        signin_action = QHBoxLayout()
        signin_action.setSpacing(9)
        self.signin_button = QPushButton("Sign in with Google…")
        self.signin_button.setAutoDefault(False)
        self.signin_button.clicked.connect(self._choose_signin)
        signin_note = QLabel("Opens your browser.")
        signin_note.setObjectName("helperText")
        signin_action.addWidget(self.signin_button)
        signin_action.addWidget(signin_note)
        signin_action.addStretch(1)
        signin_layout.addLayout(signin_action)
        layout.addWidget(self.card_signin)

        self.either_way_label = QLabel("")
        self.either_way_label.setObjectName("helperText")
        self.either_way_label.setWordWrap(True)
        layout.addWidget(self.either_way_label)
        layout.addStretch(1)
        return page

    def _build_page_signin(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 30, 20, 15)
        layout.setSpacing(13)
        self.signin_spinner = RingSpinner()
        layout.addWidget(self.signin_spinner,
                         alignment=Qt.AlignmentFlag.AlignHCenter)
        waiting = QLabel("Waiting for you to finish signing in")
        waiting.setObjectName("connCardHeading")
        waiting.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(waiting)
        browser_line = QLabel(
            "A browser window opened. Sign in there and allow access; this"
            " dialog carries on by itself.")
        browser_line.setObjectName("connIntro")
        browser_line.setWordWrap(True)
        browser_line.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(browser_line)
        timeout_line = QLabel("gives up after 5 minutes")
        timeout_line.setObjectName("connFaintMono")
        timeout_line.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(timeout_line)
        note_card = QWidget()
        note_card.setObjectName("connCard")
        note_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        note_layout = QHBoxLayout(note_card)
        note_layout.setContentsMargins(15, 13, 15, 13)
        note_layout.setSpacing(9)
        note_layout.addWidget(Dot(tone="warn"),
                              alignment=Qt.AlignmentFlag.AlignTop)
        self.signin_cancel_note = QLabel(
            "Nothing is saved yet. After sign-in the service still tests this"
            " credential against the bucket, and will refuse it if it cannot"
            " do everything a transfer needs.")
        self.signin_cancel_note.setObjectName("connBody")
        self.signin_cancel_note.setWordWrap(True)
        note_layout.addWidget(self.signin_cancel_note, 1)
        layout.addWidget(note_card)
        layout.addStretch(1)
        return page

    def _build_page_validating(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(11)
        title = QLabel("Testing this credential against the bucket")
        title.setObjectName("connCardHeading")
        layout.addWidget(title)
        explainer = QLabel(
            "The service writes a small object, composes it, reads it back"
            " and deletes it. An upload needs all five; finding a gap now"
            " beats finding it overnight.")
        explainer.setObjectName("connIntro")
        explainer.setWordWrap(True)
        layout.addWidget(explainer)
        self.probe_list = ProbeList()
        layout.addWidget(self.probe_list)
        self.validating_target = QLabel("")
        self.validating_target.setObjectName("connFaintMono")
        layout.addWidget(self.validating_target)
        layout.addStretch(1)
        return page

    def _build_page_verified(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(11)
        title_row = QHBoxLayout()
        title_row.setSpacing(11)
        self._verified_circle = _BigCircle(kind="check")
        title_row.addWidget(self._verified_circle)
        self.verified_title = QLabel("")
        self.verified_title.setObjectName("connTitle")
        title_row.addWidget(self.verified_title, 1)
        layout.addLayout(title_row)
        found_card = QWidget()
        found_card.setObjectName("connCard")
        found_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        found_layout = QVBoxLayout(found_card)
        found_layout.setContentsMargins(17, 15, 17, 15)
        found_layout.setSpacing(9)
        found_layout.addWidget(SectionLabel("What the service found"))
        self.verified_summary = QLabel("")
        self.verified_summary.setObjectName("connBody")
        self.verified_summary.setWordWrap(True)
        found_layout.addWidget(self.verified_summary)
        chips_row = QHBoxLayout()
        chips_row.setSpacing(7)
        for capability in ("list", "read", "write", "compose", "delete"):
            chip = QLabel(capability)
            chip.setObjectName("connChip")
            chips_row.addWidget(chip)
        chips_row.addStretch(1)
        found_layout.addLayout(chips_row)
        layout.addWidget(found_card)
        self.verified_notice = QWidget()
        self.verified_notice.setObjectName("connNotice")
        self.verified_notice.setProperty("tone", "accent")
        self.verified_notice.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True)
        notice_layout = QHBoxLayout(self.verified_notice)
        notice_layout.setContentsMargins(15, 13, 15, 13)
        notice_layout.setSpacing(9)
        notice_layout.addWidget(Dot(tone="accent"),
                                alignment=Qt.AlignmentFlag.AlignTop)
        self.verified_notice_text = QLabel(COPY_DELETE_ORIGINAL)
        self.verified_notice_text.setObjectName("connNoticeText")
        self.verified_notice_text.setProperty("tone", "accent")
        self.verified_notice_text.setWordWrap(True)
        notice_layout.addWidget(self.verified_notice_text, 1)
        layout.addWidget(self.verified_notice)
        self.verified_key_path = QLabel("")
        self.verified_key_path.setObjectName("connFaintMono")
        self.verified_key_path.setWordWrap(True)
        layout.addWidget(self.verified_key_path)
        layout.addStretch(1)
        return page

    def _build_page_failed(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(11)
        title_row = QHBoxLayout()
        title_row.setSpacing(11)
        title_row.addWidget(_BigCircle(kind="bang"))
        failed_title = QLabel("This credential cannot reach that bucket")
        failed_title.setObjectName("connTitle")
        title_row.addWidget(failed_title, 1)
        layout.addLayout(title_row)
        found_card = QWidget()
        found_card.setObjectName("connCard")
        found_card.setProperty("edge", "danger")
        found_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        found_layout = QVBoxLayout(found_card)
        found_layout.setContentsMargins(17, 15, 17, 15)
        found_layout.setSpacing(9)
        found_layout.addWidget(SectionLabel("What the service found"))
        self.failed_summary = QLabel("")
        self.failed_summary.setObjectName("connBody")
        self.failed_summary.setWordWrap(True)
        found_layout.addWidget(self.failed_summary)
        self.failed_chips_host = QWidget()
        chips_row = QHBoxLayout(self.failed_chips_host)
        chips_row.setContentsMargins(0, 0, 0, 0)
        chips_row.setSpacing(7)
        for capability in ("list", "read", "write", "compose", "delete"):
            chip = QLabel(f"✕ {capability}")
            chip.setObjectName("connChip")
            chip.setProperty("tone", "danger")
            chips_row.addWidget(chip)
        chips_row.addStretch(1)
        found_layout.addWidget(self.failed_chips_host)
        layout.addWidget(found_card)
        recovery_card = QWidget()
        recovery_card.setObjectName("connCard")
        recovery_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        recovery_layout = QVBoxLayout(recovery_card)
        recovery_layout.setContentsMargins(17, 15, 17, 15)
        recovery_layout.setSpacing(9)
        recovery_a = QLabel(
            "The key is valid, but it has no access to this bucket. Either"
            " the bucket name is wrong, or nobody has granted this service"
            " account object access to it. Nothing was saved.")
        recovery_b = QLabel(
            "Send your administrator the line above and ask for object access"
            " to this one bucket, nothing more.")
        for label in (recovery_a, recovery_b):
            label.setObjectName("connBody")
            label.setWordWrap(True)
            recovery_layout.addWidget(label)
        recovery_buttons = QHBoxLayout()
        recovery_buttons.setSpacing(9)
        self.copy_summary_button = QPushButton("Copy this summary")
        self.copy_summary_button.clicked.connect(self._copy_summary)
        self.check_bucket_button = QPushButton("Check the bucket name")
        self.check_bucket_button.clicked.connect(self._check_bucket_name)
        for button in (self.copy_summary_button, self.check_bucket_button):
            button.setAutoDefault(False)
        recovery_buttons.addWidget(self.copy_summary_button)
        recovery_buttons.addWidget(self.check_bucket_button)
        recovery_buttons.addStretch(1)
        recovery_layout.addLayout(recovery_buttons)
        layout.addWidget(recovery_card)
        layout.addStretch(1)
        return page

    def _copy_summary(self) -> None:
        QGuiApplication.clipboard().setText(self.failed_summary.text())

    def _check_bucket_name(self) -> None:
        self._go_to_step(1)
        self.bucket_edit.setFocus()

    def _fields(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "bucket": self.bucket_edit.text().strip(),
            "prefix": self.prefix_edit.text().strip(),
            "project": self.project_edit.text().strip(),
        }

    # -- navigation ------------------------------------------------------

    def _target_path(self) -> str:
        target = (f"gs://{self.bucket_edit.text().strip()}/"
                  f"{self.prefix_edit.text().strip()}")
        return target.rstrip("/")

    def _go_to_step(self, step: int) -> None:
        self._step = step
        self.step_rail.set_current(step)
        if step == 1:
            self._show_page(self.page_where)
            self.back_button.setEnabled(False)
            self.back_button.setText("Back")
            self.next_button.show()
            self.next_button.setDefault(True)
            self.done_button.hide()
            self.retry_button.hide()
            self._update_next()
            self.name_edit.setFocus()
        elif step == 2:
            self._phase = "idle"
            self.name_error.hide()
            self._show_page(self.page_credential)
            self.back_button.setEnabled(True)
            self.back_button.setText("Back")
            self.next_button.hide()      # step 2 has no footer primary
            self.done_button.hide()
            self.retry_button.hide()
            self.either_way_label.setText(
                "Either way, the service tests the credential against"
                f" {self._target_path()} before it saves anything.")
            self._apply_gate()

    def _on_back(self) -> None:
        if self._step == 2:
            self._go_to_step(1)

    def _update_next(self) -> None:
        ready = bool(self.name_edit.text().strip()) and bool(
            self.bucket_edit.text().strip())
        self.next_button.setEnabled(ready and self._step == 1)

    # -- health gate -----------------------------------------------------

    def _set_paths_enabled(self, enabled: bool) -> None:
        self.key_button.setEnabled(enabled)
        self.signin_button.setEnabled(enabled)

    def _apply_gate(self) -> None:
        gated = not self._health_ok
        self.gate_banner.setVisible(gated)
        for card in (self.card_key, self.card_signin):
            card.setProperty("state", "disabled" if gated else "")
            repolish(card)
        self.key_pill.set_tone("disabled" if gated else "accent")
        self.signin_pill.set_tone("disabled" if gated else "warn")
        self._set_paths_enabled(not gated)

    def _service_ok(self, _result) -> None:
        self._health_ok = True
        self._apply_gate()

    def _service_down(self, _message) -> None:
        self._health_ok = False
        self.status_label.setText(COPY_SERVICE_FIRST)
        self._apply_gate()

    def _check_again(self) -> None:
        self.status_label.setText("Checking the transfer service…")
        call_async(self.client.health, parent=self,
                   on_done=self._service_ok, on_failed=self._service_down)

    def _open_main_window(self) -> None:
        # Merely raising the main window is useless while a modal chain
        # covers it (exec() disables the owner window on Windows), so close
        # the modal dialogs first, then hand the main window focus.
        # Non-modal parents (the transfer wizard) stay open.
        top = None
        widget = self.parentWidget()
        while widget is not None:
            top = widget
            widget = widget.parentWidget()
        self.reject()
        dialog = self.parentWidget()
        while dialog is not None:
            if isinstance(dialog, QDialog) and dialog.isModal():
                dialog.reject()
            dialog = dialog.parentWidget()
        if top is not None:
            top.show()
            top.raise_()
            top.activateWindow()

    # -- service-account key path ----------------------------------------

    def _choose_key(self):
        path, _filter = QFileDialog.getOpenFileName(
            self, "Choose a service account key",
            filter="OAuth/service-account JSON (*.json)",
        )
        if not path:
            return
        try:
            key = load_key_file(path)
        except ValueError as exc:
            self.key_error_mono.setText(str(exc))
            self.key_error_block.show()
            self.key_button.setText("Choose a different file…")
            return
        self.key_error_block.hide()
        self._key_path = path
        fields = self._fields()
        self._start_create(key_profile_payload(
            name=fields["name"], bucket=fields["bucket"],
            prefix=fields["prefix"], project=fields["project"], key=key))

    # -- Google sign-in path ----------------------------------------------

    def _choose_signin(self):
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
            self.signin_error_label.setText(str(exc))
            self.signin_error_label.show()
            return
        self.signin_error_label.hide()
        self._key_path = None
        self._phase = "signing-in"
        self._login_generation += 1
        generation = self._login_generation
        self._show_page(self.page_signin)
        self.signin_spinner.start()
        self.back_button.setEnabled(False)
        self.next_button.hide()
        call_async(lambda: run_login(config, timeout_seconds=300), parent=self,
                   on_done=lambda cred: self._signed_in(cred, generation),
                   on_failed=lambda msg: self._signin_failed(msg, generation))

    def _signed_in(self, credential, generation: int) -> None:
        if generation != self._login_generation:
            return                          # cancelled; discard the late result
        self.signin_spinner.stop()
        fields = self._fields()
        self._start_create(oauth_profile_payload(
            name=fields["name"], bucket=fields["bucket"],
            prefix=fields["prefix"], project=fields["project"],
            credential=credential))

    def _signin_failed(self, message: str, generation: int) -> None:
        if generation != self._login_generation:
            return
        self.signin_spinner.stop()
        self._go_to_step(2)
        self.signin_error_label.setText(message)
        self.signin_error_label.show()

    # -- the one create call: pending / 200 / 400 -------------------------

    def _start_create(self, payload: dict) -> None:
        self._pending_payload = payload
        self._phase = "validating"
        self._step = 3
        self.step_rail.set_current(3)
        self._show_page(self.page_validating)
        self.validating_target.setText(self._target_path())
        self.back_button.setEnabled(False)
        self.next_button.hide()
        self.done_button.hide()
        self.retry_button.hide()
        self.probe_list.start()
        generation = self._login_generation
        call_async(lambda: self.client.create_profile(payload), parent=self,
                   on_done=lambda result: self._profile_created(result, generation),
                   on_failed=lambda message: self._flow_failed(message, generation))

    def _profile_created(self, result, generation: int) -> None:
        if generation != self._login_generation:
            return                          # cancelled; discard the late result
        self.probe_list.finish_all()
        self._phase = "verified"
        self.verified_title.setText(f"{result.get('name', '')} is ready to use")
        self.verified_summary.setText(result.get("summary", ""))
        is_key = self._key_path is not None
        self.verified_notice.setVisible(is_key)
        self.verified_key_path.setVisible(is_key)
        self.verified_key_path.setText(self._key_path or "")
        self._show_page(self.page_verified)
        self.back_button.setEnabled(True)
        self.back_button.setText("Add another")
        try:
            self.back_button.clicked.disconnect(self._on_back)
        except RuntimeError:
            pass
        self.back_button.clicked.connect(self._add_another)
        self.done_button.show()
        self.done_button.setDefault(True)
        self.created.emit(result)

    def _add_another(self) -> None:
        try:
            self.back_button.clicked.disconnect(self._add_another)
        except RuntimeError:
            pass
        self.back_button.clicked.connect(self._on_back)
        self._credential = None
        self._key_path = None
        self._phase = "idle"
        self.probe_list.reset()
        for edit in (self.name_edit, self.bucket_edit, self.prefix_edit,
                     self.project_edit):
            edit.clear()
        self.key_button.setText("Choose a key file…")
        self.key_error_block.hide()
        self.signin_error_label.hide()
        self.done_button.hide()
        self._go_to_step(1)

    def _flow_failed(self, message: str, generation: int) -> None:
        if generation != self._login_generation:
            return                          # cancelled; discard the late result
        self.probe_list.stop()
        code, detail = split_service_error(message)
        if code == 409 and "already exists" in detail:
            self._phase = "idle"
            self._go_to_step(1)
            self.name_error.setText(detail)
            self.name_error.show()
            self.name_edit.setFocus()
            return
        self._phase = "failed"
        self._step = 3
        self.step_rail.set_current(3)
        self.failed_summary.setText(detail)
        before_bucket = detail.startswith(
            "credential rejected before reaching the bucket")
        self.failed_chips_host.setVisible(code == 400 and not before_bucket)
        self._show_page(self.page_failed)
        self.back_button.setEnabled(False)
        self.next_button.hide()
        self.done_button.hide()
        self.retry_button.show()
        self.retry_button.setDefault(True)

    def reject(self) -> None:
        # Escape during sign-in must abandon the pending run_login: there is
        # no server-side cancel hook (flow.run_local_server blocks), so the
        # generation bump makes the eventual result a no-op and the local
        # listener times out on its own. The same counter now also guards
        # create_profile: the fixed API has no create-cancel either, and a
        # cancel here must not surface a "verified"/"failed" page (or fire
        # `created`) for a flow the user already dismissed — the late result
        # is discarded by `_profile_created`/`_flow_failed`'s generation
        # check. If the server did save the profile, both `created` call
        # sites re-fetch on close, so it still surfaces there instead.
        self._login_generation += 1
        self.signin_spinner.stop()
        super().reject()


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
                   on_done=lambda result, c=card: self._card_check_done(c, result),
                   on_failed=lambda message, c=card: self._card_check_failed(c, message))

    def _card_check_done(self, card, result) -> None:
        # refresh() deleteLater()s every card and rebuilds self.cards; a
        # check that was in flight during that refresh must not touch the
        # now-dead card its callback still closes over.
        if card in self.cards:
            card.show_check_summary(result["summary"])

    def _card_check_failed(self, card, message) -> None:
        if card in self.cards:
            card.show_error_line(split_service_error(message)[1])

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
        # A cancelled dialog can still have a create that landed server-side
        # (no create-cancel in the fixed API; the stepper's generation guard
        # only discards the late GUI result) — refresh unconditionally so a
        # profile that saved anyway still surfaces.
        self.refresh()
