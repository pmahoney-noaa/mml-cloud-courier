"""GUI entry point: `mmlct-gui`."""

from __future__ import annotations

import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from mml_cloud_transfer.gui.icons import app_icon
    from mml_cloud_transfer.gui.main_window import MainWindow
    from mml_cloud_transfer.gui.session import discover_session

    app = QApplication(sys.argv)
    app.setApplicationName("MML Cloud Transfer")
    app.setWindowIcon(app_icon())
    window = MainWindow(discover_session())
    window.show()
    code = app.exec()
    window.shutdown()
    return code


if __name__ == "__main__":
    sys.exit(main())
