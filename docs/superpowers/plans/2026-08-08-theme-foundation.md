# Theme Foundation + Honest Chrome (Plan A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Token-driven light/dark theming for the whole GUI (follows Windows live), plus the honest-chrome recommendations: rebuilt toolbar with a status pill, service-down disabling, restyled rail, left-elided paths, mono numerics, and the Files header count.

**Architecture:** One new module `gui/theme.py` holds the entire token table (from `docs/design/cloud-courier-theming/DESIGN_TOKENS.md`), produces the app-wide QSS and QPalette, and notifies custom painters on change. Every other task consumes tokens through `theme.current()` — after this plan, no hex color exists in `gui/` outside `theme.py`. Chrome changes ride on the existing handlers in `main_window.py`; no service/API contract changes anywhere.

**Tech Stack:** Python 3.12, PySide6 ≥ 6.7 (Qt 6.5+ `colorScheme()` API is available — do NOT add a winreg fallback), pytest + pytest-qt.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-08-gui-theme-and-refresh-design.md`. Appearance source of truth: `docs/design/cloud-courier-theming/{README,DESIGN_TOKENS,RECOMMENDATIONS}.md`. Codebase wins for behavior, handoff wins for appearance.
- Red is reserved for failure. No fifth color family. No between-poll animation.
- Theme setting persists in `QSettings("MML", "Cloud Courier")`, key `"theme"`, values `"system" | "light" | "dark"`, default `"system"` — NEVER in the service's `settings.json`, and NEVER in `SettingsDialog.build_payload()`.
- Exact copy strings (verbatim, including punctuation):
  - Pill ok: `Service running — transfers continue if you close this window`
  - Pill down: `Service stopped — nothing is moving`
  - Pill no-connections: `Service running — no connection set up yet`
  - Rail stalled override: `Stalled — service stopped`
  - `BANNER_TEXT` in `main_window.py` is unchanged.
- Paths elide LEFT (`Qt.ElideLeft`) with the full path as tooltip. Numerics/paths use `theme.mono_font()` (Cascadia Mono, fallback Consolas). Rail is 262px wide, fixed.
- Suite gate per task: FULL suite green (`.venv\Scripts\python -m pytest` from the worktree root). This plan adds tests, so totals grow — record the new totals in each task's report; never estimate. Known host quirk: if the `-q` summary line is missing, rerun with `-v` and read the last lines. Known flake: `tests/core/test_errors.py::test_a_real_invalid_grant_refresh_is_credential_and_pauses` may skip on a loopback blip (by design since 779c70e); `N passed, 13 skipped` and `N passed, 14 skipped` are both acceptable skip counts, nothing else is.
- GUI tests use ephemeral ports/temp dirs (`MMLCC_DATA_DIR`), never the live install (port 47821, `%ProgramData%`).
- SDD discipline: every dispatch `cd`s into the worktree FIRST; before each commit verify `git rev-parse --show-toplevel` is the worktree root AND `git log -1 --format=%h` is the expected parent; one commit per task; never amend; never bare `git stash`.
- pytest-qt is in the dev extras; use the `qtbot`/`qapp` fixtures — never instantiate `QApplication` manually in tests.

## File Structure

- Create: `src/mml_cloud_courier/gui/theme.py` (tokens, resolve, QSS, palette, apply, notifier, mono font, DWM)
- Create: `src/mml_cloud_courier/gui/status_pill.py` (the toolbar pill widget)
- Create: `src/mml_cloud_courier/gui/rail_delegate.py` (two-line rail painting)
- Modify: `gui/__main__.py` (apply theme at startup, live switching), `gui/main_window.py` (toolbar rebuild, banner, honesty, rail width/delegate, Files total), `gui/jobs_model.py` (roles + pure text helpers), `gui/job_tabs.py` (elision, mono, Files header row), `gui/files_model.py` (tooltip role), `gui/settings_dialog.py` (Appearance row), `gui/icons.py` (dots from tokens)
- Tests: `tests/gui/test_theme.py`, `tests/gui/test_status_pill.py`, plus edits to `tests/gui/{test_main_window_smoke,test_jobs_model,test_job_tabs,test_files_model,test_settings_dialog}.py`

## Setup (main session, before Task 1)

- [ ] EnterWorktree (branch `theme-foundation`); `py -3.12 -m venv .venv`; `.venv\Scripts\python -m pip install -e ".[dev]"`; copy `tools\fake-gcs-server.exe` from the main repo into `<worktree>\tools\`; run the full suite and record the baseline (expected today: `527 passed, 13 skipped` — 526 pre-existing + 1 added by 779c70e is already counted; trust the recorded run, not this parenthetical, if they differ).

---

### Task 1: Theme tokens, resolution, persistence

**Files:**
- Create: `src/mml_cloud_courier/gui/theme.py`
- Test: `tests/gui/test_theme.py`

**Interfaces:**
- Produces: `Theme` (frozen dataclass, fields exactly as below), `LIGHT: Theme`, `DARK: Theme`, `theme_setting() -> str`, `set_theme_setting(value: str) -> None`, `resolve(setting: str) -> Theme`. Later tasks rely on these exact names.

- [ ] **Step 1: Write the failing tests**

```python
# tests/gui/test_theme.py
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
```

- [ ] **Step 2: Run to verify failure** — `.venv\Scripts\python -m pytest tests/gui/test_theme.py -v`. Expected: import error (`theme` doesn't exist).

- [ ] **Step 3: Implement `theme.py` (this task's slice)**

```python
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
```

- [ ] **Step 4: Run to verify pass** — same command. Expected: all green.
- [ ] **Step 5: Full suite**, then **commit**: `git add src/mml_cloud_courier/gui/theme.py tests/gui/test_theme.py && git commit -m "feat: theme tokens, resolution, persistence"`

---

### Task 2: QSS, palette, apply, notifier, mono font, dark title bar

**Files:**
- Modify: `src/mml_cloud_courier/gui/theme.py` (append)
- Test: `tests/gui/test_theme.py` (append)

**Interfaces:**
- Consumes: `Theme`, `LIGHT`, `DARK` (Task 1).
- Produces: `qss(t: Theme) -> str`, `palette(t: Theme) -> QPalette`, `apply_theme(app, t: Theme) -> None`, `current() -> Theme`, `notifier.changed` (Signal emitting the new `Theme`), `mono_font(size_pt: float, weight: int = 400) -> QFont`, `apply_dark_titlebar(window, dark: bool) -> None`. Object names the QSS binds that later tasks must use: `primaryButton`, `segmentWell`, `segmentButton`, `textButton`, `statusPill`, `pillDot`, `serviceBanner`, `filesHeader`.

- [ ] **Step 1: Write the failing tests (append to `tests/gui/test_theme.py`)**

```python
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
```

- [ ] **Step 2: Run to verify failure** — attribute errors for `apply_theme` etc.

- [ ] **Step 3: Implement (append to `theme.py`)**

```python
import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QFont, QPalette


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
```

- [ ] **Step 4: Run `tests/gui/test_theme.py -v`** — all green.
- [ ] **Step 5: Full suite**, then **commit**: `git commit -m "feat: theme QSS, palette, apply/notify, mono font, dark titlebar"` (add both files).

---

### Task 3: Startup wiring, live switching, Appearance setting

**Files:**
- Modify: `src/mml_cloud_courier/gui/__main__.py`, `src/mml_cloud_courier/gui/settings_dialog.py`
- Test: `tests/gui/test_settings_dialog.py` (append)

**Interfaces:**
- Consumes: `theme.apply_theme`, `theme.resolve`, `theme.theme_setting`, `theme.set_theme_setting`, `theme.apply_dark_titlebar`, `theme.notifier`.
- Produces: `SettingsDialog.theme_combo` (QComboBox with userData `"system"|"light"|"dark"`). `build_payload()` MUST NOT change.

- [ ] **Step 1: Failing tests (append to `tests/gui/test_settings_dialog.py`; reuse that file's existing fake-client fixture pattern)**

```python
def test_theme_combo_lists_three_options_and_defaults_to_setting(dialog):
    datas = [dialog.theme_combo.itemData(i) for i in range(dialog.theme_combo.count())]
    assert datas == ["system", "light", "dark"]


