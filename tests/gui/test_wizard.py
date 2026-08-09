import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from mml_cloud_courier.gui.wizard import (
    NewTransferWizard, WizardState, build_submission, parse_duplicate_job_id,
    preview_scan,
)


def test_build_submission_resolves_mapped_drives():
    state = WizardState(direction="upload", profile_name="lab",
                        source="Z:\\imaging\\run47", prefix="runs/47",
                        job_name="run47", audit_hash=True)
    body = build_submission(
        state, resolver=lambda drive: r"\\server\share" if drive == "Z:" else None
    )
    assert body == {
        "name": "run47", "direction": "upload",
        "source_root": r"\\server\share\imaging\run47",
        "dest_prefix": "runs/47", "profile": "lab", "bucket": None,
        "credentials_path": None, "emulator_endpoint": None,
        "audit_hash": True, "scheduled_start_at": None,
    }


def test_parse_duplicate_job_id():
    detail = ("job 12 (incomplete) already transfers this source to"
              " gs://b/p — resume it (mmlcc resume --job-id 12) or cancel it")
    assert parse_duplicate_job_id(detail) == 12
    assert parse_duplicate_job_id("something else entirely") is None


def test_preview_scan_totals_and_cancel(tmp_path):
    for i in range(3):
        (tmp_path / f"f{i}.bin").write_bytes(b"x" * (i + 1))
    updates = []
    totals = preview_scan(str(tmp_path), emit=lambda *a: updates.append(a),
                          cancelled=lambda: False, batch=2)
    assert (totals.file_count, totals.byte_count, totals.error_count) == (3, 6, 0)
    assert updates[-1] == (3, 6, 0)

    assert preview_scan(str(tmp_path), emit=lambda *a: None,
                        cancelled=lambda: True) is None


class FakeWizardClient:
    def __init__(self):
        self.submissions = []

    def list_profiles(self):
        return [{"id": 1, "name": "lab", "bucket": "b", "default_prefix": "d",
                 "auth_type": "service_account_key", "validated_at": None}]

    def submit_job(self, payload):
        self.submissions.append(payload)
        return {"job_id": 7, "scheduled_start_at": None,
                "profile_id": 1, "preflight_summary": "This credential can ..."}


@pytest.fixture
def wizard(qtbot):
    """A NewTransferWizard over a fake client seeded with one profile
    ("lab"), with the async profile load already settled."""
    client = FakeWizardClient()
    w = NewTransferWizard(client)
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: w.profile_combo.count() == 1, timeout=5000)
    return w


def test_wizard_walks_to_submission(qtbot, tmp_path):
    from mml_cloud_courier.gui import wizard as wizard_module

    client = FakeWizardClient()
    wizard = NewTransferWizard(client)
    qtbot.addWidget(wizard)
    submitted = []
    wizard.jobSubmitted.connect(submitted.append)

    wizard.set_direction("upload")
    qtbot.waitUntil(lambda: wizard.profile_combo.count() == 1, timeout=5000)
    wizard.set_source(str(tmp_path))
    wizard.set_prefix("data")
    wizard.set_job_name("myjob")
    assert wizard_module.last_connection_name() is None
    wizard.accept_and_submit()
    qtbot.waitUntil(lambda: submitted == [7], timeout=5000)
    assert client.submissions[0]["name"] == "myjob"
    assert client.submissions[0]["profile"] == "lab"
    # remember_connection runs in _submit_done's ok-path, before accept():
    # a successful submit is what earns the last-used-connection slot.
    assert wizard_module.last_connection_name() == "lab"


class FakeConflictClient(FakeWizardClient):
    """submit_job always 409s with a detail that does NOT match the
    "job N (" duplicate pattern, so _submit_done's resume-prompt branch
    (parse_duplicate_job_id -> QMessageBox.question) never fires and the
    test never has to contend with a real modal dialog."""

    def submit_job(self, payload):
        from mml_cloud_courier.cli.service_client import ServiceError
        raise ServiceError(409, "conflicts with another connection's prefix")


def test_failed_submit_does_not_remember_connection(qtbot, tmp_path):
    from mml_cloud_courier.gui import wizard as wizard_module

    client = FakeConflictClient()
    wizard = NewTransferWizard(client)
    qtbot.addWidget(wizard)
    qtbot.waitUntil(lambda: wizard.profile_combo.count() == 1, timeout=5000)
    wizard.set_source(str(tmp_path))
    wizard.set_job_name("myjob")
    wizard.accept_and_submit()
    qtbot.waitUntil(
        lambda: wizard.status_label.text() == "conflicts with another connection's prefix",
        timeout=5000,
    )
    assert wizard_module.last_connection_name() is None


def test_one_screen_has_no_pages(qtbot, wizard):
    from PySide6.QtWidgets import QWizard
    assert not isinstance(wizard, QWizard)


def test_dialog_is_2x_its_natural_stacked_row_width(wizard):
    # The 440px minimum (2x an offscreen sizeHint) under-reported the
    # dialog's actual shown width -- 800 is 2x the shown width instead.
    assert wizard.minimumWidth() == 800


