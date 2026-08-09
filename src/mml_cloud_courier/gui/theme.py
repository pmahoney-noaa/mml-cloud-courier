"""Design tokens and theme resolution for the whole GUI.

Every color in the application comes from a Theme instance — nothing else
in gui/ may carry a hex value. Values are transcribed exactly from
docs/design/cloud-courier-theming/DESIGN_TOKENS.md.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSettings
from PySide6.QtGui import QGuiApplication


@dataclass(frozen=True)
class Theme:
    dark: bool
    bg: str; surface: str; rail: str; chrome: str; titlebar: str
    ink: str; muted: str; faint: str; disabled: str
    line: str; hairline: str; track: str; skip: str
    accent: str; accent_2: str; accent_3: str
    accent_text: str; accent_soft: str; accent_edge: str; accent_ink: str
    rail_selected: str
    danger: str; danger_soft: str; danger_edge: str; danger_text: str
    warn: str; warn_soft: str; warn_text: str


LIGHT = Theme(
    dark=False,
    bg="#f4f6f8", surface="#ffffff", rail="#ffffff", chrome="#fafbfc", titlebar="#eef1f4",
    ink="#12181f", muted="#4d5762", faint="#8a94a0", disabled="#b9c2cb",
    line="rgba(18,24,31,.12)", hairline="rgba(18,24,31,.06)",
    track="#e3e9ec", skip="#c5d0d6",
    accent="#006ea0", accent_2="#4caad7", accent_3="#95d4f6",
    accent_text="#005682", accent_soft="#e5f5fd", accent_edge="#c8dfeb", accent_ink="#ffffff",
    rail_selected="#eaf6fc",
    danger="#c13c3b", danger_soft="#ffe9e6", danger_edge="#facfca", danger_text="#892122",
    warn="#b06f35", warn_soft="#feefdc", warn_text="#774500",
)

DARK = Theme(
    dark=True,
    bg="#11171c", surface="#1a2128", rail="#161d22", chrome="#0b1116", titlebar="#070d12",
    ink="#eaeff4", muted="#a8afb5", faint="#7a8188", disabled="#474e54",
    line="rgba(160,195,235,.13)", hairline="rgba(160,195,235,.07)",
    track="#2d343a", skip="#42525f",
    accent="#2eabe1", accent_2="#1d85b0", accent_3="#77c9f3",
    accent_text="#67c4f2", accent_soft="rgba(120,175,255,.10)",
    accent_edge="rgba(120,175,255,.20)", accent_ink="#04111c",
    rail_selected="rgba(120,175,255,.09)",
    danger="#e8605b", danger_soft="rgba(255,140,120,.12)",
    danger_edge="rgba(255,140,120,.22)", danger_text="#ff9c8e",
    warn="#e8a95c", warn_soft="rgba(255,200,120,.12)", warn_text="#eeba70",
)

_VALID_SETTINGS = ("system", "light", "dark")


def _qsettings() -> QSettings:
    return QSettings("MML", "Cloud Courier")


def _style_hints():
    return QGuiApplication.styleHints()


def theme_setting() -> str:
    value = _qsettings().value("theme", "system")
    return value if value in _VALID_SETTINGS else "system"


def set_theme_setting(value: str) -> None:
    if value in _VALID_SETTINGS:
        _qsettings().setValue("theme", value)


def resolve(setting: str) -> Theme:
    if setting == "light":
        return LIGHT
    if setting == "dark":
        return DARK
    from PySide6.QtCore import Qt
    return DARK if _style_hints().colorScheme() == Qt.ColorScheme.Dark else LIGHT
