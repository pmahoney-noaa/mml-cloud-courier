from mml_cloud_courier.gui.first_run import FirstRunScreen


def test_first_run_copy_and_buttons(qtbot):
    clicks = []
    screen = FirstRunScreen(on_add_connection=lambda: clicks.append("add"),
                            on_open_guide=lambda: clicks.append("guide"))
    qtbot.addWidget(screen)
    assert screen.heading.text() == "Nothing has been transferred yet"
    assert screen.body.text().startswith("Courier needs one connection")
    assert [s.title.text() for s in screen.steps] == [
        "Add a connection", "Point it at a folder",
        "Close the window whenever you like"]
    screen.add_button.click()
    screen.guide_button.click()
    assert clicks == ["add", "guide"]
