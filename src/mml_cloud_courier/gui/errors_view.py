"""Errors as always-expanded cause cards (README screen 3, Rec 5): the
taxonomy's message/action/count per cause, a needs-you/self-clearing tag,
and per-card buttons — the grouping logic in errors_model is untouched."""
from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from mml_cloud_courier.gui import theme
from mml_cloud_courier.gui.errors_model import ErrorGroup

SELF_CLEARING_WARN = frozenset({"file_locked", "source_changed"})
# quota is backend-marked transient (core/errors._TABLE) with the same
# "retries automatically" action text as network -- tagging it "Needs you"
# would contradict the card's own action copy.
SELF_CLEARING_ACCENT = frozenset({"network", "quota"})


def group_tone(category: str) -> str:
    if category in SELF_CLEARING_WARN:
        return "warn"
    if category in SELF_CLEARING_ACCENT:
        return "accent"
    return "danger"


def _needs_you(category: str) -> bool:
    return group_tone(category) == "danger"


def order_groups(groups: list[ErrorGroup]) -> list[ErrorGroup]:
    return sorted(groups, key=lambda g: (not _needs_you(g.category), -g.count))


def header_sentence(groups: list[ErrorGroup], files_total: int | None) -> str:
    if not groups:
        return ""
    failed = sum(g.count for g in groups)
    self_clearing = sum(1 for g in groups if not _needs_you(g.category))
    needs = len(groups) - self_clearing
    cause_noun = "cause" if len(groups) == 1 else "causes"
    clears = "clears itself" if self_clearing == 1 else "clear themselves"
    needs_verb = "needs" if needs == 1 else "need"
    total_text = f"{files_total:,}" if files_total else "?"
    return (f"{failed:,} of {total_text} files did not transfer,"
            f" from {len(groups)} {cause_noun}."
            f" {self_clearing} {clears};"
            f" {needs} {needs_verb} something from you.")


def group_fill_rows(page: list[str], group_count: int) -> list[str]:
    rows = list(page)
    remaining = group_count - len(page)
    if remaining > 0:
        rows.append(f"…and {remaining:,} more")
    return rows


class ErrorCard(QWidget):
    def __init__(self, group: ErrorGroup, *, on_retry, on_exclude, on_copy,
                 samples: list[str], parent=None):
        super().__init__(parent)
        self.group = group
        tone = group_tone(group.category)
        self.setObjectName("surfaceCard")
        self.setProperty("tone", tone)

        self.message_label = QLabel(group.message)
        self.message_label.setWordWrap(True)
        font = self.message_label.font()
        font.setPointSizeF(10.5)
        font.setWeight(QFont.Weight(600))
        self.message_label.setFont(font)
        self.count_label = QLabel(
            f"{group.count:,} file" + ("" if group.count == 1 else "s"))
        self.count_label.setFont(theme.mono_font(9, 500))
        self.tag = QLabel("Needs you" if tone == "danger" else "Retries on its own")
        self.tag.setObjectName("tag")
        self.tag.setProperty("tone", tone)
        top = QHBoxLayout()
        top.addWidget(self.message_label, 1)
        top.addWidget(self.count_label)
        top.addWidget(self.tag)

        self.action_label = QLabel(group.action)
        self.action_label.setWordWrap(True)

        self.retry_button = QPushButton("Retry these files")
        self.exclude_button = QPushButton("Stop retrying")
        self.copy_button = QPushButton("Copy file list")
        self.retry_button.clicked.connect(lambda: on_retry(group.category))
        self.exclude_button.clicked.connect(lambda: on_exclude(group.category))
        self.copy_button.clicked.connect(lambda: on_copy(group.category))
        self.samples_label = QLabel(" · ".join(samples))
        self.samples_label.setFont(theme.mono_font(8.0))
        self.samples_label.setWordWrap(False)
        bottom = QHBoxLayout()
        bottom.addWidget(self.retry_button)
        bottom.addWidget(self.exclude_button)
        bottom.addWidget(self.copy_button)
        bottom.addSpacing(10)
        bottom.addWidget(self.samples_label, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(10)
        layout.addLayout(top)
        layout.addWidget(self.action_label)
        layout.addLayout(bottom)


class ErrorsTab(QWidget):
    def __init__(self, *, on_retry, on_exclude, on_copy, on_expand=None, parent=None):
        super().__init__(parent)
        self._on_retry = on_retry
        self._on_exclude = on_exclude
        self._on_copy = on_copy
        self._on_expand = on_expand
        self._groups: list[ErrorGroup] = []
        self._cards: list[ErrorCard] = []
        self._files_total: int | None = None

        self.header_label = QLabel("")
        self.header_label.setWordWrap(True)
        self._cards_layout = QVBoxLayout()
        self._cards_layout.setSpacing(11)
        cards_holder = QWidget()
        holder_layout = QVBoxLayout(cards_holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.addLayout(self._cards_layout)
        holder_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(cards_holder)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 17, 20, 14)
        layout.setSpacing(11)
        layout.addWidget(self.header_label)
        layout.addWidget(scroll, 1)

    def set_files_total(self, total: int | None) -> None:
        self._files_total = total
        self.header_label.setText(header_sentence(self._groups, total))

    def load_groups(self, groups: list[ErrorGroup]) -> None:
        ordered = order_groups(groups)
        # A live job re-pushes its error groups through here on every SSE
        # event, even when nothing about them changed. Rebuilding every
        # ErrorCard (and re-running each group's bounded on_expand
        # page-fetch) on every such no-op tick is needless sync churn
        # during event storms -- ErrorGroup is a frozen dataclass, so list
        # equality is a cheap, reliable "truly unchanged" check.
        if ordered == self._groups and self._cards:
            return
        self._groups = ordered
        self.header_label.setText(header_sentence(self._groups, self._files_total))
        for card in self._cards:
            card.deleteLater()
        self._cards = []
        while self._cards_layout.count():
            self._cards_layout.takeAt(0)
        for group in self._groups:
            try:
                page = self._on_expand(group.category) if self._on_expand else []
            except Exception:
                page = []
            samples = group_fill_rows(page[:3], group.count)
            card = ErrorCard(group, on_retry=self._on_retry,
                             on_exclude=self._on_exclude, on_copy=self._on_copy,
                             samples=samples)
            self._cards.append(card)
            self._cards_layout.addWidget(card)

    # -- test/UI hooks -------------------------------------------------
    def group_count(self) -> int:
        return len(self._groups)

    def group_label(self, i: int) -> str:
        return self._groups[i].label

    def card(self, i: int) -> ErrorCard:
        return self._cards[i]
