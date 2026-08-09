from mml_cloud_courier.gui.status_pill import PILL_TEXT, StatusPill


def test_pill_states_and_text(qtbot):
    pill = StatusPill()
    qtbot.addWidget(pill)
    assert pill.state == "ok"
    assert pill.label.text() == "Service running — transfers continue if you close this window"
    pill.set_state("down")
    assert pill.property("pillState") == "down"
    assert pill.label.text() == "Service stopped — nothing is moving"
    pill.set_state("noconn")
    assert pill.label.text() == "Service running — no connection set up yet"


def test_pill_rejects_unknown_state(qtbot):
    pill = StatusPill()
    qtbot.addWidget(pill)
    pill.set_state("bogus")
    assert pill.state == "ok"        # unchanged