def test_theme_change_persists_and_applies(dialog, monkeypatch):
    from mml_cloud_courier.gui import theme
    applied = []
    monkeypatch.setattr(theme, "set_theme_setting", lambda v: applied.append(("set", v)))
    monkeypatch.setattr(
        "mml_cloud_courier.gui.settings_dialog.apply_theme_for_setting",
        lambda v: applied.append(("apply", v)),
    )
    dialog.theme_combo.setCurrentIndex(2)   # dark
    assert ("set", "dark") in applied and ("apply", "dark") in applied


def test_build_payload_never_contains_theme(dialog):
    assert "theme" not in dialog.build_payload()
```

- [ ] **Step 2: Run to verify failure** (`tests/gui/test_settings_dialog.py -v`).

- [ ] **Step 3: Implement.** In `settings_dialog.py` add at module level:

```python
from PySide6.QtWidgets import QApplication, QComboBox

from mml_cloud_courier.gui import theme


def apply_theme_for_setting(value: str) -> None:
    app = QApplication.instance()
    if app is not None:
        theme.apply_theme(app, theme.resolve(value))
```

In `SettingsDialog.__init__`, after the auto-resume row (`form.addRow("Resume interrupted jobs on startup:", ...)`, line ~111):

```python
        self.theme_combo = QComboBox()
        for label, value in (("System", "system"), ("Light", "light"), ("Dark", "dark")):
            self.theme_combo.addItem(label, value)
        self.theme_combo.setCurrentIndex(
            max(0, ["system", "light", "dark"].index(theme.theme_setting()))
        )
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        form.addRow("Appearance:", self.theme_combo)
```

and the slot (theme is a CLIENT preference — applied immediately, never saved to the service):

```python
    def _on_theme_changed(self, index: int) -> None:
        value = self.theme_combo.itemData(index)
        theme.set_theme_setting(value)
        apply_theme_for_setting(value)
