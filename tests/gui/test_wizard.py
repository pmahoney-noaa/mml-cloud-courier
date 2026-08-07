import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from mml_cloud_courier.gui.wizard import (
    WizardState, build_submission, parse_duplicate_job_id, preview_scan,
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
              " gs://b/p — resume it (mmlct resume --job-id 12) or cancel it")
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


def test_wizard_walks_to_submission(qtbot, tmp_path):
    from mml_cloud_courier.gui.wizard import NewTransferWizard

    client = FakeWizardClient()
    wizard = NewTransferWizard(client)
    qtbot.addWidget(wizard)
    submitted = []
    wizard.jobSubmitted.connect(submitted.append)

    wizard.set_direction("upload")
    wizard.next()                       # -> connection page; profiles load async
    qtbot.waitUntil(lambda: wizard.profile_combo.count() == 1, timeout=5000)
    wizard.next()                       # -> folders
    wizard.set_source(str(tmp_path))
    wizard.set_prefix("data")
    wizard.next()                       # -> options & review
    wizard.set_job_name("myjob")
    wizard.accept_and_submit()
    qtbot.waitUntil(lambda: submitted == [7], timeout=5000)
    assert client.submissions[0]["name"] == "myjob"
    assert client.submissions[0]["profile"] == "lab"
