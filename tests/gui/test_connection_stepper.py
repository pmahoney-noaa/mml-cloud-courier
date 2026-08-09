"""Stepper shell: rail state, step-1 gating, health gate presentation."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from mml_cloud_courier.gui.connection_dialogs import (
    COPY_SERVICE_FIRST, NewConnectionDialog,
)


class HealthyClient:
    def health(self):
        return {"status": "ok"}


class DeadClient:
    def health(self):
        raise ConnectionError("nope")


def wait_health(qtbot, dialog, ok=True):
    qtbot.waitUntil(lambda: dialog._health_ok is ok, timeout=5000)


def test_opens_on_step_1_with_next_disabled(qtbot):
    dialog = NewConnectionDialog(HealthyClient())
    qtbot.addWidget(dialog)
    assert dialog._step == 1
    assert dialog.step_rail.current == 1
    assert not dialog.next_button.isEnabled()
    assert not dialog.back_button.isEnabled()
    assert dialog.next_button.text() == "Next: credential"


def test_next_enables_only_with_name_and_bucket(qtbot):
    dialog = NewConnectionDialog(HealthyClient())
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("MML imagery")
    assert not dialog.next_button.isEnabled()
    dialog.bucket_edit.setText("mml-hi-imagery-2026")
    assert dialog.next_button.isEnabled()
    dialog.name_edit.clear()
    assert not dialog.next_button.isEnabled()


def test_next_advances_and_back_returns_with_fields_intact(qtbot):
    dialog = NewConnectionDialog(HealthyClient())
    qtbot.addWidget(dialog)
    wait_health(qtbot, dialog)
    dialog.name_edit.setText("n")
    dialog.bucket_edit.setText("b")
    dialog.prefix_edit.setText("p")
    dialog.next_button.click()
    assert dialog._step == 2
    assert dialog.step_rail.current == 2
    assert "gs://b/p" in dialog.either_way_label.text()
    dialog.back_button.click()
    assert dialog._step == 1
    assert dialog.name_edit.text() == "n" and dialog.prefix_edit.text() == "p"


def test_healthy_service_enables_credential_paths_on_step2(qtbot):
    dialog = NewConnectionDialog(HealthyClient())
    qtbot.addWidget(dialog)
    wait_health(qtbot, dialog)
    assert dialog.key_button.isEnabled()
    assert dialog.signin_button.isEnabled()
    assert not dialog.gate_banner.isVisibleTo(dialog)


def test_dead_service_shows_gate_and_disabled_cards(qtbot):
    dialog = NewConnectionDialog(DeadClient())
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: "not reachable" in dialog.status_label.text(),
                    timeout=5000)
    assert dialog.status_label.text() == COPY_SERVICE_FIRST
    assert not dialog.key_button.isEnabled()
    assert not dialog.signin_button.isEnabled()
    assert dialog.card_key.property("state") == "disabled"
    assert dialog.check_again_button.objectName() == "dangerButton"
    assert dialog.open_main_button.objectName() == "dangerOutline"


def test_check_again_recovers_when_service_comes_up(qtbot):
    class FlappingClient:
        def __init__(self):
            self.up = False

        def health(self):
            if not self.up:
                raise ConnectionError("nope")
            return {"status": "ok"}

    client = FlappingClient()
    dialog = NewConnectionDialog(client)
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: "not reachable" in dialog.status_label.text(),
                    timeout=5000)
    client.up = True
    dialog.check_again_button.click()
    wait_health(qtbot, dialog)
    assert dialog.key_button.isEnabled()
    assert dialog.card_key.property("state") != "disabled"


def test_open_main_window_closes_the_stepper(qtbot):
    from PySide6.QtWidgets import QDialog
    dialog = NewConnectionDialog(DeadClient())
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: "not reachable" in dialog.status_label.text(),
                    timeout=5000)
    dialog.open_main_button.click()
    assert dialog.result() == QDialog.DialogCode.Rejected


def test_open_main_window_closes_a_modal_manager_beneath(qtbot):
    from PySide6.QtWidgets import QDialog
    from mml_cloud_courier.gui.connection_dialogs import ConnectionsDialog

    class ListingClient:
        def list_profiles(self):
            return []

        def health(self):
            raise ConnectionError("nope")

    manager = ConnectionsDialog(ListingClient())
    qtbot.addWidget(manager)
    manager.setModal(True)
    stepper = NewConnectionDialog(ListingClient(), manager)
    qtbot.addWidget(stepper)
    qtbot.waitUntil(lambda: "not reachable" in stepper.status_label.text(),
                    timeout=5000)
    stepper.open_main_button.click()
    assert stepper.result() == QDialog.DialogCode.Rejected
    assert manager.result() == QDialog.DialogCode.Rejected


def _to_step2(qtbot, dialog, name="n", bucket="b", prefix=""):
    wait_health(qtbot, dialog)
    dialog.name_edit.setText(name)
    dialog.bucket_edit.setText(bucket)
    if prefix:
        dialog.prefix_edit.setText(prefix)
    dialog.next_button.click()
    assert dialog._step == 2


def test_wrong_file_type_stays_on_step2_and_points_at_signin(qtbot, tmp_path, monkeypatch):
    import json
    bad = tmp_path / "client_secret_884213.json"
    bad.write_text(json.dumps({"type": None, "installed": {}}))
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(bad), "")))
    dialog = NewConnectionDialog(HealthyClient())
    qtbot.addWidget(dialog)
    _to_step2(qtbot, dialog)
    dialog.key_button.click()
    assert dialog.key_error_block.isVisibleTo(dialog)
    assert str(bad) in dialog.key_error_mono.text()          # raw exception, full path
    assert "OAuth client configuration" in dialog.key_error_plain.text()
    assert dialog.key_button.text() == "Choose a different file…"
    assert dialog.key_button.isEnabled()
    assert dialog.signin_button.isEnabled()                  # the other card stays live


def test_good_key_starts_create_with_key_payload(qtbot, tmp_path, monkeypatch):
    import json
    good = tmp_path / "key.json"
    good.write_text(json.dumps({"type": "service_account", "project_id": "p1"}))
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(good), "")))
    dialog = NewConnectionDialog(HealthyClient())
    qtbot.addWidget(dialog)
    _to_step2(qtbot, dialog, name="MML imagery", bucket="bkt", prefix="2026")
    dialog.key_button.click()
    assert dialog._phase == "validating"
    assert dialog._pending_payload["auth_type"] == "service_account_key"
    assert dialog._pending_payload["name"] == "MML imagery"
    assert dialog._key_path == str(good)


def test_signin_shows_waiting_page_then_feeds_oauth_payload(qtbot, tmp_path, monkeypatch):
    import json
    import threading
    from mml_cloud_courier.gui import connection_dialogs as mod
    config = tmp_path / "client.json"
    config.write_text(json.dumps({"installed": {"client_id": "x"}}))
    monkeypatch.setenv("MMLCC_OAUTH_CLIENT", str(config))
    monkeypatch.setattr(mod, "load_client_config", lambda source: {"ok": True})
    release = threading.Event()
    monkeypatch.setattr(
        mod, "run_login",
        lambda config, timeout_seconds=300: (release.wait(5),
                                             {"type": "authorized_user"})[1])
    dialog = NewConnectionDialog(HealthyClient())
    qtbot.addWidget(dialog)
    _to_step2(qtbot, dialog)
    dialog.signin_button.click()
    assert dialog._phase == "signing-in"
    assert dialog._stack.currentWidget() is dialog.page_signin
    assert not dialog.next_button.isVisibleTo(dialog)
    assert "Nothing is saved yet" in dialog.signin_cancel_note.text()
    release.set()
    qtbot.waitUntil(lambda: dialog._phase == "validating", timeout=5000)
    assert dialog._pending_payload["auth_type"] == "oauth_user"


def test_escape_during_signin_discards_the_result(qtbot, tmp_path, monkeypatch):
    import json
    import threading
    from mml_cloud_courier.gui import connection_dialogs as mod
    config = tmp_path / "client.json"
    config.write_text(json.dumps({"installed": {"client_id": "x"}}))
    monkeypatch.setenv("MMLCC_OAUTH_CLIENT", str(config))
    monkeypatch.setattr(mod, "load_client_config", lambda source: {"ok": True})
    release = threading.Event()
    monkeypatch.setattr(
        mod, "run_login",
        lambda config, timeout_seconds=300: (release.wait(5),
                                             {"type": "authorized_user"})[1])
    dialog = NewConnectionDialog(HealthyClient())
    qtbot.addWidget(dialog)
    _to_step2(qtbot, dialog)
    dialog.signin_button.click()
    generation = dialog._login_generation
    dialog.reject()                       # Escape path
    assert dialog._login_generation == generation + 1
    release.set()
    qtbot.wait(100)                       # late result must be discarded
    assert dialog._phase != "validating"


def test_signin_failure_returns_to_step2_with_message(qtbot, tmp_path, monkeypatch):
    import json
    from mml_cloud_courier.gui import connection_dialogs as mod
    config = tmp_path / "client.json"
    config.write_text(json.dumps({"installed": {"client_id": "x"}}))
    monkeypatch.setenv("MMLCC_OAUTH_CLIENT", str(config))
    monkeypatch.setattr(mod, "load_client_config", lambda source: {"ok": True})

    def failing_login(config, timeout_seconds=300):
        raise ValueError("Google did not return a refresh token; remove this app's access")

    monkeypatch.setattr(mod, "run_login", failing_login)
    dialog = NewConnectionDialog(HealthyClient())
    qtbot.addWidget(dialog)
    _to_step2(qtbot, dialog)
    dialog.signin_button.click()
    assert dialog._phase == "signing-in"
    qtbot.waitUntil(lambda: dialog._step == 2 and
                    dialog.signin_error_label.isVisibleTo(dialog), timeout=5000)
    assert "refresh token" in dialog.signin_error_label.text()
    assert dialog._phase == "idle"
    assert dialog._stack.currentWidget() is dialog.page_credential
