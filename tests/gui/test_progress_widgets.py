# tests/gui/test_progress_widgets.py
"""Custom-painted Progress widgets: logic-level tests (fractions, clamping,
token mapping); pixels are reviewed by eye."""
import pytest

from mml_cloud_courier.gui.progress_widgets import SegmentedBar


def test_segmented_bar_clamps_fractions(qtbot):
    bar = SegmentedBar()
    qtbot.addWidget(bar)
    bar.set_fractions(0.8, 0.5)          # 1.3 total: inflight clamps to 0.2
    assert bar.verified == pytest.approx(0.8)
    assert bar.inflight == pytest.approx(0.2)
    bar.set_fractions(-1, 2)             # nonsense: clamps to [0,1]
    assert bar.verified == 0.0
    assert bar.inflight == 1.0
    assert bar.minimumHeight() == 8


def test_state_card_order_and_legend(qtbot):
    from mml_cloud_courier.gui.progress_widgets import STATE_ORDER, StateBarCard
    card = StateBarCard()
    qtbot.addWidget(card)
    card.set_counts({"failed": 11, "verified": 8862, "skipped": 2104,
                     "transferring": 4, "bogus_state": 3})
    labels = card.legend_labels()
    # ordered by STATE_ORDER, zero-count states omitted, unknown states last
    assert labels[0] == ("Verified", 8862)
    assert labels[1] == ("Transferring", 4)
    assert labels[2] == ("Skipped (already up to date)", 2104)
    assert labels[3] == ("Failed", 11)
    assert labels[-1] == ("bogus_state", 3)


def test_state_order_matches_lifecycle():
    from mml_cloud_courier.gui.progress_widgets import STATE_ORDER, STATE_TOKENS
    assert STATE_ORDER == ("verified", "transferred", "transferring", "pending",
                          "skipped", "changed", "failed", "quarantined")
    assert set(STATE_TOKENS) == set(STATE_ORDER)
    assert STATE_TOKENS["failed"] == "danger" and STATE_TOKENS["skipped"] == "skip"


def test_event_kind_token_mapping():
    from mml_cloud_courier.gui.progress_widgets import event_kind_token
    assert event_kind_token("verified") == "accent_text"
    assert event_kind_token("failed") == "danger"
    assert event_kind_token("retry") == "warn"
    assert event_kind_token("run_started") == "muted"


def test_state_card_legend_not_rebuilt_when_counts_unchanged(qtbot):
    """Two SSE ticks reporting the same counts must not tear down and
    rebuild the legend row widgets -- otherwise every unchanged tick churns
    fresh QLabel/QWidget objects for no visible difference."""
    from mml_cloud_courier.gui.progress_widgets import StateBarCard

    card = StateBarCard()
    qtbot.addWidget(card)
    card.set_counts({"verified": 5, "failed": 1})

    def _legend_widget_ids():
        return [
            id(card._legend_layout.itemAt(i).widget())
            for i in range(card._legend_layout.count())
            if card._legend_layout.itemAt(i).widget() is not None
        ]

    before = _legend_widget_ids()
    card.set_counts({"verified": 5, "failed": 1})   # identical counts
    assert _legend_widget_ids() == before

    card.set_counts({"verified": 5, "failed": 2})   # actually changed
    assert _legend_widget_ids() != before


def test_state_card_theme_replay_bypasses_the_unchanged_guard(qapp, qtbot):
    """_on_theme replays set_counts with the SAME counts after a theme swap
    (so legend swatch colors repaint) -- that replay must not be swallowed
    by the same-counts early return the SSE-tick guard relies on."""
    from mml_cloud_courier.gui import theme
    from mml_cloud_courier.gui.progress_widgets import StateBarCard

    theme.apply_theme(qapp, theme.LIGHT)
    card = StateBarCard()
    qtbot.addWidget(card)
    card.set_counts({"verified": 5})
    before_id = id(card._legend_layout.itemAt(0).widget())

    theme.apply_theme(qapp, theme.DARK)
    after_id = id(card._legend_layout.itemAt(0).widget())
    assert after_id != before_id   # rebuilt with the new theme's swatch color

    theme.apply_theme(qapp, theme.LIGHT)   # leave the session qapp in LIGHT


