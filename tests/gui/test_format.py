import pytest

pytest.importorskip("PySide6")

from mml_cloud_courier.core.models import FileState, JobStatus
from mml_cloud_courier.gui.format import (
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


from mml_cloud_courier.gui.format import human_ago, iso_age_days, split_service_error


def test_split_service_error_separates_status_and_detail():
    code, detail = split_service_error(
        "409: profile 4 is used by 7 job(s) and cannot be deleted while they exist")
    assert code == 409
    assert detail == "profile 4 is used by 7 job(s) and cannot be deleted while they exist"


def test_split_service_error_passes_plain_messages_through():
    assert split_service_error("boom") == (None, "boom")
    assert split_service_error("404 not a prefix") == (None, "404 not a prefix")


def test_human_ago_buckets():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    assert human_ago(None) == "never"
    assert human_ago((now - timedelta(seconds=20)).isoformat()) == "just now"
    assert human_ago((now - timedelta(minutes=12)).isoformat()) == "12 minutes ago"
    assert human_ago((now - timedelta(hours=3)).isoformat()) == "3 hours ago"
    old = now - timedelta(days=40)
    label = human_ago(old.isoformat())
    assert str(old.astimezone().day) in label      # renders as a date, e.g. "Jun 30"
    assert "ago" not in label


def test_human_ago_handles_naive_sqlite_timestamps():
    # sqlite CURRENT_TIMESTAMP produces naive "YYYY-MM-DD HH:MM:SS" in UTC
    from datetime import datetime, timedelta, timezone
    naive = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(tzinfo=None)
    assert human_ago(naive.strftime("%Y-%m-%d %H:%M:%S")) == "5 minutes ago"


def test_iso_age_days():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    assert iso_age_days(None) is None
    assert iso_age_days("not a date") is None
    age = iso_age_days((now - timedelta(days=8)).isoformat())
    assert age is not None and 7.9 < age < 8.1
