"""Token table sanity + resolution. Appearance itself is reviewed by eye;
these tests guard the DATA the whole theme derives from."""
import dataclasses
import re

import pytest
from PySide6.QtCore import Qt

from mml_cloud_courier.gui import theme

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_RGBA = re.compile(r"^rgba\(\d+,\s*\d+,\s*\d+,\s*\.?\d+\)$")


def _luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    def lin(c): return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(fg: str, bg: str) -> float:
    l1, l2 = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


@pytest.mark.parametrize("t", [theme.LIGHT, theme.DARK], ids=["light", "dark"])
def test_every_token_is_a_color(t):
    for field in dataclasses.fields(theme.Theme):
        if field.name == "dark":
            continue
        value = getattr(t, field.name)
        assert _HEX.match(value) or _RGBA.match(value), f"{field.name}={value!r}"


@pytest.mark.parametrize("t", [theme.LIGHT, theme.DARK], ids=["light", "dark"])
def test_contrast_floors_from_the_handoff(t):
    # DESIGN_TOKENS.md states ink>=13:1 and muted>=6.5:1 on surface; we
    # assert the spec's floors (7:1 / 4.5:1) so a token tweak can't
    # silently go illegible.
    assert _contrast(t.ink, t.surface) >= 7.0
    assert _contrast(t.muted, t.surface) >= 4.5


def test_exact_anchor_values():
    assert theme.LIGHT.accent == "#006ea0"
    assert theme.DARK.accent == "#2eabe1"
    assert theme.DARK.accent_ink == "#04111c"   # dark text on dark-mode filled button
    assert theme.LIGHT.dark is False and theme.DARK.dark is True


def test_resolve_explicit_settings():
    assert theme.resolve("light") is theme.LIGHT
    assert theme.resolve("dark") is theme.DARK


def test_resolve_system_reads_style_hints(qapp, monkeypatch):
    class FakeHints:
        def colorScheme(self):
            return Qt.ColorScheme.Dark
    monkeypatch.setattr(theme, "_style_hints", lambda: FakeHints())
    assert theme.resolve("system") is theme.DARK


def test_setting_roundtrip_and_default(tmp_path, monkeypatch):
    # Isolate QSettings to a temp ini so tests never touch the real registry.
    from PySide6.QtCore import QSettings
    monkeypatch.setattr(
        theme, "_qsettings",
        lambda: QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat),
    )
    assert theme.theme_setting() == "system"
    theme.set_theme_setting("dark")
    assert theme.theme_setting() == "dark"
    theme.set_theme_setting("nonsense")     # invalid writes are ignored
    assert theme.theme_setting() == "dark"


def test_apply_theme_swaps_stylesheet_and_palette(qapp):
    theme.apply_theme(qapp, theme.DARK)
    assert theme.current() is theme.DARK
    assert theme.DARK.surface in qapp.styleSheet()
    assert qapp.palette().color(qapp.palette().ColorRole.Window).name() == theme.DARK.bg
    theme.apply_theme(qapp, theme.LIGHT)
    assert theme.current() is theme.LIGHT
    assert qapp.palette().color(qapp.palette().ColorRole.Window).name() == theme.LIGHT.bg


def test_apply_theme_emits_notifier(qapp, qtbot):
    with qtbot.waitSignal(theme.notifier.changed, timeout=1000) as blocker:
        theme.apply_theme(qapp, theme.DARK)
    assert blocker.args[0] is theme.DARK


def test_disabled_group_uses_disabled_token(qapp):
    from PySide6.QtGui import QPalette
    theme.apply_theme(qapp, theme.DARK)
    got = qapp.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
    assert got.name() == theme.DARK.disabled


def test_mono_font_prefers_cascadia():
    font = theme.mono_font(11.5, 600)
    assert font.families()[0] == "Cascadia Mono"
    assert "Consolas" in font.families()
    assert font.weight() == 600


def test_qss_mentions_every_bound_object_name():
    text = theme.qss(theme.LIGHT)
    for name in ("primaryButton", "segmentWell", "textButton",
                 "statusPill", "pillDot", "serviceBanner", "filesHeader"):
        assert name in text


def test_qcolor_hex_round_trip():
    from mml_cloud_courier.gui.theme import _qcolor
    color = _qcolor("#006ea0")
    assert color.name() == "#006ea0"


def test_qcolor_rgba_from_dark_theme():
    from mml_cloud_courier.gui.theme import _qcolor
    # theme.DARK.rail_selected == "rgba(120,175,255,.09)"
    color = _qcolor(theme.DARK.rail_selected)
    assert color.red() == 120
    assert color.green() == 175
    assert color.blue() == 255
    assert color.alpha() == round(0.09 * 255)


def test_qcolor_rgba_with_spaces():
    from mml_cloud_courier.gui.theme import _qcolor
    # Variant with spaces: "rgba(18, 24, 31, .12)"
    color = _qcolor("rgba(18, 24, 31, .12)")
    assert color.red() == 18
    assert color.green() == 24
    assert color.blue() == 31
    assert color.alpha() == round(0.12 * 255)