```

In `gui/__main__.py`, after `app.setWindowIcon(app_icon())` insert:

```python
    from mml_cloud_courier.gui import theme

    theme.apply_theme(app, theme.resolve(theme.theme_setting()))

    def _on_scheme_changed(_scheme):
        if theme.theme_setting() == "system":
            theme.apply_theme(app, theme.resolve("system"))

    app.styleHints().colorSchemeChanged.connect(_on_scheme_changed)
```

and after `window = MainWindow(...)`: `theme.apply_dark_titlebar(window, theme.current().dark)` plus re-apply on change: `theme.notifier.changed.connect(lambda t: theme.apply_dark_titlebar(window, t.dark))`.

- [ ] **Step 4: Run the file's tests** — green. **Step 5: Full suite**, then **commit**: `git commit -m "feat: theme applied at startup, follows Windows live, Appearance setting"`.

---

### Task 4: Toolbar rebuild + status pill

**Files:**
- Create: `src/mml_cloud_courier/gui/status_pill.py`
- Modify: `src/mml_cloud_courier/gui/main_window.py:185-222` (`_build_toolbar`, `_update_action_states`)
- Test: `tests/gui/test_status_pill.py`; adjust `tests/gui/test_main_window_smoke.py` where it references `*_action` attributes

**Interfaces:**
- Consumes: theme object names from Task 2 (`primaryButton`, `segmentWell`, `segmentButton`, `textButton`, `statusPill`, `pillLabel`, `pillDot`).
- Produces: `StatusPill` with `set_state(state: str) -> None` (`"ok" | "down" | "noconn"`) and `.state` property; `MainWindow.new_transfer_button`, `.pause_button`, `.resume_button`, `.cancel_button`, `.pill` (replacing the four `QAction`s — `tests/gui/test_main_window_smoke.py` and any other test touching `pause_action` etc. must be updated to the `_button` names in THIS task). `_update_action_states()` keeps its name and gains the service gate (Task 5 sets `_service_up`).

- [ ] **Step 1: Failing tests**

```python
# tests/gui/test_status_pill.py
from mml_cloud_courier.gui.status_pill import PILL_TEXT, StatusPill


def test_pill_states_and_text(qtbot):
    pill = StatusPill()
    qtbot.addWidget(pill)
    assert pill.state == "ok"
    assert pill.label.text() == "Service running — transfers continue if you close this window"
    pill.set_state("down")
    assert pill.property("pillState") == "down"
    assert pill.label.text() == "Service stopped — nothing is moving"
    pill.set_state("noconn")
    assert pill.label.text() == "Service running — no connection set up yet"


def test_pill_rejects_unknown_state(qtbot):
    pill = StatusPill()
    qtbot.addWidget(pill)
    pill.set_state("bogus")
    assert pill.state == "ok"        # unchanged
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement `status_pill.py`**

```python
"""The toolbar's persistent truth pill (Recommendation 2): one element that
always answers whether closing the window is safe."""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

PILL_TEXT = {
    "ok": "Service running — transfers continue if you close this window",
    "down": "Service stopped — nothing is moving",
    "noconn": "Service running — no connection set up yet",
}


class StatusPill(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusPill")
        self.dot = QFrame()
        self.dot.setObjectName("pillDot")
        self.dot.setFixedSize(6, 6)
        self.label = QLabel()
        self.label.setObjectName("pillLabel")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 5, 11, 5)
        layout.setSpacing(6)
        layout.addWidget(self.dot)
        layout.addWidget(self.label)
        self._state = "ok"
        self.set_state("ok")

    @property
    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        if state not in PILL_TEXT:
            return
        self._state = state
        self.label.setText(PILL_TEXT[state])
        # "noconn" keeps the ok (accent) tones; only "down" flips to danger.
        self.setProperty("pillState", state)
        self.style().unpolish(self)
        self.style().polish(self)
```

Rewrite `_build_toolbar` (replace lines 185-216) — same handlers, widget-based chrome in the spec's order:

```python
    def _build_toolbar(self) -> None:
        from PySide6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy

        from mml_cloud_courier.gui.status_pill import StatusPill

        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)

        self.new_transfer_button = QPushButton("New transfer")
        self.new_transfer_button.setObjectName("primaryButton")
        self.new_transfer_button.clicked.connect(self._open_new_transfer)
        toolbar.addWidget(self.new_transfer_button)

        well = QWidget()
        well.setObjectName("segmentWell")
        well_layout = QHBoxLayout(well)
        well_layout.setContentsMargins(2, 2, 2, 2)
        well_layout.setSpacing(0)
        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.cancel_button = QPushButton("Cancel")
        for button, slot in ((self.pause_button, self._on_pause),
                             (self.resume_button, self._on_resume),
                             (self.cancel_button, self._on_cancel)):
            button.setObjectName("segmentButton")
            button.clicked.connect(slot)
            well_layout.addWidget(button)
        toolbar.addWidget(well)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        self.pill = StatusPill()
        toolbar.addWidget(self.pill)

        for text, slot in (("Connections", self._open_connections),
                           ("Settings", self._open_settings)):
            button = QPushButton(text)
            button.setObjectName("textButton")
            button.clicked.connect(slot)
            toolbar.addWidget(button)

        self._service_up = True
        self._update_action_states()
```

and `_update_action_states` (replace lines 218-222; the `_service_up` gate is used by Task 5):

```python
    def _update_action_states(self) -> None:
        status = self._selected_status
        up = self._service_up
        self.new_transfer_button.setEnabled(up)
        self.pause_button.setEnabled(up and status in _PAUSABLE)
        self.resume_button.setEnabled(up and status in _RESUMABLE)
        self.cancel_button.setEnabled(up and status in _CANCELLABLE)
```

Update every test referencing `pause_action`/`resume_action`/`cancel_action`/`new_transfer_action` to the `_button` names (grep `tests/gui` for `_action` to find them; `test_main_window_smoke.py` is the known site). Pill wiring to poller/profiles happens in Task 5 — this task only constructs it.

- [ ] **Step 4: Run `tests/gui/test_status_pill.py tests/gui/test_main_window_smoke.py -v`** — green. **Step 5: Full suite**, **commit**: `git commit -m "feat: toolbar rebuilt - primary button, segmented transport, status pill"`.

---

### Task 5: Service-down honesty + tokenized banner + pill wiring

**Files:**
- Modify: `src/mml_cloud_courier/gui/main_window.py` (`_build_full_ui` banner block lines 108-125, `_on_jobs` line 255, `_on_down` line 271, plus ConnectionsDialog close hook), `src/mml_cloud_courier/gui/jobs_model.py` (stalled override helper)
- Test: `tests/gui/test_main_window_smoke.py` (append)

**Interfaces:**
- Consumes: `StatusPill.set_state`, `_update_action_states()` with `_service_up` (Task 4).
- Produces: `jobs_model.sync_rail(model, jobs, service_up: bool = True)` — new keyword; when `False`, jobs whose status is in `("running", "scanning")` render their second line as `Stalled — service stopped` (pure display; `STATUS_ROLE` keeps the real status). `MainWindow._service_up: bool`.

- [ ] **Step 1: Failing tests (append to `tests/gui/test_main_window_smoke.py`, reusing its existing window fixture/fake-client pattern)**

```python
def test_service_down_disables_chrome_and_flips_pill(window):
    window._on_down("boom")
    assert window.pill.state == "down"
    assert not window.new_transfer_button.isEnabled()
    assert not window.pause_button.isEnabled()
    assert not window.resume_button.isEnabled()
    assert not window.cancel_button.isEnabled()
    assert window.banner.isVisibleTo(window)
    window._on_jobs([])
    assert window.pill.state in ("ok", "noconn")
    assert window.new_transfer_button.isEnabled()


def test_banner_carries_no_inline_hex(window):
    assert window.banner.styleSheet() == ""


def test_rail_shows_stalled_override_when_down():
    from mml_cloud_courier.gui.jobs_model import build_rail_model, sync_rail
    model = build_rail_model()
    jobs = [{"id": 7, "name": "leg3", "status": "running"}]
    sync_rail(model, jobs, service_up=False)
    running_group = model.item(1)          # RAIL_GROUPS order: needs_attention, running, ...
    assert "Stalled — service stopped" in running_group.child(0).text()
    sync_rail(model, jobs, service_up=True)
    assert "Stalled — service stopped" not in model.item(1).child(0).text()
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.**

`jobs_model.py`: give `_job_item` and `sync_rail` the flag —

```python
def _job_item(job: dict, service_up: bool = True) -> QStandardItem:
    if not service_up and job["status"] in ("running", "scanning"):
        label = "Stalled — service stopped"
    else:
        label = STATUS_LABELS.get(job["status"], job["status"])
    text = f"#{job['id']} {job['name']} — {label}"
    if job["status"] == "pending" and job.get("scheduled_start_at"):
        text += f" — starts {human_schedule(job['scheduled_start_at'])}"
    item = QStandardItem(text)
    item.setData(job["id"], JOB_ID_ROLE)
    item.setData(job["status"], STATUS_ROLE)
    item.setEditable(False)
    return item


