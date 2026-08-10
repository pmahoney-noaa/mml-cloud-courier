# Files-tab live refresh, checksums, and report files table — design spec

Date: 2026-08-10
Status: approved (decisions taken by Peter Mahoney in session)
Baseline: master cfd99ac (job archiving merged); suite 709 passed / 13 skipped.

## 1. Problem and decisions

| # | Item | Decision |
| --- | --- | --- |
| 1 | BUG: Files tab never updates during a running transfer | **Polite auto-refresh**: progress-signature trigger, ~2s throttle, only while scrolled at top; changes arriving while browsing set a pending flag flushed when the scrollbar returns to top; run-settle routes through the same rules |
| 2 | SIZE/STATE alignment | **Center both** (SIZE is right-aligned today; STATE default-left) |
| 3 | Checksum visibility in the Files tab | **CRC32C column + tooltip**: new fixed mono column showing remote CRC32C (base64); tooltip carries local CRC32C, remote CRC32C, and SHA-256 when present |
| 4 | Per-file content in the generated report | **HTML table, capped**: report.html gains a Files section (path, size, state, detail, CRC32C, SHA-256); jobs over the cap embed the first 5,000 + a manifest.csv pointer. manifest.csv/summary.json unchanged |

Root cause of #1 (investigated, confirmed): the Files tab loads once at selection
(`main_window.py` `_on_rail_current_changed` → `files_tab.attach`) and its only
refresh trigger is `_refresh_selected_job`, which runs solely after explicit job
actions. The watcher's live path (`_on_watcher_snapshot`) never touches the files
model. File completions emit NO events (events are job-level milestones plus
failures), so the reliable live signal is the snapshot's `progress` payload
(`state_counts`/`files_done`), present on every tick.

## 2. Files-tab auto-refresh policy (FilesTab owns it)

`FilesTab` gains the policy as testable methods; `MainWindow` stays thin.

- `maybe_auto_refresh(progress: dict | None) -> None`:
  - Computes a signature from `progress` (`files_done`, `state_counts` — exact
    fields verified at plan time against `JobProgress`). `None`/missing → no-op.
  - If the signature equals the last seen one → no-op (no churn on idle ticks).
  - New signature: if the table's vertical scrollbar is at 0 AND ≥2.0s
    (`time.monotonic`) since the last auto-refresh → `refresh()` now; otherwise
    set `_pending_refresh = True` (both the throttled case and the
    scrolled-deep case defer the same way).
- Scrollbar `valueChanged` hook: when the value hits 0 and `_pending_refresh`
  is set → `refresh()` (throttle does not block this flush; clear the flag).
- `attach()` resets signature, pending flag, and throttle clock (job switch).
- Manual paths (`refresh()`, filter change) behave exactly as today (full
  reset; scroll-to-top acceptable on an explicit action).

MainWindow wiring:

- `_on_watcher_snapshot`: `self.files_tab.maybe_auto_refresh(snap.get("progress"))`
  (snapshots only flow for the selected job; no extra guard needed).
- `_on_watcher_settled` (final state): route through the SAME politeness —
  call `maybe_auto_refresh` with the final job's progress; if the user is
  scrolled deep, the pending flag delivers the final state when they return
  to the top. No forced yank at completion.

## 3. FileTableModel changes

- `HEADERS = ("PATH", "SIZE", "STATE", "CRC32C", "DETAIL")` — CRC32C inserted
  at index 3; DETAIL moves to 4 and keeps the stretch resize mode.
- Alignment: `TextAlignmentRole` returns AlignCenter|AlignVCenter for columns
  1 (SIZE — replaces today's right-align) and 2 (STATE). CRC32C column is
  centered too (8-char fixed-width values).
- CRC32C display (column 3): `crc32c_to_base64(row["remote_crc32c"])` when
  set, else "—" (em dash). Mono font via the existing `FontRole` approach or
  QSS — plan decides, matching how the PATH tooltip/mono conventions work.
- CRC32C tooltip (column 3): multi-line — `local  {b64-or-—}`,
  `remote {b64-or-—}`, and `sha256 {value}` only when present.
- Column widths in `FilesTab.attach`: SIZE 88 (unchanged), STATE 204 fixed
  (unchanged, load-bearing), CRC32C ~110 fixed, DETAIL stretch.
- No API/payload changes: `local_crc32c`, `remote_crc32c` (ints), `sha256`
  already flow through `GET /jobs/{id}/files` rows.

## 4. report.html files table

- `_render_html` gains a `rows` parameter (write_report already fetches them).
- New "Files (N)" section after Failures: table with columns Path (code),
  Size (human-readable via a small local `_human_bytes` in report.py
  mirroring gui/format.py's `human_bytes` semantics — the engine must NOT
  import from `gui`, which pulls in Qt), State, Detail (`error_message` or
  ""), CRC32C (remote,
  base64 via existing `_b64_or_empty`), SHA-256 (`<code>` cell, may be empty).
- Cap: module constant `_MAX_FILES_SHOWN = 5000`. Over the cap: render the
  first 5,000 and append "… and {n} more — the complete list is in
  manifest.csv beside this report." (Tests shrink the constant rather than
  seeding 5,001 rows.)
- Minimal CSS additions to the existing inline style block (bordered table
  cells, `word-break` for the sha256 column). The report stays fully
  self-contained. `summary.json` and `manifest.csv` byte-behavior unchanged.

## 5. Tests

- Model: center alignment for SIZE/STATE; CRC32C display ("—" before
  transfer, base64 after) and tooltip content incl. sha256-only-when-present;
  header/column-count updates; DETAIL still stretches.
- FilesTab policy: signature change at top → refresh; identical signature →
  no-op; second change inside 2s → deferred with pending set; scrolled-deep
  change → pending; scrollbar returning to 0 → flush; attach resets state.
  (Fake fetcher counting `set_filter` calls; drive scrollbar/pending via the
  hook method rather than real scroll geometry where offscreen ranges are 0.)
- Wiring: `_on_watcher_snapshot` invokes `maybe_auto_refresh` with the
  snapshot progress; `_on_watcher_settled` routes through it too.
- Report: files table renders with checksums; the cap note appears when rows
  exceed the (shrunken) constant; existing report tests untouched and green.
- Full suite `-o addopts= -q`, counts recorded (baseline 709 passed / 13
  skipped).

## 6. Out of scope

- No API changes; no CSV/JSON report changes; no per-file progress bars in
  the Files tab (the Progress tab owns live in-flight display); no
  persistence of filter state.

## 7. Execution & done criteria

Spec → plan (superpowers:writing-plans) → worktree SDD (~4 tasks; master
already pushed at cfd99ac; fresh worktrees need tools\ created + emulator
copied; baseline 709/13 verified before Task 1). Done when: full suite green
with recorded counts; manual smoke with the user (run a small live transfer
or observe the next real one — the live service is already on current code,
so this round's GUI changes work against it immediately; the report change
shows on the next report generation); merged --no-ff and pushed.
