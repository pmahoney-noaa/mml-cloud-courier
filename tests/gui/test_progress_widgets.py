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