def sync_rail(model, jobs, service_up: bool = True) -> None:
    ...  # unchanged except: parent.appendRow(_job_item(job, service_up))
```

`main_window.py`:
- Delete the `setStyleSheet` call on the banner (lines 113-116) and its comment — the banner is styled by `theme.qss()` via `#serviceBanner`. Keep the objectName.
- `_on_down`: set `self._service_up = False`, `self.pill.set_state("down")`, `self._update_action_states()`, keep `self.banner.show()`, and re-sync the rail with the override: `sync_rail(self.rail_model, self._last_jobs, service_up=False)` — store `self._last_jobs: list[dict] = []`, assigned at the top of `_on_jobs`.
- `_on_jobs`: first lines become

```python
        self._last_jobs = jobs
        self._service_up = True
        self.banner.hide()
        self.pill.set_state("noconn" if self._no_connections else "ok")
        self._update_action_states()
```

with `self._no_connections = False` initialized in `_build_full_ui`, refreshed by `call_async(self.client.profiles, parent=self, on_done=self._on_profiles)` at the end of `_build_full_ui` and again when `_open_connections`'s dialog closes (`ConnectionsDialog(...).exec()` returns → re-fetch):

```python
    def _on_profiles(self, profiles: list) -> None:
        self._no_connections = not profiles
        if self._service_up:
            self.pill.set_state("noconn" if self._no_connections else "ok")
```

(Confirm the client method name for listing profiles by grepping `service_client.py` / `api_client` for the profiles call — use the exact existing method; do NOT invent an endpoint.) Pass `service_up=self._service_up` at the existing `sync_rail` call site in `_on_jobs`.

- [ ] **Step 4: Run the smoke tests** — green. **Step 5: Full suite**, **commit**: `git commit -m "feat: honest chrome when the service is down; banner colors from tokens"`.

---

### Task 6: Rail restyle — two-line delegate, colored groups, fixed width

**Files:**
- Create: `src/mml_cloud_courier/gui/rail_delegate.py`
- Modify: `src/mml_cloud_courier/gui/jobs_model.py` (text-role split + helpers), `src/mml_cloud_courier/gui/main_window.py` (`_build_full_ui` rail block lines 127-136, splitter block 157-161)
- Test: `tests/gui/test_jobs_model.py` (append)

**Interfaces:**
- Consumes: `theme.current()`, `theme.notifier`, `theme.mono_font`.
- Produces: `jobs_model.rail_row_lines(job: dict, service_up: bool = True) -> tuple[str, str]` returning (`"#7 leg3"`, `"Running"`) — the single source for both lines; `_job_item` stores line1 as display text, line2 under `SECOND_LINE_ROLE = Qt.ItemDataRole.UserRole + 3`, and keeps `JOB_ID_ROLE`/`STATUS_ROLE` unchanged; `GROUP_DOT_TOKENS = {"needs_attention": "danger", "running": "accent_2", "queued": "skip", "completed": "accent"}` in `rail_delegate.py`; `RailDelegate(QStyledItemDelegate)` painting group headers (10.5px mono 600 uppercase, token-colored per README: danger / accent_text / faint, child count, 1px rule) and two-line job rows (6px status dot, 12.5px/500 name, 11px faint second line, `rail_selected` bg + 2px accent left bar when selected).

- [ ] **Step 1: Failing tests (append to `tests/gui/test_jobs_model.py`)**

```python
def test_rail_row_lines_split_name_and_status():
    from mml_cloud_courier.gui.jobs_model import rail_row_lines
    job = {"id": 121, "name": "IceSeal_Survey_2026_Leg3", "status": "running"}
    line1, line2 = rail_row_lines(job)
    assert line1 == "#121 IceSeal_Survey_2026_Leg3"
    assert line2 == "Running"
    _, down = rail_row_lines(job, service_up=False)
    assert down == "Stalled — service stopped"


def test_rail_row_lines_keeps_schedule_suffix():
    from mml_cloud_courier.gui.jobs_model import rail_row_lines
    job = {"id": 5, "name": "n", "status": "pending",
           "scheduled_start_at": "2026-08-09T02:00:00+00:00"}
    _, line2 = rail_row_lines(job)
    assert line2.startswith("Queued — starts ")


def test_job_item_roles_carry_second_line():
    from mml_cloud_courier.gui.jobs_model import SECOND_LINE_ROLE, _job_item
    item = _job_item({"id": 7, "name": "leg3", "status": "running"})
    assert item.text() == "#7 leg3"
    assert item.data(SECOND_LINE_ROLE) == "Running"
```

Adjust the Task 5 test `test_rail_shows_stalled_override_when_down` in the same commit: the override now lives in `SECOND_LINE_ROLE` data, not `.text()` — assert `child(0).data(SECOND_LINE_ROLE) == "Stalled — service stopped"`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** `jobs_model.py`: add `SECOND_LINE_ROLE = Qt.ItemDataRole.UserRole + 3` and