def test_inflight_detail_text():
    from mml_cloud_courier.gui.progress_widgets import inflight_detail_text
    entry = {"relative_path": "a/b.tif", "bytes_transferred": 100, "size_bytes": 1000,
             "method": "sliced", "slices_total": 8, "slices_done": 4}
    assert inflight_detail_text(entry) == "100 B of 1.0 KB · slice 5 of 8, 4 done"
    single = {"relative_path": "c.bin", "bytes_transferred": 5, "size_bytes": 10,
              "method": "single_shot", "slices_total": 0}
    assert inflight_detail_text(single) == "5 B of 10 B"


# -- delegate paint widths never go negative on a narrow window --------------
#
# On a narrow window, the fixed-cost text (byte detail / ISO timestamp) can
# be wider than the whole row: rect.width() - detail_width - 12 (Inflight)
# and rect.right() - kind_rect.right() - 8 (Events) then go negative. Qt's
# elidedText tolerates a negative width by returning "", but relying on that
# rather than clamping is exactly the kind of thing that breaks silently on
# a Qt/binding version bump — so both computed widths are clamped to
# max(0, ...) before being handed to elidedText/QRect. These tests spy on
# QFontMetrics.elidedText to prove the width argument it receives is never
# negative, on rects narrow enough that it otherwise would be.


def test_inflight_delegate_never_passes_negative_elide_width(qapp, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QFontMetrics, QPainter, QPixmap
    from PySide6.QtWidgets import QStyleOptionViewItem

    from mml_cloud_courier.gui.progress_widgets import INFLIGHT_ROLE, InflightDelegate

    seen_widths = []
    original = QFontMetrics.elidedText

    def spy(self, text, mode, width, *args):
        seen_widths.append(width)
        return original(self, text, mode, width, *args)

    monkeypatch.setattr(QFontMetrics, "elidedText", spy)

    class _Index:
        def data(self, role):
            if role == INFLIGHT_ROLE:
                return {"relative_path": "a/very/long/nested/path/to/some/file.tif",
                        "bytes_transferred": 500_000_000, "size_bytes": 800_000_000,
                        "method": "sliced", "slices_total": 8, "slices_done": 4}
            return None

    pixmap = QPixmap(200, 60)
    painter = QPainter(pixmap)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 20, 40)   # far narrower than the byte/slice detail text alone
    InflightDelegate().paint(painter, option, _Index())
    painter.end()

    assert seen_widths, "elidedText was never called"
    assert all(w >= 0 for w in seen_widths)


def test_events_delegate_never_passes_negative_elide_width(qapp, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QFontMetrics, QPainter, QPixmap
    from PySide6.QtWidgets import QStyleOptionViewItem

    from mml_cloud_courier.gui.progress_widgets import EVENT_ROLE, EventsDelegate

    seen_widths = []
    original = QFontMetrics.elidedText

    def spy(self, text, mode, width, *args):
        seen_widths.append(width)
        return original(self, text, mode, width, *args)

    monkeypatch.setattr(QFontMetrics, "elidedText", spy)

    class _Index:
        def data(self, role):
            if role == EVENT_ROLE:
                return ("2026-08-08T12:00:00.123456", "verified", "some detail text here")
            return None

    pixmap = QPixmap(200, 30)
    painter = QPainter(pixmap)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 40, 22)   # the ISO timestamp alone eats most of this
    EventsDelegate().paint(painter, option, _Index())
    painter.end()

    assert seen_widths, "elidedText was never called"
    assert all(w >= 0 for w in seen_widths)
