# Connections Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the connections manager as status cards and the New-connection dialog as a three-step stepper per the committed handoff (`docs/design/cloud-courier-connections/README.md`), plus a rail profile-filter and two helper labels in the transfer dialog.

**Architecture:** `gui/connection_dialogs.py` keeps its public surface (four contractual constants, `load_key_file`, payload builders, `ConnectionsDialog`, `NewConnectionDialog`) and orchestrates all client I/O; a new flat `gui/connection_widgets.py` holds client-agnostic visual primitives; all new styling is added to `theme.qss()` (applied at QApplication level, so live re-theming is free); custom-painted widgets repaint via `theme.notifier.changed` bound-method subscriptions.

**Tech Stack:** Python 3.12, PySide6 (Qt Widgets, QSS), pytest + pytest-qt (offscreen), FastAPI service untouched.

**Authoritative documents (read them before your task):**
- Spec: `docs/superpowers/specs/2026-08-09-connections-redesign-design.md`
- Handoff (appearance authority): `docs/design/cloud-courier-connections/README.md`
- Ranked recommendations: `docs/design/cloud-courier-connections/RECOMMENDATIONS.md`

## Global Constraints

- The four constants in `gui/connection_dialogs.py` — `COPY_CHOOSE_KEY`, `COPY_CHOOSE_SIGNIN`, `COPY_DELETE_ORIGINAL`, `COPY_SERVICE_FIRST` — stay **byte-for-byte verbatim**. `test_copy_follows_the_spec_and_the_gate_findings` must never be edited.
- Profiles API fixed: only `client.health()`, `list_profiles()`, `create_profile(payload)`, `check_profile(id)`, `delete_profile(id)`. No new endpoints, no payload changes. GUI creates only `service_account_key` and `oauth_user`.
- Health gate: nothing credential-shaped (file browse, browser sign-in) reachable until `/health` answers. Both credential buttons disabled synchronously at construction.
- All colors from `Theme` tokens; `tests/gui/test_theme.py::test_no_hex_colors_outside_theme_py` must stay green — **zero 6-digit hex literals in any `gui/*.py` except `theme.py`**. New modules sit flat in `gui/` (the hex test's glob is non-recursive). Red = failure only; success uses `accent`.
- Pinned attribute contract: `ConnectionsDialog.new_button` (objectName `primaryButton`), `.close_button`; `NewConnectionDialog.key_button` (objectName `primaryButton`), `.signin_button`, `.status_label` (carries `COPY_SERVICE_FIRST` after a failed health check), `.created` Signal(dict). `NewConnectionDialog(client, parent=None)` and `ConnectionsDialog(client, parent=None)` signatures unchanged.
- Qt gotchas: custom QWidget subclasses need `WA_StyledBackground` for QSS backgrounds; `setDefault` on the footer primary and `setAutoDefault(False)` on every other button in a QDialog; `QTreeView.setCurrentIndex` auto-expands ancestors.
- Tests: never the live install (port 47821, `%ProgramData%`); QSettings isolation is autouse in `tests/gui/conftest.py`. Run targeted tests with `python -m pytest <file> -q -o addopts=`; full suite with `python -m pytest -o addopts= -q` (the bare `-q` run drops its final summary line on this host — never estimate counts).
- Suite baseline on master: **636 passed, 13 skipped** (the invalid_grant loopback test may add one skip).
- SDD conventions: every dispatch `cd`s into the worktree as its FIRST command and re-verifies `git rev-parse --show-toplevel` plus the expected parent commit before each commit; one commit per task; never amend; never bare `git stash`.

---

### Task 0: Worktree setup (no commit)

Executed once by the orchestrator before dispatching Task 1.

- [ ] **Step 1: Push master to origin** (worktrees branch from origin/master)

```bash
cd C:/Users/pmaho/Documents/VibeCode/mml_cloud_transfer
git push origin master
```

- [ ] **Step 2: Create the worktree** via EnterWorktree (branch name suggestion: `connections-redesign`).

- [ ] **Step 3: Provision the worktree venv** (PowerShell, from the worktree root):

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item C:\Users\pmaho\Documents\VibeCode\mml_cloud_transfer\tools\fake-gcs-server.exe tools\fake-gcs-server.exe
```

- [ ] **Step 4: Baseline check** — `.venv\Scripts\python -m pytest -o addopts= -q` and record the counts (expect 636 passed, 13 skipped, possibly +1 skip).

---

### Task 1: Error/time formatting helpers in `gui/format.py`

**Files:**
- Modify: `src/mml_cloud_courier/gui/format.py`
- Test: `tests/gui/test_format.py` (exists; append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `split_service_error(message: str) -> tuple[int | None, str]`; `human_ago(iso: str | None) -> str`; `iso_age_days(iso: str | None) -> float | None`. Tasks 3, 5, 6 import these from `mml_cloud_courier.gui.format`.

- [ ] **Step 1: Write the failing tests** (append to `tests/gui/test_format.py`):

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python -m pytest tests/gui/test_format.py -q -o addopts=`
Expected: FAIL with ImportError (`split_service_error` not defined).

- [ ] **Step 3: Implement** (append to `src/mml_cloud_courier/gui/format.py`; add `import re` and `from datetime import datetime, timezone` at the top):

```python
_SERVICE_ERROR_RE = re.compile(r"^(\d{3}): (.*)$", re.DOTALL)


def split_service_error(message: str) -> tuple[int | None, str]:
    """call_async delivers ServiceError as str(exc) == '409: detail'.
    Return (status_code, detail), or (None, message) for anything else."""
    match = _SERVICE_ERROR_RE.match(message)
    if match is None:
        return None, message
    return int(match.group(1)), match.group(2)


def _parse_iso(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if then.tzinfo is None:            # sqlite CURRENT_TIMESTAMP is naive UTC
        then = then.replace(tzinfo=timezone.utc)
    return then


def iso_age_days(iso: str | None) -> float | None:
    then = _parse_iso(iso)
    if then is None:
        return None
    return (datetime.now(timezone.utc) - then).total_seconds() / 86400


def human_ago(iso: str | None) -> str:
    then = _parse_iso(iso)
    if then is None:
        return "never"
    seconds = (datetime.now(timezone.utc) - then).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    local = then.astimezone()
    label = f"{local:%b} {local.day}"
    if local.year != datetime.now().astimezone().year:
        label += f", {local.year}"
    return label
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv\Scripts\python -m pytest tests/gui/test_format.py -q -o addopts=`
Expected: PASS (all, including pre-existing format tests).

- [ ] **Step 5: Commit**

```bash
git add src/mml_cloud_courier/gui/format.py tests/gui/test_format.py
git commit -m "feat: service-error splitting and relative-time helpers for connections"
```

---

### Task 2: Connections QSS vocabulary + visual primitives (`gui/connection_widgets.py`)

**Files:**
- Modify: `src/mml_cloud_courier/gui/theme.py` (extend the `qss()` f-string, before the closing `"""`)
- Create: `src/mml_cloud_courier/gui/connection_widgets.py`
- Test: `tests/gui/test_connection_widgets.py` (new)

**Interfaces:**
- Consumes: `theme.current()`, `theme.notifier`, `Theme` tokens.
- Produces (imported by Tasks 3–6 from `mml_cloud_courier.gui.connection_widgets`):
  - `repolish(widget: QWidget) -> None` — unpolish/polish widget + descendants after a dynamic-property change
  - `pill_font() -> QFont`, `section_font() -> QFont`
  - `class Pill(QLabel)` — `Pill(text: str = "", tone: str = "accent")`, `.set_tone(tone: str)`, auto-uppercases
  - `class SectionLabel(QLabel)` — mono caps `faint` section header
  - `class Dot(QWidget)` — `Dot(tone: str = "accent", diameter: int = 7)`
  - `class StepRail(QWidget)` — `.set_current(step: int)` (1-based), `.current` attr; labels Where/Credential/Verify
  - `class RingSpinner(QWidget)` — 44px spinning arc; `.start()`, `.stop()`
  - `class ProbeList(QWidget)` — `.reset()`, `.start(interval_ms: int = 900)`, `.finish_all()`, `.stop()`, `.states() -> list[str]` (each `"pending" | "running" | "passed"`); constant `ProbeList.PROBES`
  - `AUTH_PRESENTATION: dict[str, tuple[str, str]]`, `ADC_NOTE: str`, `last_check_line(profile: dict) -> tuple[str, str]` (text, tone `"muted" | "warn"`)

- [ ] **Step 1: Write the failing tests** (`tests/gui/test_connection_widgets.py`):

```python
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from datetime import datetime, timedelta, timezone

from mml_cloud_courier.gui.connection_widgets import (
    ADC_NOTE, AUTH_PRESENTATION, Pill, ProbeList, StepRail, last_check_line,
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python -m pytest tests/gui/test_connection_widgets.py -q -o addopts=`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 3: Extend `theme.qss()`** — add before the closing `"""` of the f-string in `src/mml_cloud_courier/gui/theme.py` (note: `#ffffff` literals are legal inside theme.py only; the serviceBanner rule already does this):

```text
QWidget#connHeader {{ background: {t.chrome}; border-bottom: 1px solid {t.line}; }}
QWidget#connFooter {{ background: {t.chrome}; border-top: 1px solid {t.line}; }}
QLabel#connTitle {{ font-size: 17px; font-weight: 600; background: transparent; }}
QLabel#connIntro {{ color: {t.muted}; font-size: 13px; background: transparent; }}
QWidget#connCard {{ background: {t.surface}; border: 1px solid {t.line}; border-radius: 9px; }}
QWidget#connCard[accent="true"] {{ border-color: {t.accent_edge}; }}
QWidget#connCard[edge="danger"] {{ border-color: {t.danger_edge}; }}
QLabel#connPill {{ border-radius: 4px; padding: 4px 7px; }}
QLabel#connPill[tone="accent"] {{ background: {t.accent_soft}; color: {t.accent_text}; }}
QLabel#connPill[tone="warn"] {{ background: {t.warn_soft}; color: {t.warn_text}; }}
QLabel#connPill[tone="track"] {{ background: {t.track}; color: {t.muted}; }}
QLabel#connPill[tone="danger"] {{ background: {t.danger_soft}; color: {t.danger_text}; }}
QLabel#connPill[tone="disabled"] {{ background: {t.track}; color: {t.faint}; }}
QLabel#connName {{ font-size: 14px; font-weight: 600; background: transparent; }}
QLabel#connCardHeading {{ font-size: 14px; font-weight: 600; background: transparent; }}
QLabel#connBody {{ font-size: 13px; background: transparent; }}
QLabel#connMono {{ color: {t.muted}; background: transparent;
                   font-family: "Cascadia Mono","Consolas"; font-size: 11.5px; }}
QLabel#connFaintMono {{ color: {t.faint}; background: transparent;
                        font-family: "Cascadia Mono","Consolas"; font-size: 11.5px; }}
QLabel#connMuted {{ color: {t.muted}; font-size: 11.5px; background: transparent; }}
QLabel#connWarnLine {{ color: {t.warn_text}; font-size: 11.5px; background: transparent; }}
QLabel#connDangerLine {{ color: {t.danger_text}; font-size: 11.5px; background: transparent; }}
QLabel#connDangerMono {{ color: {t.danger_text}; background: transparent;
                         font-family: "Cascadia Mono","Consolas"; font-size: 11.5px; }}
QLabel#helperText {{ color: {t.faint}; font-size: 11.5px; background: transparent; }}
QWidget#connDangerRegion {{ background: {t.danger_soft}; border-top: 1px solid {t.danger_edge};
                            border-bottom-left-radius: 9px; border-bottom-right-radius: 9px; }}
QWidget#connNotice[tone="danger"] {{ background: {t.danger_soft};
    border: 1px solid {t.danger_edge}; border-radius: 9px; }}
QWidget#connNotice[tone="accent"] {{ background: {t.accent_soft};
    border: 1px solid {t.accent_edge}; border-radius: 9px; }}
QWidget#connNotice QLabel {{ background: transparent; }}
QLabel#connNoticeText[tone="danger"] {{ color: {t.danger_text}; font-size: 13px; }}
QLabel#connNoticeText[tone="accent"] {{ color: {t.accent_text}; font-size: 13px; }}
QPushButton#dangerButton {{ background: {t.danger}; color: #ffffff; border: none;
                            padding: 7px 13px; font-weight: 600; border-radius: 6px; }}
QPushButton#dangerOutline {{ background: transparent; color: {t.danger_text};
                             border: 1px solid {t.danger_edge}; border-radius: 6px; }}
QLabel#connChip {{ background: {t.accent_soft}; color: {t.accent_text}; border-radius: 4px;
    padding: 4px 7px; font-family: "Cascadia Mono","Consolas"; font-size: 11.5px; }}
QLabel#connChip[tone="danger"] {{ background: {t.danger_soft}; color: {t.danger_text}; }}
QWidget#connCard[state="disabled"] QLabel#connCardHeading {{ color: {t.faint}; }}
QWidget#connCard[state="disabled"] QLabel#connBody {{ color: {t.disabled}; }}
QWidget#connCard[state="disabled"] QLabel#helperText {{ color: {t.disabled}; }}
QWidget#connFilterBar {{ background: {t.accent_soft}; border-bottom: 1px solid {t.accent_edge}; }}
QWidget#connFilterBar QLabel {{ background: transparent; color: {t.accent_text};
                                font-size: 12.5px; }}
QScrollArea#connScroll {{ border: none; background: transparent; }}
QScrollArea#connScroll > QWidget > QWidget {{ background: transparent; }}
QWidget#connField {{ background: {t.surface}; border: 1px solid {t.line}; border-radius: 6px; }}
QWidget#connField[focus="true"] {{ border-color: {t.accent_edge}; }}
QWidget#connField QLineEdit {{ border: none; background: transparent; padding: 0; }}
QLabel#connFieldPrefix {{ color: {t.faint}; background: transparent;
                          font-family: "Cascadia Mono","Consolas"; }}
```

- [ ] **Step 4: Implement `src/mml_cloud_courier/gui/connection_widgets.py`:**

```python
"""Client-agnostic visual primitives for the connections manager and the
New-connection stepper. QSS-styled parts ride the app-level stylesheet
(theme.qss) and restyle automatically; custom-painted parts read
theme.current() in paintEvent and repaint on theme.notifier.changed via a
bound-method connection, which Qt drops automatically when the widget dies."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from mml_cloud_courier.gui import theme
from mml_cloud_courier.gui.format import human_ago, iso_age_days

AUTH_PRESENTATION: dict[str, tuple[str, str]] = {
    "service_account_key": ("SERVICE ACCOUNT KEY", "accent"),
    "oauth_user": ("GOOGLE SIGN-IN", "warn"),
    "adc": ("COMMAND-LINE CREDENTIALS", "track"),
}
ADC_NOTE = ("Created outside this app. It works, but only this machine's"
            " signed-in account can use it.")
STALE_OAUTH_DAYS = 7


def last_check_line(profile: dict) -> tuple[str, str]:
    """The card's third row: (text, tone). Tone is 'muted' or 'warn'.
    No capability claims from list data — list_profiles has none (spec §9.1)."""
    auth = profile.get("auth_type")
    if auth == "adc":
        return ADC_NOTE, "muted"
    at = profile.get("validated_at")
    if not at:
        return "Never checked.", "muted"
    when = human_ago(at)
    age = iso_age_days(at)
    if auth == "oauth_user" and age is not None and age > STALE_OAUTH_DAYS:
        return (f"Checked {when} — this sign-in may have expired."
                " Check it before the next transfer.", "warn")
    return f"Checked {when}.", "muted"


def repolish(widget: QWidget) -> None:
    """Re-evaluate QSS after a dynamic property change, descendants included."""
    widgets = [widget, *widget.findChildren(QWidget)]
    for w in widgets:
        w.style().unpolish(w)
        w.style().polish(w)


def pill_font() -> QFont:
    font = QFont()
    font.setFamilies(["Cascadia Mono", "Consolas", "monospace"])
    font.setPixelSize(10)
    font.setWeight(QFont.Weight.Medium)
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 105)
    return font


def section_font() -> QFont:
    font = pill_font()
    font.setWeight(QFont.Weight.Normal)
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
    return font


class Pill(QLabel):
    def __init__(self, text: str = "", tone: str = "accent", parent=None):
        super().__init__(parent)
        self.setObjectName("connPill")
        self.setFont(pill_font())
        self.set_text(text)
        self.set_tone(tone)

    def set_text(self, text: str) -> None:
        super().setText(text.upper())

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        repolish(self)


class SectionLabel(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text.upper(), parent)
        self.setObjectName("sectionLabel")
        self.setFont(section_font())


class Dot(QWidget):
    """A small filled circle in a semantic color."""

    def __init__(self, tone: str = "accent", diameter: int = 7, parent=None):
        super().__init__(parent)
        self._tone = tone
        self._diameter = diameter
        self.setFixedSize(diameter, diameter)
        theme.notifier.changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _t) -> None:
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = theme._qcolor(getattr(theme.current(), self._tone))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(0, 0, self._diameter, self._diameter)


class StepRail(QWidget):
    """Three 20px circles + labels joined by 1px rules. States per the
    handoff: completed (filled accent, check), current (accent_soft fill,
    accent border, numeral), future (line border, faint numeral)."""

    LABELS = ("Where", "Credential", "Verify")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current = 1
        self.setFixedHeight(28)
        theme.notifier.changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _t) -> None:
        self.update()

    def set_current(self, step: int) -> None:
        self.current = step
        self.update()

    def paintEvent(self, _event) -> None:
        t = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        label_font = self.font()
        metrics_width = self.width()
        circle_d = 20
        y = (self.height() - circle_d) // 2
        gap = 9
        # measure label widths at both weights to lay out three segments
        from PySide6.QtGui import QFontMetrics
        widths = []
        for i, label in enumerate(self.LABELS):
            f = QFont(label_font)
            f.setWeight(QFont.Weight.DemiBold if i + 1 == self.current
                        else QFont.Weight.Normal)
            widths.append(QFontMetrics(f).horizontalAdvance(label))
        fixed = 3 * circle_d + sum(widths) + 6 * gap
        rule_w = max(12, (metrics_width - fixed) // 2)
        x = 0
        for i, label in enumerate(self.LABELS):
            step = i + 1
            # circle
            cx, cy = x, y
            if step < self.current:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(theme._qcolor(t.accent))
                painter.drawEllipse(cx, cy, circle_d, circle_d)
                painter.setPen(QPen(theme._qcolor(t.accent_ink), 2))
                painter.drawLine(cx + 6, cy + 10, cx + 9, cy + 13)
                painter.drawLine(cx + 9, cy + 13, cx + 14, cy + 7)
            elif step == self.current:
                painter.setBrush(theme._qcolor(t.accent_soft))
                painter.setPen(QPen(theme._qcolor(t.accent), 1.5))
                painter.drawEllipse(cx + 1, cy + 1, circle_d - 2, circle_d - 2)
                painter.setPen(theme._qcolor(t.accent_text))
                painter.drawText(cx, cy, circle_d, circle_d,
                                 Qt.AlignmentFlag.AlignCenter, str(step))
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(theme._qcolor(t.line), 1.5))
                painter.drawEllipse(cx + 1, cy + 1, circle_d - 2, circle_d - 2)
                painter.setPen(theme._qcolor(t.faint))
                painter.drawText(cx, cy, circle_d, circle_d,
                                 Qt.AlignmentFlag.AlignCenter, str(step))
            x += circle_d + gap
            # label
            f = QFont(label_font)
            if step == self.current:
                f.setWeight(QFont.Weight.DemiBold)
                painter.setPen(theme._qcolor(t.ink))
            elif step < self.current:
                painter.setPen(theme._qcolor(t.muted))
            else:
                painter.setPen(theme._qcolor(t.faint))
            painter.setFont(f)
            painter.drawText(x, 0, widths[i], self.height(),
                             Qt.AlignmentFlag.AlignVCenter, label)
            x += widths[i] + gap
            # rule to the next circle
            if i < 2:
                color = t.accent if step < self.current else t.line
                painter.setPen(QPen(theme._qcolor(color), 1))
                painter.drawLine(x, self.height() // 2, x + rule_w,
                                 self.height() // 2)
                x += rule_w + gap


class RingSpinner(QWidget):
    """44px ring: 3px track circle with an accent arc, spinning."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(44, 44)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        theme.notifier.changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _t) -> None:
        self.update()

    def _tick(self) -> None:
        self._angle = (self._angle - 6) % 360
        self.update()

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def paintEvent(self, _event) -> None:
        t = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)
        painter.setPen(QPen(theme._qcolor(t.track), 3))
        painter.drawEllipse(rect)
        painter.setPen(QPen(theme._qcolor(t.accent), 3))
        painter.drawArc(rect, self._angle * 16, 100 * 16)


class _ProbeCircle(QWidget):
    """16px status circle: passed = filled accent + check, running = accent
    border, pending = line border."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "pending"
        self.setFixedSize(16, 16)
        theme.notifier.changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _t) -> None:
        self.update()

    def set_state(self, state: str) -> None:
        self.state = state
        self.update()

    def paintEvent(self, _event) -> None:
        t = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.state == "passed":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme._qcolor(t.accent))
            painter.drawEllipse(0, 0, 16, 16)
            painter.setPen(QPen(theme._qcolor(t.accent_ink), 2))
            painter.drawLine(4, 8, 7, 11)
            painter.drawLine(7, 11, 12, 5)
        else:
            color = t.accent if self.state == "running" else t.line
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(theme._qcolor(color), 1.5))
            painter.drawEllipse(1, 1, 14, 14)


class ProbeList(QWidget):
    """The five preflight probes, client-side paced. The service exposes no
    per-probe signal (and the API is fixed), so rows advance on a timer while
    the single create_profile call is pending; the LAST row must stay
    'running' until the caller resolves it — finish_all() on 200, stop() on
    failure. Rows are never individually marked failed: which probe failed is
    unknown client-side."""

    PROBES = (
        ("List objects", "listing…"),
        ("Read an object", "reading…"),
        ("Write a test object", "writing…"),
        ("Compose slices", "composing…"),
        ("Delete the test object", "deleting…"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("connCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._running = -1
        self._circles: list[_ProbeCircle] = []
        self._names: list[QLabel] = []
        self._details: list[QLabel] = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 4, 0, 4)
        column.setSpacing(0)
        for i, (name, verb) in enumerate(self.PROBES):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(15, 11, 15, 11)
            row_layout.setSpacing(11)
            circle = _ProbeCircle()
            name_label = QLabel(name)
            name_label.setObjectName("connBody")
            detail = QLabel("")
            detail.setObjectName("helperText")
            self._verb = verb  # not used; verbs come from PROBES per row
            row_layout.addWidget(circle)
            row_layout.addWidget(name_label)
            row_layout.addWidget(detail)
            row_layout.addStretch(1)
            column.addWidget(row)
            self._circles.append(circle)
            self._names.append(name_label)
            self._details.append(detail)

    def states(self) -> list[str]:
        return [c.state for c in self._circles]

    def reset(self) -> None:
        self._timer.stop()
        self._running = -1
        for circle, detail in zip(self._circles, self._details):
            circle.set_state("pending")
            detail.setText("")

    def start(self, interval_ms: int = 900) -> None:
        self.reset()
        self._running = 0
        self._apply()
        self._timer.start(interval_ms)

    def _advance(self) -> None:
        if self._running >= len(self.PROBES) - 1:
            self._timer.stop()      # cap: last probe stays running
            return
        self._circles[self._running].set_state("passed")
        self._details[self._running].setText("")
        self._running += 1
        self._apply()

    def _apply(self) -> None:
        self._circles[self._running].set_state("running")
        self._details[self._running].setText(self.PROBES[self._running][1])

    def stop(self) -> None:
        self._timer.stop()

    def finish_all(self) -> None:
        self._timer.stop()
        for circle, detail in zip(self._circles, self._details):
            circle.set_state("passed")
            detail.setText("")
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv\Scripts\python -m pytest tests/gui/test_connection_widgets.py tests/gui/test_theme.py -q -o addopts=`
Expected: PASS — including `test_no_hex_colors_outside_theme_py` (the primitives use `theme._qcolor(getattr(...))`, no hex).

- [ ] **Step 6: Commit**

```bash
git add src/mml_cloud_courier/gui/theme.py src/mml_cloud_courier/gui/connection_widgets.py tests/gui/test_connection_widgets.py
git commit -m "feat: connections QSS vocabulary and visual primitives"
```

---

### Task 3: Connections manager rebuilt as status cards

**Files:**
- Modify: `src/mml_cloud_courier/gui/connection_dialogs.py` (replace `ConnectionsDialog`; everything above it — constants, `load_key_file`, payload builders, `NewConnectionDialog` — untouched in this task)
- Create: `ConnectionCard` in `src/mml_cloud_courier/gui/connection_widgets.py`
- Modify: `tests/gui/test_connection_dialogs.py` (rewrite ONE test — see spec §10; the copy test is untouchable)
- Test: `tests/gui/test_connection_manager.py` (new)

**Interfaces:**
- Consumes: Task 1 `split_service_error`; Task 2 primitives, `AUTH_PRESENTATION`, `last_check_line`, `repolish`.
- Produces:
  - `ConnectionCard(profile: dict, parent=None)` with signals `check_clicked = Signal()`, `remove_confirmed = Signal()`, `show_jobs_clicked = Signal()`; methods `set_checking() -> None`, `show_check_summary(text: str) -> None`, `show_error_line(text: str) -> None`, `show_refusal(n: int) -> None`, `reset_region() -> None`; attrs `.profile`, `.check_button`, `.remove_button`, `.check_line` (QLabel), `.region` (QWidget), `.pill`
  - `ConnectionsDialog(client, parent=None)` with `showJobsForProfile = Signal(int, str)`, attrs `.new_button`, `.close_button`, `.cards: list[ConnectionCard]`, `._profiles: list[dict]`, method `refresh()`
- Task 7 connects `showJobsForProfile`.

- [ ] **Step 1: Rewrite the structural test** in `tests/gui/test_connection_dialogs.py`. Replace the whole body of `test_connections_dialog_new_button_is_primary` (per-card design removed the dialog-level `check_button`/`remove_button`; spec §10 authorizes exactly this one rewrite):

```python
def test_connections_dialog_new_button_is_primary(qtbot):
    class ListingClient:
        def list_profiles(self):
            return []

    dialog = ConnectionsDialog(ListingClient())
    qtbot.addWidget(dialog)
    assert dialog.new_button.objectName() == "primaryButton"
    assert dialog.close_button.objectName() != "primaryButton"
```

- [ ] **Step 2: Write the failing manager tests** (`tests/gui/test_connection_manager.py`):

```python
"""Manager: status cards per profile, per-card actions, inline confirm and
refusal (never a modal on a modal), no raw enum values, no raw 409 string."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from datetime import datetime, timedelta, timezone

from mml_cloud_courier.cli.service_client import ServiceError
from mml_cloud_courier.gui.connection_dialogs import ConnectionsDialog

NOW = datetime.now(timezone.utc)


def profile(pid=1, name="MML imagery", auth="service_account_key",
            bucket="mml-hi-imagery-2026", prefix="2026", validated=None):
    return {"id": pid, "name": name, "auth_type": auth, "bucket": bucket,
            "default_prefix": prefix, "project_id": "",
            "created_at": NOW.isoformat(), "validated_at": validated}


class FakeClient:
    def __init__(self, profiles):
        self.profiles = profiles
        self.checked: list[int] = []
        self.deleted: list[int] = []
        self.delete_error: Exception | None = None
        self.check_result = {"ok": True, "preflight": {}, "summary":
            "This credential can list, read, write, compose and delete to gs://b/p."}

    def list_profiles(self):
        return self.profiles

    def check_profile(self, profile_id, **_kw):
        self.checked.append(profile_id)
        return self.check_result

    def delete_profile(self, profile_id):
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(profile_id)
        return {"deleted": profile_id}


def wait_cards(qtbot, dialog, count):
    qtbot.waitUntil(lambda: len(dialog.cards) == count, timeout=5000)


def test_cards_render_pills_and_lines_not_raw_enums(qtbot):
    fresh = (NOW - timedelta(minutes=12)).isoformat()
    stale = (NOW - timedelta(days=9)).isoformat()
    client = FakeClient([
        profile(1, auth="service_account_key", validated=fresh),
        profile(2, "PAM archive", auth="oauth_user", bucket="mml-acoustics-archive",
                prefix="", validated=stale),
        profile(3, "Bering CTD", auth="adc", bucket="mml-oceanography",
                prefix="ctd", validated=fresh),
    ])
    dialog = ConnectionsDialog(client)
    qtbot.addWidget(dialog)
    wait_cards(qtbot, dialog, 3)
    key_card, oauth_card, adc_card = dialog.cards
    assert key_card.pill.text() == "SERVICE ACCOUNT KEY"
    assert "Checked 12 minutes ago." == key_card.check_line.text()
    assert oauth_card.pill.text() == "GOOGLE SIGN-IN"
    assert "may have expired" in oauth_card.check_line.text()
    assert adc_card.pill.text() == "COMMAND-LINE CREDENTIALS"
    assert "signed-in account" in adc_card.check_line.text()
    for card in dialog.cards:
        assert "service_account_key" not in card.check_line.text()
        assert card.check_button.objectName() != "primaryButton"
        assert card.remove_button.objectName() != "primaryButton"
    # the gs:// line
    assert key_card.target_label.text() == "gs://mml-hi-imagery-2026/2026"
    assert oauth_card.target_label.text() == "gs://mml-acoustics-archive"


def test_check_now_rewrites_the_line_in_place(qtbot):
    client = FakeClient([profile(1)])
    dialog = ConnectionsDialog(client)
    qtbot.addWidget(dialog)
    wait_cards(qtbot, dialog, 1)
    card = dialog.cards[0]
    card.check_button.click()
    qtbot.waitUntil(lambda: "can list, read, write" in card.check_line.text(),
                    timeout=5000)
    assert client.checked == [1]


def test_remove_shows_inline_confirm_then_deletes(qtbot):
    client = FakeClient([profile(1)])
    dialog = ConnectionsDialog(client)
    qtbot.addWidget(dialog)
    wait_cards(qtbot, dialog, 1)
    card = dialog.cards[0]
    assert not card.region.isVisibleTo(dialog)
    card.remove_button.click()
    assert card.region.isVisibleTo(dialog)
    assert "cannot be undone" in card.region_text.text()
    card.confirm_button.click()
    qtbot.waitUntil(lambda: client.deleted == [1], timeout=5000)


def test_keep_it_collapses_the_confirm(qtbot):
    client = FakeClient([profile(1)])
    dialog = ConnectionsDialog(client)
    qtbot.addWidget(dialog)
    wait_cards(qtbot, dialog, 1)
    card = dialog.cards[0]
    card.remove_button.click()
    card.keep_button.click()
    assert not card.region.isVisibleTo(dialog)
    assert client.deleted == []


def test_delete_refusal_renders_inline_with_count_and_route(qtbot):
    client = FakeClient([profile(4, "Leg 2 imagery (2025)")])
    client.delete_error = ServiceError(
        409, "profile 4 is used by 7 job(s) and cannot be deleted while they exist")
    dialog = ConnectionsDialog(client)
    qtbot.addWidget(dialog)
    wait_cards(qtbot, dialog, 1)
    card = dialog.cards[0]
    card.remove_button.click()
    card.confirm_button.click()
    qtbot.waitUntil(lambda: "used by 7 jobs" in card.region_text.text(), timeout=5000)
    assert "profile 4" not in card.region_text.text()       # raw string never shown
    assert card.show_jobs_button.text() == "Show those 7 jobs"
    fired = []
    dialog.showJobsForProfile.connect(lambda pid, name: fired.append((pid, name)))
    card.show_jobs_button.click()
    assert fired == [(4, "Leg 2 imagery (2025)")]
    assert dialog.result() == 1     # accepted/closed so the rail is visible


def test_empty_state_and_new_button(qtbot):
    client = FakeClient([])
    dialog = ConnectionsDialog(client)
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog.empty_label.isVisibleTo(dialog), timeout=5000)
    assert dialog.empty_label.text() == "No connections yet."
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv\Scripts\python -m pytest tests/gui/test_connection_manager.py tests/gui/test_connection_dialogs.py -q -o addopts=`
Expected: manager tests FAIL (no `cards` attribute); rewritten structural test FAILS (no such attributes yet); the other five existing tests PASS.

- [ ] **Step 4: Implement.** Append `ConnectionCard` to `connection_widgets.py`:

```python
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPushButton


class ConnectionCard(QWidget):
    """One profile as a status card. Client-agnostic: the dialog owns I/O and
    calls the state methods; the card only renders and emits intent."""

    check_clicked = Signal()
    remove_confirmed = Signal()
    show_jobs_clicked = Signal()

    def __init__(self, profile: dict, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.setObjectName("connCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        label, tone = AUTH_PRESENTATION.get(
            profile.get("auth_type", ""),
            (str(profile.get("auth_type", "")).upper(), "track"))
        self.name_label = QLabel(profile["name"])
        self.name_label.setObjectName("connName")
        self.pill = Pill(label, tone=tone)
        target = f"gs://{profile['bucket']}/{profile.get('default_prefix') or ''}"
        self.target_label = QLabel(target.rstrip("/"))
        self.target_label.setObjectName("connMono")
        text, line_tone = last_check_line(profile)
        self.check_line = QLabel(text)
        self.check_line.setObjectName(
            "connWarnLine" if line_tone == "warn" else "connMuted")
        self.check_line.setWordWrap(True)

        self.check_button = QPushButton("Check now")
        self.remove_button = QPushButton("Remove")
        for button in (self.check_button, self.remove_button):
            button.setAutoDefault(False)
        self.check_button.clicked.connect(self.check_clicked.emit)
        self.remove_button.clicked.connect(self._show_confirm)

        info = QVBoxLayout()
        info.setSpacing(5)
        name_row = QHBoxLayout()
        name_row.setSpacing(9)
        name_row.addWidget(self.name_label)
        name_row.addWidget(self.pill)
        name_row.addStretch(1)
        info.addLayout(name_row)
        info.addWidget(self.target_label)
        info.addWidget(self.check_line)

        buttons = QVBoxLayout()
        buttons.setSpacing(7)
        buttons.addWidget(self.check_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(15, 13, 15, 13)
        content_layout.setSpacing(11)
        content_layout.addLayout(info, 1)
        content_layout.addLayout(buttons)

        # inline confirm / refusal region — never a QMessageBox over a dialog
        self.region = QWidget()
        self.region.setObjectName("connDangerRegion")
        self.region.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        region_layout = QVBoxLayout(self.region)
        region_layout.setContentsMargins(15, 13, 15, 13)
        region_layout.setSpacing(9)
        head_row = QHBoxLayout()
        head_row.setSpacing(9)
        head_row.addWidget(Dot(tone="danger"),
                           alignment=Qt.AlignmentFlag.AlignTop)
        self.region_text = QLabel("")
        self.region_text.setObjectName("connDangerLine")
        self.region_text.setWordWrap(True)
        head_row.addWidget(self.region_text, 1)
        region_layout.addLayout(head_row)
        self.region_body = QLabel("")
        self.region_body.setObjectName("connDangerLine")
        self.region_body.setWordWrap(True)
        region_layout.addWidget(self.region_body)
        region_buttons = QHBoxLayout()
        region_buttons.setSpacing(9)
        self.confirm_button = QPushButton("Remove")
        self.confirm_button.setObjectName("dangerButton")
        self.show_jobs_button = QPushButton("")
        self.show_jobs_button.setObjectName("dangerButton")
        self.keep_button = QPushButton("Keep it")
        self.keep_button.setObjectName("dangerOutline")
        for button in (self.confirm_button, self.show_jobs_button,
                       self.keep_button):
            button.setAutoDefault(False)
        self.confirm_button.clicked.connect(self.remove_confirmed.emit)
        self.show_jobs_button.clicked.connect(self.show_jobs_clicked.emit)
        self.keep_button.clicked.connect(self.reset_region)
        region_buttons.addWidget(self.confirm_button)
        region_buttons.addWidget(self.show_jobs_button)
        region_buttons.addWidget(self.keep_button)
        region_buttons.addStretch(1)
        region_layout.addLayout(region_buttons)
        self.region.hide()

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(content)
        column.addWidget(self.region)

    def _show_confirm(self) -> None:
        self.region_text.setText(
            f"Remove '{self.profile['name']}'? Its saved credential is deleted"
            " with it. This cannot be undone.")
        self.region_body.setText("")
        self.region_body.hide()
        self.confirm_button.show()
        self.show_jobs_button.hide()
        self.region.show()

    def show_refusal(self, n: int) -> None:
        self.region_text.setText(
            f"This connection is used by {n} jobs and cannot be deleted while"
            " they exist.")
        self.region_body.setText(
            "Their reports and bucket paths are read back through it. Delete or"
            " archive those jobs first, or leave this connection in place and"
            " stop using it for new transfers.")
        self.region_body.show()
        self.confirm_button.hide()
        self.show_jobs_button.setText(f"Show those {n} jobs")
        self.show_jobs_button.show()
        self.region.show()

    def reset_region(self) -> None:
        self.region.hide()

    def set_checking(self) -> None:
        self.check_button.setEnabled(False)
        self.remove_button.setEnabled(False)
        self.check_line.setObjectName("connMuted")
        self.check_line.setText("Checking…")
        repolish(self.check_line)

    def _restore_buttons(self) -> None:
        self.check_button.setEnabled(True)
        self.remove_button.setEnabled(True)

    def show_check_summary(self, text: str) -> None:
        self._restore_buttons()
        self.check_line.setObjectName("connMuted")
        self.check_line.setText(f"Checked just now — {text}")
        repolish(self.check_line)

    def show_error_line(self, text: str) -> None:
        self._restore_buttons()
        self.check_line.setObjectName("connDangerLine")
        self.check_line.setText(text)
        repolish(self.check_line)
```

Then replace `ConnectionsDialog` in `connection_dialogs.py` (imports to add there: `re`; `from PySide6.QtCore import Qt`; `QScrollArea`, `QWidget` from QtWidgets; `from mml_cloud_courier.gui.connection_widgets import ConnectionCard`; `from mml_cloud_courier.gui.format import split_service_error`; drop `QListWidget`/`QMessageBox` if now unused). In the `ConnectionCard` code above, the QtWidgets import needed in `connection_widgets.py` is `QPushButton` (added to the existing import line):

```python
class ConnectionsDialog(QDialog):
    """Status-card manager per the committed handoff. Per-card actions —
    no selection-driven button bar. New connection is the one filled control."""

    showJobsForProfile = Signal(int, str)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.setWindowTitle("Connections")
        self.setFixedWidth(640)
        self.setMinimumHeight(420)
        self._profiles: list[dict] = []
        self.cards: list[ConnectionCard] = []

        header = QWidget()
        header.setObjectName("connHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 20, 20, 15)
        header_layout.setSpacing(15)
        title_column = QVBoxLayout()
        title_column.setSpacing(7)
        title = QLabel("Connections")
        title.setObjectName("connTitle")
        intro = QLabel("Each connection is a bucket and a credential the"
                       " service keeps and uses on its own. Transfers pick"
                       " one by name.")
        intro.setObjectName("connIntro")
        intro.setWordWrap(True)
        title_column.addWidget(title)
        title_column.addWidget(intro)
        header_layout.addLayout(title_column, 1)
        self.new_button = QPushButton("New connection")
        self.new_button.setObjectName("primaryButton")
        self.new_button.setAutoDefault(False)
        header_layout.addWidget(self.new_button,
                                alignment=Qt.AlignmentFlag.AlignTop)

        self._cards_host = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_host)
        self._cards_layout.setContentsMargins(20, 15, 20, 15)
        self._cards_layout.setSpacing(11)
        self.empty_label = QLabel("No connections yet.")
        self.empty_label.setObjectName("connMuted")
        self.empty_label.hide()
        self.error_label = QLabel("")
        self.error_label.setObjectName("connDangerLine")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        self._cards_layout.addWidget(self.empty_label)
        self._cards_layout.addWidget(self.error_label)
        self._cards_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setObjectName("connScroll")
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._cards_host)

        footer = QWidget()
        footer.setObjectName("connFooter")
        footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 13, 20, 13)
        note = QLabel("Checking re-runs the same probe used when the"
                      " connection was created.")
        note.setObjectName("helperText")
        note.setWordWrap(True)
        footer_layout.addWidget(note, 1)
        self.close_button = QPushButton("Close")
        self.close_button.setAutoDefault(False)
        footer_layout.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(scroll, 1)
        layout.addWidget(footer)

        self.new_button.clicked.connect(self._new_connection)
        self.close_button.clicked.connect(self.close)
        self.resize(640, 560)
        self.refresh()

    def refresh(self) -> None:
        call_async(self.client.list_profiles, parent=self,
                   on_done=self._profiles_loaded, on_failed=self._list_failed)

    def _profiles_loaded(self, profiles):
        self._profiles = profiles
        for card in self.cards:
            card.setParent(None)
            card.deleteLater()
        self.cards = []
        self.error_label.hide()
        self.empty_label.setVisible(not profiles)
        for profile in profiles:
            card = ConnectionCard(profile)
            card.check_clicked.connect(
                lambda c=card: self._check_card(c))
            card.remove_confirmed.connect(
                lambda c=card: self._delete_card(c))
            card.show_jobs_clicked.connect(
                lambda c=card: self._show_jobs(c))
            # insert above the trailing stretch
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
            self.cards.append(card)

    def _list_failed(self, message):
        _code, detail = split_service_error(message)
        self.error_label.setText(detail)
        self.error_label.show()

    def _check_card(self, card) -> None:
        card.set_checking()
        call_async(lambda: self.client.check_profile(card.profile["id"]),
                   parent=self,
                   on_done=lambda result, c=card: c.show_check_summary(
                       result["summary"]),
                   on_failed=lambda message, c=card: c.show_error_line(
                       split_service_error(message)[1]))

    def _delete_card(self, card) -> None:
        call_async(lambda: self.client.delete_profile(card.profile["id"]),
                   parent=self,
                   on_done=lambda _r: self.refresh(),
                   on_failed=lambda message, c=card: self._delete_failed(c, message))

    def _delete_failed(self, card, message) -> None:
        code, detail = split_service_error(message)
        match = re.search(r"used by (\d+) job", detail)
        if code == 409 and match:
            card.show_refusal(int(match.group(1)))
        else:
            card.reset_region()
            card.show_error_line(detail)

    def _show_jobs(self, card) -> None:
        self.showJobsForProfile.emit(card.profile["id"], card.profile["name"])
        self.accept()

    def _new_connection(self):
        dialog = NewConnectionDialog(self.client, self)
        dialog.created.connect(lambda _result: self.refresh())
        dialog.exec()
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv\Scripts\python -m pytest tests/gui/test_connection_manager.py tests/gui/test_connection_dialogs.py tests/gui/test_theme.py -q -o addopts=`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add src/mml_cloud_courier/gui/connection_dialogs.py src/mml_cloud_courier/gui/connection_widgets.py tests/gui/test_connection_manager.py tests/gui/test_connection_dialogs.py
git commit -m "feat: connections manager rebuilt as status cards with inline confirm/refusal"
```

---

### Task 4: Stepper shell — rail, navigation, step 1, health gate

**Files:**
- Modify: `src/mml_cloud_courier/gui/connection_dialogs.py` (replace `NewConnectionDialog`; constants and helpers above it untouched)
- Test: `tests/gui/test_connection_stepper.py` (new)

**Interfaces:**
- Consumes: Task 2 `StepRail`, `SectionLabel`, `Pill`, `Dot`, `ProbeList`, `RingSpinner`, `repolish`; the module's own four constants.
- Produces (relied on by Tasks 5–6, same class, later tasks extend these methods):
  - `NewConnectionDialog(client, parent=None)`; attrs `.step_rail`, `.name_edit`, `.bucket_edit`, `.prefix_edit`, `.project_edit`, `.name_error` (QLabel), `.next_button`, `.back_button`, `.cancel_button`, `.key_button`, `.signin_button`, `.status_label` (gate banner text), `.gate_banner` (QWidget), `.check_again_button`, `.open_main_button`, `.card_key`, `.card_signin`, `.either_way_label`, `._stack` (QStackedWidget), `.created = Signal(dict)`
  - `._go_to_step(n: int)`; `._step` int; `._phase` str (`"idle"` here); `._health_ok` bool; `._service_ok/_service_down` callbacks
  - Pages as attrs: `.page_where`, `.page_credential` (Tasks 5–6 add `.page_signin`, `.page_validating`, `.page_verified`, `.page_failed`)
- Existing pinned tests must pass unmodified: construction with a client exposing only `health()`; `key_button`/`signin_button` disabled synchronously; `status_label.text()` contains "not reachable" after health failure; `key_button.objectName() == "primaryButton"`.

- [ ] **Step 1: Write the failing tests** (`tests/gui/test_connection_stepper.py`):

```python
"""Stepper shell: rail state, step-1 gating, health gate presentation."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from mml_cloud_courier.gui.connection_dialogs import (
    COPY_SERVICE_FIRST, NewConnectionDialog,
)


class HealthyClient:
    def health(self):
        return {"status": "ok"}


class DeadClient:
    def health(self):
        raise ConnectionError("nope")


def wait_health(qtbot, dialog, ok=True):
    qtbot.waitUntil(lambda: dialog._health_ok is ok, timeout=5000)


def test_opens_on_step_1_with_next_disabled(qtbot):
    dialog = NewConnectionDialog(HealthyClient())
    qtbot.addWidget(dialog)
    assert dialog._step == 1
    assert dialog.step_rail.current == 1
    assert not dialog.next_button.isEnabled()
    assert not dialog.back_button.isEnabled()
    assert dialog.next_button.text() == "Next: credential"


def test_next_enables_only_with_name_and_bucket(qtbot):
    dialog = NewConnectionDialog(HealthyClient())
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("MML imagery")
    assert not dialog.next_button.isEnabled()
    dialog.bucket_edit.setText("mml-hi-imagery-2026")
    assert dialog.next_button.isEnabled()
    dialog.name_edit.clear()
    assert not dialog.next_button.isEnabled()


def test_next_advances_and_back_returns_with_fields_intact(qtbot):
    dialog = NewConnectionDialog(HealthyClient())
    qtbot.addWidget(dialog)
    wait_health(qtbot, dialog)
    dialog.name_edit.setText("n")
    dialog.bucket_edit.setText("b")
    dialog.prefix_edit.setText("p")
    dialog.next_button.click()
    assert dialog._step == 2
    assert dialog.step_rail.current == 2
    assert "gs://b/p" in dialog.either_way_label.text()
    dialog.back_button.click()
    assert dialog._step == 1
    assert dialog.name_edit.text() == "n" and dialog.prefix_edit.text() == "p"


def test_healthy_service_enables_credential_paths_on_step2(qtbot):
    dialog = NewConnectionDialog(HealthyClient())
    qtbot.addWidget(dialog)
    wait_health(qtbot, dialog)
    assert dialog.key_button.isEnabled()
    assert dialog.signin_button.isEnabled()
    assert not dialog.gate_banner.isVisibleTo(dialog)


def test_dead_service_shows_gate_and_disabled_cards(qtbot):
    dialog = NewConnectionDialog(DeadClient())
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: "not reachable" in dialog.status_label.text(),
                    timeout=5000)
    assert dialog.status_label.text() == COPY_SERVICE_FIRST
    assert not dialog.key_button.isEnabled()
    assert not dialog.signin_button.isEnabled()
    assert dialog.card_key.property("state") == "disabled"
    assert dialog.check_again_button.objectName() == "dangerButton"
    assert dialog.open_main_button.objectName() == "dangerOutline"


def test_check_again_recovers_when_service_comes_up(qtbot):
    class FlappingClient:
        def __init__(self):
            self.up = False

        def health(self):
            if not self.up:
                raise ConnectionError("nope")
            return {"status": "ok"}

    client = FlappingClient()
    dialog = NewConnectionDialog(client)
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: "not reachable" in dialog.status_label.text(),
                    timeout=5000)
    client.up = True
    dialog.check_again_button.click()
    wait_health(qtbot, dialog)
    assert dialog.key_button.isEnabled()
    assert dialog.card_key.property("state") != "disabled"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python -m pytest tests/gui/test_connection_stepper.py -q -o addopts=`
Expected: FAIL (no `step_rail` attribute etc.).

- [ ] **Step 3: Implement — replace `NewConnectionDialog`** in `connection_dialogs.py`. Keep the class docstring's meaning (health gate; copy strings gate-findings-bound). Structure (complete code):

```python
class NewConnectionDialog(QDialog):
    """Three-step stepper: Where → Credential → Verify. Nothing
    credential-shaped (browsing for a key, opening a sign-in browser tab) is
    reachable until the service has answered /health — the gate covers step 2
    as a whole. The copy strings are gate-findings-bound; tests assert on
    their phrases, so they are not to be reworded."""

    created = Signal(dict)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.setWindowTitle("New connection")
        self.setFixedWidth(600)
        self._step = 1
        self._phase = "idle"
        self._health_ok = False
        self._credential: dict | None = None
        self._key_path: str | None = None
        self._login_generation = 0

        header = QWidget()
        header.setObjectName("connHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 18, 20, 15)
        header_layout.setSpacing(14)
        title = QLabel("New connection")
        title.setObjectName("connTitle")
        self.step_rail = StepRail()
        header_layout.addWidget(title)
        header_layout.addWidget(self.step_rail)

        self._stack = QStackedWidget()
        self.page_where = self._build_page_where()
        self.page_credential = self._build_page_credential()
        self._stack.addWidget(self.page_where)
        self._stack.addWidget(self.page_credential)

        footer = QWidget()
        footer.setObjectName("connFooter")
        footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 13, 20, 13)
        footer_layout.setSpacing(9)
        self.back_button = QPushButton("Back")
        self.back_button.setAutoDefault(False)
        self.back_button.clicked.connect(self._on_back)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        self.next_button = QPushButton("Next: credential")
        self.next_button.setObjectName("primaryButton")
        self.next_button.clicked.connect(lambda: self._go_to_step(2))
        footer_layout.addWidget(self.back_button)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.cancel_button)
        footer_layout.addWidget(self.next_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self._stack, 1)
        layout.addWidget(footer)

        # gate: both credential paths dead until /health answers (synchronous,
        # before the check is even dispatched)
        self._set_paths_enabled(False)
        call_async(self.client.health, parent=self,
                   on_done=self._service_ok, on_failed=self._service_down)
        self._go_to_step(1)

    # -- pages -----------------------------------------------------------

    def _build_page_where(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(15)
        layout.addWidget(SectionLabel("Where the data goes"))

        def field(label_text, optional=False):
            row = QHBoxLayout()
            row.setSpacing(7)
            label = QLabel(label_text)
            label.setObjectName("connBody")
            row.addWidget(label)
            if optional:
                marker = QLabel("optional")
                marker.setObjectName("helperText")
                row.addWidget(marker)
            row.addStretch(1)
            return row

        self.name_edit = QLineEdit()
        self.name_error = QLabel("")
        self.name_error.setObjectName("connDangerLine")
        self.name_error.setWordWrap(True)
        self.name_error.hide()
        name_helper = QLabel("How this appears when you start a transfer.")
        self.bucket_edit = QLineEdit()
        bucket_helper = QLabel(
            "The bucket your administrator set up for this lab.")
        self.prefix_edit = QLineEdit()
        prefix_helper = QLabel(
            "A folder inside the bucket that transfers start from. Leave it"
            " blank to start at the root.")
        self.project_edit = QLineEdit()
        self.project_edit.setPlaceholderText("Taken from the key file")
        project_helper = QLabel(
            "Only needed when the credential does not name a project.")
        for helper in (name_helper, bucket_helper, prefix_helper,
                       project_helper):
            helper.setObjectName("helperText")
            helper.setWordWrap(True)

        # bucket field: static gs:// prefix inside the field frame
        bucket_frame = QWidget()
        bucket_frame.setObjectName("connField")
        bucket_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bucket_row = QHBoxLayout(bucket_frame)
        bucket_row.setContentsMargins(11, 8, 11, 8)
        bucket_row.setSpacing(2)
        gs_label = QLabel("gs://")
        gs_label.setObjectName("connFieldPrefix")
        bucket_row.addWidget(gs_label)
        bucket_row.addWidget(self.bucket_edit, 1)

        layout.addLayout(field("Name"))
        layout.addWidget(self.name_edit)
        layout.addWidget(self.name_error)
        layout.addWidget(name_helper)
        layout.addLayout(field("Bucket"))
        layout.addWidget(bucket_frame)
        layout.addWidget(bucket_helper)
        layout.addLayout(field("Default prefix", optional=True))
        layout.addWidget(self.prefix_edit)
        layout.addWidget(prefix_helper)
        layout.addLayout(field("Project ID", optional=True))
        layout.addWidget(self.project_edit)
        layout.addWidget(project_helper)
        layout.addStretch(1)

        self.name_edit.textChanged.connect(self._update_next)
        self.bucket_edit.textChanged.connect(self._update_next)
        return page

    def _build_page_credential(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(11)

        self.gate_banner = QWidget()
        self.gate_banner.setObjectName("connNotice")
        self.gate_banner.setProperty("tone", "danger")
        self.gate_banner.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True)
        gate_layout = QVBoxLayout(self.gate_banner)
        gate_layout.setContentsMargins(15, 13, 15, 13)
        gate_layout.setSpacing(9)
        gate_row = QHBoxLayout()
        gate_row.setSpacing(9)
        gate_row.addWidget(Dot(tone="danger"),
                           alignment=Qt.AlignmentFlag.AlignTop)
        self.status_label = QLabel("Checking the transfer service…")
        self.status_label.setObjectName("connNoticeText")
        self.status_label.setProperty("tone", "danger")
        self.status_label.setWordWrap(True)
        gate_row.addWidget(self.status_label, 1)
        gate_layout.addLayout(gate_row)
        gate_buttons = QHBoxLayout()
        gate_buttons.setSpacing(9)
        self.check_again_button = QPushButton("Check again")
        self.check_again_button.setObjectName("dangerButton")
        self.check_again_button.clicked.connect(self._check_again)
        self.open_main_button = QPushButton("Open the main window")
        self.open_main_button.setObjectName("dangerOutline")
        self.open_main_button.clicked.connect(self._open_main_window)
        for button in (self.check_again_button, self.open_main_button):
            button.setAutoDefault(False)
        gate_buttons.addWidget(self.check_again_button)
        gate_buttons.addWidget(self.open_main_button)
        gate_buttons.addStretch(1)
        gate_layout.addLayout(gate_buttons)
        self.gate_banner.hide()
        layout.addWidget(self.gate_banner)

        layout.addWidget(SectionLabel("How the service signs in"))

        # Card A — service account key (recommended)
        self.card_key = QWidget()
        self.card_key.setObjectName("connCard")
        self.card_key.setProperty("accent", "true")
        self.card_key.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        key_layout = QVBoxLayout(self.card_key)
        key_layout.setContentsMargins(17, 15, 17, 15)
        key_layout.setSpacing(9)
        key_head = QHBoxLayout()
        key_head.setSpacing(9)
        key_heading = QLabel("Service account key")
        key_heading.setObjectName("connCardHeading")
        key_head.addWidget(key_heading)
        key_head.addWidget(Pill("Recommended", tone="accent"))
        key_head.addStretch(1)
        key_layout.addLayout(key_head)
        key_body = QLabel(COPY_CHOOSE_KEY)
        key_body.setObjectName("connBody")
        key_body.setWordWrap(True)
        key_layout.addWidget(key_body)
        self.key_error_block = QWidget()
        self.key_error_block.setObjectName("connNotice")
        self.key_error_block.setProperty("tone", "danger")
        self.key_error_block.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True)
        key_error_layout = QVBoxLayout(self.key_error_block)
        key_error_layout.setContentsMargins(13, 11, 13, 11)
        key_error_layout.setSpacing(7)
        self.key_error_mono = QLabel("")
        self.key_error_mono.setObjectName("connDangerMono")
        self.key_error_mono.setWordWrap(True)
        self.key_error_plain = QLabel(
            "That file is an OAuth client configuration, not a key. Use it"
            " under Google sign-in below, or ask your administrator for a"
            " service-account key.")
        self.key_error_plain.setObjectName("connBody")
        self.key_error_plain.setWordWrap(True)
        key_error_layout.addWidget(self.key_error_mono)
        key_error_layout.addWidget(self.key_error_plain)
        self.key_error_block.hide()
        key_layout.addWidget(self.key_error_block)
        key_action = QHBoxLayout()
        key_action.setSpacing(9)
        self.key_button = QPushButton("Choose a key file…")
        self.key_button.setObjectName("primaryButton")
        self.key_button.setAutoDefault(False)
        self.key_button.clicked.connect(self._choose_key)
        key_note = QLabel("A .json file your administrator sends you.")
        key_note.setObjectName("helperText")
        key_action.addWidget(self.key_button)
        key_action.addWidget(key_note)
        key_action.addStretch(1)
        key_layout.addLayout(key_action)
        layout.addWidget(self.card_key)

        # Card B — Google sign-in
        self.card_signin = QWidget()
        self.card_signin.setObjectName("connCard")
        self.card_signin.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True)
        signin_layout = QVBoxLayout(self.card_signin)
        signin_layout.setContentsMargins(17, 15, 17, 15)
        signin_layout.setSpacing(9)
        signin_head = QHBoxLayout()
        signin_head.setSpacing(9)
        signin_heading = QLabel("Google sign-in")
        signin_heading.setObjectName("connCardHeading")
        signin_head.addWidget(signin_heading)
        signin_head.addWidget(Pill("Can expire in ~7 days", tone="warn"))
        signin_head.addStretch(1)
        signin_layout.addLayout(signin_head)
        signin_body = QLabel(COPY_CHOOSE_SIGNIN)
        signin_body.setObjectName("connBody")
        signin_body.setWordWrap(True)
        signin_layout.addWidget(signin_body)
        self.signin_error_label = QLabel("")
        self.signin_error_label.setObjectName("connDangerLine")
        self.signin_error_label.setWordWrap(True)
        self.signin_error_label.hide()
        signin_layout.addWidget(self.signin_error_label)
        signin_action = QHBoxLayout()
        signin_action.setSpacing(9)
        self.signin_button = QPushButton("Sign in with Google…")
        self.signin_button.setAutoDefault(False)
        self.signin_button.clicked.connect(self._choose_signin)
        signin_note = QLabel("Opens your browser.")
        signin_note.setObjectName("helperText")
        signin_action.addWidget(self.signin_button)
        signin_action.addWidget(signin_note)
        signin_action.addStretch(1)
        signin_layout.addLayout(signin_action)
        layout.addWidget(self.card_signin)

        self.either_way_label = QLabel("")
        self.either_way_label.setObjectName("helperText")
        self.either_way_label.setWordWrap(True)
        layout.addWidget(self.either_way_label)
        layout.addStretch(1)
        return page

    # -- navigation ------------------------------------------------------

    def _target_path(self) -> str:
        target = (f"gs://{self.bucket_edit.text().strip()}/"
                  f"{self.prefix_edit.text().strip()}")
        return target.rstrip("/")

    def _go_to_step(self, step: int) -> None:
        self._step = step
        self.step_rail.set_current(step)
        if step == 1:
            self._stack.setCurrentWidget(self.page_where)
            self.back_button.setEnabled(False)
            self.back_button.setText("Back")
            self.next_button.show()
            self.next_button.setDefault(True)
            self._update_next()
            self.name_edit.setFocus()
        elif step == 2:
            self._phase = "idle"
            self.name_error.hide()
            self._stack.setCurrentWidget(self.page_credential)
            self.back_button.setEnabled(True)
            self.back_button.setText("Back")
            self.next_button.hide()      # step 2 has no footer primary
            self.either_way_label.setText(
                "Either way, the service tests the credential against"
                f" {self._target_path()} before it saves anything.")
            self._apply_gate()

    def _on_back(self) -> None:
        if self._step == 2:
            self._go_to_step(1)

    def _update_next(self) -> None:
        ready = bool(self.name_edit.text().strip()) and bool(
            self.bucket_edit.text().strip())
        self.next_button.setEnabled(ready and self._step == 1)

    # -- health gate -----------------------------------------------------

    def _set_paths_enabled(self, enabled: bool) -> None:
        self.key_button.setEnabled(enabled)
        self.signin_button.setEnabled(enabled)

    def _apply_gate(self) -> None:
        gated = not self._health_ok
        self.gate_banner.setVisible(gated)
        for card in (self.card_key, self.card_signin):
            card.setProperty("state", "disabled" if gated else "")
            repolish(card)
        self._set_paths_enabled(not gated)

    def _service_ok(self, _result) -> None:
        self._health_ok = True
        self._apply_gate()

    def _service_down(self, _message) -> None:
        self._health_ok = False
        self.status_label.setText(COPY_SERVICE_FIRST)
        self._apply_gate()

    def _check_again(self) -> None:
        self.status_label.setText("Checking the transfer service…")
        call_async(self.client.health, parent=self,
                   on_done=self._service_ok, on_failed=self._service_down)

    def _open_main_window(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        top = parent.window()
        top.show()
        top.raise_()
        top.activateWindow()

    # -- credential paths (Task 5) ---------------------------------------

    def _choose_key(self):
        pass

    def _choose_signin(self):
        pass
```

Imports to add at the top of `connection_dialogs.py`: `from PySide6.QtCore import Qt`; `QStackedWidget`, `QScrollArea`, `QWidget` in the QtWidgets import; `from mml_cloud_courier.gui.connection_widgets import (ConnectionCard, Dot, Pill, ProbeList, RingSpinner, SectionLabel, StepRail, repolish)`. Note `_apply_gate` keeps the cards **readable** while disabled — only the two action buttons are `setEnabled(False)`; body text dims via the `[state="disabled"]` QSS rules.

- [ ] **Step 4: Run to verify pass**

Run: `.venv\Scripts\python -m pytest tests/gui/test_connection_stepper.py tests/gui/test_connection_dialogs.py -q -o addopts=`
Expected: PASS — including the untouched pinned tests (`key_button` primary, health gating via `status_label`).

- [ ] **Step 5: Commit**

```bash
git add src/mml_cloud_courier/gui/connection_dialogs.py tests/gui/test_connection_stepper.py
git commit -m "feat: new-connection stepper shell - rail, step 1, health gate"
```

---

### Task 5: Step 2 credential flows — key file, wrong-file-type, Google sign-in

**Files:**
- Modify: `src/mml_cloud_courier/gui/connection_dialogs.py` (fill `_choose_key`, `_choose_signin`; add the sign-in page)
- Test: `tests/gui/test_connection_stepper.py` (append)

**Interfaces:**
- Consumes: Task 4 shell; `load_key_file`, `key_profile_payload`, `oauth_profile_payload`, `load_client_config`, `run_login` (already imported at module top).
- Produces: `.page_signin` attr; `._start_create(payload: dict) -> None` **stub** that Task 6 replaces with the real create flow (in this task it only records `self._pending_payload = payload` and sets `self._phase = "validating"`); `._signed_in(credential)`; `._login_generation` discard mechanics; `.signin_cancel_note` attr.

- [ ] **Step 1: Write the failing tests** (append to `tests/gui/test_connection_stepper.py`):

```python
def _to_step2(qtbot, dialog, name="n", bucket="b", prefix=""):
    wait_health(qtbot, dialog)
    dialog.name_edit.setText(name)
    dialog.bucket_edit.setText(bucket)
    if prefix:
        dialog.prefix_edit.setText(prefix)
    dialog.next_button.click()
    assert dialog._step == 2


def test_wrong_file_type_stays_on_step2_and_points_at_signin(qtbot, tmp_path, monkeypatch):
    import json
    bad = tmp_path / "client_secret_884213.json"
    bad.write_text(json.dumps({"type": None, "installed": {}}))
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(bad), "")))
    dialog = NewConnectionDialog(HealthyClient())
    qtbot.addWidget(dialog)
    _to_step2(qtbot, dialog)
    dialog.key_button.click()
    assert dialog.key_error_block.isVisibleTo(dialog)
    assert str(bad) in dialog.key_error_mono.text()          # raw exception, full path
    assert "OAuth client configuration" in dialog.key_error_plain.text()
    assert dialog.key_button.text() == "Choose a different file…"
    assert dialog.key_button.isEnabled()
    assert dialog.signin_button.isEnabled()                  # the other card stays live


def test_good_key_starts_create_with_key_payload(qtbot, tmp_path, monkeypatch):
    import json
    good = tmp_path / "key.json"
    good.write_text(json.dumps({"type": "service_account", "project_id": "p1"}))
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(good), "")))
    dialog = NewConnectionDialog(HealthyClient())
    qtbot.addWidget(dialog)
    _to_step2(qtbot, dialog, name="MML imagery", bucket="bkt", prefix="2026")
    dialog.key_button.click()
    assert dialog._phase == "validating"
    assert dialog._pending_payload["auth_type"] == "service_account_key"
    assert dialog._pending_payload["name"] == "MML imagery"
    assert dialog._key_path == str(good)


def test_signin_shows_waiting_page_then_feeds_oauth_payload(qtbot, tmp_path, monkeypatch):
    import json
    import threading
    from mml_cloud_courier.gui import connection_dialogs as mod
    config = tmp_path / "client.json"
    config.write_text(json.dumps({"installed": {"client_id": "x"}}))
    monkeypatch.setenv("MMLCC_OAUTH_CLIENT", str(config))
    monkeypatch.setattr(mod, "load_client_config", lambda source: {"ok": True})
    release = threading.Event()
    monkeypatch.setattr(
        mod, "run_login",
        lambda config, timeout_seconds=300: (release.wait(5),
                                             {"type": "authorized_user"})[1])
    dialog = NewConnectionDialog(HealthyClient())
    qtbot.addWidget(dialog)
    _to_step2(qtbot, dialog)
    dialog.signin_button.click()
    assert dialog._phase == "signing-in"
    assert dialog._stack.currentWidget() is dialog.page_signin
    assert not dialog.next_button.isVisibleTo(dialog)
    assert "Nothing is saved yet" in dialog.signin_cancel_note.text()
    release.set()
    qtbot.waitUntil(lambda: dialog._phase == "validating", timeout=5000)
    assert dialog._pending_payload["auth_type"] == "oauth_user"


def test_escape_during_signin_discards_the_result(qtbot, tmp_path, monkeypatch):
    import json
    import threading
    from mml_cloud_courier.gui import connection_dialogs as mod
    config = tmp_path / "client.json"
    config.write_text(json.dumps({"installed": {"client_id": "x"}}))
    monkeypatch.setenv("MMLCC_OAUTH_CLIENT", str(config))
    monkeypatch.setattr(mod, "load_client_config", lambda source: {"ok": True})
    release = threading.Event()
    monkeypatch.setattr(
        mod, "run_login",
        lambda config, timeout_seconds=300: (release.wait(5),
                                             {"type": "authorized_user"})[1])
    dialog = NewConnectionDialog(HealthyClient())
    qtbot.addWidget(dialog)
    _to_step2(qtbot, dialog)
    dialog.signin_button.click()
    generation = dialog._login_generation
    dialog.reject()                       # Escape path
    assert dialog._login_generation == generation + 1
    release.set()
    qtbot.wait(100)                       # late result must be discarded
    assert dialog._phase != "validating"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python -m pytest tests/gui/test_connection_stepper.py -q -o addopts=`
Expected: new tests FAIL (`_choose_key` is a stub; no `page_signin`).

- [ ] **Step 3: Implement.** In `NewConnectionDialog.__init__`, after `page_credential` add:

```python
        self.page_signin = self._build_page_signin()
        self._stack.addWidget(self.page_signin)
```

Add the page builder and replace the two stubs + `reject`:

```python
    def _build_page_signin(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 30, 20, 15)
        layout.setSpacing(13)
        self.signin_spinner = RingSpinner()
        layout.addWidget(self.signin_spinner,
                         alignment=Qt.AlignmentFlag.AlignHCenter)
        waiting = QLabel("Waiting for you to finish signing in")
        waiting.setObjectName("connCardHeading")
        waiting.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(waiting)
        browser_line = QLabel(
            "A browser window opened. Sign in there and allow access; this"
            " dialog carries on by itself.")
        browser_line.setObjectName("connIntro")
        browser_line.setWordWrap(True)
        browser_line.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(browser_line)
        timeout_line = QLabel("gives up after 5 minutes")
        timeout_line.setObjectName("connFaintMono")
        timeout_line.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(timeout_line)
        note_card = QWidget()
        note_card.setObjectName("connCard")
        note_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        note_layout = QHBoxLayout(note_card)
        note_layout.setContentsMargins(15, 13, 15, 13)
        note_layout.setSpacing(9)
        note_layout.addWidget(Dot(tone="warn"),
                              alignment=Qt.AlignmentFlag.AlignTop)
        self.signin_cancel_note = QLabel(
            "Nothing is saved yet. After sign-in the service still tests this"
            " credential against the bucket, and will refuse it if it cannot"
            " do everything a transfer needs.")
        self.signin_cancel_note.setObjectName("connBody")
        self.signin_cancel_note.setWordWrap(True)
        note_layout.addWidget(self.signin_cancel_note, 1)
        layout.addWidget(note_card)
        layout.addStretch(1)
        return page

    def _fields(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "bucket": self.bucket_edit.text().strip(),
            "prefix": self.prefix_edit.text().strip(),
            "project": self.project_edit.text().strip(),
        }

    # -- service-account key path ----------------------------------------

    def _choose_key(self):
        path, _filter = QFileDialog.getOpenFileName(
            self, "Choose a service account key",
            filter="OAuth/service-account JSON (*.json)",
        )
        if not path:
            return
        try:
            key = load_key_file(path)
        except ValueError as exc:
            self.key_error_mono.setText(str(exc))
            self.key_error_block.show()
            self.key_button.setText("Choose a different file…")
            return
        self.key_error_block.hide()
        self._key_path = path
        fields = self._fields()
        self._start_create(key_profile_payload(
            name=fields["name"], bucket=fields["bucket"],
            prefix=fields["prefix"], project=fields["project"], key=key))

    # -- Google sign-in path ----------------------------------------------

    def _choose_signin(self):
        source = os.environ.get("MMLCC_OAUTH_CLIENT")
        if not source:
            path, _filter = QFileDialog.getOpenFileName(
                self, "Choose the OAuth client configuration",
                filter="OAuth client JSON (*.json)",
            )
            if not path:
                return
            source = path
        try:
            config = load_client_config(source)
        except ValueError as exc:
            self.signin_error_label.setText(str(exc))
            self.signin_error_label.show()
            return
        self.signin_error_label.hide()
        self._key_path = None
        self._phase = "signing-in"
        self._login_generation += 1
        generation = self._login_generation
        self._stack.setCurrentWidget(self.page_signin)
        self.signin_spinner.start()
        self.back_button.setEnabled(False)
        self.next_button.hide()
        call_async(lambda: run_login(config, timeout_seconds=300), parent=self,
                   on_done=lambda cred: self._signed_in(cred, generation),
                   on_failed=lambda msg: self._signin_failed(msg, generation))

    def _signed_in(self, credential, generation: int) -> None:
        if generation != self._login_generation:
            return                          # cancelled; discard the late result
        self.signin_spinner.stop()
        fields = self._fields()
        self._start_create(oauth_profile_payload(
            name=fields["name"], bucket=fields["bucket"],
            prefix=fields["prefix"], project=fields["project"],
            credential=credential))

    def _signin_failed(self, message: str, generation: int) -> None:
        if generation != self._login_generation:
            return
        self.signin_spinner.stop()
        self._go_to_step(2)
        self.signin_error_label.setText(message)
        self.signin_error_label.show()

    # -- create (real flow lands in Task 6) -------------------------------

    def _start_create(self, payload: dict) -> None:
        self._pending_payload = payload
        self._phase = "validating"

    def reject(self) -> None:
        # Escape during sign-in must abandon the pending run_login: there is
        # no server-side cancel hook (flow.run_local_server blocks), so the
        # generation bump makes the eventual result a no-op and the local
        # listener times out on its own.
        self._login_generation += 1
        self.signin_spinner.stop()
        super().reject()
```

(`QFileDialog` is already imported; `RingSpinner` was imported in Task 4.)

- [ ] **Step 4: Run to verify pass**

Run: `.venv\Scripts\python -m pytest tests/gui/test_connection_stepper.py tests/gui/test_connection_dialogs.py -q -o addopts=`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mml_cloud_courier/gui/connection_dialogs.py tests/gui/test_connection_stepper.py
git commit -m "feat: stepper step 2 - key file, wrong-file-type recovery, sign-in flow"
```

---

### Task 6: Step 3 — validating, verified, failed, duplicate-name routing

**Files:**
- Modify: `src/mml_cloud_courier/gui/connection_dialogs.py`
- Test: `tests/gui/test_connection_stepper.py` (append)

**Interfaces:**
- Consumes: Tasks 4–5; Task 2 `ProbeList`; Task 1 `split_service_error`.
- Produces: `.page_validating`, `.page_verified`, `.page_failed`, `.probe_list` (ProbeList), `.done_button`, `.retry_button`, `.copy_summary_button`, `.check_bucket_button`, `.verified_title`, `.verified_summary`, `.verified_notice` (QWidget), `.verified_key_path` (QLabel), `.failed_summary`, `.failed_chips_host` (QWidget); real `._start_create`; `created` emitted with the create response.

- [ ] **Step 1: Write the failing tests** (append to `tests/gui/test_connection_stepper.py`):

```python
class CreateClient(HealthyClient):
    """create_profile blocks until released, then returns/raises."""

    def __init__(self):
        import threading
        self.release = threading.Event()
        self.result: dict | Exception = {}
        self.payloads: list[dict] = []

    def create_profile(self, payload):
        self.payloads.append(payload)
        self.release.wait(5)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _choose_good_key(qtbot, dialog, tmp_path, monkeypatch,
                     name="MML imagery", bucket="bkt", prefix="2026"):
    import json
    good = tmp_path / "key.json"
    good.write_text(json.dumps({"type": "service_account", "project_id": "p"}))
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(good), "")))
    _to_step2(qtbot, dialog, name=name, bucket=bucket, prefix=prefix)
    dialog.key_button.click()
    return str(good)


def test_validating_page_paces_probes_and_shows_target(qtbot, tmp_path, monkeypatch):
    client = CreateClient()
    dialog = NewConnectionDialog(client)
    qtbot.addWidget(dialog)
    _choose_good_key(qtbot, dialog, tmp_path, monkeypatch)
    assert dialog._stack.currentWidget() is dialog.page_validating
    assert dialog.step_rail.current == 3
    assert dialog.probe_list.states()[0] == "running"
    assert "gs://bkt/2026" in dialog.validating_target.text()
    client.result = {"id": 9, "name": "MML imagery", "summary": "s"}
    client.release.set()
    qtbot.waitUntil(lambda: dialog._phase == "verified", timeout=5000)


def test_verified_key_creation_shows_notice_and_path(qtbot, tmp_path, monkeypatch):
    from mml_cloud_courier.gui.connection_dialogs import COPY_DELETE_ORIGINAL
    client = CreateClient()
    client.result = {
        "id": 9, "name": "MML imagery",
        "summary": "This credential can list, read, write, compose and"
                   " delete to gs://bkt/2026.",
    }
    client.release.set()          # create returns immediately
    dialog = NewConnectionDialog(client)
    qtbot.addWidget(dialog)
    created = []
    dialog.created.connect(created.append)
    key_path = _choose_good_key(qtbot, dialog, tmp_path, monkeypatch)
    qtbot.waitUntil(lambda: dialog._phase == "verified", timeout=5000)
    assert dialog.verified_title.text() == "MML imagery is ready to use"
    assert "can list, read, write" in dialog.verified_summary.text()
    assert dialog.verified_notice.isVisibleTo(dialog)
    assert COPY_DELETE_ORIGINAL in dialog.verified_notice_text.text()
    assert dialog.verified_key_path.text() == key_path
    assert dialog.back_button.text() == "Add another"
    assert dialog.done_button.isVisibleTo(dialog)
    assert created and created[0]["id"] == 9


def test_verified_oauth_creation_hides_the_delete_notice(qtbot, tmp_path, monkeypatch):
    import json
    from mml_cloud_courier.gui import connection_dialogs as mod
    client = CreateClient()
    client.result = {"id": 3, "name": "PAM", "summary": "s"}
    client.release.set()
    config = tmp_path / "client.json"
    config.write_text(json.dumps({"installed": {}}))
    monkeypatch.setenv("MMLCC_OAUTH_CLIENT", str(config))
    monkeypatch.setattr(mod, "load_client_config", lambda source: {})
    monkeypatch.setattr(mod, "run_login",
                        lambda config, timeout_seconds=300: {"type": "authorized_user"})
    dialog = NewConnectionDialog(client)
    qtbot.addWidget(dialog)
    _to_step2(qtbot, dialog, name="PAM", bucket="b")
    dialog.signin_button.click()
    qtbot.waitUntil(lambda: dialog._phase == "verified", timeout=5000)
    assert not dialog.verified_notice.isVisibleTo(dialog)


def test_preflight_400_shows_failure_with_chips_and_recovery(qtbot, tmp_path, monkeypatch):
    from mml_cloud_courier.cli.service_client import ServiceError
    client = CreateClient()
    client.result = ServiceError(
        400, "This credential cannot access gs://bkt/2026 at all.")
    client.release.set()
    dialog = NewConnectionDialog(client)
    qtbot.addWidget(dialog)
    _choose_good_key(qtbot, dialog, tmp_path, monkeypatch)
    qtbot.waitUntil(lambda: dialog._phase == "failed", timeout=5000)
    assert "cannot access gs://bkt/2026" in dialog.failed_summary.text()
    assert dialog.failed_chips_host.isVisibleTo(dialog)
    assert dialog.retry_button.isVisibleTo(dialog)
    # Try another credential returns to step 2 with fields intact
    dialog.retry_button.click()
    assert dialog._step == 2
    assert dialog.name_edit.text() == "MML imagery"


def test_before_bucket_rejection_hides_chips(qtbot, tmp_path, monkeypatch):
    from mml_cloud_courier.cli.service_client import ServiceError
    client = CreateClient()
    client.result = ServiceError(
        400, "credential rejected before reaching the bucket: bad key")
    client.release.set()
    dialog = NewConnectionDialog(client)
    qtbot.addWidget(dialog)
    _choose_good_key(qtbot, dialog, tmp_path, monkeypatch)
    qtbot.waitUntil(lambda: dialog._phase == "failed", timeout=5000)
    assert not dialog.failed_chips_host.isVisibleTo(dialog)
    assert "before reaching the bucket" in dialog.failed_summary.text()


def test_duplicate_name_routes_to_step1_name_field(qtbot, tmp_path, monkeypatch):
    from mml_cloud_courier.cli.service_client import ServiceError
    client = CreateClient()
    client.result = ServiceError(409, "a profile named 'MML imagery' already exists")
    client.release.set()
    dialog = NewConnectionDialog(client)
    qtbot.addWidget(dialog)
    _choose_good_key(qtbot, dialog, tmp_path, monkeypatch)
    qtbot.waitUntil(lambda: dialog._step == 1, timeout=5000)
    assert dialog.name_error.isVisibleTo(dialog)
    assert "already exists" in dialog.name_error.text()
    assert dialog.name_edit.text() == "MML imagery"     # fields survive


def test_add_another_resets_to_pristine_step1(qtbot, tmp_path, monkeypatch):
    client = CreateClient()
    client.result = {"id": 9, "name": "MML imagery", "summary": "s"}
    client.release.set()
    dialog = NewConnectionDialog(client)
    qtbot.addWidget(dialog)
    _choose_good_key(qtbot, dialog, tmp_path, monkeypatch)
    qtbot.waitUntil(lambda: dialog._phase == "verified", timeout=5000)
    dialog.back_button.click()          # "Add another"
    assert dialog._step == 1
    assert dialog.name_edit.text() == ""
    assert dialog.bucket_edit.text() == ""
    assert dialog._phase == "idle"
```

Note in the failure-navigation test: after `retry_button` the dialog is on step 2; clicking `next_button` is hidden on step 2, so drop those last two lines when implementing if they prove wrong — the assertions that matter are retry → step 2 with fields intact. `check_bucket_button` gets its own assertion instead:

```python
def test_check_the_bucket_name_returns_to_step1_bucket_focused(qtbot, tmp_path, monkeypatch):
    from mml_cloud_courier.cli.service_client import ServiceError
    client = CreateClient()
    client.result = ServiceError(
        400, "This credential cannot access gs://bkt/2026 at all.")
    client.release.set()
    dialog = NewConnectionDialog(client)
    qtbot.addWidget(dialog)
    _choose_good_key(qtbot, dialog, tmp_path, monkeypatch)
    qtbot.waitUntil(lambda: dialog._phase == "failed", timeout=5000)
    dialog.check_bucket_button.click()
    assert dialog._step == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python -m pytest tests/gui/test_connection_stepper.py -q -o addopts=`
Expected: new tests FAIL (no `page_validating`).

- [ ] **Step 3: Implement.** Add pages in `__init__` after `page_signin`:

```python
        self.page_validating = self._build_page_validating()
        self.page_verified = self._build_page_verified()
        self.page_failed = self._build_page_failed()
        for page in (self.page_validating, self.page_verified, self.page_failed):
            self._stack.addWidget(page)
```

Add to the footer (in `__init__`, after `next_button`):

```python
        self.done_button = QPushButton("Done")
        self.done_button.setObjectName("primaryButton")
        self.done_button.clicked.connect(self.accept)
        self.retry_button = QPushButton("Try another credential")
        self.retry_button.setObjectName("primaryButton")
        self.retry_button.clicked.connect(lambda: self._go_to_step(2))
        self.done_button.hide()
        self.retry_button.hide()
        footer_layout.addWidget(self.done_button)
        footer_layout.addWidget(self.retry_button)
```

Page builders and flow:

```python
    def _build_page_validating(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(11)
        title = QLabel("Testing this credential against the bucket")
        title.setObjectName("connCardHeading")
        layout.addWidget(title)
        explainer = QLabel(
            "The service writes a small object, composes it, reads it back"
            " and deletes it. An upload needs all five; finding a gap now"
            " beats finding it overnight.")
        explainer.setObjectName("connIntro")
        explainer.setWordWrap(True)
        layout.addWidget(explainer)
        self.probe_list = ProbeList()
        layout.addWidget(self.probe_list)
        self.validating_target = QLabel("")
        self.validating_target.setObjectName("connFaintMono")
        layout.addWidget(self.validating_target)
        layout.addStretch(1)
        return page

    def _build_page_verified(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(11)
        title_row = QHBoxLayout()
        title_row.setSpacing(11)
        self._verified_circle = _BigCircle(kind="check")
        title_row.addWidget(self._verified_circle)
        self.verified_title = QLabel("")
        self.verified_title.setObjectName("connTitle")
        title_row.addWidget(self.verified_title, 1)
        layout.addLayout(title_row)
        found_card = QWidget()
        found_card.setObjectName("connCard")
        found_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        found_layout = QVBoxLayout(found_card)
        found_layout.setContentsMargins(17, 15, 17, 15)
        found_layout.setSpacing(9)
        found_layout.addWidget(SectionLabel("What the service found"))
        self.verified_summary = QLabel("")
        self.verified_summary.setObjectName("connBody")
        self.verified_summary.setWordWrap(True)
        found_layout.addWidget(self.verified_summary)
        chips_row = QHBoxLayout()
        chips_row.setSpacing(7)
        for capability in ("list", "read", "write", "compose", "delete"):
            chip = QLabel(capability)
            chip.setObjectName("connChip")
            chips_row.addWidget(chip)
        chips_row.addStretch(1)
        found_layout.addLayout(chips_row)
        layout.addWidget(found_card)
        self.verified_notice = QWidget()
        self.verified_notice.setObjectName("connNotice")
        self.verified_notice.setProperty("tone", "accent")
        self.verified_notice.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True)
        notice_layout = QHBoxLayout(self.verified_notice)
        notice_layout.setContentsMargins(15, 13, 15, 13)
        notice_layout.setSpacing(9)
        notice_layout.addWidget(Dot(tone="accent"),
                                alignment=Qt.AlignmentFlag.AlignTop)
        self.verified_notice_text = QLabel(COPY_DELETE_ORIGINAL)
        self.verified_notice_text.setObjectName("connNoticeText")
        self.verified_notice_text.setProperty("tone", "accent")
        self.verified_notice_text.setWordWrap(True)
        notice_layout.addWidget(self.verified_notice_text, 1)
        layout.addWidget(self.verified_notice)
        self.verified_key_path = QLabel("")
        self.verified_key_path.setObjectName("connFaintMono")
        self.verified_key_path.setWordWrap(True)
        layout.addWidget(self.verified_key_path)
        layout.addStretch(1)
        return page

    def _build_page_failed(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(11)
        title_row = QHBoxLayout()
        title_row.setSpacing(11)
        title_row.addWidget(_BigCircle(kind="bang"))
        failed_title = QLabel("This credential cannot reach that bucket")
        failed_title.setObjectName("connTitle")
        title_row.addWidget(failed_title, 1)
        layout.addLayout(title_row)
        found_card = QWidget()
        found_card.setObjectName("connCard")
        found_card.setProperty("edge", "danger")
        found_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        found_layout = QVBoxLayout(found_card)
        found_layout.setContentsMargins(17, 15, 17, 15)
        found_layout.setSpacing(9)
        found_layout.addWidget(SectionLabel("What the service found"))
        self.failed_summary = QLabel("")
        self.failed_summary.setObjectName("connBody")
        self.failed_summary.setWordWrap(True)
        found_layout.addWidget(self.failed_summary)
        self.failed_chips_host = QWidget()
        chips_row = QHBoxLayout(self.failed_chips_host)
        chips_row.setContentsMargins(0, 0, 0, 0)
        chips_row.setSpacing(7)
        for capability in ("list", "read", "write", "compose", "delete"):
            chip = QLabel(f"\u2715 {capability}")
            chip.setObjectName("connChip")
            chip.setProperty("tone", "danger")
            chips_row.addWidget(chip)
        chips_row.addStretch(1)
        found_layout.addWidget(self.failed_chips_host)
        layout.addWidget(found_card)
        recovery_card = QWidget()
        recovery_card.setObjectName("connCard")
        recovery_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        recovery_layout = QVBoxLayout(recovery_card)
        recovery_layout.setContentsMargins(17, 15, 17, 15)
        recovery_layout.setSpacing(9)
        recovery_a = QLabel(
            "The key is valid, but it has no access to this bucket. Either"
            " the bucket name is wrong, or nobody has granted this service"
            " account object access to it. Nothing was saved.")
        recovery_b = QLabel(
            "Send your administrator the line above and ask for object access"
            " to this one bucket, nothing more.")
        for label in (recovery_a, recovery_b):
            label.setObjectName("connBody")
            label.setWordWrap(True)
            recovery_layout.addWidget(label)
        recovery_buttons = QHBoxLayout()
        recovery_buttons.setSpacing(9)
        self.copy_summary_button = QPushButton("Copy this summary")
        self.copy_summary_button.clicked.connect(self._copy_summary)
        self.check_bucket_button = QPushButton("Check the bucket name")
        self.check_bucket_button.clicked.connect(self._check_bucket_name)
        for button in (self.copy_summary_button, self.check_bucket_button):
            button.setAutoDefault(False)
        recovery_buttons.addWidget(self.copy_summary_button)
        recovery_buttons.addWidget(self.check_bucket_button)
        recovery_buttons.addStretch(1)
        recovery_layout.addLayout(recovery_buttons)
        layout.addWidget(recovery_card)
        layout.addStretch(1)
        return page

    def _copy_summary(self) -> None:
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(self.failed_summary.text())

    def _check_bucket_name(self) -> None:
        self._go_to_step(1)
        self.bucket_edit.setFocus()

    # -- the one create call: pending / 200 / 400 -------------------------

    def _start_create(self, payload: dict) -> None:
        self._pending_payload = payload
        self._phase = "validating"
        self._step = 3
        self.step_rail.set_current(3)
        self._stack.setCurrentWidget(self.page_validating)
        self.validating_target.setText(self._target_path())
        self.back_button.setEnabled(False)
        self.next_button.hide()
        self.done_button.hide()
        self.retry_button.hide()
        self.probe_list.start()
        call_async(lambda: self.client.create_profile(payload), parent=self,
                   on_done=self._profile_created, on_failed=self._flow_failed)

    def _profile_created(self, result) -> None:
        self.probe_list.finish_all()
        self._phase = "verified"
        self.verified_title.setText(f"{result.get('name', '')} is ready to use")
        self.verified_summary.setText(result.get("summary", ""))
        is_key = self._key_path is not None
        self.verified_notice.setVisible(is_key)
        self.verified_key_path.setVisible(is_key)
        self.verified_key_path.setText(self._key_path or "")
        self._stack.setCurrentWidget(self.page_verified)
        self.back_button.setEnabled(True)
        self.back_button.setText("Add another")
        try:
            self.back_button.clicked.disconnect(self._on_back)
        except RuntimeError:
            pass
        self.back_button.clicked.connect(self._add_another)
        self.done_button.show()
        self.done_button.setDefault(True)
        self.created.emit(result)

    def _add_another(self) -> None:
        try:
            self.back_button.clicked.disconnect(self._add_another)
        except RuntimeError:
            pass
        self.back_button.clicked.connect(self._on_back)
        self._credential = None
        self._key_path = None
        self._phase = "idle"
        self.probe_list.reset()
        for edit in (self.name_edit, self.bucket_edit, self.prefix_edit,
                     self.project_edit):
            edit.clear()
        self.key_button.setText("Choose a key file…")
        self.key_error_block.hide()
        self.done_button.hide()
        self._go_to_step(1)

    def _flow_failed(self, message: str) -> None:
        self.probe_list.stop()
        code, detail = split_service_error(message)
        if code == 409 and "already exists" in detail:
            self._phase = "idle"
            self._go_to_step(1)
            self.name_error.setText(detail)
            self.name_error.show()
            self.name_edit.setFocus()
            return
        self._phase = "failed"
        self._step = 3
        self.step_rail.set_current(3)
        self.failed_summary.setText(detail)
        before_bucket = detail.startswith(
            "credential rejected before reaching the bucket")
        self.failed_chips_host.setVisible(code == 400 and not before_bucket)
        self._stack.setCurrentWidget(self.page_failed)
        self.back_button.setEnabled(False)
        self.next_button.hide()
        self.done_button.hide()
        self.retry_button.show()
        self.retry_button.setDefault(True)
```

Also update `_go_to_step` (Task 4's version) to hide `done_button`/`retry_button` on entry to steps 1 and 2 (add `self.done_button.hide()` and `self.retry_button.hide()` guarded by `hasattr` OR — simpler — build the footer buttons before `_go_to_step(1)` runs, which the `__init__` order above already guarantees; just add the two `hide()` calls to both branches).

Add `_BigCircle` to `connection_widgets.py` (import it in `connection_dialogs.py`):

```python
class _BigCircle(QWidget):
    """24px filled circle: accent+check for verified, danger+'!' for failed."""

    def __init__(self, kind: str = "check", parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(24, 24)
        theme.notifier.changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _t) -> None:
        self.update()

    def paintEvent(self, _event) -> None:
        t = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        tone = t.accent if self.kind == "check" else t.danger
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme._qcolor(tone))
        painter.drawEllipse(0, 0, 24, 24)
        painter.setPen(QPen(theme._qcolor(t.accent_ink), 2))
        if self.kind == "check":
            painter.drawLine(6, 12, 10, 16)
            painter.drawLine(10, 16, 18, 8)
        else:
            painter.drawLine(12, 6, 12, 14)
            painter.drawLine(12, 17, 12, 19)
```

(`accent_ink` is white-on-accent in light and near-black in dark by design; on the danger circle it matches the handoff's glyph treatment.)

- [ ] **Step 4: Run to verify pass**

Run: `.venv\Scripts\python -m pytest tests/gui/test_connection_stepper.py tests/gui/test_connection_dialogs.py tests/gui/test_connection_manager.py -q -o addopts=`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mml_cloud_courier/gui/connection_dialogs.py src/mml_cloud_courier/gui/connection_widgets.py tests/gui/test_connection_stepper.py
git commit -m "feat: stepper step 3 - paced validation, verified/failed states, 409 routing"
```

---

### Task 7: Rail profile filter ("Show those N jobs")

**Files:**
- Modify: `src/mml_cloud_courier/gui/main_window.py`
- Test: `tests/gui/test_rail_filter.py` (new)

**Interfaces:**
- Consumes: Task 3 `ConnectionsDialog.showJobsForProfile` Signal(int, str).
- Produces: `MainWindow.show_jobs_for_profile(profile_id: int, name: str)`, `MainWindow.clear_profile_filter()`, `.filter_bar` (QWidget), `.filter_label` (QLabel), `.show_all_button`, `._profile_filter: int | None`.

Reference points in `main_window.py` (verify line numbers on the worktree before editing): rail built around lines 136–164; `splitter.addWidget(self.rail_view)` ~line 186; `_on_jobs` ~line 371; `_on_down` re-sync ~line 394–395; `_open_connections` ~line 551.

- [ ] **Step 1: Write the failing tests** (`tests/gui/test_rail_filter.py`):

```python
"""Rail profile filter: client-side, jobs already carry profile_id.
The filter bar appears above the rail; polls preserve the filter; the
first-run gate keeps consulting the unfiltered list."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from mml_cloud_courier.gui.main_window import MainWindow
from mml_cloud_courier.gui.session import discover_session


@pytest.fixture
def window(qtbot, gui_host):
    win = MainWindow(discover_session(), poll_interval=60)
    qtbot.addWidget(win)
    yield win
    win.shutdown()


def _job(job_id, profile_id, status="completed", name=None):
    return {"id": job_id, "name": name or f"job-{job_id}", "status": status,
            "direction": "upload", "profile_id": profile_id, "progress": {}}
    # If sync_rail KeyErrors on a missing field, extend this dict with that
    # field (check _rail_signature in gui/jobs_model.py) — do not weaken the
    # assertions below.


JOBS = [_job(1, 10), _job(2, 10), _job(3, 20)]


@pytest.mark.gui
def test_filter_limits_rail_and_shows_bar(qtbot, window):
    window._on_jobs(JOBS)
    assert sorted(window.rail_job_ids()) == [1, 2, 3]
    window.show_jobs_for_profile(10, "MML imagery")
    assert sorted(window.rail_job_ids()) == [1, 2]
    assert window.filter_bar.isVisibleTo(window)
    assert "MML imagery" in window.filter_label.text()


@pytest.mark.gui
def test_poll_preserves_filter_and_show_all_clears(qtbot, window):
    window._on_jobs(JOBS)
    window.show_jobs_for_profile(20, "PAM archive")
    window._on_jobs(JOBS)                      # next poll tick
    assert window.rail_job_ids() == [3]
    window.show_all_button.click()
    assert sorted(window.rail_job_ids()) == [1, 2, 3]
    assert not window.filter_bar.isVisibleTo(window)


@pytest.mark.gui
def test_filtered_out_selection_clears(qtbot, window):
    window._on_jobs(JOBS)
    window.select_job(3)
    qtbot.waitUntil(lambda: window.selected_job_id == 3, timeout=5000)
    window.show_jobs_for_profile(10, "MML imagery")
    assert window.selected_job_id is None


@pytest.mark.gui
def test_first_run_gate_uses_unfiltered_jobs(qtbot, window):
    window._no_connections = True
    window._on_jobs(JOBS)
    window.show_jobs_for_profile(99, "gone")   # filters out every job
    # jobs exist, so first-run must NOT take over even though the rail is empty
    assert window._content_stack.currentWidget() is not window._first_run
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python -m pytest tests/gui/test_rail_filter.py -q -o addopts=`
Expected: FAIL (no `show_jobs_for_profile`).

- [ ] **Step 3: Implement in `main_window.py`.**

In `__init__`, next to the existing rail construction (after `self.rail_view` setup, before the splitter), build the bar and a rail column container; then put the **container** in the splitter instead of `rail_view`:

```python
        self._profile_filter: int | None = None
        self.filter_bar = QWidget()
        self.filter_bar.setObjectName("connFilterBar")
        self.filter_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        filter_layout = QHBoxLayout(self.filter_bar)
        filter_layout.setContentsMargins(11, 6, 11, 6)
        filter_layout.setSpacing(9)
        self.filter_label = QLabel("")
        self.filter_label.setWordWrap(True)
        filter_layout.addWidget(self.filter_label, 1)
        self.show_all_button = QPushButton("Show all")
        self.show_all_button.setObjectName("textButton")
        self.show_all_button.clicked.connect(self.clear_profile_filter)
        filter_layout.addWidget(self.show_all_button)
        self.filter_bar.hide()

        rail_column = QWidget()
        rail_column_layout = QVBoxLayout(rail_column)
        rail_column_layout.setContentsMargins(0, 0, 0, 0)
        rail_column_layout.setSpacing(0)
        rail_column_layout.addWidget(self.filter_bar)
        rail_column_layout.addWidget(self.rail_view, 1)
        rail_column.setFixedWidth(262)
```

Change `self.rail_view.setFixedWidth(262)` to live on the container as above (remove the old call), and `splitter.addWidget(self.rail_view)` → `splitter.addWidget(rail_column)`.

Add the filter methods and thread the filter through both sync paths:

```python
    def _filtered_jobs(self, jobs: list[dict]) -> list[dict]:
        if self._profile_filter is None:
            return jobs
        return [job for job in jobs
                if job.get("profile_id") == self._profile_filter]

    def show_jobs_for_profile(self, profile_id: int, name: str) -> None:
        self._profile_filter = profile_id
        self.filter_label.setText(f'Showing jobs using "{name}"')
        self.filter_bar.show()
        filtered_ids = {job["id"] for job in self._filtered_jobs(self._last_jobs)}
        if self._selected_job_id is not None and self._selected_job_id not in filtered_ids:
            self.rail_view.selectionModel().clearSelection()
            self._selected_job_id = None
            self._selected_status = None
            self._update_action_states()
        self._sync_rail_preserving_expansion(
            self._filtered_jobs(self._last_jobs), service_up=self._service_up)

    def clear_profile_filter(self) -> None:
        self._profile_filter = None
        self.filter_bar.hide()
        self._sync_rail_preserving_expansion(
            self._last_jobs, service_up=self._service_up)
```

In `_on_jobs`, change the sync line to
`self._sync_rail_preserving_expansion(self._filtered_jobs(jobs), service_up=self._service_up)`
(`self._last_jobs = jobs` stays the FULL list, so `_update_first_run` keeps consulting unfiltered jobs — no change needed there). In `_on_down`, change the re-sync to `self._sync_rail_preserving_expansion(self._filtered_jobs(self._last_jobs), service_up=False)`.

Wire the dialog in `_open_connections`:

```python
    def _open_connections(self) -> None:
        dialog = ConnectionsDialog(self.client, self)
        dialog.showJobsForProfile.connect(self.show_jobs_for_profile)
        dialog.exec()
        call_async(self.client.list_profiles, parent=self, on_done=self._on_profiles)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv\Scripts\python -m pytest tests/gui/test_rail_filter.py tests/gui/test_main_window_smoke.py -q -o addopts=`
Expected: PASS (smoke test proves the rail container swap broke nothing).

- [ ] **Step 5: Commit**

```bash
git add src/mml_cloud_courier/gui/main_window.py tests/gui/test_rail_filter.py
git commit -m "feat: rail profile filter behind the delete-refusal route"
```

---

### Task 8: Transfer-dialog helper text (A/B outcome)

**Files:**
- Modify: `src/mml_cloud_courier/gui/wizard.py`
- Test: `tests/gui/test_wizard.py` (append)

**Interfaces:**
- Consumes: nothing new. Strings are verbatim from the Option B exploration (spec §7).
- Produces: `NewTransferWizard.prefix_helper`, `.name_helper` (QLabels, objectName `helperText`).

- [ ] **Step 1: Write the failing test** (append to `tests/gui/test_wizard.py`, using that file's existing client-fixture pattern for constructing the wizard — reuse whatever fixture the neighboring tests use):

```python
def test_helper_text_explains_prefix_and_job_name(qtbot, wizard):
    # A/B decision: one screen keeps Option B's per-step explanations as
    # helper text (spec §7) — exact strings, not paraphrases.
    assert wizard.prefix_helper.text() == (
        "A connection is a bucket and the credential the service uses."
        " The prefix is the folder inside it.")
    assert wizard.name_helper.text() == (
        "Anything already in the bucket and unchanged is skipped, so"
        " nothing is sent twice.")
    assert wizard.prefix_helper.objectName() == "helperText"
    assert wizard.name_helper.objectName() == "helperText"
```

If `test_wizard.py` has no reusable `wizard` fixture, construct inline exactly as its first test does (same fake client), and adjust the signature accordingly.

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python -m pytest tests/gui/test_wizard.py -q -o addopts=`
Expected: the new test FAILS (no `prefix_helper`); all existing wizard tests PASS.

- [ ] **Step 3: Implement in `wizard.py`.** After `layout.addWidget(self.prefix_edit)` (~line 223):

```python
        self.prefix_helper = QLabel(
            "A connection is a bucket and the credential the service uses."
            " The prefix is the folder inside it.")
        self.prefix_helper.setObjectName("helperText")
        self.prefix_helper.setWordWrap(True)
        layout.addWidget(self.prefix_helper)
```

After `layout.addLayout(form)` for the job-name row (~line 257):

```python
        self.name_helper = QLabel(
            "Anything already in the bucket and unchanged is skipped, so"
            " nothing is sent twice.")
        self.name_helper.setObjectName("helperText")
        self.name_helper.setWordWrap(True)
        layout.addWidget(self.name_helper)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv\Scripts\python -m pytest tests/gui/test_wizard.py -q -o addopts=`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mml_cloud_courier/gui/wizard.py tests/gui/test_wizard.py
git commit -m "feat: transfer-dialog helper text under Prefix and Job name"
```

---

### Task 9: Full-suite verification and recorded counts (no new code)

**Files:** none (fixes only if something is red).

- [ ] **Step 1: Full suite** — `.venv\Scripts\python -m pytest -o addopts= -q`. Record the exact counts. Expected: baseline 636 + all new tests passing, 13 (or 14) skipped, zero failures. If anything is red, fix it in this task with a focused commit per fix (`superpowers:systematic-debugging` first).

- [ ] **Step 2: Hex acceptance** — run the targeted test and a belt-and-braces grep:

```bash
.venv/Scripts/python -m pytest tests/gui/test_theme.py -q -o addopts=
grep -rnE "#[0-9a-fA-F]{6}\b" src/mml_cloud_courier/gui --include="*.py" | grep -v "theme.py"
```

Expected: test PASS; grep output empty.

- [ ] **Step 3: Contractual copy check** — `git diff master -- tests/gui/test_connection_dialogs.py` must show exactly one rewritten test (`test_connections_dialog_new_button_is_primary`) and no edit to `test_copy_follows_the_spec_and_the_gate_findings`; `git diff master -- src/mml_cloud_courier/gui/connection_dialogs.py | grep -E "^[-+]COPY_"` must be empty.

- [ ] **Step 4: Report** the recorded counts (never estimated) to the orchestrator for the merge decision.

---

## After Task 9 (orchestrator, not a dispatched task)

Manual smoke check with the user before merging (spec §12): launch the GUI **from the worktree venv** against the live service, read-only — open the manager, walk the stepper to step 2 in both themes, do not create/delete anything:

```powershell
.venv\Scripts\python -m mml_cloud_courier.gui
```

Then `superpowers:finishing-a-development-branch`: merge to master with `--no-ff`, push origin. Suite counts go in the merge commit message.

## Self-review notes (already applied)

- Spec coverage: §3 modules → Tasks 1–2; §4 manager → Task 3; §5 stepper → Tasks 4–6; §6 rail filter → Task 7; §7 wizard → Task 8; §§9–10 gap-fills and test contract → embedded in Tasks 3–6; §12 done criteria → Task 9 + orchestrator close-out.
- The four constants: no task touches them; Task 9 Step 3 proves it mechanically.
- Type consistency: `split_service_error` (Tasks 1/3/6), `ConnectionCard` API (Tasks 3), `StepRail.set_current`/`.current` (Tasks 2/4/6), `ProbeList.start/finish_all/stop/states` (Tasks 2/6), `showJobsForProfile(int, str)` → `show_jobs_for_profile(profile_id, name)` (Tasks 3/7) all match.
- Known judgment call for implementers: if `sync_rail` needs more job-dict keys than `_job()` provides in Task 7, extend the dict — never weaken assertions.