```python
def rail_row_lines(job: dict, service_up: bool = True) -> tuple[str, str]:
    if not service_up and job["status"] in ("running", "scanning"):
        status = "Stalled — service stopped"
    else:
        status = STATUS_LABELS.get(job["status"], job["status"])
    if job["status"] == "pending" and job.get("scheduled_start_at"):
        status += f" — starts {human_schedule(job['scheduled_start_at'])}"
    return f"#{job['id']} {job['name']}", status


def _job_item(job: dict, service_up: bool = True) -> QStandardItem:
    line1, line2 = rail_row_lines(job, service_up)
    item = QStandardItem(line1)
    item.setData(job["id"], JOB_ID_ROLE)
    item.setData(job["status"], STATUS_ROLE)
    item.setData(line2, SECOND_LINE_ROLE)
    item.setEditable(False)
    return item
```

(`build_rail_model` stops calling `group_icon` — the delegate draws headers; `icons.group_icon` remains for Task 8 to retire or keep for the tray.) `rail_delegate.py`:

```python
"""Paints the rail: colored uppercase group headers with counts, and
two-line job rows with a status dot. All colors read theme.current() at
paint time — never cached."""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from mml_cloud_courier.gui import theme
from mml_cloud_courier.gui.jobs_model import (
    JOB_ID_ROLE, RAIL_GROUPS, SECOND_LINE_ROLE, STATUS_ROLE,
)

GROUP_DOT_TOKENS = {"needs_attention": "danger", "running": "accent_2",
                    "queued": "skip", "completed": "accent"}
_HEADER_TEXT_TOKENS = {"needs_attention": "danger", "running": "accent_text",
                       "queued": "faint", "completed": "faint"}
_STATUS_DOT_TOKENS = {"incomplete": "danger", "stalled": "warn", "paused": "warn",
                      "running": "accent_2", "scanning": "accent_2",
                      "pending": "skip", "complete": "accent", "cancelled": "skip"}


def _color(token: str) -> QColor:
    value = getattr(theme.current(), token)
    if value.startswith("rgba("):
        parts = value[5:-1].split(",")
        return QColor(int(parts[0]), int(parts[1]), int(parts[2]),
                      round(float(parts[3]) * 255))
    return QColor(value)


class RailDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:
        if index.data(JOB_ID_ROLE) is None:
            return QSize(option.rect.width(), 30)
        return QSize(option.rect.width(), 44)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        t = theme.current()
        if index.data(JOB_ID_ROLE) is None:
            group = RAIL_GROUPS[index.row()]
            font = theme.mono_font(8.0, 600)
            font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 109)
            painter.setFont(font)
            painter.setPen(_color(_HEADER_TEXT_TOKENS[group]))
            label = f"{index.data(Qt.ItemDataRole.DisplayRole).upper()}  {index.model().itemFromIndex(index).rowCount()}"
            painter.drawText(rect.adjusted(6, 11, -6, -6),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
        else:
            if option.state & QStyle.StateFlag.State_Selected:
                painter.fillRect(rect, _color("rail_selected"))
                painter.fillRect(QRect(rect.left(), rect.top(), 2, rect.height()),
                                 _color("accent"))
            dot = _STATUS_DOT_TOKENS.get(index.data(STATUS_ROLE), "danger")
            painter.setBrush(_color(dot))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(rect.left() + 10, rect.top() + 12, 6, 6)
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            painter.setPen(_color("ink" if selected else "muted"))
            name_font = painter.font()
            name_font.setPointSizeF(9.5)
            name_font.setWeight(QFont.Weight(500))
            painter.setFont(name_font)
            metrics = painter.fontMetrics()
            text_rect = rect.adjusted(25, 8, -8, 0)
            line1 = metrics.elidedText(index.data(Qt.ItemDataRole.DisplayRole),
                                       Qt.TextElideMode.ElideRight, text_rect.width())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, line1)
            painter.setPen(_color("faint"))
            small = painter.font()
            small.setPointSizeF(8.5)
            small.setWeight(QFont.Weight(400))
            painter.setFont(small)
            line2 = painter.fontMetrics().elidedText(
                index.data(SECOND_LINE_ROLE) or "", Qt.TextElideMode.ElideRight,
                text_rect.width())
            painter.drawText(text_rect.adjusted(0, 18, 0, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, line2)
        painter.restore()
```

`main_window.py` `_build_full_ui`: after creating `self.rail_view` — `self.rail_view.setObjectName("railView")`, `self.rail_view.setItemDelegate(RailDelegate(self.rail_view))`, `self.rail_view.setFixedWidth(262)`, and repaint on theme change: `theme.notifier.changed.connect(lambda _t: self.rail_view.viewport().update())`. The splitter keeps working with a fixed-width first pane.

