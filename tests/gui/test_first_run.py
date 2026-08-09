from PySide6.QtCore import Qt

from mml_cloud_courier.gui.first_run import FirstRunScreen


def test_step_card_paints_styled_background(qtbot):
    # _StepCard is a custom QWidget subclass carrying objectName
    # "firstRunStep" -- without WA_StyledBackground its QSS background/border
    # never paints and the card renders as an invisible bubble.
    screen = FirstRunScreen(on_add_connection=lambda: None, on_open_guide=lambda: None)
    qtbot.addWidget(screen)
    for step in screen.steps:
        assert step.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)


def test_first_run_copy_and_buttons(qtbot):
    clicks = []
    screen = FirstRunScreen(on_add_connection=lambda: clicks.append("add"),
                            on_open_guide=lambda: clicks.append("guide"))
    qtbot.addWidget(screen)
    assert screen.heading.text() == "Nothing has been transferred yet"
    assert screen.body.text() == (
        "Courier needs one connection before it can move anything — a"
        " bucket, and a credential the service can use on its own. After"
        " that, every transfer is a folder and a Start."
    )
    assert [s.title.text() for s in screen.steps] == [
        "Add a connection", "Point it at a folder",
        "Close the window whenever you like"]
    screen.add_button.click()
    screen.guide_button.click()
    assert clicks == ["add", "guide"]
