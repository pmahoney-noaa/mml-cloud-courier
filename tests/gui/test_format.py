import pytest

pytest.importorskip("PySide6")

from mml_cloud_transfer.core.models import FileState, JobStatus
from mml_cloud_transfer.gui.format import (
    STATE_LABELS, STATUS_LABELS, human_bytes, human_duration, human_rate,
)


def test_human_bytes_matches_spec_style():
    assert human_bytes(6_600_000_000_000) == "6.6 TB"
    assert human_bytes(480_000_000) == "480 MB"
    assert human_bytes(999) == "999 B"
    assert human_bytes(0) == "0 B"


def test_human_rate_and_duration():
    assert human_rate(12_400_000) == "12.4 MB/s"
    assert human_duration(398) == "6m 38s"
    assert human_duration(7_440) == "2h 4m"
    assert human_duration(45) == "45s"


def test_every_status_and_state_has_a_label():
    assert set(STATUS_LABELS) == {s.value for s in JobStatus}
    assert set(STATE_LABELS) == {s.value for s in FileState}
