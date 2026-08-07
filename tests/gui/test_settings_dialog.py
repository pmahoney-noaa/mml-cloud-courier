import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from mml_cloud_transfer.gui.settings_dialog import (
    SettingsDialog, policy_fields, policy_text,
)


def test_policy_conversions_round_trip():
    text = policy_text(8, 1024, 1024)
    assert text == "8388608,1073741824,1073741824"
    assert policy_fields(text) == (8, 1024, 1024)
    assert policy_fields(None) is None


class FakeSettingsClient:
    def __init__(self):
        self.put_payloads = []

    def get_settings(self):
        return {"file_workers": 4, "size_policy": None,
                "auto_resume_on_startup": True, "stored": {},
                "restart_required": False}

    def put_settings(self, payload):
        self.put_payloads.append(payload)
        return {**self.get_settings(), "stored": dict(payload),
                "restart_required": True}


def test_dialog_builds_the_put_body(qtbot):
    client = FakeSettingsClient()
    dialog = SettingsDialog(client)
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog.workers_spin.value() == 4, timeout=5000)

    dialog.workers_spin.setValue(8)
    dialog.custom_policy_radio.setChecked(True)
    dialog.single_spin.setValue(8)
    dialog.resumable_spin.setValue(1024)
    dialog.slice_spin.setValue(1024)

    assert dialog.build_payload() == {
        "file_workers": 8,
        "size_policy": "8388608,1073741824,1073741824",
        "auto_resume_on_startup": True,
    }
