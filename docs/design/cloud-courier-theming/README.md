# Handoff: MML Cloud Courier — light/dark theming and UI refresh

## Overview

MML Cloud Courier is a PySide6 desktop client for a Windows transfer service. It moves
research data between a local folder and a GCS bucket, verifies every file with a checksum,
and records the outcome of each file in a ledger. The GUI is a thin client: closing the
window minimizes to the tray and the service keeps working.

This package covers two pieces of work:

1. **A full light/dark theme** for the existing main window, driven by one boolean, with
   every color value specified. This is the primary deliverable.
2. **A set of UI/UX recommendations** — layout, alignment, and information-hierarchy changes
   that address the three problems the redesign was commissioned for: *hard to tell what's
   happening during a transfer*, *too many steps to start a job*, and *looks dated*. These are
   in `RECOMMENDATIONS.md`, ranked and each marked with its implementation cost.

The two are separable. The theme can ship without any layout change; the layout changes are
independently useful without the theme.

## About the design files

The files in this bundle are **design references written in HTML**. They are prototypes that
show intended look, spacing, and behavior. They are **not production code and must not be
ported or embedded**.

The target codebase is Python + PySide6 (Qt Widgets). The task is to **recreate these designs
in Qt**, using QSS stylesheets, `QPalette`, and the existing widget tree in
`src/mml_cloud_courier/gui/`. Every measurement in this document is given in CSS pixels at
100% scale; Qt logical pixels map 1:1 at 100% DPI, and Qt handles the scaling above that, so
use the numbers as-is in layout margins, spacings, and QSS.

Where a value here conflicts with something already in the codebase, the codebase wins for
*behavior* and this document wins for *appearance*.

## Fidelity

**High-fidelity.** Colors, type sizes, weights, spacing, and radii are final. Recreate them
precisely. The one thing deliberately left open is iconography: the prototypes use plain
shapes because `icons.py` already draws icons programmatically, and that approach should
continue.

## The theming model

The whole theme is **one boolean** (`dark`) plus a token table. Every color in the UI derives
from that table — there are no ad-hoc colors anywhere in the design. This matters for two
reasons specific to this codebase:

- `main_window.py` currently hard-codes `#f2dede` / `#a94442` on the service banner with a
  comment explaining the bug it works around (a background-only stylesheet inherits the
  palette's text color, which is white under Windows dark mode — white on pink). Token-driven
  theming removes that entire class of bug: both colors always come from the same table.
- Windows 11 users increasingly run the OS in dark mode. Qt does not restyle a widget app
  automatically. Following the system setting is a small amount of code (below) and is the
  single most effective "looks dated" fix available.

### Recommended implementation

Create `src/mml_cloud_courier/gui/theme.py`:

```python
from dataclasses import dataclass

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

LIGHT = Theme(dark=False, ...)   # values in DESIGN_TOKENS.md
DARK  = Theme(dark=True,  ...)

def current() -> Theme: ...      # reads the setting, falls back to system
def qss(t: Theme) -> str: ...    # one f-string producing the app stylesheet
```

Apply with `app.setStyleSheet(qss(current()))` at startup, and re-apply on change. Widgets
that paint themselves (the rail delegate, any custom progress painting) should read the same
`Theme` object rather than carrying their own colors.

**Detecting the system theme** — Qt 6.5+ exposes it directly:

```python
from PySide6.QtGui import QGuiApplication
from PySide6.QtCore import Qt
scheme = QGuiApplication.styleHints().colorScheme()          # Qt.ColorScheme.Dark / Light
QGuiApplication.styleHints().colorSchemeChanged.connect(on_scheme_changed)
```

If you must support Qt < 6.5 on the deployed Python, fall back to reading
`HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize\AppsUseLightTheme`
via `winreg`, polled once at startup. Do not attempt to detect it from `QPalette`.

**The setting** — add `theme` to the existing settings surface (`settings_dialog.py`) with
three values: `system` (default), `light`, `dark`. `system` is what most users will want and
what the screenshots in this bundle assume.

**Also set `QPalette`**, not only the stylesheet. Native pieces that QSS does not reach —
`QFileDialog`, `QMessageBox`, tooltips, the tray menu, text selection, the caret — read the
palette. At minimum set `Window`, `WindowText`, `Base`, `AlternateBase`, `Text`, `Button`,
`ButtonText`, `Highlight`, `HighlightedText`, `ToolTipBase`, `ToolTipText`, `PlaceholderText`.
Under dark mode also set the window frame dark via `DwmSetWindowAttribute`
(`DWMWA_USE_IMMERSIVE_DARK_MODE = 20`) so the title bar does not stay white above a dark app.

**Disabled states**: Qt derives disabled text from the palette's disabled group. Set it
explicitly to the `disabled` token in both modes; the default derivation is unreadable on the
dark surfaces specified here.

## Screens

All screens are the same window: **1100 × 700**, the size `main_window.py` already sets. The
window is a 34px title bar (system-drawn — the prototypes draw a stand-in), a 40px toolbar, and
a 626px body. When the service banner shows, it inserts between title bar and toolbar and adds
42px; let the window grow rather than compressing the body.

### 1. Main window — Progress tab

The default view. A job is selected in the rail; this tab answers "what is happening right now".

**Layout**

| Region | Size | Notes |
|---|---|---|
| Toolbar | 100% × 40 | padding `0 14`, gap `10`, `chrome` bg, 1px `line` bottom |
| Rail | 262 × 626 | `rail` bg, 1px `line` right, padding `8 0` |
| Content | fill × 626 | tab strip 40px, then padding `18 20`, gap `15` |

**Toolbar, left to right**
- `New transfer` — primary button. Padding `7 13`, radius 6, 12.5px/600, `accent` bg,
  `accent_ink` text. This is the only filled button in the toolbar.
- 1px × 20 `line` divider.
- Segmented group `Pause · Resume · Cancel` in a `track`-filled container, radius 6, padding 2.
  Each segment padding `6 12`, radius 4, 12.5px/500. The enabled one gets `surface` bg and
  `ink` text; unavailable ones get `disabled` text and no background.
- Flexible spacer.
- **Service pill** — radius 999, padding `5 11`, `accent_soft` bg, 1px `accent_edge` border,
  6px dot in `accent`, then 11.5px/500 `accent_text`:
  *"Service running — transfers continue if you close this window"*.
  This is a new element and it earns its place: the close-to-tray guarantee currently only
  appears as a one-time balloon, and users who miss it assume closing kills the transfer.
- Divider, then `Connections` and `Settings` as 12.5px/500 `muted` text buttons.

**Rail** — `QTreeView` with the existing `build_rail_model()`. Restyle only.
- Group headers: 10.5px/600 mono, uppercase, `letter-spacing: .09em`, colored by group —
  `danger` for *Needs attention*, `accent_text` for *Running*, `faint` for the rest — followed
  by the count in 10.5px mono `faint`, then a 1px `line` rule filling the remaining width.
  Padding `11 6 6`.
- Job rows: padding 8, radius 6, gap 9. A 6px dot (top margin 4) in the status color; then
  name at 12.5px/500 (`ink` when selected, `muted` otherwise, ellipsized) and status beneath at
  11px `faint`, 2px above.
- Selected row: `rail_selected` background, 2px `accent` left border.
- Job names carry their id: `#121 IceSeal_Survey_2026_Leg3`.

**Tab strip** — padding `0 20` on `chrome`, 1px `line` bottom. Each tab padding `13 15 11`,
13px. Active: 600 weight, `ink`, 2px bottom border in `accent` — or in `danger` when the
active tab is Errors. Inactive: 400 weight, `faint`.

**Content**
1. **Headline row**, baseline-aligned: job name 18px/600, `letter-spacing: -.015em`,
   ellipsized; beneath it at 11.5px mono `faint`, `Upload · D:\field\leg3 → gs://bucket/prefix`.
   Right-aligned on the same row, the percentage at 26px/600 mono, `letter-spacing: -.02em`.
2. **Progress bar** — 8px, radius 4, `track` bg. Two segments: verified in `accent`, in-flight
   in `accent_2`. Below at 12px, gap 16: `8,866 of 14,208 files · 503 GB of 812 GB` in `muted`,
   the rate in 12px/500 mono `ink`, the ETA in 12px `faint`.
3. **"Every file, by state" card** — `surface`, 1px `line`, radius 9, padding `13 15`. Label in
   10.5px mono uppercase `faint`, `letter-spacing: .08em`. Then a 9px stacked bar, radius 3,
   with one segment per state in state order (verified, checking, transferring, waiting,
   skipped, failed) sized by count. Then a wrapping legend, gaps `8 18`: an 8px radius-2 swatch,
   the label at 11.5px `muted`, the count at 11.5px/500 mono `ink`.
   **This card is the single most valuable addition in the redesign.** It is the only place the
   user can see that 2,104 files were skipped as already-uploaded, which is otherwise
   indistinguishable from files that silently failed.
4. **Two cards**, grid `1.15fr 1fr`, gap 15, filling the remaining height:
   - *In progress — 8 files*: per file, the path at 11.5px mono `ink` ellipsized **from the
     left** (`direction: rtl; text-align: left` in CSS — in Qt use
     `QFontMetrics.elidedText(..., Qt.ElideLeft)`), then a 4px `track` bar with `accent_2` fill
     and, right-aligned, the byte and slice detail at 10.5px mono `faint`. Slice counts come
     straight from the resumable-upload state the service already reports.
   - *Events*: rows of `time · kind · message`. Time 10.5px mono `faint`, kind 10.5px/500 mono
     in the outcome color (verified `accent_text`, failed `danger`, retry `warn`) in a fixed
     52px column, message 11.5px `muted` ellipsized.

### 2. Files tab

**Header row** — padding `13 20`, 1px `line` bottom: a state filter (`All states` + caret) at
padding `7 11`, radius 6, `surface`, 1px `line`; a path filter filling the width; then, right
aligned, `14,208 files · showing 1–200` at 11.5px mono `faint`. That last count is important —
without it a virtualized 61,000-row table looks broken while it pages.

**Columns** — `1fr | 88px | 204px | 0.72fr` = Path, Size, State, Detail. Header row padding
`9 20`, 10.5px mono uppercase `faint`, `letter-spacing: .07em`; Size right-aligned; State and
Detail get 16px left padding.

**Rows** — padding `8 20`, 1px `hairline` bottom, alternating rows tinted
`rgba(255,255,255,.018)` dark / `rgba(18,24,31,.014)` light.
- Path: 11.5px mono `ink`, **left-elided**.
- Size: 11.5px mono `muted`, right-aligned.
- State: 7px radius-2 swatch in the state color, 7px gap, label at 11.5px. `danger_text` for
  *Failed* and *Excluded after repeated failures*; `muted` otherwise.
  **The 204px width is a hard requirement**: `format.py` defines the string
  *"Excluded after repeated failures"*, and truncating it makes an excluded file
  indistinguishable from a failed one — which is the one distinction this tab exists to draw.
- Detail: 11.5px `faint`, ellipsized. Carries `crc32c 8f2ad1`, `slice 5 of 8, 4 done`,
  `unchanged since Aug 4`, `checksum mismatch after 3 attempts`.

### 3. Errors tab

Keeps the existing grouping-by-cause model from `errors_model.py`, and surfaces it instead of
hiding it in a `QTreeWidget`. **This taxonomy is the best thing in the current app** and it is
currently invisible until the user expands a node.

Header strip, padding `17 20 14`, 1px `line` bottom: one sentence at 13px `muted` —
*"423 of 61,022 files did not transfer, from four causes. Two clear themselves; two need
something from you."*

Then one card per cause, gap 11, padding `13 16`, radius 9, `surface`, 1px `line`, plus a **3px
left border** in the cause's tone (`danger` for a cause needing the user, `warn` for
file-locked, `accent_2` for network).