def test_validation_messages_in_order(qtbot, wizard, tmp_path):
    wizard.profile_combo.setCurrentIndex(-1)
    wizard.accept_and_submit()
    assert "connection" in wizard.status_label.text()

    wizard.profile_combo.setCurrentIndex(0)
    wizard.accept_and_submit()
    assert "folder" in wizard.status_label.text()

    wizard.set_source(str(tmp_path))
    wizard.set_job_name("")
    wizard.accept_and_submit()
    assert "name" in wizard.status_label.text()


def test_set_profile_by_name(wizard):
    assert wizard.set_profile_by_name("lab") is True
    assert wizard.profile_combo.currentIndex() == 0
    assert wizard.state.profile_name == "lab"
    assert wizard.set_profile_by_name("no-such-connection") is False


def test_reject_stops_pending_preview_timer(wizard, tmp_path):
    # A source edit arms the 400ms debounce timer. Dismissing the dialog
    # (Cancel) must stop it too, not just cancel the in-flight scan event —
    # otherwise a timer left running fires _restart_preview on the hidden
    # dialog and starts a scan nothing will ever collect.
    wizard.set_source(str(tmp_path))
    assert wizard._preview_timer.isActive()
    wizard.reject()
    assert not wizard._preview_timer.isActive()


class FakeMultiProfileClient(FakeWizardClient):
    """Same idiom as FakeWizardClient, but seeded with two connections so
    preferred-profile selection has something to pick between."""

    def list_profiles(self):
        return [
            {"id": 1, "name": "lab", "bucket": "b", "default_prefix": "d",
             "auth_type": "service_account_key", "validated_at": None},
            {"id": 2, "name": "gate-oauth", "bucket": "b2", "default_prefix": "d2",
             "auth_type": "oauth", "validated_at": None},
        ]


@pytest.fixture
def make_wizard(qtbot):
    """Build a NewTransferWizard over FakeMultiProfileClient, forwarding
    constructor kwargs (e.g. preferred_profile) straight through."""
    def _make(**kwargs):
        client = FakeMultiProfileClient()
        w = NewTransferWizard(client, **kwargs)
        qtbot.addWidget(w)
        return w
    return _make


def test_preferred_profile_selected_on_load(qtbot, make_wizard):
    wizard = make_wizard(preferred_profile="gate-oauth")   # fixture seeds profiles incl. gate-oauth
    qtbot.waitUntil(lambda: wizard.profile_combo.count() > 0)
    assert wizard.state.profile_name == "gate-oauth"


def test_enter_in_any_field_does_not_flip_direction_to_upload(qtbot, wizard):
    # Every autoDefault QPushButton in a QDialog implicitly becomes THE
    # default button once the window is active and nothing is explicitly
    # marked default -- upload_button being first in the widget hierarchy
    # meant Enter pressed anywhere (e.g. the prefix field, while direction
    # is download) triggered *it* instead of Start. Reproducing this
    # requires the dialog to actually be shown/active (isDefault() is
    # False for everyone on an unshown QDialog), matching the live-app bug.
    from PySide6.QtCore import Qt

    wizard.show()
    qtbot.waitExposed(wizard)
    wizard.set_direction("download")
    wizard.prefix_edit.setFocus()
    qtbot.keyClick(wizard.prefix_edit, Qt.Key.Key_Return)
    assert wizard.state.direction == "download"
    assert not wizard.upload_button.isDefault()


def test_stale_remote_preview_dropped_when_superseded(wizard):
    # The download-path preview_remote result had no supersede guard (unlike
    # the upload path's guarded_emit): a slow bucket-listing response that
    # lands after the source/direction/profile changed again would still
    # overwrite the preview label with stale data.
    import threading

    result = {"objects": 5, "bytes": 100, "truncated": False}

    live_event = threading.Event()
    wizard.preview_label.setText("")
    wizard._maybe_remote_preview_done(result, live_event)
    assert "5" in wizard.preview_label.text()

    stale_event = threading.Event()
    stale_event.set()
    wizard.preview_label.setText("unchanged")
    wizard._maybe_remote_preview_done(result, stale_event)
    assert wizard.preview_label.text() == "unchanged"


def test_prefix_edit_restarts_preview_timer_only_for_download(wizard):
    # An upload rescan on prefix edits would be waste -- prefix is only the
    # live-preview driver on the download path (upload previews the local
    # source tree instead).
    wizard.set_direction("download")
    wizard._preview_timer.stop()
    wizard.prefix_edit.setText("some/prefix")
    assert wizard._preview_timer.isActive()

    wizard.set_direction("upload")
    wizard._preview_timer.stop()
    wizard.prefix_edit.setText("some/other/prefix")
    assert not wizard._preview_timer.isActive()


def test_remember_connection_roundtrip(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from mml_cloud_courier.gui import wizard as wizard_module
    monkeypatch.setattr(
        wizard_module, "_qsettings",
        lambda: QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat))
    assert wizard_module.last_connection_name() is None
    wizard_module.remember_connection("NOAA-CCEP")
    assert wizard_module.last_connection_name() == "NOAA-CCEP"
