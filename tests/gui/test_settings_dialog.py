import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from mml_cloud_courier.gui.settings_dialog import (
    SettingsDialog, policy_fields, policy_text,
)


def test_policy_conversions_round_trip():
    text = policy_text(8, 1024, 1024)
    assert text == "8388608,1073741824,1073741824"
    assert policy_fields(text) == (8, 1024, 1024)
    assert policy_fields(None) is None


class FakeSettingsClient:
    def __init__(self, stored=None):
        self.put_payloads = []
        self.stored = stored if stored is not None else {}

    def get_settings(self):
        return {"file_workers": 4, "size_policy": None,
                "auto_resume_on_startup": True, "stored": self.stored,
                "restart_required": False}

    def put_settings(self, payload):
        self.put_payloads.append(payload)
        # Apply payload to stored dict (like a real server would)
        new_stored = dict(self.stored)
        for key, value in payload.items():
            if key == "size_policy" and value == "":
                # Clear explicit: remove size_policy from stored
                new_stored.pop("size_policy", None)
            else:
                # Set or overwrite
                new_stored[key] = value
        self.stored = new_stored
        return {"file_workers": 4, "size_policy": None,
                "auto_resume_on_startup": True, "stored": self.stored,
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


def test_dialog_untouched_non_aligned_stored_policy_omits_size_policy(qtbot):
    """Non-aligned stored policy, load untouched → build_payload() omits size_policy."""
    stored = {"size_policy": "1000000,2000000,1500000"}
    client = FakeSettingsClient(stored=stored)
    dialog = SettingsDialog(client)
    qtbot.addWidget(dialog)
    # Wait for load to complete by checking that custom_policy_radio is now checked
    qtbot.waitUntil(lambda: dialog.custom_policy_radio.isChecked(), timeout=5000)

    # Untouched: just build payload (no spin changes)
    payload = dialog.build_payload()
    # size_policy should NOT be in payload (dirty flag is False, so we omit it)
    assert "size_policy" not in payload


def test_dialog_edited_non_aligned_stored_policy_sends_new_value(qtbot):
    """Non-aligned stored policy, user edits single_spin → build_payload() includes new size_policy."""
    stored = {"size_policy": "1000000,2000000,1500000"}
    client = FakeSettingsClient(stored=stored)
    dialog = SettingsDialog(client)
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog.custom_policy_radio.isChecked(), timeout=5000)

    # User edits one spin
    dialog.single_spin.setValue(8)

    payload = dialog.build_payload()
    # Now size_policy should be in payload with the new value
    assert payload["size_policy"] == policy_text(8, dialog.resumable_spin.value(),
                                                   dialog.slice_spin.value())


def test_dialog_prefers_stored_over_running_workers_and_auto_resume(qtbot):
    """Staged (stored) file_workers/auto_resume must win over the running values.

    Regression test: reopening the dialog used to load the RUNNING values into
    the spinbox/checkbox, so any Save would silently overwrite a staged change
    (e.g. workers=8 staged, awaiting restart) with the currently-running value.
    """
    stored = {"file_workers": 8, "auto_resume_on_startup": False}
    client = FakeSettingsClient(stored=stored)
    dialog = SettingsDialog(client)
    qtbot.addWidget(dialog)
    # Running values from get_settings() are file_workers=4, auto_resume=True;
    # stored overrides should win instead.
    qtbot.waitUntil(lambda: dialog.workers_spin.value() == 8, timeout=5000)

    assert dialog.workers_spin.value() == 8
    assert dialog.auto_resume_check.isChecked() is False

    # An untouched Save must round-trip the stored values, not the running ones.
    payload = dialog.build_payload()
    assert payload["file_workers"] == 8
    assert payload["auto_resume_on_startup"] is False


def test_dialog_two_save_sequence_refreshes_had_stored_policy(qtbot):
    """Save custom policy, then switch to default → second save sends clear."""
    client = FakeSettingsClient(stored={})
    dialog = SettingsDialog(client)
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog.workers_spin.value() == 4, timeout=5000)

    # First save: set custom policy
    dialog.custom_policy_radio.setChecked(True)
    dialog.single_spin.setValue(8)
    dialog.resumable_spin.setValue(512)
    dialog.slice_spin.setValue(512)
    payload1 = dialog.build_payload()
    assert "size_policy" in payload1

    # Simulate save completing: refresh the flag from response
    response = client.put_settings(payload1)
    dialog._had_stored_policy = "size_policy" in response["stored"]
    dialog._policy_dirty = False

    # Second action: switch to default (sets dirty again)
    dialog.default_radio.setChecked(True)

    payload2 = dialog.build_payload()
    # Now payload should have size_policy: "" to clear it
    assert payload2.get("size_policy") == ""


@pytest.fixture
def dialog(qtbot):
    """A loaded SettingsDialog over a fresh FakeSettingsClient, matching this
    file's existing construction pattern (client -> dialog -> qtbot.addWidget
    -> wait for the async load to land)."""
    client = FakeSettingsClient()
    d = SettingsDialog(client)
    qtbot.addWidget(d)
    qtbot.waitUntil(lambda: d.workers_spin.value() == 4, timeout=5000)
    return d


@pytest.fixture
def isolated_theme_settings(tmp_path, monkeypatch):
    """Isolate QSettings to a temp ini so theme tests never touch the real
    registry — same idiom as tests/gui/test_theme.py's
    test_setting_roundtrip_and_default."""
    from PySide6.QtCore import QSettings
    from mml_cloud_courier.gui import theme
    monkeypatch.setattr(
        theme, "_qsettings",
        lambda: QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat),
    )


def test_theme_combo_lists_three_options_and_defaults_to_setting(isolated_theme_settings, qtbot):
    from mml_cloud_courier.gui import theme
    theme.set_theme_setting("light")   # seed the isolated store BEFORE constructing the dialog
    client = FakeSettingsClient()
    d = SettingsDialog(client)
    qtbot.addWidget(d)
    qtbot.waitUntil(lambda: d.workers_spin.value() == 4, timeout=5000)

    datas = [d.theme_combo.itemData(i) for i in range(d.theme_combo.count())]
    assert datas == ["system", "light", "dark"]
    assert d.theme_combo.currentIndex() == 1


def test_theme_change_persists_and_applies(isolated_theme_settings, qtbot, monkeypatch):
    from mml_cloud_courier.gui import theme
    theme.set_theme_setting("light")   # known starting point: index 1, so ->2 is a real transition
    client = FakeSettingsClient()
    d = SettingsDialog(client)
    qtbot.addWidget(d)
    qtbot.waitUntil(lambda: d.workers_spin.value() == 4, timeout=5000)

    applied = []
    monkeypatch.setattr(theme, "set_theme_setting", lambda v: applied.append(("set", v)))
    monkeypatch.setattr(
        "mml_cloud_courier.gui.settings_dialog.apply_theme_for_setting",
        lambda v: applied.append(("apply", v)),
    )
    d.theme_combo.setCurrentIndex(2)   # dark
    assert ("set", "dark") in applied and ("apply", "dark") in applied


def test_build_payload_never_contains_theme(dialog):
    assert "theme" not in dialog.build_payload()
