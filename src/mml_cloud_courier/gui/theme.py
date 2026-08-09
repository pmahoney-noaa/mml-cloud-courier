"""Design tokens and theme resolution for the whole GUI.

Every color in the application comes from a Theme instance — nothing else
in gui/ may carry a hex value. Values are transcribed exactly from
docs/design/cloud-courier-theming/DESIGN_TOKENS.md.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette


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


class ThemeNotifier(QObject):
    changed = Signal(object)      # Theme


notifier = ThemeNotifier()
_current: Theme = LIGHT


def current() -> Theme:
    return _current


def mono_font(size_pt: float, weight: int = 400) -> QFont:
    font = QFont()
    font.setFamilies(["Cascadia Mono", "Consolas", "monospace"])
    font.setPointSizeF(size_pt)
    font.setWeight(QFont.Weight(weight))
    return font


def _qcolor(value: str) -> QColor:
    if value.startswith("rgba("):
        parts = value[5:-1].split(",")
        r, g, b = (int(p) for p in parts[:3])
        alpha = float(parts[3])
        return QColor(r, g, b, round(alpha * 255))
    return QColor(value)


def palette(t: Theme) -> QPalette:
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: t.bg,
        QPalette.ColorRole.WindowText: t.ink,
        QPalette.ColorRole.Base: t.surface,
        QPalette.ColorRole.AlternateBase: t.bg,
        QPalette.ColorRole.Text: t.ink,
        QPalette.ColorRole.Button: t.surface,
        QPalette.ColorRole.ButtonText: t.ink,
        QPalette.ColorRole.Highlight: t.accent,
        QPalette.ColorRole.HighlightedText: t.accent_ink,
        QPalette.ColorRole.ToolTipBase: t.surface,
        QPalette.ColorRole.ToolTipText: t.ink,
        QPalette.ColorRole.PlaceholderText: t.faint,
        QPalette.ColorRole.Link: t.accent_text,
    }
    for role, value in roles.items():
        p.setColor(role, _qcolor(value))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, _qcolor(t.disabled))
    return p


def qss(t: Theme) -> str:
    return f"""
QWidget {{ background: {t.bg}; color: {t.ink}; font-size: 13px; }}
QToolBar {{ background: {t.chrome}; border: none; border-bottom: 1px solid {t.line};
            padding: 0 14px; spacing: 10px; }}
QPushButton {{ background: {t.surface}; color: {t.ink}; border: 1px solid {t.line};
               border-radius: 6px; padding: 6px 11px; font-size: 12.5px; font-weight: 500; }}
QPushButton:hover {{ border-color: {t.accent_edge}; }}
QPushButton:focus {{ outline: none; border: 2px solid {t.accent}; }}
QPushButton:disabled {{ color: {t.disabled}; background: {t.track}; border-color: transparent; }}
QPushButton#primaryButton {{ background: {t.accent}; color: {t.accent_ink};
                             border: none; padding: 7px 13px; font-weight: 600; }}
QPushButton#primaryButton:disabled {{ background: {t.track}; color: {t.disabled}; }}
QWidget#segmentWell {{ background: {t.track}; border-radius: 6px; }}
QPushButton#segmentButton {{ background: transparent; border: none; border-radius: 4px;
                             padding: 6px 12px; }}
QPushButton#segmentButton:enabled {{ background: {t.surface}; color: {t.ink}; }}
QPushButton#segmentButton:disabled {{ background: transparent; color: {t.disabled}; }}
QPushButton#segmentButton:checked {{ background: {t.accent}; color: {t.accent_ink}; }}
QPushButton#textButton {{ background: transparent; border: none; color: {t.muted}; }}
QPushButton#textButton:hover {{ color: {t.ink}; }}
QWidget#statusPill {{ border-radius: 12px; padding: 0px; background: {t.accent_soft};
                      border: 1px solid {t.accent_edge}; }}
QWidget#statusPill[pillState="down"] {{ background: {t.danger_soft}; border-color: {t.danger_edge}; }}
QLabel#pillLabel {{ background: transparent; border: none; color: {t.accent_text};
                    font-size: 11.5px; font-weight: 500; }}
