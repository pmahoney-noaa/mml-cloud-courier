import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from datetime import datetime, timedelta, timezone

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QVBoxLayout, QWidget

from mml_cloud_courier.gui.connection_widgets import (
    ADC_NOTE, AUTH_PRESENTATION, Dot, Pill, ProbeList, RingSpinner,
    SectionLabel, StepRail, last_check_line, pill_font, repolish,
    section_font,
)


def test_pill_uppercases_and_carries_tone(qtbot):
    pill = Pill("Recommended", tone="accent")
    qtbot.addWidget(pill)
    assert pill.text() == "RECOMMENDED"
    assert pill.property("tone") == "accent"
    pill.set_tone("disabled")
    assert pill.property("tone") == "disabled"


def test_auth_presentation_never_shows_raw_enums():
    assert AUTH_PRESENTATION["service_account_key"] == ("SERVICE ACCOUNT KEY", "accent")
    assert AUTH_PRESENTATION["oauth_user"] == ("GOOGLE SIGN-IN", "warn")
    assert AUTH_PRESENTATION["adc"] == ("COMMAND-LINE CREDENTIALS", "track")
    assert "machine's signed-in account" in ADC_NOTE


def test_last_check_line_variants():
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(minutes=12)).isoformat()
    stale = (now - timedelta(days=8)).isoformat()
    text, tone = last_check_line(
        {"auth_type": "service_account_key", "validated_at": fresh})
    assert text == "Checked 12 minutes ago." and tone == "muted"
    text, tone = last_check_line({"auth_type": "oauth_user", "validated_at": stale})
    assert "may have expired" in text and tone == "warn"
    text, tone = last_check_line({"auth_type": "oauth_user", "validated_at": fresh})
    assert "may have expired" not in text and tone == "muted"
    text, tone = last_check_line({"auth_type": "adc", "validated_at": fresh})
    assert text == ADC_NOTE and tone == "muted"
    text, tone = last_check_line(
        {"auth_type": "service_account_key", "validated_at": None})
    assert text == "Never checked." and tone == "muted"


def test_step_rail_tracks_current(qtbot):
    rail = StepRail()
    qtbot.addWidget(rail)
    assert rail.current == 1
    rail.set_current(2)
    assert rail.current == 2


def test_step_rail_run_fits_its_width_on_every_step(qtbot):
    rail = StepRail()
    qtbot.addWidget(rail)
    rail.resize(560, 28)   # the width the 600-wide stepper's header gives it
    for step in (1, 2, 3):
        rail.set_current(step)
        segments = rail._segments()
        kind, index, x, width = segments[-1]
        assert (kind, index) == ("label", 2)      # trailing "Verify" label
        assert x + width <= rail.width()           # fits — never clips


def test_probe_list_paces_and_caps(qtbot):
    probes = ProbeList()
    qtbot.addWidget(probes)
    assert probes.states() == ["pending"] * 5
    probes.start(interval_ms=10)
    assert probes.states()[0] == "running"
    qtbot.waitUntil(
        lambda: probes.states() == ["passed"] * 4 + ["running"], timeout=5000)
    # the cap: the last probe must never self-complete while the call is pending
    qtbot.wait(50)
    assert probes.states() == ["passed"] * 4 + ["running"]
    probes.finish_all()
    assert probes.states() == ["passed"] * 5


def test_qss_contains_connection_vocabulary():
    from mml_cloud_courier.gui import theme
    sheet = theme.qss(theme.LIGHT)
    for selector in ("connCard", "connPill", "helperText", "connNotice",
                     "dangerButton", "connFilterBar"):
        assert selector in sheet


def test_paint_backed_primitives_render_without_error(qtbot):
    # Dot, RingSpinner, and SectionLabel are otherwise never constructed or
    # painted by these tests; force their paintEvent paths to run via
    # grab() so a bad theme-attribute lookup or invalid QFont weight would
    # surface here instead of silently in a later task.
    for tone in ("accent", "warn", "danger"):
        dot = Dot(tone=tone, diameter=7)
        qtbot.addWidget(dot)
        pixmap = dot.grab()
        assert not pixmap.isNull()
        assert pixmap.size() == dot.size() * dot.devicePixelRatioF()

    spinner = RingSpinner()
    qtbot.addWidget(spinner)
    spinner.start()
    pixmap = spinner.grab()
    assert not pixmap.isNull()
    assert pixmap.size() == spinner.size() * spinner.devicePixelRatioF()
    spinner.stop()

    label = SectionLabel("Filters")
    qtbot.addWidget(label)
    pixmap = label.grab()
    assert not pixmap.isNull()


def test_fonts_and_repolish_are_well_formed(qtbot):
    pf = pill_font()
    sf = section_font()
    assert isinstance(pf, QFont) and isinstance(sf, QFont)
    assert pf.families()[0] == "Cascadia Mono"
    assert sf.families()[0] == "Cascadia Mono"
    assert pf.weight() == QFont.Weight.Medium
    assert sf.weight() == QFont.Weight.Normal

    host = QWidget()
    qtbot.addWidget(host)
    layout = QVBoxLayout(host)
    pill = Pill("ok", tone="accent")
    layout.addWidget(pill)
    repolish(host)  # must not raise across the widget + descendant tree
