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
