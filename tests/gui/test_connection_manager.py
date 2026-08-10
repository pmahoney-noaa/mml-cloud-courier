"""Manager: status cards per profile, per-card actions, inline confirm and
refusal (never a modal on a modal), no raw enum values, no raw 409 string."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from datetime import datetime, timedelta, timezone

from mml_cloud_courier.cli.service_client import ServiceError
from mml_cloud_courier.gui.connection_dialogs import ConnectionsDialog

NOW = datetime.now(timezone.utc)


def profile(pid=1, name="MML imagery", auth="service_account_key",
            bucket="mml-hi-imagery-2026", prefix="2026", validated=None):
    return {"id": pid, "name": name, "auth_type": auth, "bucket": bucket,
            "default_prefix": prefix, "project_id": "",
            "created_at": NOW.isoformat(), "validated_at": validated}


class FakeClient:
    def __init__(self, profiles):
        self.profiles = profiles
        self.checked: list[int] = []
        self.deleted: list[int] = []
        self.delete_error: Exception | None = None
        self.check_result = {"ok": True, "preflight": {}, "summary":
            "This credential can list, read, write, compose and delete to gs://b/p."}

    def health(self):
        return {"status": "ok"}

    def list_profiles(self):
        return self.profiles

    def check_profile(self, profile_id, **_kw):
        self.checked.append(profile_id)
        return self.check_result

    def delete_profile(self, profile_id):
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(profile_id)
        return {"deleted": profile_id}


def wait_cards(qtbot, dialog, count):
    qtbot.waitUntil(lambda: len(dialog.cards) == count, timeout=5000)


def test_cards_render_pills_and_lines_not_raw_enums(qtbot):
    fresh = (NOW - timedelta(minutes=12)).isoformat()
    stale = (NOW - timedelta(days=9)).isoformat()
    client = FakeClient([
        profile(1, auth="service_account_key", validated=fresh),
        profile(2, "PAM archive", auth="oauth_user", bucket="mml-acoustics-archive",
                prefix="", validated=stale),
        profile(3, "Bering CTD", auth="adc", bucket="mml-oceanography",
                prefix="ctd", validated=fresh),
    ])
    dialog = ConnectionsDialog(client)
    qtbot.addWidget(dialog)
    wait_cards(qtbot, dialog, 3)
    key_card, oauth_card, adc_card = dialog.cards
    assert key_card.pill.text() == "SERVICE ACCOUNT KEY"
    assert "Checked 12 minutes ago." == key_card.check_line.text()
    assert oauth_card.pill.text() == "GOOGLE SIGN-IN"
    assert "may have expired" in oauth_card.check_line.text()
    assert adc_card.pill.text() == "COMMAND-LINE CREDENTIALS"
    assert "signed-in account" in adc_card.check_line.text()
    for card in dialog.cards:
        assert "service_account_key" not in card.check_line.text()
        assert card.check_button.objectName() != "primaryButton"
        assert card.remove_button.objectName() != "primaryButton"
    # the gs:// line
    assert key_card.target_label.text() == "gs://mml-hi-imagery-2026/2026"
    assert oauth_card.target_label.text() == "gs://mml-acoustics-archive"


def test_check_now_rewrites_the_line_in_place(qtbot):
    client = FakeClient([profile(1)])
    dialog = ConnectionsDialog(client)
    qtbot.addWidget(dialog)
    wait_cards(qtbot, dialog, 1)
    card = dialog.cards[0]
    card.check_button.click()
    qtbot.waitUntil(lambda: "can list, read, write" in card.check_line.text(),
                    timeout=5000)
    assert client.checked == [1]


def test_remove_shows_inline_confirm_then_deletes(qtbot):
    client = FakeClient([profile(1)])
    dialog = ConnectionsDialog(client)
    qtbot.addWidget(dialog)
    wait_cards(qtbot, dialog, 1)
    card = dialog.cards[0]
    assert not card.region.isVisibleTo(dialog)
    card.remove_button.click()
    assert card.region.isVisibleTo(dialog)
    assert "cannot be undone" in card.region_text.text()
    card.confirm_button.click()
    qtbot.waitUntil(lambda: client.deleted == [1], timeout=5000)


def test_keep_it_collapses_the_confirm(qtbot):
    client = FakeClient([profile(1)])
    dialog = ConnectionsDialog(client)
    qtbot.addWidget(dialog)
    wait_cards(qtbot, dialog, 1)
    card = dialog.cards[0]
    card.remove_button.click()
    card.keep_button.click()
    assert not card.region.isVisibleTo(dialog)
    assert client.deleted == []


def test_delete_refusal_renders_inline_with_count_and_route(qtbot):
    client = FakeClient([profile(4, "Leg 2 imagery (2025)")])
    client.delete_error = ServiceError(
        409, "profile 4 is used by 7 job(s) and cannot be deleted while they exist")
    dialog = ConnectionsDialog(client)
    qtbot.addWidget(dialog)
    wait_cards(qtbot, dialog, 1)
    card = dialog.cards[0]
    card.remove_button.click()
    card.confirm_button.click()
    qtbot.waitUntil(lambda: "used by 7 jobs" in card.region_text.text(), timeout=5000)
    assert "profile 4" not in card.region_text.text()       # raw string never shown
    assert card.show_jobs_button.text() == "Show those 7 jobs"
    fired = []
    dialog.showJobsForProfile.connect(lambda pid, name: fired.append((pid, name)))
    card.show_jobs_button.click()
    assert fired == [(4, "Leg 2 imagery (2025)")]
    assert dialog.result() == 1     # accepted/closed so the rail is visible


def test_delete_refusal_uses_singular_grammar_for_one_job(qtbot):
    client = FakeClient([profile(4, "Leg 2 imagery (2025)")])
    client.delete_error = ServiceError(
        409, "profile 4 is used by 1 job(s) and cannot be deleted while they exist")
    dialog = ConnectionsDialog(client)
    qtbot.addWidget(dialog)
    wait_cards(qtbot, dialog, 1)
    card = dialog.cards[0]
    card.remove_button.click()
    card.confirm_button.click()
    qtbot.waitUntil(lambda: "used by 1 job" in card.region_text.text(), timeout=5000)
    assert "used by 1 jobs" not in card.region_text.text()
    assert card.region_text.text() == (
        "This connection is used by 1 job and cannot be deleted while it exists.")
    assert card.show_jobs_button.text() == "Show that job"


def test_empty_state_and_new_button(qtbot):
    client = FakeClient([])
    dialog = ConnectionsDialog(client)
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog.empty_label.isVisibleTo(dialog), timeout=5000)
    assert dialog.empty_label.text() == "No connections yet."


def test_new_connection_refreshes_the_list_after_close(qtbot, monkeypatch):
    from mml_cloud_courier.gui import connection_dialogs as mod
    client = FakeClient([profile(1)])
    calls = {"n": 0}
    original = client.list_profiles

    def counting_list():
        calls["n"] += 1
        return original()

    client.list_profiles = counting_list
    dialog = ConnectionsDialog(client)
    qtbot.addWidget(dialog)
    wait_cards(qtbot, dialog, 1)
    before = calls["n"]
    monkeypatch.setattr(mod.NewConnectionDialog, "exec", lambda self: 0)
    dialog._new_connection()
    qtbot.waitUntil(lambda: calls["n"] > before, timeout=5000)


def test_delete_failure_after_refresh_ignores_the_dead_card(qtbot, monkeypatch):
    client = FakeClient([profile(1)])
    dialog = ConnectionsDialog(client)
    qtbot.addWidget(dialog)
    wait_cards(qtbot, dialog, 1)
    old_card = dialog.cards[0]
    dialog._profiles_loaded([profile(1)])        # refresh replaces every card
    assert old_card not in dialog.cards
    touched = []
    monkeypatch.setattr(old_card, "show_refusal", lambda n: touched.append(n))
    monkeypatch.setattr(old_card, "show_error_line", lambda t: touched.append(t))
    monkeypatch.setattr(old_card, "reset_region", lambda: touched.append("reset"))
    dialog._delete_failed(
        old_card,
        "409: profile 1 is used by 2 job(s) and cannot be deleted while they exist")
    assert touched == []                          # the dead card was never touched
