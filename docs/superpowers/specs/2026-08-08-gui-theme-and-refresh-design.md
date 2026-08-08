# GUI theme + refresh — design

**Source:** Claude Design handoff "Cloud Courier theming" (2026-08-08). The three
markdown documents from that bundle are committed verbatim under
`docs/design/cloud-courier-theming/` — `README.md` (screens, measurements,
theming model), `DESIGN_TOKENS.md` (the full token table, type/spacing/radius
scales, mandatory column widths), `RECOMMENDATIONS.md` (11 ranked UX changes).
**Those three documents ARE the appearance spec**; this document adds the
decisions, architecture, phasing, and testing strategy around them. The
prototype HTML files and screenshots remain untracked in
`.claude/design/design_handoff_cloud_courier_theming/` (local viewing aids —
every measurement they show is in the committed text docs).

Precedence rule (from the handoff, adopted): where the handoff conflicts with
the codebase, **the codebase wins for behavior, the handoff wins for
appearance**.

## Goals

The redesign was commissioned against three problems: *hard to tell what's
happening during a transfer*, *too many steps to start a job*, *looks dated*.

## Decisions (2026-08-08)

- **Scope: everything.** Full token-driven light/dark theme AND all 11
  recommendations, including the four redesigned screens (Progress additions,
  Errors cards, Summary, first-run) and the service-down state.
- **Wizard: full Recommendation 4.** One-screen replacement for the four-page
  QWizard PLUS the main-window folder drop target. Validation rules and the
  submission payload are preserved verbatim.
- **Two implementation plans**, each its own SDD worktree → review → merge:
  - **Plan A — theme foundation + honest chrome** (theme core, toolbar/pill,
    service-down honesty, rail restyle, elision, mono numerics, Files header
    count, realignments, restyled tabs/banner).
  - **Plan B — information reworks** (Progress-tab cards incl. "Every file, by
    state", Errors cards, Summary, first-run screen, wizard collapse + drop
    target).
- **Theme persistence deviation:** the `theme` setting (`system|light|dark`,
  default `system`) is exposed in the existing settings dialog but persisted in
  GUI-side `QSettings("MML", "Cloud Courier")`, NOT the service's
  `settings.json`. The service is headless; a display theme is not service
  config. This is the one deliberate deviation from the handoff.
- **Icons stay programmatic** (`icons.py`), per the handoff. The custom icon
  set (whale-in-cloud) is parked separately and is not part of this work.
- The handoff's "deliberately not recommended" list is binding: no aggregate
  dashboards, no between-poll progress interpolation, no illustrated empty
  states, no fifth color family.

## Architecture

### Theme core — new `src/mml_cloud_courier/gui/theme.py`

```python
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

LIGHT: Theme   # values transcribed exactly from DESIGN_TOKENS.md
DARK: Theme

def theme_setting() -> str                  # "system" | "light" | "dark", from QSettings
def set_theme_setting(value: str) -> None
def resolve(setting: str) -> Theme          # "system" reads QGuiApplication.styleHints().colorScheme()
def current() -> Theme                      # the last-applied Theme (module state)
def qss(t: Theme) -> str                    # ONE f-string: the whole app stylesheet
def mono_font(size_pt: float, weight: int = 400) -> QFont   # Cascadia Mono, fallback Consolas
def apply_theme(app: QApplication, t: Theme) -> None
    # setStyleSheet(qss(t)) + full QPalette (incl. Disabled group and the roles
    # named in the handoff README) + updates current() + emits notifier.changed(t)

class ThemeNotifier(QObject):
    changed = Signal(object)                # Theme
notifier: ThemeNotifier                     # module singleton
```

- Custom painters (rail delegate, stacked/state bars, `icons.py` group dots)
  read `theme.current()` at paint time and repaint on `notifier.changed` —
  no widget caches hex values. This structurally retires the
  hard-coded-banner-colors bug class called out in the handoff.
- Live switching: `gui/__main__.py` connects
  `styleHints().colorSchemeChanged` → re-resolve (only when setting is
  `system`) → `apply_theme`; the settings dialog change does the same
  directly. No restart.
- **Dark title bar:** after `apply_theme`, top-level windows call
  `DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE=20, ...)` via
  ctypes; failures are swallowed (cosmetic, and absent off-Windows/CI).
- Disabled states come from the palette's Disabled group set to the `disabled`
  token — never from Qt's derived defaults.
- PySide6 floor is ≥ 6.7 (pyproject), so `colorScheme()` exists; the winreg
  fallback in the handoff is NOT implemented (YAGNI on our floor).

### Plan A — theme foundation + honest chrome

Touches: `theme.py` (new), `gui/__main__.py`, `main_window.py`, `tray.py`
(menu inherits palette; pill lives in the toolbar not the tray),
`jobs_model.py`/new rail delegate, `job_tabs.py` (restyle-only parts),
`files_model.py` view header, `settings_dialog.py`, `format.py` (unchanged
strings, consumed by delegates), tests.

1. Token table + QSS + palette + DWM + live switching + `theme` in the
   settings dialog (Recommendation 8).