Card contents:
- Top row, items top-aligned: the `message` string at 14px/600 taking the remaining width and
  wrapping; then the count (`6 files`) at 12px/500 mono `faint`; then a tag —
  `Needs you` on `danger_soft`/`danger_text`, or `Retries on its own` on `warn_soft`/`warn_text`
  or `accent_soft`/`accent_text` — 10.5px/500 mono, uppercase, `letter-spacing: .05em`,
  padding `4 7`, radius 4. Both trailing items are fixed-width and must not shrink.
- The `action` string at 12.5px `muted`, 10px below.
- Bottom row: three buttons — `Retry these files`, `Stop retrying`, `Copy file list` — each
  padding `6 11`, radius 6, 11.5px/500, 1px `line`; the first gets a `surface`-tinted fill. Then
  the sample file paths at 10.5px mono `faint`, ellipsized, ending in `…and N more`.
- **Per-card buttons, not one shared button bar.** The current single bar acts on whichever
  node is selected, which is ambiguous when four causes are visible at once.

Order: causes needing the user first, self-clearing causes last. Within each, most files first.

### 4. Summary tab

Padding `22 20`, gap 18.
1. **Verdict** — the status word at 22px/600, `letter-spacing: -.02em`, beside a
   `Needs attention` tag on `danger_soft`. Below, one plain sentence: *"60,599 of 61,022 files
   arrived and verified. 423 did not, from four causes — two of them still need you."*
2. **Four stat cells** in a 1px-gap grid on a `line` background with a `line` border, radius 9,
   overflow hidden (the gap draws the dividers). Each: label 10.5px mono uppercase `faint`,
   value 19px/600 mono. *Files · Transferred · Duration · Did not transfer* — the last in
   `danger`.
3. **"Final state of every file"** — per state, a 200px label at 12.5px `muted`, a 7px `track`
   bar with the state color filling its share, and the count right-aligned in a 64px column at
   12px/500 mono `ink`.
4. Footer row: an explanatory sentence at 12px `faint` — *"Excluded files stay recorded in the
   ledger. The job keeps the Incomplete verdict until they transfer or you stop retrying
   them."* — then `Open report` (outline) and `Resume remaining` (filled `accent`), padding
   `9 16`, radius 7.

### 5. Service-stopped state

