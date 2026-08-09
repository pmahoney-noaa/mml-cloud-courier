"""Programmatic icons: no binary assets in the repo. Simple filled shapes
read fine at tray size; Phase 6 can swap real artwork in one place."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from mml_cloud_courier.gui import theme


# Every token used in this module (accent) is a plain hex string in BOTH
# Theme constants, so no rgba parsing is needed here.
def _token_color(token: str) -> str:
    return getattr(theme.current(), token)


def app_icon() -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(_token_color("accent")))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 28, 28, 6, 6)
    painter.setBrush(QColor("white"))
    # an up-arrow: the product moves data to the cloud
    painter.drawPolygon([QPoint(16, 7), QPoint(25, 17), QPoint(7, 17)])
    painter.drawRect(13, 17, 6, 8)
    painter.end()
    return QIcon(pixmap)
