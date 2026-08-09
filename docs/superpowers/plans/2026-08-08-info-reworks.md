# Information Reworks + One-Screen Wizard (Plan B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The four redesigned screens (Progress cards including "Every file, by state", Errors cause-cards, Summary, first-run) plus the one-screen wizard with a main-window drop target — Plan B of the GUI refresh, on top of the merged theme foundation.

**Architecture:** New focused widget modules (`progress_widgets.py`, `errors_view.py`, `first_run.py`) consume `theme.current()` at paint time and the poll payloads the tabs already receive — zero service/API changes. `wizard.py` is rewritten as one `QDialog` screen that preserves every public contract (`NewTransferWizard` name, signals, programmatic setters, `build_submission` payload byte-identical). `main_window.py` gains the first-run swap, the drop target, and the no-connections dimming the final Plan A review flagged for pickup.

**Tech Stack:** Python 3.12, PySide6 ≥ 6.7, pytest + pytest-qt. Base: master 762c7b4 (Plan A merged).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-08-gui-theme-and-refresh-design.md`. Appearance source: `docs/design/cloud-courier-theming/{README,DESIGN_TOKENS,RECOMMENDATIONS}.md` — codebase wins for behavior, handoff wins for appearance. Red reserved for failure; no fifth color family; no between-poll animation; no new endpoints; polling (`JobsPoller`/`JobWatcher`) unchanged.
- ALL colors via `theme.current()` / QSS object names — the Plan A acceptance test (`test_no_hex_colors_outside_theme_py`) stays green; never weaken it.
- State order everywhere (stacked bar, legends, summary rows) is FIXED, from `format.py` life-cycle order with these token mappings (DESIGN_TOKENS "State colors" table):
  `verified→accent, transferred→accent_2 ("Checking"), transferring→accent_3, pending→track ("Waiting"), skipped→skip, changed→warn, failed→danger, quarantined→danger (label in danger_text)`.
- Error-card tones (README screen 3): self-clearing categories `{"file_locked", "source_changed"}` tag `Retries on its own` in warn tones; `{"network"}` same tag in accent tones; every other category (`permission_denied, path_too_long, checksum_mismatch, quota, credential, not_found, conflict, unknown`) tags `Needs you` in danger tones. Cards order: needs-you first, then self-clearing; within each, count descending.
- Verbatim copy (em-dashes U+2014, exact punctuation):
  - Summary footer: `Excluded files stay recorded in the ledger. The job keeps the Incomplete verdict until they transfer or you stop retrying them.`
  - First-run heading: `Nothing has been transferred yet`
  - First-run body: `Courier needs one connection before it can move anything — a bucket, and a credential the service can use on its own. After that, every transfer is a folder and a Start.`
  - First-run steps (titles): `Add a connection` / `Point it at a folder` / `Close the window whenever you like`
  - Tags: `Needs you` / `Retries on its own`
  - Stat cell labels: `FILES` / `TRANSFERRED` / `DURATION` / `DID NOT TRANSFER`
- Sanctioned deviation from README prose: dynamic sentences use numerals, not number words (`from 4 causes`, not `from four causes`).
- Wizard contract preservation (tests depend on these): module `gui/wizard.py`, class `NewTransferWizard(client, parent)`, signals `jobSubmitted(int)` + `previewUpdated(int, int, int)`, attrs `state: WizardState`, `profile_id`, `profile_combo`, setters `set_direction/set_source/set_prefix/set_job_name`, method `accept_and_submit()`, pure helpers `build_submission` / `parse_duplicate_job_id` / `preview_scan` UNCHANGED, 409→resume flow unchanged, mapped-drive warning inline under the folder field, ClassicStyle rationale replaced by themed QDialog.
- Suite gate per task: FULL suite green. Baseline on 762c7b4: **567 passed, 13 skipped**. Counts grow; report literal lines; host quirk: `-q` full-suite drops the final summary line — use `-o addopts= -ra` and cross-check `--junitxml`; NEVER estimate. Known flake: the invalid_grant loopback test may add one skip.
- SDD discipline: dispatch cd's into the worktree FIRST; pre-commit verify toplevel + expected parent; one commit per task; never amend; never bare `git stash`. pytest-qt fixtures only (never construct QApplication manually); isolate QSettings via the `theme._qsettings`-style monkeypatch idiom when a test touches persisted state.

## File Structure

- Create: `src/mml_cloud_courier/gui/progress_widgets.py` (SegmentedBar, StateBarCard, InflightEntry, EventsDelegate + STATE_ORDER/STATE_TOKENS)
- Create: `src/mml_cloud_courier/gui/errors_view.py` (ErrorCard, ErrorsTab — moves from job_tabs.py; `group_fill_rows` moves here)
- Create: `src/mml_cloud_courier/gui/first_run.py` (FirstRunScreen)
- Rewrite: `src/mml_cloud_courier/gui/wizard.py` (one-screen QDialog; pure helpers untouched)
- Modify: `src/mml_cloud_courier/gui/job_tabs.py` (ProgressTab rebuild, SummaryTab rebuild, ErrorsTab/group_fill_rows removed), `main_window.py` (errors import, first-run stack, drop target, no-conn dimming, set-total unchanged), `theme.py` (QSS additions: `surfaceCard`, `sectionLabel`, `tag` tones, `statCell`/`statValue`, `firstRunStep`, `stepBadge`), `rail_delegate.py` (dead `GROUP_DOT_TOKENS` removal), `docs/gui.md` (drop-target sentence)
- Tests: `tests/gui/test_progress_widgets.py` (new), `tests/gui/test_errors_view.py` (new), `tests/gui/test_first_run.py` (new), plus reworks in `test_job_tabs.py`, `test_wizard.py`, `test_main_window_smoke.py`

## Setup (main session, before Task 1)

- [ ] Push/ff check: origin/master must equal local master (EnterWorktree branches from origin). EnterWorktree (branch `info-reworks`); verify base = 762c7b4 (ff from local master if the worktree lands stale); `py -3.12 -m venv .venv`; `.venv\Scripts\python -m pip install -e ".[dev]"`; copy `tools\fake-gcs-server.exe`; full suite → record baseline (expect 567 passed, 13 skipped; recorded run governs).

---

### Task 1: Progress headline row + two-segment bar + counts/rate/ETA

**Files:**
- Create: `src/mml_cloud_courier/gui/progress_widgets.py`
- Modify: `src/mml_cloud_courier/gui/job_tabs.py` (ProgressTab `__init__`/`reset`/`update_snapshot`/`_update_throughput`)
- Test: `tests/gui/test_progress_widgets.py` (new), `tests/gui/test_job_tabs.py` (adjust)

**Interfaces:**
- Consumes: `theme.current()`, `theme.mono_font`, `theme.notifier.changed`; snapshot keys `name`, `direction`, `source_root`, `dest_prefix`, `progress.{files_total,files_done,bytes_total,bytes_done}`.
- Produces: `SegmentedBar(QWidget)` with `set_fractions(verified: float, inflight: float)` (0..1 each, clamped so verified+inflight ≤ 1; 8px high, radius 4, track bg, `accent` + `accent_2` segments, repaints on `theme.notifier.changed` via bound method); `ProgressTab.headline_name` (QLabel 13.5pt/600), `ProgressTab.headline_route` (QLabel mono 8.5pt faint, `Upload · D:\field\leg3 → gs://bucket/prefix`), `ProgressTab.percent_label` (QLabel mono 19.5pt/600, right-aligned), `ProgressTab.rate_label`, `ProgressTab.eta_label`. ETA text `human_duration(remaining_bytes / rate)` when rate > 0 and bytes_total known, else empty.

- [ ] **Step 1: Write the failing tests**

```python
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
```

```python
# append to tests/gui/test_job_tabs.py
def test_progress_headline_and_route(qtbot):
    tab = ProgressTab()
    qtbot.addWidget(tab)
    tab.update_snapshot({
        "name": "IceSeal_Survey_2026_Leg3", "status": "running",
        "direction": "upload", "source_root": r"D:\field\leg3",
        "dest_prefix": "scratch/leg3",
        "progress": {"files_total": 10, "files_done": 5,
                     "bytes_total": 1000, "bytes_done": 620},
    })
    assert tab.headline_name.text() == "IceSeal_Survey_2026_Leg3"
    assert tab.headline_route.text() == r"Upload · D:\field\leg3 → scratch/leg3"
    assert tab.percent_label.text() == "62%"


def test_progress_eta_appears_with_rate(qtbot, monkeypatch):
    import itertools
    times = itertools.count(step=1.0)
    monkeypatch.setattr("mml_cloud_courier.gui.job_tabs.time",
                        type("T", (), {"monotonic": staticmethod(lambda: next(times))}))
    tab = ProgressTab()
    qtbot.addWidget(tab)
    snap = {"status": "running",
            "progress": {"files_total": 1, "files_done": 0,
                         "bytes_total": 1000, "bytes_done": 0}}
    tab.update_snapshot(snap)
    snap2 = {**snap, "progress": {**snap["progress"], "bytes_done": 100}}
    tab.update_snapshot(snap2)           # 100 B/s instant rate
    assert tab.rate_label.text() == "100 B/s"
    assert tab.eta_label.text() != ""    # (1000-100)/100 = 9s remaining
```

