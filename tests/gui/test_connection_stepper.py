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