The banner from `main_window.py`, restyled and given honest surroundings:
- Banner: padding `11 16`, `danger_soft` bg, 1px `danger_edge` bottom, a 7px `danger` dot, the
  message at 12.5px `danger_text`, and `Start the service` as a filled `danger` button at
  padding `7 14`, radius 6. Message text unchanged from `BANNER_TEXT`.
- Toolbar: `New transfer` goes to `track`/`disabled`; the Pause/Resume/Cancel group all go
  `disabled`; the pill flips to `danger_soft` / `danger` dot / `danger_text` and reads
  *"Service stopped — nothing is moving"*.
- Rail: the running job's status changes to *"Stalled — service stopped"* with a `warn` dot.

The point is that no part of the window should look operable when nothing can operate.

### 6. First-run state

No rail, no empty table. A centered 520px column:
- A 56px radius-14 `accent_soft` tile with a 18px radius-4 `accent`-bordered square.
- Heading *"Nothing has been transferred yet"* at 21px/600, `letter-spacing: -.02em`.
- Body at 14px/1.6 `muted`: *"Courier needs one connection before it can move anything — a
  bucket, and a credential the service can use on its own. After that, every transfer is a
  folder and a Start."*
- Three numbered steps as left-aligned cards, gap 11, padding `13 15`, radius 9, `surface`,
  1px `line`: a 20px circular `accent_soft` badge with an 11px mono `accent_text` numeral, then
  a 13px/600 title and a 12px/1.45 `muted` body. The three: *Add a connection* / *Point it at a
  folder* / *Close the window whenever you like* — the third explains the service model, which
  is the one concept a new user must have.
- Two buttons: `Add a connection` (filled `accent`) and `Read the setup guide` (outline).
- The toolbar dims exactly as in the service-stopped state, and the pill reads *"Service
  running — no connection set up yet"*. Offering `New transfer` before a connection exists
  leads straight to a dead end in the wizard.

## Interactions and behavior

Nothing in this package changes the service contract. Behavior to preserve or add:

- **Polling** stays as-is (`JobsPoller`, `JobWatcher`). The redesign adds no new endpoints.
- **Theme switching** must be live: re-apply the stylesheet and palette on
  `colorSchemeChanged` and on the settings change, without restarting. Widgets holding
  their own colors must subscribe.
- **Progress animation**: the prototypes tick the percentage for demonstration only. Drive it
  from real poll data; do not animate between polls beyond what a `QProgressBar` does natively.
- **Rail selection** is already wired; keep the pending-select behavior.
- **Elision**: paths elide left everywhere they appear. Qt: `Qt.ElideLeft`.
- **Tooltips**: any elided path gets the full path as a tooltip. Any state chip gets the
  `message`/`action` pair as a tooltip.
- **Focus**: every button and row needs a visible keyboard focus ring — 2px `accent` outline at
  2px offset. Qt's default focus rect disappears on styled widgets; add it in QSS with
  `:focus`.
- **Hover**: rows tint by 4% of `ink`; buttons lighten their border to `accent_edge`.
- **`Stop retrying these files`** keeps its existing confirmation dialog, including the text
  about the job finishing as INCOMPLETE.

## State

No new application state beyond:
- `theme: "system" | "light" | "dark"` in settings (persisted).
- `resolved_dark: bool`, derived, recomputed on `colorSchemeChanged`.
- The Files tab's two filters (state, path substring) — the model already pages; both filters
  belong in the query the page fetcher issues, not in client-side filtering.

## Design tokens

See `DESIGN_TOKENS.md` for the full table in both modes, with hex values, plus the type,
spacing, radius, and elevation scales.

## Assets

None. No image or icon files are used. Icons stay programmatic per `icons.py`. Fonts:
the prototypes use **Instrument Sans** and **Geist Mono**; ship-safe equivalents on Windows are
**Segoe UI Variable** (or Segoe UI) for the interface and **Cascadia Mono** for numerics and
paths — both present on Windows 10 1809+ and Windows 11. Use the mono face for everything
numeric (percentages, rates, counts, byte sizes, checksums, timestamps, paths); it stops the
figures from jittering as they update.

## Files in this bundle

| File | What it is |
|---|---|
| `README.md` | This document |
| `DESIGN_TOKENS.md` | Full token table, both modes, plus type/spacing/radius scales |
| `RECOMMENDATIONS.md` | Ranked UI/UX changes beyond theming, with cost and rationale |
| `Console Window.dc.html` | The main window: all four tabs, both themes, service-down and first-run. Props `dark`, `tab`, `mode` |
| `Console Theme.dc.html` | The condensed window used for palette exploration; five palettes |
| `Cloud Courier Redesign.dc.html` | The full exploration: every direction and turn, with rationale |
| `screenshots/` | 2× PNGs of all twelve states, both themes — see `screenshots/README.md` |

Open any of them directly in a browser. `Console Window.dc.html` is the authoritative one for
the shipping design.