- [ ] **Step 2: Run to verify failure** — `.venv\Scripts\python -m pytest tests/gui/test_progress_widgets.py tests/gui/test_job_tabs.py -v`: import error / attribute errors. (Existing `test_job_tabs` tests referencing removed widgets — e.g. old `headline_label` — will be adjusted in Step 3; list every adjustment in the report.)

- [ ] **Step 3: Implement.** `progress_widgets.py` (new):

```python
"""Custom-painted pieces of the Progress tab. Every color is read from
theme.current() at paint time — nothing here caches a hex value."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from mml_cloud_courier.gui import theme


def token_color(token: str) -> QColor:
    return theme._qcolor(getattr(theme.current(), token))


class SegmentedBar(QWidget):
    """The 8px two-segment progress bar: verified in accent, in-flight in
    accent_2, on a track background. Radius 4."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.verified = 0.0
        self.inflight = 0.0
        self.setMinimumHeight(8)
        self.setMaximumHeight(8)
        theme.notifier.changed.connect(self._on_theme)   # bound: auto-disconnects

    def _on_theme(self, _t) -> None:
        self.update()

    def set_fractions(self, verified: float, inflight: float) -> None:
        self.verified = min(1.0, max(0.0, verified))
        self.inflight = min(1.0 - self.verified, max(0.0, inflight))
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        path = QPainterPath()
        path.addRoundedRect(rect, 4, 4)
        painter.setClipPath(path)
        painter.fillRect(rect, token_color("track"))
        width = rect.width()
        verified_width = round(width * self.verified)
        inflight_width = round(width * self.inflight)
        if verified_width:
            painter.fillRect(0, 0, verified_width, rect.height(), token_color("accent"))
        if inflight_width:
            painter.fillRect(verified_width, 0, inflight_width, rect.height(),
                             token_color("accent_2"))
        painter.end()
```

`job_tabs.py` ProgressTab rebuild (top section only in this task — the two lists stay where they are for now):

```python
    def __init__(self, parent=None):
        super().__init__(parent)

        self.headline_name = QLabel("")
        font = self.headline_name.font()
        font.setPointSizeF(13.5)
        font.setWeight(QFont.Weight(600))
        self.headline_name.setFont(font)
        self.headline_route = QLabel("")
        self.headline_route.setFont(theme.mono_font(8.5))
        self.percent_label = QLabel("")
        self.percent_label.setFont(theme.mono_font(19.5, 600))
        self.percent_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)

        name_column = QVBoxLayout()
        name_column.setSpacing(2)
        name_column.addWidget(self.headline_name)
        name_column.addWidget(self.headline_route)
        headline_row = QHBoxLayout()
        headline_row.addLayout(name_column, 1)
        headline_row.addWidget(self.percent_label)

        self.bar = SegmentedBar()
        self.counts_label = QLabel("")
        self.counts_label.setFont(theme.mono_font(9))
        self.rate_label = QLabel("")
        self.rate_label.setFont(theme.mono_font(9, 500))
        self.eta_label = QLabel("")
        self.eta_label.setFont(theme.mono_font(9))
        under_bar = QHBoxLayout()
        under_bar.setSpacing(16)
        under_bar.addWidget(self.counts_label)
        under_bar.addStretch(1)
        under_bar.addWidget(self.rate_label)
        under_bar.addWidget(self.eta_label)

        self.headline_label = self.headline_name   # back-compat alias; remove in Task 9
        self.inflight_list = QListWidget()
        self.inflight_list.setTextElideMode(Qt.TextElideMode.ElideLeft)
        self.inflight_list.setFont(theme.mono_font(8.5))
        self.events_list = QListWidget()
        self.events_list.setTextElideMode(Qt.TextElideMode.ElideLeft)
        self.events_list.setFont(theme.mono_font(8.5))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 20, 18)
        layout.setSpacing(15)
        layout.addLayout(headline_row)
        layout.addWidget(self.bar)
        layout.addLayout(under_bar)
        layout.addWidget(QLabel("In progress:"))
        layout.addWidget(self.inflight_list, 1)
        layout.addWidget(QLabel("Events:"))
        layout.addWidget(self.events_list, 1)

        self._events: list[str] = []
        self._last_bytes: int | None = None
        self._last_time: float | None = None
        self._rate: float | None = None
```

`update_snapshot` changes: set `headline_name` from `snap.get("name", "")` falling back to keeping the previous text when the key is absent (SSE snapshots may omit it); `headline_route` from `f"{snap['direction'].title()} · {snap['source_root']} → {snap['dest_prefix']}"` when all three keys are present, else keep previous; `percent_label` = `f"{int(fraction * 100)}%"`; `self.bar.set_fractions(fraction, inflight_fraction)` where `inflight_fraction = sum(e.get("bytes_transferred", 0) for e in transferring) / bytes_total` when `bytes_total` else 0 — compute AFTER reading `transferring`; status text moves out of the old `headline_label` (STATUS_LABELS text now lives in the rail + Summary; the Progress headline is the job NAME per README screen 1). `_update_throughput` additionally sets `eta_label`: when `self._rate and self._rate > 0 and bytes_total and bytes_done <= bytes_total`: `self.eta_label.setText(human_duration((bytes_total - bytes_done) / self._rate))` else clear — pass `bytes_total`/`bytes_done` into `_update_throughput(bytes_done, bytes_total)` (update its one call site). `reset()` clears the new labels too. Imports: `QFont` from QtGui, `SegmentedBar` from progress_widgets.

Adjust existing tests that asserted `headline_label` shows STATUS text (grep `tests/gui` for `headline_label`): they now assert via the alias against the NAME semantics — rewrite those assertions to match the new contract (name in headline, status no longer on this tab).

- [ ] **Step 4: Run both test files** — green. **Step 5: Full suite**, **Step 6: Commit** `git commit -m "feat: progress headline row, two-segment bar, rate+ETA"`.

---

### Task 2: "Every file, by state" card

**Files:**
- Modify: `src/mml_cloud_courier/gui/progress_widgets.py` (append StateBarCard), `src/mml_cloud_courier/gui/theme.py` (QSS additions), `src/mml_cloud_courier/gui/job_tabs.py` (wire card into ProgressTab)
- Test: `tests/gui/test_progress_widgets.py` (append)

**Interfaces:**
- Consumes: `token_color` (Task 1), `progress.state_counts` (dict state→count, states from `format.py` STATE_LABELS keys).
- Produces: `STATE_ORDER = ("verified", "transferred", "transferring", "pending", "skipped", "changed", "failed", "quarantined")`; `STATE_TOKENS = {"verified": "accent", "transferred": "accent_2", "transferring": "accent_3", "pending": "track", "skipped": "skip", "changed": "warn", "failed": "danger", "quarantined": "danger"}`; `StateBarCard(QWidget)` with `set_counts(counts: dict[str, int]) -> None` and readable `legend_labels() -> list[tuple[str, int]]` (ordered (label, count) pairs for nonzero states, labels from STATE_LABELS). QSS object names added to `theme.qss`: `surfaceCard` (`background: {t.surface}; border: 1px solid {t.line}; border-radius: 9px;`), `sectionLabel` (`color: {t.faint}; background: transparent;`).

- [ ] **Step 1: Failing tests (append to test_progress_widgets.py)**

```python
def test_state_card_order_and_legend(qtbot):
    from mml_cloud_courier.gui.progress_widgets import STATE_ORDER, StateBarCard
    card = StateBarCard()
    qtbot.addWidget(card)
    card.set_counts({"failed": 11, "verified": 8862, "skipped": 2104,
                     "transferring": 4, "bogus_state": 3})
    labels = card.legend_labels()
    # ordered by STATE_ORDER, zero-count states omitted, unknown states last
    assert labels[0] == ("Verified", 8862)
    assert labels[1] == ("Transferring", 4)
    assert labels[2] == ("Skipped (already up to date)", 2104)
    assert labels[3] == ("Failed", 11)
    assert labels[-1] == ("bogus_state", 3)


def test_state_order_matches_lifecycle():
    from mml_cloud_courier.gui.progress_widgets import STATE_ORDER, STATE_TOKENS
    assert STATE_ORDER == ("verified", "transferred", "transferring", "pending",
                          "skipped", "changed", "failed", "quarantined")
    assert set(STATE_TOKENS) == set(STATE_ORDER)
    assert STATE_TOKENS["failed"] == "danger" and STATE_TOKENS["skipped"] == "skip"
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Append to `progress_widgets.py`:

```python
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout

from mml_cloud_courier.gui.format import STATE_LABELS

STATE_ORDER = ("verified", "transferred", "transferring", "pending",
               "skipped", "changed", "failed", "quarantined")
STATE_TOKENS = {"verified": "accent", "transferred": "accent_2",
                "transferring": "accent_3", "pending": "track",
                "skipped": "skip", "changed": "warn",
                "failed": "danger", "quarantined": "danger"}


class _StackedStateBar(QWidget):
    """9px stacked bar, one segment per nonzero state in STATE_ORDER."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._counts: dict[str, int] = {}
        self.setMinimumHeight(9)
        self.setMaximumHeight(9)
        theme.notifier.changed.connect(self._on_theme)

    def _on_theme(self, _t) -> None:
        self.update()

    def set_counts(self, counts: dict[str, int]) -> None:
        self._counts = dict(counts)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        path = QPainterPath()
        path.addRoundedRect(rect, 3, 3)
        painter.setClipPath(path)
        painter.fillRect(rect, token_color("track"))
        total = sum(v for v in self._counts.values() if v > 0)
        if total:
            x = 0
            for state in STATE_ORDER:
                count = self._counts.get(state, 0)
                if count <= 0:
                    continue
                seg = round(rect.width() * count / total)
                painter.fillRect(x, 0, seg, rect.height(),
                                 token_color(STATE_TOKENS[state]))
                x += seg
        painter.end()


class StateBarCard(QWidget):
    """README screen 1 item 3 — the single most valuable addition: every
    file's state at once, so skipped is distinguishable from failed."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("surfaceCard")
        self.title = QLabel("EVERY FILE, BY STATE")
        self.title.setObjectName("sectionLabel")
        self.title.setFont(theme.mono_font(8.0))
        self.bar = _StackedStateBar()
        self._legend_layout = QHBoxLayout()
        self._legend_layout.setSpacing(18)
        self._legend: list[tuple[str, int]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 13, 15, 13)
        layout.setSpacing(9)
        layout.addWidget(self.title)
        layout.addWidget(self.bar)
        layout.addLayout(self._legend_layout)
        theme.notifier.changed.connect(self._on_theme)

    def _on_theme(self, _t) -> None:
        self.set_counts(dict(self._raw_counts)) if hasattr(self, "_raw_counts") else None

    def legend_labels(self) -> list[tuple[str, int]]:
        return list(self._legend)

    def set_counts(self, counts: dict[str, int]) -> None:
        self._raw_counts = dict(counts)
        self.bar.set_counts(counts)
        ordered = [(state, counts[state]) for state in STATE_ORDER
                   if counts.get(state, 0) > 0]
        ordered += [(state, count) for state, count in counts.items()
                    if state not in STATE_TOKENS and count > 0]
        self._legend = [(STATE_LABELS.get(state, state), count)
                        for state, count in ordered]
        while self._legend_layout.count():
            item = self._legend_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        t = theme.current()
        for (state, count), (label, _c) in zip(ordered, self._legend):
            swatch = QLabel()
            swatch.setFixedSize(8, 8)
            token = STATE_TOKENS.get(state, "skip")
            swatch.setStyleSheet(
                f"background: {getattr(t, token)}; border-radius: 2px;")
            entry_label = QLabel(f"{label}  ")
            count_label = QLabel(f"{count:,}")
            count_label.setFont(theme.mono_font(8.5, 500))
            cell = QHBoxLayout()
            cell.setSpacing(7)
            cell.addWidget(swatch)
            cell.addWidget(entry_label)
            cell.addWidget(count_label)
            holder = QWidget()
            holder.setLayout(cell)
            self._legend_layout.addWidget(holder)
        self._legend_layout.addStretch(1)
```

(Note: the swatch `setStyleSheet` uses a token VALUE f-string, not a hex literal — the acceptance grep only forbids literal hex in source. It re-renders on theme change via `_on_theme` → `set_counts` replay.) `theme.py` `qss()`: add

```python
QWidget#surfaceCard {{ background: {t.surface}; border: 1px solid {t.line}; border-radius: 9px; }}
QLabel#sectionLabel {{ color: {t.faint}; background: transparent; letter-spacing: 1px; }}
```

`job_tabs.py`: `self.state_card = StateBarCard()` inserted into the ProgressTab layout after `under_bar`; `update_snapshot` calls `self.state_card.set_counts(progress.get("state_counts") or {})`; `reset()` calls `self.state_card.set_counts({})`.

- [ ] **Step 4: Run tests** — green. **Step 5: Full suite**, **Step 6: Commit** `git commit -m "feat: every-file-by-state card - stacked bar + legend"`.

---

### Task 3: In-progress card + Events card

**Files:**
- Modify: `src/mml_cloud_courier/gui/progress_widgets.py` (append InflightDelegate + EventsDelegate + roles), `src/mml_cloud_courier/gui/job_tabs.py` (ProgressTab bottom grid; feed roles)
- Test: `tests/gui/test_progress_widgets.py` (append), `tests/gui/test_job_tabs.py` (adjust event/inflight assertions)

**Interfaces:**
- Consumes: Task 1 layout; `transferring` entries `{relative_path, bytes_transferred, size_bytes, method, slices_total, slices_done}`; event dicts `{at, kind, detail}`.
- Produces: roles `INFLIGHT_ROLE = Qt.ItemDataRole.UserRole + 1` (the raw entry dict) and `EVENT_ROLE = Qt.ItemDataRole.UserRole + 1` (the `(at, kind, detail)` tuple); `InflightDelegate` (paints left-elided mono path, a 4px `track` bar with `accent_2` fill under it, byte/slice detail right-aligned mono faint — 40px row); `EventsDelegate` (time mono faint, kind in a fixed 52px column colored by outcome — `verified→accent_text`, `failed→danger`, `retry→warn`, else `muted` — detail elided muted; 22px row); `event_kind_token(kind: str) -> str` pure helper; `inflight_detail_text(entry: dict) -> str` pure helper (`"503 MB of 812 MB · slice 5 of 8, 4 done"` shape: bytes always, slice suffix only for sliced method with slices_total > 0).

- [ ] **Step 1: Failing tests (append)**

```python
def test_event_kind_token_mapping():
    from mml_cloud_courier.gui.progress_widgets import event_kind_token
    assert event_kind_token("verified") == "accent_text"
    assert event_kind_token("failed") == "danger"
    assert event_kind_token("retry") == "warn"
    assert event_kind_token("run_started") == "muted"


def test_inflight_detail_text():
    from mml_cloud_courier.gui.progress_widgets import inflight_detail_text
    entry = {"relative_path": "a/b.tif", "bytes_transferred": 100, "size_bytes": 1000,
             "method": "sliced", "slices_total": 8, "slices_done": 4}
    assert inflight_detail_text(entry) == "100 B of 1.0 KB · slice 5 of 8, 4 done"
    single = {"relative_path": "c.bin", "bytes_transferred": 5, "size_bytes": 10,
              "method": "single_shot", "slices_total": 0}
    assert inflight_detail_text(single) == "5 B of 10 B"
```

(Adjust expected byte strings to `format.human_bytes` actual output — run `human_bytes(1000)` first; it yields `"1.0 KB"`. If it differs, fix the EXPECTATION, not the helper.)

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Append to `progress_widgets.py`:

```python
from PySide6.QtCore import QRect, QSize
from PySide6.QtWidgets import QStyledItemDelegate

from mml_cloud_courier.gui.format import human_bytes

INFLIGHT_ROLE = Qt.ItemDataRole.UserRole + 1
EVENT_ROLE = Qt.ItemDataRole.UserRole + 1

_EVENT_KIND_TOKENS = {"verified": "accent_text", "failed": "danger", "retry": "warn"}


def event_kind_token(kind: str) -> str:
    return _EVENT_KIND_TOKENS.get(kind, "muted")


def inflight_detail_text(entry: dict) -> str:
    text = (f"{human_bytes(entry.get('bytes_transferred', 0))} of "
            f"{human_bytes(entry.get('size_bytes', 0))}")
    slices_total = entry.get("slices_total", 0)
    if entry.get("method") == "sliced" and slices_total > 0:
        slices_done = entry.get("slices_done", 0)
        current = min(slices_done + 1, slices_total)
        text += f" · slice {current} of {slices_total}, {slices_done} done"
    return text


class InflightDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), 40)

    def paint(self, painter, option, index) -> None:
        entry = index.data(INFLIGHT_ROLE) or {}
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(8, 6, -8, -6)
        painter.setFont(theme.mono_font(8.5))
        metrics = painter.fontMetrics()
        detail = inflight_detail_text(entry)
        detail_width = metrics.horizontalAdvance(detail)
        painter.setPen(token_color("ink"))
        path_text = metrics.elidedText(entry.get("relative_path", ""),
                                       Qt.TextElideMode.ElideLeft,
                                       rect.width() - detail_width - 12)
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, path_text)
        painter.setPen(token_color("faint"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, detail)
        size = entry.get("size_bytes", 0)
        fraction = (entry.get("bytes_transferred", 0) / size) if size else 0.0
        bar = QRect(rect.left(), rect.bottom() - 4, rect.width(), 4)
        painter.fillRect(bar, token_color("track"))
        painter.fillRect(QRect(bar.left(), bar.top(),
                               round(bar.width() * min(1.0, fraction)), 4),
                         token_color("accent_2"))
        painter.restore()


class EventsDelegate(QStyledItemDelegate):
    KIND_COLUMN = 52

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), 22)

    def paint(self, painter, option, index) -> None:
        at, kind, detail = index.data(EVENT_ROLE) or ("", "", "")
        painter.save()
        rect = option.rect.adjusted(8, 3, -8, -3)
        painter.setFont(theme.mono_font(8.0))
        metrics = painter.fontMetrics()
        time_width = metrics.horizontalAdvance(at) + 10
        painter.setPen(token_color("faint"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, at)
        painter.setPen(token_color(event_kind_token(kind)))
        kind_rect = QRect(rect.left() + time_width, rect.top(), self.KIND_COLUMN, rect.height())
        painter.drawText(kind_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(kind, Qt.TextElideMode.ElideRight, self.KIND_COLUMN))
        painter.setPen(token_color("muted"))
        detail_rect = QRect(kind_rect.right() + 8, rect.top(),
                            rect.right() - kind_rect.right() - 8, rect.height())
        painter.drawText(detail_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(detail, Qt.TextElideMode.ElideRight, detail_rect.width()))
        painter.restore()
```

`job_tabs.py` ProgressTab: the two `QListWidget`s move into a bottom card row — wrap each in a `surfaceCard` QWidget with a `sectionLabel` title (`IN PROGRESS` gains a live count suffix set in `_update_inflight`: `f"IN PROGRESS — {len(transferring)} FILES"`; `EVENTS`), delete the old bare `QLabel("In progress:")/("Events:")` rows; `cards_row = QHBoxLayout()` with stretches 115/100 (the 1.15fr/1fr grid). `_update_inflight` stores each entry dict via `item.setData(INFLIGHT_ROLE, entry)` (display text stays as accessible fallback); install `InflightDelegate` on `inflight_list`. `_append_events` stores `(event.get("at",""), event.get("kind",""), event.get("detail",""))` under `EVENT_ROLE` per item (keep the `-200:` cap on a parallel tuple list `self._event_tuples`); install `EventsDelegate`; repaint both viewports on `theme.notifier.changed` (bound method `_on_theme_repaint`). Adjust any existing tests asserting the old plain-text list items to assert the ROLE data instead.

- [ ] **Step 4: Run tests** — green. **Step 5: Full suite**, **Step 6: Commit** `git commit -m "feat: in-progress and events cards with painted rows"`.

---

### Task 4: Errors tab — always-expanded cause cards

**Files:**
- Create: `src/mml_cloud_courier/gui/errors_view.py`
- Modify: `src/mml_cloud_courier/gui/job_tabs.py` (delete ErrorsTab + group_fill_rows), `src/mml_cloud_courier/gui/main_window.py` (import swap + header sentence data), `src/mml_cloud_courier/gui/theme.py` (tag QSS)
- Test: `tests/gui/test_errors_view.py` (new); update imports in `tests/gui/test_job_tabs.py`/`test_errors_model.py` if they touch ErrorsTab/group_fill_rows

**Interfaces:**
- Consumes: `ErrorGroup` (unchanged), callbacks `on_retry(category)`, `on_exclude(category)`, `on_copy(category)`, `on_expand(category) -> list[str]` — SAME constructor signature as the old ErrorsTab so `main_window.py` only changes its import.
- Produces: `errors_view.SELF_CLEARING_WARN = frozenset({"file_locked", "source_changed"})`, `SELF_CLEARING_ACCENT = frozenset({"network"})`, `group_tone(category) -> str` returning `"warn" | "accent" | "danger"`, `order_groups(groups) -> list[ErrorGroup]` (needs-you first, then self-clearing; count desc within each), `header_sentence(groups, files_total) -> str` (`"423 of 61,022 files did not transfer, from 4 causes. 2 clear themselves; 2 need something from you."` — numerals; singular forms `cause`, `clears itself`, `needs something from you` when 1; empty string when no groups), `group_fill_rows` (moved verbatim from job_tabs), `ErrorsTab(QWidget)` with `load_groups(groups)`, `set_files_total(total: int | None)`, and per-card buttons `Retry these files` / `Stop retrying` / `Copy file list` wired to THAT card's category. Test hooks kept: `group_count()`, `group_label(i)`; `card(i) -> ErrorCard` added. QSS: `QLabel#tag[tone="danger"] {{ background: {t.danger_soft}; color: {t.danger_text}; }}` + warn/accent variants + shared `QLabel#tag {{ border-radius: 4px; padding: 4px 7px; }}`, and `QWidget#surfaceCard[tone="danger"] {{ border-left: 3px solid {t.danger}; }}` + warn/accent variants.

- [ ] **Step 1: Failing tests (new file test_errors_view.py)**

```python
import pytest

from mml_cloud_courier.gui.errors_model import ErrorGroup
from mml_cloud_courier.gui.errors_view import (
    ErrorsTab, group_tone, header_sentence, order_groups,
)


def _group(category, count, message="msg", action="act", quarantined=0):
    return ErrorGroup(category=category, count=count, quarantined=quarantined,
                      message=message, action=action)


def test_group_tone():
    assert group_tone("permission_denied") == "danger"
    assert group_tone("credential") == "danger"
    assert group_tone("file_locked") == "warn"
    assert group_tone("source_changed") == "warn"
    assert group_tone("network") == "accent"
    assert group_tone("never_heard_of_it") == "danger"   # unknown = needs you


def test_order_groups_needs_you_first_then_count():
    groups = [_group("network", 400), _group("credential", 2),
              _group("file_locked", 6), _group("permission_denied", 15)]
    ordered = order_groups(groups)
    assert [g.category for g in ordered] == [
        "permission_denied", "credential", "network", "file_locked"]


def test_header_sentence_counts():
    groups = [_group("credential", 2), _group("permission_denied", 15),
              _group("network", 400), _group("file_locked", 6)]
    text = header_sentence(groups, files_total=61022)
    assert text == ("423 of 61,022 files did not transfer, from 4 causes."
                    " 2 clear themselves; 2 need something from you.")
    assert header_sentence([], files_total=10) == ""
    one = header_sentence([_group("network", 3)], files_total=10)
    assert one == ("3 of 10 files did not transfer, from 1 cause."
                   " 1 clears itself; 0 need something from you.")


def test_cards_built_per_group_with_own_buttons(qtbot):
    calls = []
    tab = ErrorsTab(on_retry=lambda c: calls.append(("retry", c)),
                    on_exclude=lambda c: calls.append(("exclude", c)),
                    on_copy=lambda c: calls.append(("copy", c)),
                    on_expand=lambda c: [f"{c}/a.bin"])
    qtbot.addWidget(tab)
    tab.load_groups([_group("network", 400), _group("credential", 2)])
    assert tab.group_count() == 2
    first = tab.card(0)                      # ordered: credential (needs-you) first
    assert first.group.category == "credential"
    assert first.tag.text() == "Needs you"
    assert tab.card(1).tag.text() == "Retries on its own"
    first.retry_button.click()
    tab.card(1).copy_button.click()
    assert calls == [("retry", "credential"), ("copy", "network")]
    # sample rows fetched via on_expand, with the "…and N more" trailer
    assert "credential/a.bin" in first.samples_label.text()
    assert "…and 1 more" in first.samples_label.text()
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement `errors_view.py`.**

```python
"""Errors as always-expanded cause cards (README screen 3, Rec 5): the
taxonomy's message/action/count per cause, a needs-you/self-clearing tag,
and per-card buttons — the grouping logic in errors_model is untouched."""
from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from mml_cloud_courier.gui import theme
from mml_cloud_courier.gui.errors_model import ErrorGroup

SELF_CLEARING_WARN = frozenset({"file_locked", "source_changed"})
SELF_CLEARING_ACCENT = frozenset({"network"})


def group_tone(category: str) -> str:
    if category in SELF_CLEARING_WARN:
        return "warn"
    if category in SELF_CLEARING_ACCENT:
        return "accent"
    return "danger"


def _needs_you(category: str) -> bool:
    return group_tone(category) == "danger"


def order_groups(groups: list[ErrorGroup]) -> list[ErrorGroup]:
    return sorted(groups, key=lambda g: (not _needs_you(g.category), -g.count))


def header_sentence(groups: list[ErrorGroup], files_total: int | None) -> str:
    if not groups:
        return ""
    failed = sum(g.count for g in groups)
    self_clearing = sum(1 for g in groups if not _needs_you(g.category))
    needs = len(groups) - self_clearing
    cause_noun = "cause" if len(groups) == 1 else "causes"
    clears = "clears itself" if self_clearing == 1 else "clear themselves"
    needs_verb = "needs" if needs == 1 else "need"
    total_text = f"{files_total:,}" if files_total else "?"
    return (f"{failed:,} of {total_text} files did not transfer,"
            f" from {len(groups)} {cause_noun}."
            f" {self_clearing} {clears};"
            f" {needs} {needs_verb} something from you.")


def group_fill_rows(page: list[str], group_count: int) -> list[str]:
    rows = list(page)
    remaining = group_count - len(page)
    if remaining > 0:
        rows.append(f"…and {remaining:,} more")
    return rows


class ErrorCard(QWidget):
    def __init__(self, group: ErrorGroup, *, on_retry, on_exclude, on_copy,
                 samples: list[str], parent=None):
        super().__init__(parent)
        self.group = group
        tone = group_tone(group.category)
        self.setObjectName("surfaceCard")
        self.setProperty("tone", tone)

        self.message_label = QLabel(group.message)
        self.message_label.setWordWrap(True)
        font = self.message_label.font()
        font.setPointSizeF(10.5)
        font.setWeight(QFont.Weight(600))
        self.message_label.setFont(font)
        self.count_label = QLabel(
            f"{group.count:,} file" + ("" if group.count == 1 else "s"))
        self.count_label.setFont(theme.mono_font(9, 500))
        self.tag = QLabel("Needs you" if tone == "danger" else "Retries on its own")
        self.tag.setObjectName("tag")
        self.tag.setProperty("tone", tone)
        top = QHBoxLayout()
        top.addWidget(self.message_label, 1)
        top.addWidget(self.count_label)
        top.addWidget(self.tag)

        self.action_label = QLabel(group.action)
        self.action_label.setWordWrap(True)

        self.retry_button = QPushButton("Retry these files")
        self.exclude_button = QPushButton("Stop retrying")
        self.copy_button = QPushButton("Copy file list")
        self.retry_button.clicked.connect(lambda: on_retry(group.category))
        self.exclude_button.clicked.connect(lambda: on_exclude(group.category))
        self.copy_button.clicked.connect(lambda: on_copy(group.category))
        self.samples_label = QLabel(" · ".join(samples))
        self.samples_label.setFont(theme.mono_font(8.0))
        self.samples_label.setWordWrap(False)
        bottom = QHBoxLayout()
        bottom.addWidget(self.retry_button)
        bottom.addWidget(self.exclude_button)
        bottom.addWidget(self.copy_button)
        bottom.addSpacing(10)
        bottom.addWidget(self.samples_label, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(10)
        layout.addLayout(top)
        layout.addWidget(self.action_label)
        layout.addLayout(bottom)


class ErrorsTab(QWidget):
    def __init__(self, *, on_retry, on_exclude, on_copy, on_expand=None, parent=None):
        super().__init__(parent)
        self._on_retry = on_retry
        self._on_exclude = on_exclude
        self._on_copy = on_copy
        self._on_expand = on_expand
        self._groups: list[ErrorGroup] = []
        self._cards: list[ErrorCard] = []
        self._files_total: int | None = None

        self.header_label = QLabel("")
        self.header_label.setWordWrap(True)
        self._cards_layout = QVBoxLayout()
        self._cards_layout.setSpacing(11)
        cards_holder = QWidget()
        holder_layout = QVBoxLayout(cards_holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.addLayout(self._cards_layout)
        holder_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(cards_holder)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 17, 20, 14)
        layout.setSpacing(11)
        layout.addWidget(self.header_label)
        layout.addWidget(scroll, 1)

    def set_files_total(self, total: int | None) -> None:
        self._files_total = total
        self.header_label.setText(header_sentence(self._groups, total))

    def load_groups(self, groups: list[ErrorGroup]) -> None:
        self._groups = order_groups(groups)
        self.header_label.setText(header_sentence(self._groups, self._files_total))
        for card in self._cards:
            card.deleteLater()
        self._cards = []
        while self._cards_layout.count():
            self._cards_layout.takeAt(0)
        for group in self._groups:
            try:
                page = self._on_expand(group.category) if self._on_expand else []
            except Exception:
                page = []
            samples = group_fill_rows(page[:3], group.count)
            card = ErrorCard(group, on_retry=self._on_retry,
                             on_exclude=self._on_exclude, on_copy=self._on_copy,
                             samples=samples)
            self._cards.append(card)
            self._cards_layout.addWidget(card)

    # -- test/UI hooks -------------------------------------------------
    def group_count(self) -> int:
        return len(self._groups)

    def group_label(self, i: int) -> str:
        return self._groups[i].label

    def card(self, i: int) -> ErrorCard:
        return self._cards[i]
```

(Sample-trailer math: `group_fill_rows(page[:3], group.count)` sizes the `…and N more` trailer as `count - len(shown)` — for the test's credential group, count=2 with 1 sample row → `…and 1 more`.)

`theme.py` `qss()` additions:

```python
QLabel#tag {{ border-radius: 4px; padding: 4px 7px; font-size: 10.5px; font-weight: 500; }}
QLabel#tag[tone="danger"] {{ background: {t.danger_soft}; color: {t.danger_text}; }}
QLabel#tag[tone="warn"] {{ background: {t.warn_soft}; color: {t.warn_text}; }}
QLabel#tag[tone="accent"] {{ background: {t.accent_soft}; color: {t.accent_text}; }}
QWidget#surfaceCard[tone="danger"] {{ border-left: 3px solid {t.danger}; }}
QWidget#surfaceCard[tone="warn"] {{ border-left: 3px solid {t.warn}; }}
QWidget#surfaceCard[tone="accent"] {{ border-left: 3px solid {t.accent_2}; }}
```

`job_tabs.py`: delete `ErrorsTab`, `group_fill_rows`, `_CATEGORY_ROLE`, `_FILLED_ROLE`, the now-unused `QTreeWidget/QTreeWidgetItem` imports. `main_window.py`: `from mml_cloud_courier.gui.errors_view import ErrorsTab`; in `_render_job` add `self.errors_tab.set_files_total((job.get("progress") or {}).get("files_total"))`; `_on_expand_error_group` stays (same single-page contract; it now runs synchronously during `load_groups` — same thread, same bounded one-page cost as the old expand handler). The `Stop retrying` confirmation dialog in `_on_exclude_errors` is UNCHANGED. Update any test importing `ErrorsTab`/`group_fill_rows` from `job_tabs` to the new module (grep `tests/` for both names).

- [ ] **Step 4: Run test_errors_view.py + the updated files** — green. **Step 5: Full suite**, **Step 6: Commit** `git commit -m "feat: errors tab as always-expanded cause cards"`.

---

### Task 5: Summary tab rebuild

**Files:**
- Modify: `src/mml_cloud_courier/gui/job_tabs.py` (SummaryTab), `src/mml_cloud_courier/gui/theme.py` (statCell QSS)
- Test: `tests/gui/test_job_tabs.py` (rework SummaryTab tests)

**Interfaces:**
- Consumes: `STATE_ORDER`/`STATE_TOKENS`/`token_color` (progress_widgets), `_verdict_style` (unchanged), job payload (`status`, `progress.{files_total,bytes_total,bytes_done,state_counts}`, `started_at`, `finished_at`).
- Produces: SummaryTab attrs `verdict_label` (existing), `verdict_tag` (QLabel objectName `tag`, tone `danger`, text `Needs attention`, visible only for status `incomplete`), `stat_values: dict[str, QLabel]` keyed `files|transferred|duration|did_not_transfer`, `state_rows_layout`, `footer_label` (verbatim footer copy, visible when any `quarantined` count > 0), buttons unchanged (`report_button`, `resume_button`). `did_not_transfer` = `state_counts.get("failed", 0) + state_counts.get("quarantined", 0)`, its value label styled `color: {t.danger}` via inline token f-string. `Transferred` = `human_bytes(progress.bytes_done)`.

- [ ] **Step 1: Failing tests (rework the SummaryTab section of test_job_tabs.py; keep any still-valid assertions)**

```python
def _summary_job(status="incomplete"):
    return {"status": status, "started_at": "2026-08-08T10:00:00+00:00",
            "finished_at": "2026-08-08T11:30:00+00:00",
            "progress": {"files_total": 61022, "bytes_total": 900_000,
                         "bytes_done": 850_000,
                         "state_counts": {"verified": 60599, "failed": 3,
                                          "quarantined": 420}}}


def test_summary_stat_cells(qtbot):
    tab = SummaryTab(on_open_report=lambda: None, on_resume=lambda: None)
    qtbot.addWidget(tab)
    tab.update_job(_summary_job())
    assert tab.stat_values["files"].text() == "61,022"
    assert tab.stat_values["transferred"].text() == "850 KB"
    assert tab.stat_values["did_not_transfer"].text() == "423"
    assert tab.stat_values["duration"].text() == "1h 30m"
    assert tab.verdict_tag.isVisibleTo(tab)
    assert tab.footer_label.isVisibleTo(tab)   # quarantined > 0


def test_summary_tag_and_footer_hidden_when_clean(qtbot):
    tab = SummaryTab(on_open_report=lambda: None, on_resume=lambda: None)
    qtbot.addWidget(tab)
    job = _summary_job(status="complete")
    job["progress"]["state_counts"] = {"verified": 61022}
    tab.update_job(job)
    assert not tab.verdict_tag.isVisibleTo(tab)
    assert not tab.footer_label.isVisibleTo(tab)
```

(Cross-check `human_bytes(850_000)` — with the /1000 unit ladder it renders `850 KB`; fix EXPECTATIONS to actual output, never the formatter.)

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** SummaryTab `__init__` rebuild (keep constructor signature, `_last_job` replay, theme-changed hook, button wiring):

```python
        self.verdict_label = QLabel("")
        verdict_font = self.verdict_label.font()
        verdict_font.setPointSizeF(16.5)
        verdict_font.setWeight(QFont.Weight(600))
        self.verdict_label.setFont(verdict_font)
        self.verdict_tag = QLabel("Needs attention")
        self.verdict_tag.setObjectName("tag")
        self.verdict_tag.setProperty("tone", "danger")
        self.verdict_tag.hide()
        verdict_row = QHBoxLayout()
        verdict_row.addWidget(self.verdict_label)
        verdict_row.addWidget(self.verdict_tag)
        verdict_row.addStretch(1)

        self.stat_values: dict[str, QLabel] = {}
        stats_row = QHBoxLayout()
        stats_row.setSpacing(1)
        for key, label_text in (("files", "FILES"), ("transferred", "TRANSFERRED"),
                                ("duration", "DURATION"),
                                ("did_not_transfer", "DID NOT TRANSFER")):
            cell = QWidget()
            cell.setObjectName("statCell")
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(13, 10, 13, 10)
            title = QLabel(label_text)
            title.setObjectName("sectionLabel")
            title.setFont(theme.mono_font(8.0))
            value = QLabel("")
            value.setFont(theme.mono_font(14, 600))
            self.stat_values[key] = value
            cell_layout.addWidget(title)
            cell_layout.addWidget(value)
            stats_row.addWidget(cell, 1)

        self.state_rows_layout = QVBoxLayout()
        self.state_rows_layout.setSpacing(6)

        self.footer_label = QLabel(
            "Excluded files stay recorded in the ledger. The job keeps the"
            " Incomplete verdict until they transfer or you stop retrying them."
        )
        self.footer_label.setWordWrap(True)
        self.footer_label.hide()
```

Layout order: verdict_row, stats_row, state_rows_layout, stretch, footer_label + buttons in one bottom row (buttons right). `update_job`: existing verdict text/style; `self.verdict_tag.setVisible(status == "incomplete")`; fill `stat_values` (`files` = `f"{files_total:,}"`, `transferred` = `human_bytes(bytes_done)`, `duration` from `_duration_text`'s delta portion — refactor `_duration_text` into `_duration_value(started, finished) -> str` returning just `human_duration(delta)` or `""`, keep `_duration_text` calling it for any other user; grep first), `did_not_transfer` = failed+quarantined with `self.stat_values["did_not_transfer"].setStyleSheet(f"color: {theme.current().danger};")` re-applied in `_on_theme_changed` replay (already replays `update_job`). State rows: clear `state_rows_layout`; for each nonzero state in STATE_ORDER order (+unknowns): a row of 200px-min label (`STATE_LABELS`), a 7px `_StackedStateBar`-style single-fill bar — reuse: add `SingleStateBar` to progress_widgets? NO — reuse `_StackedStateBar` with a single-state counts dict `{state: count, "__rest__": total-count}`… that paints rest as track already since unknown states aren't painted — actually unknown states ARE appended in legend but `_StackedStateBar.paint` only paints STATE_ORDER states, so `{state: count}` alone fills proportional-to-total incorrectly (total = count → full bar). Simplest correct: give `_StackedStateBar` an optional `set_counts(counts, total=None)` where `total` overrides the denominator; rows pass `total=files_total`. Implement that (default None = sum, preserving Task 2 behavior + tests), then each row: `bar.set_counts({state: count}, total=files_total)`. Count label right-aligned mono 64px min-width. `theme.py` QSS: `QWidget#statCell {{ background: {t.surface}; border: 1px solid {t.line}; border-radius: 9px; }}`. Old `_counts_form`/`totals_label`/`duration_label` deleted — grep tests for them and rework those assertions to the new attrs.

- [ ] **Step 4: Run reworked tests** — green. **Step 5: Full suite**, **Step 6: Commit** `git commit -m "feat: summary tab - verdict tag, stat cells, state rows, footer"`.

---

### Task 6: First-run screen + no-connections dimming

**Files:**
- Create: `src/mml_cloud_courier/gui/first_run.py`
- Modify: `src/mml_cloud_courier/gui/main_window.py` (stack + trigger + dimming), `src/mml_cloud_courier/gui/theme.py` (step QSS)
- Test: `tests/gui/test_first_run.py` (new), `tests/gui/test_main_window_smoke.py` (append)

**Interfaces:**
- Consumes: `_no_connections` / `_last_jobs` / pill (Plan A), `ConnectionsDialog` opener.
- Produces: `FirstRunScreen(on_add_connection, on_open_guide)` — centered 520px column: `heading` (verbatim), `body` (verbatim), three step cards (`firstRunStep` objectName; `stepBadge` numeral labels; titles verbatim; bodies: 1 `A connection is a bucket plus a credential the service stores for itself.` 2 `Pick any folder — the scan preview shows what will move before anything starts.` 3 `Transfers run in the Windows service; closing this window never stops them.`), buttons `add_button` ("Add a connection", objectName `primaryButton`) and `guide_button` ("Read the setup guide"). `MainWindow`: `self._first_run = FirstRunScreen(...)` and `self._content_stack = QStackedWidget` holding [splitter, first_run]; `_update_first_run()` shows first_run when `self._no_connections and not self._last_jobs`, called from `_on_jobs` and `_on_profiles`; `_update_action_states` gains `self.new_transfer_button.setEnabled(up and not self._no_connections)` (Pause/Resume/Cancel unchanged); guide button opens `docs/gui.md` resolved relative to the package (`Path(mml_cloud_courier.__file__).resolve().parents[2] / "docs" / "gui.md"` — the editable-install repo root; if missing, fall back to the GitHub URL `https://github.com/pmahoney-noaa/mml-cloud-courier/blob/master/docs/gui.md`) via `QDesktopServices.openUrl`.

- [ ] **Step 1: Failing tests**

```python
# tests/gui/test_first_run.py
from mml_cloud_courier.gui.first_run import FirstRunScreen


def test_first_run_copy_and_buttons(qtbot):
    clicks = []
    screen = FirstRunScreen(on_add_connection=lambda: clicks.append("add"),
                            on_open_guide=lambda: clicks.append("guide"))
    qtbot.addWidget(screen)
    assert screen.heading.text() == "Nothing has been transferred yet"
    assert screen.body.text().startswith("Courier needs one connection")
    assert [s.title.text() for s in screen.steps] == [
        "Add a connection", "Point it at a folder",
        "Close the window whenever you like"]
    screen.add_button.click()
    screen.guide_button.click()
    assert clicks == ["add", "guide"]
```

```python
# append to tests/gui/test_main_window_smoke.py
def test_first_run_swap_and_new_transfer_dimming(window):
    window._no_connections = True
    window._on_jobs([])                       # no jobs + no connections
    assert window._content_stack.currentWidget() is window._first_run
    assert not window.new_transfer_button.isEnabled()
    assert window.pill.state == "noconn"
    window._no_connections = False
    window._on_jobs([{"id": 1, "name": "j", "status": "complete"}])
    assert window._content_stack.currentWidget() is not window._first_run
    assert window.new_transfer_button.isEnabled()
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** `first_run.py`:

```python
"""README screen 6: the one screen a brand-new install shows — three
sentences and three steps, no decoration."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from mml_cloud_courier.gui import theme

_STEPS = (
    ("Add a connection",
     "A connection is a bucket plus a credential the service stores for itself."),
    ("Point it at a folder",
     "Pick any folder — the scan preview shows what will move before anything starts."),
    ("Close the window whenever you like",
     "Transfers run in the Windows service; closing this window never stops them."),
)


class _StepCard(QWidget):
    def __init__(self, number: int, title: str, body: str, parent=None):
        super().__init__(parent)
        self.setObjectName("firstRunStep")
        self.badge = QLabel(str(number))
        self.badge.setObjectName("stepBadge")
        self.badge.setFixedSize(20, 20)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setFont(theme.mono_font(8.5))
        self.title = QLabel(title)
        font = self.title.font()
        font.setPointSizeF(10)
        font.setWeight(QFont.Weight(600))
        self.title.setFont(font)
        self.body = QLabel(body)
        self.body.setWordWrap(True)
        text_column = QVBoxLayout()
        text_column.setSpacing(3)
        text_column.addWidget(self.title)
        text_column.addWidget(self.body)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 13, 15, 13)
        layout.setSpacing(11)
        layout.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(text_column, 1)


class FirstRunScreen(QWidget):
    def __init__(self, *, on_add_connection, on_open_guide, parent=None):
        super().__init__(parent)
        self.heading = QLabel("Nothing has been transferred yet")
        font = self.heading.font()
        font.setPointSizeF(15.5)
        font.setWeight(QFont.Weight(600))
        self.heading.setFont(font)
        self.body = QLabel(
            "Courier needs one connection before it can move anything — a"
            " bucket, and a credential the service can use on its own. After"
            " that, every transfer is a folder and a Start."
        )
        self.body.setWordWrap(True)

        self.steps = [_StepCard(i + 1, title, body)
                      for i, (title, body) in enumerate(_STEPS)]

        self.add_button = QPushButton("Add a connection")
        self.add_button.setObjectName("primaryButton")
        self.add_button.clicked.connect(lambda: on_add_connection())
        self.guide_button = QPushButton("Read the setup guide")
        self.guide_button.clicked.connect(lambda: on_open_guide())
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.guide_button)
        buttons.addStretch(1)

        column = QVBoxLayout()
        column.setSpacing(11)
        column.addStretch(1)
        column.addWidget(self.heading)
        column.addWidget(self.body)
        for step in self.steps:
            column.addWidget(step)
        column.addLayout(buttons)
        column.addStretch(2)
        holder = QWidget()
        holder.setMaximumWidth(520)
        holder.setLayout(column)
        outer = QHBoxLayout(self)
        outer.addStretch(1)
        outer.addWidget(holder)
        outer.addStretch(1)
```

`theme.py` QSS: `QWidget#firstRunStep {{ background: {t.surface}; border: 1px solid {t.line}; border-radius: 9px; }}`, `QLabel#stepBadge {{ background: {t.accent_soft}; color: {t.accent_text}; border-radius: 10px; }}`.

`main_window.py`: import `QStackedWidget`, `FirstRunScreen`, `QDesktopServices`/`QUrl` already imported; build in `_build_full_ui`:

```python
        self._first_run = FirstRunScreen(
            on_add_connection=self._open_connections,
            on_open_guide=self._open_setup_guide,
        )
        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(splitter)
        self._content_stack.addWidget(self._first_run)
```

(the stack replaces `splitter` in the central layout); `_open_setup_guide` per the interface block (module-level `import mml_cloud_courier`, `from pathlib import Path`); `_update_first_run()` sets the stack's current widget; call it at the end of `_on_jobs` and `_on_profiles`; `_update_action_states` change per interface. NOTE ordering: `_build_toolbar` runs before `_on_profiles` ever fires and reads `self._no_connections` in `_update_action_states` — initialize `self._no_connections = False` BEFORE `_build_toolbar()` is called in `_build_full_ui` (move the existing initialization up; grep to confirm current position).

- [ ] **Step 4: Run tests** — green. **Step 5: Full suite**, **Step 6: Commit** `git commit -m "feat: first-run screen + no-connections dimming"`.

---

### Task 7: One-screen wizard

**Files:**
- Rewrite: `src/mml_cloud_courier/gui/wizard.py` (pure helpers `WizardState`/`build_submission`/`parse_duplicate_job_id`/`preview_scan` UNCHANGED — byte-identical)
- Test: `tests/gui/test_wizard.py` (rework page-flow tests; keep payload/duplicate/preview-logic tests untouched)

**Interfaces:**
- Consumes: `theme` QSS (`primaryButton`, `segmentWell`, `segmentButton`), `NewConnectionDialog`, `call_async`, `preview_remote`, `submit_job`, `resume`.
- Produces: `NewTransferWizard(QDialog)` — SAME name/module. Preserved API: signals `jobSubmitted(int)`/`previewUpdated(int,int,int)`; attrs `client`, `state`, `profile_id`, `profile_combo`; setters `set_direction/set_source/set_prefix/set_job_name`; `accept_and_submit()`; `accept()`/`reject()`/`closeEvent` cancel the preview. NEW: `set_profile_by_name(name: str) -> bool` (selects the combo row whose profile name matches; used by Task 8). Single screen layout: direction as a two-button segment well (`Upload`/`Download`, checkable, exclusive); left column = folder field + Browse + inline `mapped_label` (mapped-drive warning, verbatim behavior from FoldersPage `_update_mapped_label`); right column = connection combo + Refresh + New connection… + prefix field; beneath: live `preview_label` (same `preview_scan`/`preview_remote` logic, debounced to source/direction/profile changes via a 400ms `QTimer.singleShot` restart guard); name field prefilled `{leaf}-{date}` exactly as OptionsPage did, refreshed whenever source changes AND the user hasn't edited the name (track `_name_touched` set by user edits, cleared never — programmatic `set_job_name` sets it too); a `More options` QToolButton (checkable, arrow) revealing `start_later_checkbox` + `datetime_edit` + `audit_checkbox` + `audit_note` (all verbatim widgets/behavior from OptionsPage); `status_label`; bottom row `Cancel` + `Start transfer` (`primaryButton`). Validation on Start (replaces per-page validatePage): connection selected (else status `Select a connection to continue, or create one.`), non-empty source (else `Choose a local folder to continue.`), non-empty name (else `Give the job a name to continue.`); then the EXACT existing submit flow (`accept_and_submit` — `cancel_preview`, `sync_state`, `build_submission`, threaded submit, 409→resume dialog, `jobSubmitted`, accept).
- The old page classes (`DirectionPage` etc.) are DELETED. `sync_state()` and `_update_summary` fold into the dialog (summary label optional — the screen shows the fields themselves; DROP `_update_summary`/`summary_label`, they were review-page artifacts).

- [ ] **Step 1: Rework tests.** In `tests/gui/test_wizard.py`: KEEP untouched every test of `build_submission`, `parse_duplicate_job_id`, `preview_scan`, and any 409/submit test that drives the wizard via `set_*` + `accept_and_submit` (these are the payload-equality golden tests — they must pass UNMODIFIED; if one drives page navigation (`wizard.next()`, `currentId`), rewrite ONLY the navigation scaffolding to direct setter calls, preserving the assertions verbatim). ADD:

```python
def test_one_screen_has_no_pages(qtbot, wizard):
    from PySide6.QtWidgets import QWizard
    assert not isinstance(wizard, QWizard)


def test_validation_messages_in_order(qtbot, wizard):
    wizard.profile_combo.setCurrentIndex(-1)
    wizard.accept_and_submit()
    assert "connection" in wizard.status_label.text()
    # (then select a profile via the test fixture's seeded combo, clear source)
```

(Adapt to the file's existing wizard fixture — it seeds a fake client; reuse it. The exact assertion strings are the three validation messages in the Produces block.)

- [ ] **Step 2: Run — new tests fail** (wizard is still a QWizard).

- [ ] **Step 3: Implement the rewrite.** Module docstring updated; keep imports minimal; the dialog:

```python
class NewTransferWizard(QDialog):
    jobSubmitted = Signal(int)
    previewUpdated = Signal(int, int, int)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.state = WizardState()
        self.profile_id: int | None = None
        self._profiles: list[dict] = []
        self._name_touched = False
        self._scan_cancel = threading.Event()
        self.setWindowTitle("New transfer")
        ...  # build the layout per the Produces block
```

Key mechanics (write fully in the implementation, the structure is dictated):
- Direction well: two checkable `segmentButton`s in a `segmentWell` QWidget, exclusive via `QButtonGroup`; `set_direction` checks the right one; toggling updates the folder/prefix field labels (upload: `Source folder (this computer):` / `Destination prefix (in the bucket):`; download: the FoldersPage inverses) and restarts the preview.
- Connection loading: `_refresh()` verbatim from ConnectionPage (`list_profiles` via call_async, combo text `f"{name} — gs://bucket/prefix"`), `_loaded` also captures `self._profiles`; combo change sets `state.profile_name`/`self.profile_id` immediately (no validatePage anymore) and restarts a download-direction preview.
- `set_profile_by_name(name)`: linear scan of `self._profiles`, `setCurrentIndex`, return found.
- Preview: `_restart_preview()` = cancel old event, new event, then the EXACT `_start_preview` body from OptionsPage (upload→`preview_scan` threaded; download→`preview_remote`), debounced: `self._preview_timer = QTimer(self); self._preview_timer.setSingleShot(True); self._preview_timer.setInterval(400); self._preview_timer.timeout.connect(self._restart_preview)`; source/direction/profile changes call `self._preview_timer.start()`.
- Name prefill: on source change, if not `self._name_touched`: `leaf = PurePath(source).name or source or "transfer"; self.name_edit.setText(f"{leaf}-{date.today().isoformat()}")`; `self.name_edit.textEdited.connect(lambda _t: setattr(self, "_name_touched", True))` (textEdited fires only on user edits — programmatic setText does not; `set_job_name` sets `_name_touched = True` explicitly).
- `sync_state()` verbatim semantics from OptionsPage minus `_update_summary`.
- `accept_and_submit()` — byte-identical body to today's except `self.options_page.` references become `self.` (cancel_preview/sync_state/set_status live on the dialog now). Validation FIRST (the three checks in order), returning early with the status message without submitting.
- `accept()` — QDialog default accept comes from the Start button only; `Start transfer` connects to `accept_and_submit`; `super().accept()` still called on success (preserving `_submit_done`/`_resumed` verbatim). `reject`/`closeEvent` cancel the preview as today.
- `cancel_preview()`/`set_status()` — move from OptionsPage verbatim.

- [ ] **Step 4: Run test_wizard.py -v** — all green, payload tests UNMODIFIED. **Step 5: Full suite**, **Step 6: Commit** `git commit -m "feat: one-screen wizard - direction well, side-by-side fields, inline preview, More options"`.

---

### Task 8: Main-window drop target + last-used connection

**Files:**
- Modify: `src/mml_cloud_courier/gui/main_window.py` (drops + wizard opener), `src/mml_cloud_courier/gui/wizard.py` (remember/apply last connection)
- Test: `tests/gui/test_main_window_smoke.py` (append), `tests/gui/test_wizard.py` (append)

**Interfaces:**
- Consumes: `NewTransferWizard.set_source/set_profile_by_name`, `theme._qsettings` idiom.
- Produces: `wizard.last_connection_name() -> str | None` + `wizard.remember_connection(name)` (QSettings("MML", "Cloud Courier") key `last_connection`; written in `_submit_done` success path with `self.state.profile_name`); `MainWindow.setAcceptDrops(True)`; `dragEnterEvent` accepts exactly one local-file URL that `Path.is_dir()`; `dropEvent` calls `self._open_new_transfer(prefill_source=path)`; `_open_new_transfer(prefill_source: str | None = None)` — after constructing the wizard: if prefill_source, `wizard.set_source(prefill_source)`; then `name = last_connection_name()`; if name: `wizard.set_profile_by_name(name)` — but the combo populates ASYNC, so the wizard applies it itself: pass `preferred_profile=name` into the wizard constructor instead — `NewTransferWizard(client, parent, preferred_profile=None)` keyword; `_loaded` selects it when present (first load only, and only when the user hasn't already picked a row: guard on `self._preferred_applied`).

- [ ] **Step 1: Failing tests**

```python
# append to tests/gui/test_wizard.py — reuse the file's fake-client fixture idiom
def test_preferred_profile_selected_on_load(qtbot, make_wizard):
    wizard = make_wizard(preferred_profile="gate-oauth")   # fixture seeds profiles incl. gate-oauth
    qtbot.waitUntil(lambda: wizard.profile_combo.count() > 0)
    assert wizard.state.profile_name == "gate-oauth"


def test_remember_connection_roundtrip(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from mml_cloud_courier.gui import wizard as wizard_module
    monkeypatch.setattr(
        wizard_module, "_qsettings",
        lambda: QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat))
    assert wizard_module.last_connection_name() is None
    wizard_module.remember_connection("NOAA-CCEP")
    assert wizard_module.last_connection_name() == "NOAA-CCEP"
```

```python
# append to tests/gui/test_main_window_smoke.py
def test_drop_event_opens_prefilled_wizard(window, tmp_path, monkeypatch):
    opened = {}
    monkeypatch.setattr(window, "_open_new_transfer",
                        lambda prefill_source=None: opened.setdefault("src", prefill_source))
    from PySide6.QtCore import QMimeData, QUrl
    from PySide6.QtGui import QDropEvent
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path))])
    event = type("E", (), {"mimeData": lambda self=None: mime,
                           "acceptProposedAction": lambda self=None: None})()
    window.dropEvent(event)
    assert opened["src"] == str(tmp_path)
```

(If constructing a synthetic QDropEvent proves friendlier than the stub, use the real class; the assertion is what matters.)

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** `wizard.py` module level:

```python
from PySide6.QtCore import QSettings


def _qsettings() -> QSettings:
    return QSettings("MML", "Cloud Courier")


def last_connection_name() -> str | None:
    value = _qsettings().value("last_connection")
    return value or None


def remember_connection(name: str | None) -> None:
    if name:
        _qsettings().setValue("last_connection", name)
```

Constructor gains `preferred_profile: str | None = None` keyword (default None → falls back to `last_connection_name()`); `self._preferred_applied = False`; in `_loaded`, after populating: if not `self._preferred_applied` and the preferred name matches a profile, select it; set `self._preferred_applied = True` either way once the user changes the combo (connect `activated` → set flag). `_submit_done` success path adds `remember_connection(self.state.profile_name)` before `super().accept()`. `main_window.py`: `self.setAcceptDrops(True)` in `_build_full_ui`; `dragEnterEvent(event)` accepts when exactly one URL, `url.isLocalFile()`, and `Path(url.toLocalFile()).is_dir()` — else ignore; `dropEvent` extracts the dir and calls `self._open_new_transfer(prefill_source=path)`; `_open_new_transfer` gains the keyword, passes `wizard.set_source(prefill_source)` after construction when given. Also gate drops on service/connections: in `dragEnterEvent`, ignore when `not self._service_up or self._no_connections` (a drop must never open a dead-end wizard — mirrors the dimmed button).

- [ ] **Step 4: Run tests** — green. **Step 5: Full suite**, **Step 6: Commit** `git commit -m "feat: drop a folder to start a transfer; wizard remembers last connection"`.

---

### Task 9: Cleanups + acceptance

**Files:**
- Modify: `src/mml_cloud_courier/gui/rail_delegate.py` (delete dead `GROUP_DOT_TOKENS`), `src/mml_cloud_courier/gui/job_tabs.py` (Files empty-header text; STATE column Fixed; remove the Task 1 `headline_label` alias after re-pointing tests), `docs/gui.md` (drop-target sentence)
- Test: `tests/gui/test_job_tabs.py` (append)

**Interfaces:**
- Consumes: everything landed.
- Produces: `FilesTab._update_header` shows `No files yet` when `loaded == 0` (both filtered and unfiltered); STATE column `setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)` after the `resizeSection(2, 204)`; `headline_label` alias removed (grep tests first, re-point to `headline_name`); `GROUP_DOT_TOKENS` gone; docs/gui.md gains, after the Launch paragraph: `Drop a folder anywhere on the window to start a transfer to your last-used connection — the one-screen dialog opens pre-filled.`

- [ ] **Step 1: Failing tests (append to test_job_tabs.py)**

```python
def test_files_header_empty_state(qtbot):
    tab = FilesTab()
    qtbot.addWidget(tab)
    tab.attach(lambda **kw: [])
    tab.set_total(0)
    assert tab.header_label.text() == "No files yet"


def test_files_state_column_fixed(qtbot):
    from PySide6.QtWidgets import QHeaderView
    tab = FilesTab()
    qtbot.addWidget(tab)
    tab.attach(lambda **kw: [])
    header = tab.table.horizontalHeader()
    assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.Fixed
    assert header.sectionSize(2) == 204
```

- [ ] **Step 2: Run to verify failure.** **Step 3: Implement** (all four bullets; `_update_header` early-returns `No files yet` when loaded == 0). **Step 4: Green.** **Step 5:** Full suite + the two acceptance greps from Plan A's Done-when (hex grep still empty) + `git grep -n "GROUP_DOT_TOKENS\|headline_label" -- src tests` → empty. **Step 6: Commit** `git commit -m "chore: plan-b cleanups - empty files header, fixed state column, dead code, docs"`.

---

## Manual smoke check (main session, after Task 9, before merge)

- [ ] Worktree `mmlcc-gui` against the live service (read-only): Progress tab shows headline/percentage/two-segment bar/state card on a completed job; Errors tab renders cards for any job with errors (jobs 13-14 had locked-file errors historically — select one); Summary shows the four cells; first-run does NOT show (profiles exist); drop a folder on the window → prefilled one-screen dialog (CANCEL it — do not submit against live); flip themes live; the two parked appearance items from Plan A (toolbar dividers, rail header rule) get their eye-check here too.

## Done when

- Full suite green (counts recorded; only the known loopback skip may vary).
- Plan A's hex acceptance test still green; no `GROUP_DOT_TOKENS`/`headline_label` references remain.
- Wizard payload tests pass UNMODIFIED (byte-identical `build_submission`).
- All four screens + wizard + drop demonstrated in the manual smoke check.
