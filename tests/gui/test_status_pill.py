from PySide6.QtCore import Qt

from mml_cloud_courier.gui.status_pill import PILL_TEXT, StatusPill


def test_pill_paints_styled_background(qtbot):
    # StatusPill is a custom QWidget subclass carrying objectName
    # "statusPill" -- without WA_StyledBackground its QSS background/border
    # never paints and the pill renders as an invisible bubble.
    pill = StatusPill()
    qtbot.addWidget(pill)
    assert pill.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)


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


def test_pill_down_recolors_label_and_dot(qtbot, qapp):
    from PySide6.QtGui import QPalette

    from mml_cloud_courier.gui import theme
    theme.apply_theme(qapp, theme.LIGHT)
    pill = StatusPill()
    qtbot.addWidget(pill)
    pill.show()
    pill.set_state("down")
    got = pill.label.palette().color(QPalette.ColorRole.WindowText).name()
    assert got == theme.LIGHT.danger_text
    theme.apply_theme(qapp, theme.LIGHT)   # leave global state clean
