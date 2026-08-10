"""Render the committed icon assets from the SVG masters.

Dev-run only — never imported by the app, never a build step:

    .venv\\Scripts\\python scripts\\render_icons.py
    .venv\\Scripts\\python scripts\\render_icons.py --preview preview.png

Outputs are committed package data: src/mml_cloud_courier/gui/assets/
mark-<size>.png plus mmlcc.ico. Output is byte-stable, so re-running with
unchanged masters leaves git clean. PySide6 imports live inside functions
so importing this module (tests do) never needs Qt.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MASTER = REPO / "assets" / "icons" / "mark.svg"
SMALL_MASTER = REPO / "assets" / "icons" / "mark-16.svg"  # optional
OUT_DIR = REPO / "src" / "mml_cloud_courier" / "gui" / "assets"
SIZES = (16, 20, 24, 32, 48, 64, 128, 256)
SMALL_CUTOFF = 20  # sizes <= this use SMALL_MASTER when it exists

# Preview backdrops only (never shipped): app light window / dark taskbar.
PREVIEW_BACKDROPS = ("#f5f7fa", "#101418")
PREVIEW_SIZES = (16, 32, 256)
PREVIEW_TILE = 300


def build_ico(png_entries: list[tuple[int, bytes]]) -> bytes:
    """Pack PNG-compressed images into one .ico (valid on Vista+).

    png_entries: (pixel_size, png_bytes) pairs. Sizes >= 256 are encoded
    with width/height byte 0, per the ICO format."""
    header = struct.pack("<HHH", 0, 1, len(png_entries))
    directory = b""
    blobs = b""
    offset = len(header) + 16 * len(png_entries)
    for size, png in png_entries:
        edge = 0 if size >= 256 else size
        directory += struct.pack(
            "<BBBBHHII", edge, edge, 0, 0, 1, 32, len(png), offset
        )
        blobs += png
        offset += len(png)
    return header + directory + blobs


def _render_png(svg_path: Path, size: int) -> bytes:
    from PySide6.QtCore import QBuffer, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        raise SystemExit(f"invalid SVG: {svg_path}")
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    buffer = QBuffer()
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(buffer.data())


def _write_preview(out: Path) -> None:
    from PySide6.QtGui import QColor, QImage, QPainter

    columns = len(PREVIEW_SIZES)
    image = QImage(
        PREVIEW_TILE * columns,
        PREVIEW_TILE * len(PREVIEW_BACKDROPS),
        QImage.Format.Format_ARGB32,
    )
    painter = QPainter(image)
    for row, backdrop in enumerate(PREVIEW_BACKDROPS):
        for col, size in enumerate(PREVIEW_SIZES):
            painter.fillRect(
                col * PREVIEW_TILE, row * PREVIEW_TILE,
                PREVIEW_TILE, PREVIEW_TILE, QColor(backdrop),
            )
            mark = QImage.fromData(
                (OUT_DIR / f"mark-{size}.png").read_bytes()
            )
            painter.drawImage(
                col * PREVIEW_TILE + (PREVIEW_TILE - size) // 2,
                row * PREVIEW_TILE + (PREVIEW_TILE - size) // 2,
                mark,
            )
    painter.end()
    image.save(str(out), "PNG")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render committed icon assets from assets/icons/*.svg"
    )
    parser.add_argument(
        "--preview", metavar="OUT_PNG", default=None,
        help="also write a light/dark preview sheet to this path",
    )
    args = parser.parse_args(argv)

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication.instance() or QGuiApplication([sys.argv[0]])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[tuple[int, bytes]] = []
    for size in SIZES:
        use_small = size <= SMALL_CUTOFF and SMALL_MASTER.exists()
        master = SMALL_MASTER if use_small else MASTER
        png = _render_png(master, size)
        (OUT_DIR / f"mark-{size}.png").write_bytes(png)
        entries.append((size, png))
    (OUT_DIR / "mmlcc.ico").write_bytes(build_ico(entries))
    if args.preview:
        _write_preview(Path(args.preview))
    print(f"rendered {len(SIZES)} PNGs + mmlcc.ico into {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
