"""GUI entry point: `mmlct-gui`."""

from __future__ import annotations

import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from mml_cloud_courier.gui.icons import app_icon
    from mml_cloud_courier.gui.main_window import MainWindow
    from mml_cloud_courier.gui.session import discover_session

    app = QApplication(sys.argv)
    app.setApplicationName("MML Cloud Transfer")
    app.setWindowIcon(app_icon())
    if QSystemTrayIcon.isSystemTrayAvailable():
        # Without this, Qt quits as soon as the last *visible* window closes.
        # Closing the main window to the tray, then opening and finishing a
        # non-modal wizard (the wizard becomes the last visible window), would
        # otherwise kill the app seconds after promising the tray keeps
        # transfers running in the background. The tray's own Quit action
        # still calls QApplication.quit() explicitly, so exit is unaffected.
        # When no tray is available, the default behavior is kept so closing
        # the window exits the app as expected.
        app.setQuitOnLastWindowClosed(False)
    window = MainWindow(discover_session())
    window.show()
    code = app.exec()
    window.shutdown()
    return code


if __name__ == "__main__":
    sys.exit(main())