2. Toolbar per README screen 1: filled `New transfer` left, segmented
   Pause/Resume/Cancel well, spacer, **status pill**, Connections/Settings as
   text buttons (Recommendations 2, 11). Pill states: running / stopped
   ("Service stopped — nothing is moving", danger tones) / running-no-
   connections ("Service running — no connection set up yet").
3. Service-down honesty (Recommendation 3): transport controls + `New
   transfer` disable; running rail rows read "Stalled — service stopped"
   (warn dot); banner restyled from tokens, its text (`BANNER_TEXT`) and
   elevation behavior unchanged.
4. Rail restyle (Recommendation 6): `QStyledItemDelegate` over the existing
   `build_rail_model()` — colored uppercase group headers with counts, 2-line
   job rows with status dot, `#id name`, selected treatment. 262px fixed width.
5. Left elision + tooltips everywhere a path renders (Recommendation 7);
   `mono_font()` on every numeric/path label (Recommendation 9); Files header
   gains `N files · showing X–Y` (Recommendation 10); realignment items
   (Recommendation 11) that don't belong to a Plan B screen.

### Plan B — information reworks

Touches: `job_tabs.py` (Progress/Summary rebuild), new `errors_view.py`
(cards; `errors_model.py` untouched), new `first_run.py`, `wizard.py`
(rewrite), `main_window.py` (drop target, first-run swap), tests.

1. **Progress tab** per README screen 1: headline row (18px name / 26px mono
   percentage), two-segment progress bar, **"Every file, by state" card**
   (stacked bar + legend in the fixed state order and colors from
   DESIGN_TOKENS.md — fed by the state counts the tab already derives from
   poll data), *In progress* card (per-file left-elided path, 4px bar, slice
   detail), *Events* card (52px kind column, outcome-colored).
2. **Errors tab** per README screen 3 (Recommendation 5): per-cause cards,
   always expanded, ordered needs-you-first; tag (`Needs you` on danger tones /
   `Retries on its own` on warn or accent tones); the `message` and `action`
   strings and the three actions come from the existing model/handlers —
   **per-card buttons**, same confirmation dialog on `Stop retrying`.
3. **Summary tab** per README screen 4: verdict + tag, four stat cells, final
   state-of-every-file rows (200px labels), footer sentence + `Open report` /
   `Resume remaining`.
4. **First-run** per README screen 6: centered column, three step cards, `Add
   a connection` (filled) + `Read the setup guide` (opens the repo's local
   `docs/gui.md` via QDesktopServices). Toolbar dims as in service-stopped;
   pill reads the no-connection line. Shown when the service reports zero
   profiles AND zero jobs.
5. **Wizard** (Recommendation 4, full): one-screen `QDialog` — direction
   toggle, folder + connection/prefix side by side, live inline scan preview
   (existing preview worker), pre-filled `{leaf}-{date}` name, `Start later` +
   SHA-256 audit behind a More-options disclosure, one `Start transfer`.
   Mapped-drive warning inline under the folder field. Validation rules and
   submission payload byte-identical to today. Main window:
   `setAcceptDrops(True)`; a dropped folder opens the screen pre-filled
   (source, last-used connection, derived name).

### Explicit non-changes

No service/API contract changes; polling (`JobsPoller`, `JobWatcher`) as-is;
no new endpoints; Files-tab filters run in the page-fetcher query (the model
already pages); rail pending-select behavior preserved; red reserved for
failure; no animation between polls.

## Testing strategy

- **Behavior via pytest-qt** (house TDD): pill state text per service/
  connection state; toolbar disabling when down; rail delegate text/second
  line; left-elision (`ElideLeft`) + tooltip presence; Files header count
  string; state-card segment counts vs a synthetic files payload; error-card
  ordering, tags, and that each card's buttons invoke the existing handlers
  with that card's cause; Summary numbers; first-run trigger condition; wizard
  submit payload equality against a golden payload from the old wizard's
  tests; drop-event prefill; `resolve()` for the three settings incl. a fake
  styleHints for `system`; `apply_theme` swaps stylesheet + palette live.
- **Token sanity test** instead of appearance tests: both `Theme` constants
  complete (no empty fields), all hex parseable, and the contrast floors the
  handoff states (`ink`/`muted` on `surface` ≥ 7:1 and 4.5:1 respectively in
  both modes) hold via a small relative-luminance check.
- QSS/pixel appearance is reviewed by eye against the untracked screenshots,
  not unit-tested.
- Suite counts will grow; each plan's gate is "full suite green", not a frozen
  count. GUI tests keep using ephemeral ports/temp dirs and never touch the
  live install.

## Acceptance

- **Plan A done when:** app follows Windows light/dark live and via the
  setting; title bar matches; no hard-coded colors remain in `gui/` outside
  `theme.py` (grep-checkable: `#[0-9a-fA-F]{6}` under `src/mml_cloud_courier/gui/`
  hits only `theme.py`); pill + honest-disable behaviors pass their tests;
  full suite green.
- **Plan B done when:** the four screens match the committed README specs
  (eye-check against screenshots), wizard collapses to one screen with
  payload-equality test green, drop target works, full suite green.
