import pytest

pytest.importorskip("PySide6")

from mml_cloud_transfer.gui.tray import notification_for


def test_notifications_speak_plainly():
    title, body = notification_for({"id": 1, "name": "run47"}, "complete")
    assert title == "Transfer complete" and "run47" in body
    title, body = notification_for({"id": 1, "name": "run47"}, "incomplete")
    assert "needs attention" in title.lower()
    title, body = notification_for({"id": 1, "name": "run47"}, "stalled")
    assert "retrying" in body