QWidget#statusPill[pillState="down"] QLabel#pillLabel {{ color: {t.danger_text}; }}
QFrame#pillDot {{ background: {t.accent}; border-radius: 3px; border: none; }}
QWidget#statusPill[pillState="down"] QFrame#pillDot {{ background: {t.danger}; }}
QWidget#serviceBanner {{ background: {t.danger_soft}; border-bottom: 1px solid {t.danger_edge}; }}
QWidget#serviceBanner QLabel {{ background: transparent; color: {t.danger_text}; font-size: 12.5px; }}
QWidget#serviceBanner QPushButton {{ background: {t.danger}; color: #ffffff; border: none;
                                     padding: 7px 14px; border-radius: 6px; }}
QLabel#filesHeader {{ color: {t.faint}; background: transparent; }}
QLabel#headlineRoute {{ color: {t.faint}; background: transparent; }}
QWidget#surfaceCard {{ background: {t.surface}; border: 1px solid {t.line}; border-radius: 9px; }}
QWidget#statCell {{ background: {t.surface}; border: 1px solid {t.line}; border-radius: 9px; }}
QWidget#surfaceCard[tone="danger"] {{ border-left: 3px solid {t.danger}; }}
QWidget#surfaceCard[tone="warn"] {{ border-left: 3px solid {t.warn}; }}
QWidget#surfaceCard[tone="accent"] {{ border-left: 3px solid {t.accent_2}; }}
QLabel#sectionLabel {{ color: {t.faint}; background: transparent; letter-spacing: 1px; }}
QLabel#tag {{ border-radius: 4px; padding: 4px 7px; font-size: 10.5px; font-weight: 500; }}
QLabel#tag[tone="danger"] {{ background: {t.danger_soft}; color: {t.danger_text}; }}
QLabel#tag[tone="warn"] {{ background: {t.warn_soft}; color: {t.warn_text}; }}
QLabel#tag[tone="accent"] {{ background: {t.accent_soft}; color: {t.accent_text}; }}
QTabWidget::pane {{ border: none; }}
QTabBar {{ background: {t.chrome}; }}
QTabBar::tab {{ background: transparent; color: {t.faint}; padding: 13px 15px 11px;
                font-size: 13px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {t.ink}; font-weight: 600; border-bottom: 2px solid {t.accent}; }}
QTreeView, QListWidget, QTableView, QTreeWidget {{ background: {t.surface};
    alternate-background-color: {t.bg}; border: 1px solid {t.line}; border-radius: 6px; }}
QTreeView#railView {{ background: {t.rail}; border: none; border-right: 1px solid {t.line};
                      border-radius: 0; }}
QHeaderView::section {{ background: {t.surface}; color: {t.faint}; border: none;
                        border-bottom: 1px solid {t.line}; padding: 9px 8px;
                        font-size: 10.5px; }}
QProgressBar {{ background: {t.track}; border: none; border-radius: 4px; max-height: 8px;
                text-align: center; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 4px; }}
QLineEdit, QComboBox, QSpinBox {{ background: {t.surface}; color: {t.ink};
    border: 1px solid {t.line}; border-radius: 6px; padding: 5px 8px; }}
QStatusBar {{ background: {t.chrome}; color: {t.muted}; }}
QToolTip {{ background: {t.surface}; color: {t.ink}; border: 1px solid {t.line}; }}
QWidget#firstRunStep {{ background: {t.surface}; border: 1px solid {t.line}; border-radius: 9px; }}
QLabel#stepBadge {{ background: {t.accent_soft}; color: {t.accent_text}; border-radius: 10px; }}
"""


def apply_dark_titlebar(window, dark: bool) -> None:
    """DWMWA_USE_IMMERSIVE_DARK_MODE(20). Cosmetic; failures are swallowed."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        value = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(window.winId()), 20, ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception:
        pass


def apply_theme(app, t: Theme) -> None:
    global _current
    _current = t
    app.setStyleSheet(qss(t))
    app.setPalette(palette(t))
    notifier.changed.emit(t)
