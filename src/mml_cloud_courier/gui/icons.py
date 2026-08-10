"""Icon assets: the whale-fluke-into-cloud mark, rendered from
assets/icons/*.svg by scripts/render_icons.py into gui/assets/. The mark
is self-contained (brand colors baked into the SVG), so there is no theme
coupling and nothing repaints on theme change."""

from __future__ import annotations

from importlib.resources import files

from PySide6.QtGui import QIcon, QPixmap

_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)


def app_icon() -> QIcon:
    icon = QIcon()
    assets = files("mml_cloud_courier.gui") / "assets"
    for size in _SIZES:
        pixmap = QPixmap()
        pixmap.loadFromData((assets / f"mark-{size}.png").read_bytes(), "PNG")
        icon.addPixmap(pixmap)
    return icon
