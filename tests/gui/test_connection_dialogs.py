import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

import json

from mml_cloud_courier.gui.connection_dialogs import (
    COPY_CHOOSE_KEY, COPY_CHOOSE_SIGNIN, COPY_DELETE_ORIGINAL,
    ConnectionsDialog, NewConnectionDialog, key_profile_payload,
    load_key_file, oauth_profile_payload,
)


def test_copy_follows_the_spec_and_the_gate_findings():
    assert "least-privilege" in COPY_CHOOSE_KEY
    assert "unattended, recurring" in COPY_CHOOSE_KEY
    assert "7 days" in COPY_CHOOSE_SIGNIN            # gate Finding 2, honestly
    assert "keep running after you sign out" in COPY_CHOOSE_SIGNIN
    assert "delete the original" in COPY_DELETE_ORIGINAL


def test_load_key_file_rejects_non_keys(tmp_path):
    path = tmp_path / "k.json"
    path.write_text(json.dumps({"type": "authorized_user"}))
    with pytest.raises(ValueError, match="authorized_user"):
        load_key_file(str(path))


def test_payload_builders_shape_the_api_body(tmp_path):
    key = {"type": "service_account", "project_id": "proj-1"}
    body = key_profile_payload(name="n", bucket="b", prefix="p", project="", key=key)
    assert body == {"name": "n", "bucket": "b", "auth_type": "service_account_key",
                    "credential": key, "project_id": "proj-1", "default_prefix": "p"}
    cred = {"type": "authorized_user", "refresh_token": "r"}
    body = oauth_profile_payload(name="n", bucket="b", prefix="", project="x",
                                 credential=cred)
    assert body["auth_type"] == "oauth_user" and body["project_id"] == "x"


class DeadClient:
    def health(self):
        raise ConnectionError("nope")


def test_new_connection_dialog_key_button_is_primary(qtbot):
    # Light-touch consistency pass (item H): the create action a new
    # connection should default toward gets the shared primaryButton QSS.
    dialog = NewConnectionDialog(DeadClient())
    qtbot.addWidget(dialog)
    assert dialog.key_button.objectName() == "primaryButton"
    assert dialog.signin_button.objectName() != "primaryButton"


def test_connections_dialog_new_button_is_primary(qtbot):
    class ListingClient:
        def list_profiles(self):
            return []

    dialog = ConnectionsDialog(ListingClient())
    qtbot.addWidget(dialog)
    assert dialog.new_button.objectName() == "primaryButton"
    assert dialog.check_button.objectName() != "primaryButton"
    assert dialog.remove_button.objectName() != "primaryButton"
    assert dialog.close_button.objectName() != "primaryButton"


def test_dialog_disables_credential_paths_until_the_service_answers(qtbot):
    dialog = NewConnectionDialog(DeadClient())
    qtbot.addWidget(dialog)
    # Both path buttons are disabled synchronously in __init__, before the
    # health check is even dispatched (that's the point: no browser/file
    # dialog can open before the service is confirmed reachable). Waiting
    # on button-enabled state is therefore trivially true at t=0 -- and
    # pytest-qt's waitUntil returns on the very first check without ever
    # pumping the Qt event loop, so the background thread's queued failure
    # signal would never actually be delivered. Wait on the label instead,
    # which only changes once the async _service_down callback has run.
    qtbot.waitUntil(lambda: "not reachable" in dialog.status_label.text(), timeout=5000)
    assert not dialog.key_button.isEnabled()
    assert not dialog.signin_button.isEnabled()