- [ ] **Step 4: Run `tests/gui/test_jobs_model.py tests/gui/test_main_window_smoke.py -v`** — green. **Step 5: Full suite**, **commit**: `git commit -m "feat: rail delegate - colored groups, two-line rows, 262px"`.

---

### Task 7: Left elision + tooltips, mono numerics, Files header count

**Files:**
- Modify: `src/mml_cloud_courier/gui/job_tabs.py` (ProgressTab lines 71-87 fonts/elision, FilesTab lines 181-228), `src/mml_cloud_courier/gui/files_model.py` (tooltip role), `src/mml_cloud_courier/gui/main_window.py` (`_render_job` passes the files total)
- Test: `tests/gui/test_files_model.py`, `tests/gui/test_job_tabs.py` (append)

**Interfaces:**
- Consumes: `theme.mono_font`.
- Produces: `FileTableModel.data` answers `Qt.ItemDataRole.ToolTipRole` for column 0 with the full `relative_path`; `FilesTab.set_total(total: int | None)` and `FilesTab.header_label: QLabel` (objectName `filesHeader`) reading `{total:,} files · showing 1–{loaded:,}` (or `showing 1–{loaded:,}` while a state filter is active or total is None); `MainWindow._render_job` calls `self.files_tab.set_total(job.get("planned_files"))`.

- [ ] **Step 1: Failing tests**

```python
# append to tests/gui/test_files_model.py
def test_path_column_tooltip_is_full_path():
    from PySide6.QtCore import Qt
    model = FileTableModel(lambda **kw: [])
    model._rows = [{"relative_path": "leg3/imagery/IMG_1147.tif",
                    "size_bytes": 5, "state": "verified"}]
    index = model.index(0, 0)
    assert model.data(index, Qt.ItemDataRole.ToolTipRole) == "leg3/imagery/IMG_1147.tif"
    assert model.data(model.index(0, 1), Qt.ItemDataRole.ToolTipRole) is None
```

```python
# append to tests/gui/test_job_tabs.py
def test_files_header_counts(qtbot):
    tab = FilesTab()
    qtbot.addWidget(tab)
    tab.attach(lambda **kw: [
        {"relative_path": f"f{i}", "size_bytes": 1, "state": "verified"}
        for i in range(3)
    ] if kw.get("offset", 0) == 0 else [])
    tab.set_total(14208)
    assert tab.header_label.text() == "14,208 files · showing 1–3"
    tab.state_combo.setCurrentIndex(1)          # any state filter
    assert tab.header_label.text() == "showing 1–3"


def test_files_table_elides_left(qtbot):
    from PySide6.QtCore import Qt
    tab = FilesTab()
    qtbot.addWidget(tab)
    assert tab.table.textElideMode() == Qt.TextElideMode.ElideLeft


def test_inflight_and_events_lists_elide_left(qtbot):
    from PySide6.QtCore import Qt
    tab = ProgressTab()
    qtbot.addWidget(tab)
    assert tab.inflight_list.textElideMode() == Qt.TextElideMode.ElideLeft
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.**

`files_model.py` `data()` — replace the role gate:

```python
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.ToolTipRole and index.column() == 0:
            return self._rows[index.row()]["relative_path"]
        if role != Qt.ItemDataRole.DisplayRole:
            return None
```

`job_tabs.py` FilesTab: build a header row (`QHBoxLayout`: `state_combo`, stretch, `self.header_label = QLabel("")` with `setObjectName("filesHeader")` and `theme.mono_font(8.5)`); `self.table.setTextElideMode(Qt.TextElideMode.ElideLeft)`; `self.table.setAlternatingRowColors(True)`; track totals:

```python
    def set_total(self, total: int | None) -> None:
        self._total = total
        self._update_header()

    def _update_header(self) -> None:
        loaded = self._model.rowCount() if self._model is not None else 0
        filtered = self.state_combo.currentData() is not None
        if self._total is None or filtered:
            self.header_label.setText(f"showing 1–{loaded:,}")
        else:
            self.header_label.setText(f"{self._total:,} files · showing 1–{loaded:,}")
