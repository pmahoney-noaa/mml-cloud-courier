# Icon Set — Design

**Date:** 2026-08-10
**Status:** Approved (brainstorm 2026-08-10; all three scope decisions user-selected)
**Sequencing:** First sub-project of the round icons → Phase 6 packaging → README.
Its committed `mmlcc.ico` is a direct input to the packaging sub-project.

## Goal

Replace the programmatic placeholder icon with the accepted **whale-fluke-into-cloud**
mark, rendered from SVG masters into committed raster assets, visible in the running
app (title bar, tray, taskbar) and ready for the Phase 6 exe/installer.

## Decisions (user-selected)

1. **Inventory: one mark everywhere.** A single mark serves the window icon, tray
   icon, taskbar, exe `.ico`, and installer artwork. No tray-state variants, no
   toolbar glyphs. New surfaces can be added later from the same SVG master.
2. **Theming: one self-contained mark.** Fixed brand colors — a filled cloud-blue
   badge with the fluke/cloud reversed out in white — that reads on any backdrop.
   No light/dark variants, no runtime tinting. The theme-change icon-repaint hooks
   in `gui/__main__.py` and `gui/tray.py` are **deleted** (net code removal).
   Drafting starts from the app's accent blue; final color values are fixed in the
   SVG at the preview checkpoint. Color literals live **only** in SVG files.
3. **Shipping: committed rasters as package data.** A dev-run rasterizer renders
   PNGs + a multi-resolution `.ico`, which are committed under
   `src/mml_cloud_courier/gui/assets/` and ship in the wheel. No build-time render
   step, no runtime SVG loading.

## Assets and layout

| Path | Role | Committed | Ships in wheel |
|---|---|---|---|
| `assets/icons/mark.svg` | Master artwork (full-size) | yes | no |
| `assets/icons/mark-16.svg` | Optional simplified small-size master — created **only if** the 16 px render of the main master is illegible at the preview checkpoint | conditional | no |
| `src/mml_cloud_courier/gui/assets/mark-{16,20,24,32,48,64,128,256}.png` | Rendered rasters | yes | yes |
| `src/mml_cloud_courier/gui/assets/mmlcc.ico` | Multi-res icon (all sizes above) | yes | yes |

Masters live at the repo root (`assets/`) because they are source, not payload.
Rendered assets live inside the package so `importlib.resources` reaches them in
dev (editable install), wheel, and frozen (PyInstaller) contexts alike.

## Rasterizer — `scripts/render_icons.py`

> **Amendment (2026-08-10, planning):** originally `tools/render_icons.py`;
> `/tools/` is gitignored (downloaded binaries only), so tracked dev scripts
> live in a new `scripts/` directory instead.

- Dev-run script (never a build step, never imported by the app). Uses the venv's
  PySide6 `QtSvg`/`QSvgRenderer` to render each size; when `mark-16.svg` exists it
  is used for the 16 and 20 px renders (else the main master serves all sizes).
- Assembles `mmlcc.ico` itself with a small pure-Python ICO packer (ICO with
  PNG-compressed entries is valid on Windows Vista+; Qt cannot write multi-res ICO).
- Deterministic output paths; running it twice with unchanged masters produces
  identical files (byte-stable PNG encoding settings) so `git status` stays clean.
- Needs a Qt platform; must run under `QT_QPA_PLATFORM=offscreen` so it works
  headless (existing test-suite convention).

## Wiring changes

- `gui/icons.py`: `app_icon()` keeps its name and signature but becomes a pure
  loader — a module-cached `QIcon` built by `addFile()`-ing each PNG size via
  `importlib.resources`. No `QPainter`, no `theme` import, no color code. The
  module docstring's "Phase 6 can swap real artwork in one place" promise is
  fulfilled at that one place.
- `gui/__main__.py`: drop the theme-connected `setWindowIcon` refresh lambda;
  set the window icon once (no longer needs to wait for theme application).
- `gui/tray.py`: drop `_refresh_icon` and its `theme.notifier.changed` connect
  (and the corresponding disconnect in `shutdown()`).
- `pyproject.toml`: no change expected — hatchling includes package-dir files by
  default; the plan must **verify** the wheel actually contains `gui/assets/*`.

## Constraints

- `test_no_hex_colors_outside_theme_py` scans `gui/*.py`: after this change
  `icons.py` contains no color literals at all. SVG/asset files are out of scope
  for that test. `theme.py` remains the only `.py` exception.
- The engine never imports gui/Qt — untouched here (all changes are gui-side +
  root-level scripts/assets).
- Existing GUI tests that call `app_icon()` (directly or via tray/main-window
  construction) must keep passing; offscreen rendering never paints, so any new
  assertion that needs pixels must force with `grab()` (recorded Qt gotcha) — but
  the planned tests below avoid pixel assertions entirely.

## Tests

- `app_icon()` returns a non-null `QIcon` whose `availableSizes()` include the
  expected set (16…256).
- Rendered assets exist as package resources (every size + the `.ico`), guarding
  against a half-committed regeneration.
- A wheel-content check (build step in the plan, not necessarily a pytest) that
  `gui/assets/` ships.
- No new pixel/color assertions; the hex-color acceptance test is unmodified.

## Preview checkpoint (user gate, before any wiring)

Render a preview sheet — the mark at 16/32/256 on light and dark backdrops — and
iterate with the user until the artwork is accepted. Only then wire the loader.
The decision on `mark-16.svg` (simplified small master) is made here.

## Done when

- Full suite green with recorded counts (`-o addopts= -q`; never estimate from
  the bare `-q` run on this host).
- Mark visible in the running app in both themes: title bar, tray, taskbar —
  smoke-checked with the user.
- `mmlcc.ico` committed and loadable, ready for the packaging sub-project.
- Merged to master `--no-ff` and pushed.

## Out of scope

Tray-state variants, toolbar glyphs, installer wizard artwork beyond the .ico,
any theme-aware icon behavior, README/marketing imagery.
