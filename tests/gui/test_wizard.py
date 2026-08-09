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
    wizard.accept_and_submit()
    qtbot.waitUntil(lambda: submitted == [7], timeout=5000)
    assert client.submissions[0]["name"] == "myjob"
    assert client.submissions[0]["profile"] == "lab"


def test_one_screen_has_no_pages(qtbot, wizard):
    from PySide6.QtWidgets import QWizard
    assert not isinstance(wizard, QWizard)


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


def test_remember_connection_roundtrip(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from mml_cloud_courier.gui import wizard as wizard_module
    monkeypatch.setattr(
        wizard_module, "_qsettings",
        lambda: QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat))
    assert wizard_module.last_connection_name() is None
    wizard_module.remember_connection("NOAA-CCEP")
    assert wizard_module.last_connection_name() == "NOAA-CCEP"
