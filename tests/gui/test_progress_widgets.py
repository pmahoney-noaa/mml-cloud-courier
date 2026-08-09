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
