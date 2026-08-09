"""README screen 6: the one screen a brand-new install shows — three
sentences and three steps, no decoration."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from mml_cloud_courier.gui import theme

_STEPS = (
    ("Add a connection",
     "A connection is a bucket plus a credential the service stores for itself."),
    ("Point it at a folder",
     "Pick any folder — the scan preview shows what will move before anything starts."),
    ("Close the window whenever you like",
     "Transfers run in the Windows service; closing this window never stops them."),
)


class _StepCard(QWidget):
    def __init__(self, number: int, title: str, body: str, parent=None):
        super().__init__(parent)
        self.setObjectName("firstRunStep")
        # Custom QWidget subclasses don't paint QSS backgrounds/borders
        # unless this attribute is set -- without it the card is invisible
        # (transparent) and just shows whatever is behind it.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.badge = QLabel(str(number))
        self.badge.setObjectName("stepBadge")
        self.badge.setFixedSize(20, 20)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setFont(theme.mono_font(8.5))
        self.title = QLabel(title)
        font = self.title.font()
        font.setPointSizeF(10)
        font.setWeight(QFont.Weight(600))
        self.title.setFont(font)
        self.body = QLabel(body)
        self.body.setWordWrap(True)
        text_column = QVBoxLayout()
        text_column.setSpacing(3)
        text_column.addWidget(self.title)
        text_column.addWidget(self.body)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 13, 15, 13)
        layout.setSpacing(11)
        layout.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(text_column, 1)


class FirstRunScreen(QWidget):
    def __init__(self, *, on_add_connection, on_open_guide, parent=None):
        super().__init__(parent)
        self.heading = QLabel("Nothing has been transferred yet")
        font = self.heading.font()
        font.setPointSizeF(15.5)
        font.setWeight(QFont.Weight(600))
        self.heading.setFont(font)
        self.body = QLabel(
            "Courier needs one connection before it can move anything — a"
            " bucket, and a credential the service can use on its own. After"
            " that, every transfer is a folder and a Start."
        )
        self.body.setWordWrap(True)

        self.steps = [_StepCard(i + 1, title, body)
                      for i, (title, body) in enumerate(_STEPS)]

        self.add_button = QPushButton("Add a connection")
        self.add_button.setObjectName("primaryButton")
        self.add_button.clicked.connect(lambda: on_add_connection())
        self.guide_button = QPushButton("Read the setup guide")
        self.guide_button.clicked.connect(lambda: on_open_guide())
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.guide_button)
        buttons.addStretch(1)

        column = QVBoxLayout()
        column.setSpacing(11)
        column.addStretch(1)
        column.addWidget(self.heading)
        column.addWidget(self.body)
        for step in self.steps:
            column.addWidget(step)
        column.addLayout(buttons)
        column.addStretch(2)
        holder = QWidget()
        holder.setMaximumWidth(520)
        holder.setLayout(column)
        outer = QHBoxLayout(self)
        outer.addStretch(1)
        outer.addWidget(holder)
        outer.addStretch(1)
