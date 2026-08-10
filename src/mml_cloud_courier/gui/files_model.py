"""Virtualized file table: Qt pulls pages only as the user scrolls.

The fetcher is one HTTP GET against localhost answering from an indexed
SQLite page — synchronous by choice; the worst hiccup stalls a scrollbar,
never a transfer. A short page marks exhaustion.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from mml_cloud_courier.core.hashing import crc32c_to_base64
from mml_cloud_courier.gui import theme
from mml_cloud_courier.gui.format import STATE_LABELS, human_bytes
from mml_cloud_courier.gui.progress_widgets import STATE_TEXT_TOKENS

PAGE = 500


def _crc_b64_or_dash(value: int | None) -> str:
    return crc32c_to_base64(value) if value is not None else "—"


class FileTableModel(QAbstractTableModel):
    HEADERS = ("PATH", "SIZE", "STATE", "CRC32C", "DETAIL")

    def __init__(self, fetcher: Callable[..., list[dict]], parent=None):
        super().__init__(parent)
        self._fetcher = fetcher
        self._rows: list[dict] = []
        self._state: str | None = None
        self._category: str | None = None
        self._exhausted = True   # nothing to fetch until set_filter()
        self.last_error: str | None = None

    def set_filter(self, state: str | None = None, category: str | None = None) -> None:
        self.beginResetModel()
        self._state, self._category = state, category
        self._rows = []
        self._exhausted = False
        self.last_error = None
        self.endResetModel()
        self.fetchMore(QModelIndex())

    def refresh(self) -> None:
        self.set_filter(state=self._state, category=self._category)

    # ---- Qt plumbing ----------------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def canFetchMore(self, parent: QModelIndex) -> bool:
        return not parent.isValid() and not self._exhausted

    def fetchMore(self, parent: QModelIndex) -> None:
        if self._exhausted:
            return
        try:
            page = self._fetcher(state=self._state, category=self._category,
                                 limit=PAGE, offset=len(self._rows))
        except Exception as exc:
            # Stop hammering a failing endpoint; refresh() retries deliberately.
            self._exhausted = True
            self.last_error = str(exc)
            return
        if len(page) < PAGE:
            self._exhausted = True
        if not page:
            return
        first = len(self._rows)
        self.beginInsertRows(QModelIndex(), first, first + len(page) - 1)
        self._rows += page
        self.endInsertRows()

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.ToolTipRole and index.column() == 0:
            return self._rows[index.row()]["relative_path"]
        if role == Qt.ItemDataRole.ToolTipRole and index.column() == 3:
            return self._checksum_tooltip(self._rows[index.row()])
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() in (1, 2, 3):
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.FontRole and index.column() == 3:
            return theme.mono_font(8.5)
        if index.column() == 2 and role == Qt.ItemDataRole.ForegroundRole:
            state = self._rows[index.row()]["state"]
            token = STATE_TEXT_TOKENS.get(state, "muted")
            return theme._qcolor(getattr(theme.current(), token))
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        row = self._rows[index.row()]
        column = index.column()
        if column == 0:
            return row["relative_path"]
        if column == 1:
            return human_bytes(row["size_bytes"])
        if column == 2:
            return STATE_LABELS.get(row["state"], row["state"])
        if column == 3:
            return _crc_b64_or_dash(row.get("remote_crc32c"))
        return row.get("error_message") or ""

    @staticmethod
    def _checksum_tooltip(row: dict) -> str:
        lines = [
            f"local  {_crc_b64_or_dash(row.get('local_crc32c'))}",
            f"remote {_crc_b64_or_dash(row.get('remote_crc32c'))}",
        ]
        if row.get("sha256"):
            lines.append(f"sha256 {row['sha256']}")
        return "\n".join(lines)
