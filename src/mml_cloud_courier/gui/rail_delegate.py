"""Paints the rail: colored uppercase group headers with counts, and
two-line job rows with a status dot. All colors read theme.current() at
paint time — never cached."""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from mml_cloud_courier.gui import theme
from mml_cloud_courier.gui.jobs_model import (
    JOB_ID_ROLE, RAIL_GROUPS, SECOND_LINE_ROLE, STALLED_OVERRIDE, STATUS_ROLE,
)

_HEADER_TEXT_TOKENS = {"needs_attention": "danger", "running": "accent_text",
                       "queued": "faint", "completed": "faint"}
_STATUS_DOT_TOKENS = {"incomplete": "danger", "stalled": "warn", "paused": "warn",
                      "running": "accent_2", "scanning": "accent_2",
                      "pending": "skip", "complete": "accent", "cancelled": "skip"}


def _dot_token(status: str, second_line: str | None) -> str:
    # A row whose second line has been overridden to the stalled message
    # (real status still "running"/"scanning") must show a warn dot, not
    # whatever the raw status maps to.
    if second_line == STALLED_OVERRIDE:
        return "warn"
    return _STATUS_DOT_TOKENS.get(status, "danger")


def _color(token: str) -> QColor:
    return theme._qcolor(getattr(theme.current(), token))


class RailDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:
        if index.data(JOB_ID_ROLE) is None:
            return QSize(option.rect.width(), 30)
        return QSize(option.rect.width(), 44)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        if index.data(JOB_ID_ROLE) is None:
            group = RAIL_GROUPS[index.row()]
            font = theme.mono_font(8.0, 600)
            font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 109)
            painter.setFont(font)
            painter.setPen(_color(_HEADER_TEXT_TOKENS[group]))
            label = f"{index.data(Qt.ItemDataRole.DisplayRole).upper()}  {index.model().itemFromIndex(index).rowCount()}"
            painter.drawText(rect.adjusted(6, 11, -6, -6),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
        else:
            if option.state & QStyle.StateFlag.State_Selected:
                painter.fillRect(rect, _color("rail_selected"))
                painter.fillRect(QRect(rect.left(), rect.top(), 2, rect.height()),
                                 _color("accent"))
            dot = _dot_token(index.data(STATUS_ROLE), index.data(SECOND_LINE_ROLE))
            painter.setBrush(_color(dot))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(rect.left() + 10, rect.top() + 12, 6, 6)
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            painter.setPen(_color("ink" if selected else "muted"))
            name_font = painter.font()
            name_font.setPointSizeF(9.5)
            name_font.setWeight(QFont.Weight(500))
            painter.setFont(name_font)
            metrics = painter.fontMetrics()
            text_rect = rect.adjusted(25, 8, -8, 0)
            line1 = metrics.elidedText(index.data(Qt.ItemDataRole.DisplayRole),
                                       Qt.TextElideMode.ElideRight, text_rect.width())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, line1)
            painter.setPen(_color("faint"))
            small = painter.font()
            small.setPointSizeF(8.5)
            small.setWeight(QFont.Weight(400))
            painter.setFont(small)
            line2 = painter.fontMetrics().elidedText(
                index.data(SECOND_LINE_ROLE) or "", Qt.TextElideMode.ElideRight,
                text_rect.width())
            painter.drawText(text_rect.adjusted(0, 18, 0, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, line2)
        painter.restore()