```

with `self._total: int | None = None` in `__init__`, `_update_header()` called from `refresh()` and from a `rowsInserted`/`modelReset` connection made in `attach()`.

Files columns (DESIGN_TOKENS.md "not negotiable" table — QSS can't uppercase, so the model returns uppercase headers directly): in `files_model.py` set `HEADERS = ("PATH", "SIZE", "STATE", "DETAIL")` (update any test asserting the old header strings — grep `tests/gui` for `"File"`/`"Problem"` header assertions), and in `data()` answer `Qt.ItemDataRole.TextAlignmentRole` for column 1 with `int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)`. In FilesTab `__init__` after `setModel` is not possible (model attaches later), so in `attach()` after `self.table.setModel(...)`:

```python
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)   # PATH
        header.resizeSection(1, 88)                                      # SIZE
        header.resizeSection(2, 204)   # STATE — hard requirement: "Excluded after
                                       # repeated failures" must render in full
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)   # DETAIL
```

(`from PySide6.QtWidgets import QHeaderView` joins the imports.) Add a test asserting the 204px section: `assert tab.table.horizontalHeader().sectionSize(2) == 204` inside `test_files_header_counts` after `attach`.

ProgressTab: `self.inflight_list.setTextElideMode(Qt.TextElideMode.ElideLeft)`, same for `events_list`; `self.counts_label.setFont(theme.mono_font(9))`, `self.throughput_label.setFont(theme.mono_font(9, 500))`, `self.inflight_list.setFont(theme.mono_font(8.5))`, `self.events_list.setFont(theme.mono_font(8.5))`. `main_window.py` `_render_job`: add `self.files_tab.set_total(job.get("planned_files"))`.

- [ ] **Step 4: Run both test files** — green. **Step 5: Full suite**, **commit**: `git commit -m "feat: left elision + tooltips, mono numerics, files header count"`.

---

### Task 8: Retire remaining hard-coded colors + acceptance sweep

**Files:**
- Modify: `src/mml_cloud_courier/gui/icons.py` (dots/app-icon colors from tokens), `src/mml_cloud_courier/gui/job_tabs.py` `_verdict_style` (line ~423 — read it first; restyle from tokens)
- Test: `tests/gui/test_theme.py` (append the acceptance test)

**Interfaces:**
- Consumes: `theme.current()`, `GROUP_DOT_TOKENS` (Task 6).
- Produces: a `gui/`-wide invariant later plans rely on: hex colors only in `theme.py`.

- [ ] **Step 1: Failing acceptance test (append to `tests/gui/test_theme.py`)**

```python
def test_no_hex_colors_outside_theme_py():
    import pathlib, re
    gui_dir = pathlib.Path(theme.__file__).parent
    offenders = []
    for path in sorted(gui_dir.glob("*.py")):
        if path.name == "theme.py":
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"#[0-9a-fA-F]{6}\b", line):
                offenders.append(f"{path.name}:{line_number}")
    assert offenders == []
```

- [ ] **Step 2: Run to verify failure** — it must list `icons.py` (GROUP_COLORS, app_icon) and any other current offender the test surfaces (fix every one it names; `_verdict_style` in `job_tabs.py` is expected).

- [ ] **Step 3: Implement.** `icons.py`: delete `GROUP_COLORS`; `group_icon(group)` maps through the delegate's token names —

```python
from mml_cloud_courier.gui import theme

_GROUP_TOKENS = {"needs_attention": "danger", "running": "accent_2",
                 "queued": "skip", "completed": "accent"}


def _token_color(token: str) -> str:
    return getattr(theme.current(), token)


def group_icon(group: str) -> QIcon:
    return _circle(_token_color(_GROUP_TOKENS.get(group, "skip")))


def app_icon() -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(_token_color("accent")))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 28, 28, 6, 6)
    painter.setBrush(QColor("white"))
    # an up-arrow: the product moves data to the cloud
    painter.drawPolygon([QPoint(16, 7), QPoint(25, 17), QPoint(7, 17)])
    painter.drawRect(13, 17, 6, 8)
    painter.end()
    return QIcon(pixmap)
```

(`_circle`/`app_icon` accept only hex tokens here — every token used (`danger`, `accent_2`, `skip`, `accent`) is hex in both modes, so no rgba parsing is needed; add a comment saying exactly that.) `_verdict_style` in `job_tabs.py`: rewrite to return token-derived colors via `theme.current()` (read the function first; keep its status→tone mapping — complete→accent, incomplete/cancelled→danger, else muted — expressed in tokens; danger stays failure-only).

- [ ] **Step 4: Run `tests/gui/test_theme.py -v`** — green. **Step 5: FULL suite + record final totals**, **commit**: `git commit -m "feat: last hard-coded colors retired; gui hex-free outside theme.py"`.

---

## Manual smoke check (main session, after Task 8, before merge)

- [ ] `mmlcc-gui` from the worktree venv (`MMLCC_SERVICE_URL`/`MMLCC_DATA_DIR` pointed at a scratch service or the live one READ-ONLY — do not mutate live jobs): window renders in both themes (flip Windows dark mode live and via Settings→Appearance), title bar follows, pill reads correctly, banner appears with the service stopped, rail shows two-line rows.

## Done when

- Full suite green (new totals recorded; only the known OAuth-loopback skip may vary by 1).
- `git grep -nE "#[0-9a-fA-F]{6}" -- src/mml_cloud_courier/gui ':!src/mml_cloud_courier/gui/theme.py'` → empty.
- All five Task-level behaviors demonstrably pass their tests: theme resolution/persistence, live apply, pill states, service-down disabling, elision/tooltips/header count.
- Manual smoke check performed in both themes.
