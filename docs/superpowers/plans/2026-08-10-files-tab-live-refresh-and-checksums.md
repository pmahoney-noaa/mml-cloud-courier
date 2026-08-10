# Files-Tab Live Refresh + Checksums + Report Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Files tab update live during a running transfer (politely — never yanking scroll), center SIZE/STATE, add a CRC32C column with a checksum tooltip, and embed a capped per-file table in report.html.

**Architecture:** `FilesTab` owns the auto-refresh policy as testable methods (signature-change trigger from watcher-snapshot progress, ≥2s throttle, top-of-table gate, pending flush on scroll-return); `FileTableModel` grows a CRC32C column at index 3 (DETAIL → 4); `engine/report.py` threads the already-fetched rows into `_render_html` with a local byte formatter (the engine must not import gui/Qt).

**Tech Stack:** Python 3.12, PySide6, pytest + pytest-qt (offscreen).

**Authoritative documents:** spec `docs/superpowers/specs/2026-08-10-files-tab-live-refresh-and-checksums-design.md`.

## Global Constraints

- File completions emit NO events — the live trigger is the snapshot's `progress` dict (`asdict(JobProgress)`: keys incl. `files_done`, `state_counts`). Signature = `(files_done, sorted state_counts items)`.
- Politeness contract (binding): auto-refresh fires only when the table's vertical scrollbar value is 0 AND ≥2.0s (`time.monotonic`) since the last auto-refresh; otherwise `_pending_refresh` is set; a later tick or the scrollbar returning to 0 flushes it (the scroll-return flush is NOT throttle-blocked). `attach()` resets signature, pending, and clock. Run-settle routes through the same policy — no forced yank at completion.
- Columns: `HEADERS = ("PATH", "SIZE", "STATE", "CRC32C", "DETAIL")`. SIZE/STATE/CRC32C centered (SIZE's right-align is REPLACED). STATE stays 204px Fixed (load-bearing); SIZE 88; CRC32C 110 Fixed; PATH and DETAIL Stretch. CRC32C shows `crc32c_to_base64(remote_crc32c)` or "—"; its tooltip lists local, remote, and sha256-only-when-present.
- No API/payload changes: `local_crc32c`/`remote_crc32c` (raw ints) and `sha256` already flow through `GET /jobs/{id}/files` rows. `crc32c_to_base64(value: int)` (core/hashing.py:80) has NO None handling — guard before calling.
- report.html: files table capped by module constant `_MAX_FILES_SHOWN = 5000`; over the cap, first 5,000 + "… and {n} more — the complete list is in manifest.csv beside this report." The report must stay self-contained — existing tests assert `"http://" not in html` and no `<script src`/`<link`; do not introduce URLs anywhere. `summary.json` and `manifest.csv` byte-behavior unchanged. Engine must NOT import from `gui` (Qt) — mirror `human_bytes` locally.
- Zero 6-digit hex in gui/*.py outside theme.py (report.py is engine/, exempt — it already contains hex).
- Tests never touch the live install; targeted runs `.venv\Scripts\python -m pytest <files> -q -o addopts=`; full suite `-o addopts= -q` only (counts recorded, never estimated).
- Suite baseline on master 06ab5d1: **709 passed, 13 skipped**.
- SDD conventions: every dispatch cds into the worktree FIRST and re-verifies `git rev-parse --show-toplevel` + expected parent commit before each commit; one commit per task; never amend; never bare `git stash`.

---

### Task 0: Worktree setup (no commit)

- [ ] **Step 1:** `git push origin master` from the main checkout.
- [ ] **Step 2:** EnterWorktree (suggested name: `files-live-refresh`).
- [ ] **Step 3:** Provision (PowerShell, from the worktree root — fresh worktrees lack `tools\`):

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]" --quiet
New-Item -ItemType Directory -Force tools | Out-Null
Copy-Item C:\Users\pmaho\Documents\VibeCode\mml_cloud_transfer\tools\fake-gcs-server.exe tools\fake-gcs-server.exe
```

- [ ] **Step 4:** Baseline `.venv\Scripts\python -m pytest -o addopts= -q` — expect exactly **709 passed, 13 skipped**; record.

---

### Task 1: FileTableModel — CRC32C column + centered SIZE/STATE

**Files:**
- Modify: `src/mml_cloud_courier/gui/files_model.py`
- Modify: `src/mml_cloud_courier/gui/job_tabs.py` (`FilesTab.attach` column setup, lines ~363-369)
- Test: `tests/gui/test_files_model.py` (append + extend the `_rows` helper), `tests/gui/test_job_tabs.py` (append)

**Interfaces:**
- Consumes: `crc32c_to_base64` from `mml_cloud_courier.core.hashing`; `theme.mono_font`.
- Produces: `FileTableModel.HEADERS == ("PATH", "SIZE", "STATE", "CRC32C", "DETAIL")`; column semantics for every later consumer (DETAIL at index 4). Task 2 does not depend on columns; Task 3 is independent.

- [ ] **Step 1: Write the failing tests.** In `tests/gui/test_files_model.py`, extend `_rows` so checksum fields exist but stay optional:

```python
def _rows(n, state="verified", **extra):
    return [{"relative_path": f"f{i}.bin", "size_bytes": 1000 + i,
             "state": state, "error_message": None, **extra} for i in range(n)]
```

Append:

```python
def test_headers_include_crc32c_before_detail(qapp):
    assert FileTableModel.HEADERS == ("PATH", "SIZE", "STATE", "CRC32C", "DETAIL")


def test_size_state_and_crc32c_are_centered(qapp):
    from PySide6.QtCore import Qt
    model = FileTableModel(RecordingFetcher(_rows(1)))
    model.set_filter()
    center = int(Qt.AlignmentFlag.AlignCenter)
    for column in (1, 2, 3):
        assert model.data(model.index(0, column),
                          Qt.ItemDataRole.TextAlignmentRole) == center
    assert model.data(model.index(0, 0),
                      Qt.ItemDataRole.TextAlignmentRole) is None


def test_crc32c_column_renders_base64_or_dash(qapp):
    from mml_cloud_courier.core.hashing import crc32c_to_base64
    done = _rows(1, remote_crc32c=3405691582, local_crc32c=3405691582)
    pending = _rows(1, state="pending")          # no checksum keys at all
    model = FileTableModel(RecordingFetcher(done))
    model.set_filter()
    assert model.data(model.index(0, 3)) == crc32c_to_base64(3405691582)
    model = FileTableModel(RecordingFetcher(pending))
    model.set_filter()
    assert model.data(model.index(0, 3)) == "\u2014"


def test_crc32c_tooltip_lists_local_remote_and_optional_sha256(qapp):
    from PySide6.QtCore import Qt
    from mml_cloud_courier.core.hashing import crc32c_to_base64
    with_sha = _rows(1, remote_crc32c=99, local_crc32c=99, sha256="ab" * 32)
    model = FileTableModel(RecordingFetcher(with_sha))
    model.set_filter()
    tip = model.data(model.index(0, 3), Qt.ItemDataRole.ToolTipRole)
    b64 = crc32c_to_base64(99)
    assert f"local  {b64}" in tip and f"remote {b64}" in tip
    assert "sha256 " + "ab" * 32 in tip
    without = _rows(1, remote_crc32c=99)
    model = FileTableModel(RecordingFetcher(without))
    model.set_filter()
    tip = model.data(model.index(0, 3), Qt.ItemDataRole.ToolTipRole)
    assert "sha256" not in tip
    assert "local  \u2014" in tip                 # missing local renders as dash


def test_detail_column_moved_to_index_4(qapp):
    rows = _rows(1, state="failed")
    rows[0]["error_message"] = "in use by EDITOR.EXE"
    model = FileTableModel(RecordingFetcher(rows))
    model.set_filter()
    assert model.data(model.index(0, 4)) == "in use by EDITOR.EXE"
    assert model.columnCount() == 5
```

Append to `tests/gui/test_job_tabs.py` (beside `test_files_state_column_fixed`):

```python
def test_files_crc32c_column_fixed_and_detail_stretches(qtbot):
    from PySide6.QtWidgets import QHeaderView
    tab = FilesTab()
    qtbot.addWidget(tab)
    tab.attach(lambda **kw: [])
    header = tab.table.horizontalHeader()
    assert header.sectionResizeMode(3) == QHeaderView.ResizeMode.Fixed
    assert header.sectionSize(3) == 110
    assert header.sectionResizeMode(4) == QHeaderView.ResizeMode.Stretch
    assert header.sectionSize(2) == 204          # STATE unchanged, load-bearing
```

- [ ] **Step 2: Run to verify failure.**

Run: `.venv\Scripts\python -m pytest tests/gui/test_files_model.py tests/gui/test_job_tabs.py -q -o addopts=`
Expected: new tests FAIL (HEADERS has 4 entries; col 3 renders error_message); existing tests PASS.

- [ ] **Step 3: Implement.** `files_model.py` — add `from mml_cloud_courier.core.hashing import crc32c_to_base64` to imports; replace `HEADERS` and the role handling in `data()`:

```python
    HEADERS = ("PATH", "SIZE", "STATE", "CRC32C", "DETAIL")
```

```python
    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.ToolTipRole and index.column() == 0:
            return self._rows[index.row()]["relative_path"]
        if role == Qt.ItemDataRole.ToolTipRole and index.column() == 3:
            return self._checksum_tooltip(self._rows[index.row()])
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() in (1, 2, 3):
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.FontRole and index.column() == 3:
            return theme.mono_font(8.5)
        if index.column() == 2 and role == Qt.ItemDataRole.ForegroundRole:
            state = self._rows[index.row()]["state"]
            token = STATE_TEXT_TOKENS.get(state, "muted")
            return theme._qcolor(getattr(theme.current(), token))
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        row = self._rows[index.row()]
        column = index.column()
        if column == 0:
            return row["relative_path"]
        if column == 1:
            return human_bytes(row["size_bytes"])
        if column == 2:
            return STATE_LABELS.get(row["state"], row["state"])
        if column == 3:
            return _crc_b64_or_dash(row.get("remote_crc32c"))
        return row.get("error_message") or ""

    @staticmethod
    def _checksum_tooltip(row: dict) -> str:
        lines = [
            f"local  {_crc_b64_or_dash(row.get('local_crc32c'))}",
            f"remote {_crc_b64_or_dash(row.get('remote_crc32c'))}",
        ]
        if row.get("sha256"):
            lines.append(f"sha256 {row['sha256']}")
        return "\n".join(lines)
```

with the module-level helper (crc32c_to_base64 has no None handling — guard here):

```python
def _crc_b64_or_dash(value: int | None) -> str:
    return crc32c_to_base64(value) if value is not None else "\u2014"
```

Note `Qt.AlignmentFlag.AlignCenter` already includes vertical centering — no `AlignVCenter` OR needed (the test pins `int(AlignCenter)`).

`job_tabs.py` `FilesTab.attach` column block becomes:

```python
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)   # PATH
        header.resizeSection(1, 88)                                      # SIZE
        header.resizeSection(2, 204)   # STATE — hard requirement: "Excluded after
                                       # repeated failures" must render in full
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(3, 110)                                     # CRC32C
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)   # DETAIL
```

- [ ] **Step 4: Run to verify pass.**

Run: `.venv\Scripts\python -m pytest tests/gui/test_files_model.py tests/gui/test_job_tabs.py tests/gui/test_theme.py -q -o addopts=`
Expected: PASS (hex test proves no color literals crept in).

- [ ] **Step 5: Commit.**

```bash
git add src/mml_cloud_courier/gui/files_model.py src/mml_cloud_courier/gui/job_tabs.py tests/gui/test_files_model.py tests/gui/test_job_tabs.py
git commit -m "feat: CRC32C column with checksum tooltip; center SIZE/STATE/CRC32C"
```

---

### Task 2: FilesTab polite auto-refresh + watcher wiring

**Files:**
- Modify: `src/mml_cloud_courier/gui/job_tabs.py` (`FilesTab.__init__`, `attach`; new methods)
- Modify: `src/mml_cloud_courier/gui/main_window.py` (`_on_watcher_snapshot` ~line 669, `_on_watcher_settled` ~line 687)
- Test: `tests/gui/test_job_tabs.py` (append)

**Interfaces:**
- Consumes: snapshot `progress` dicts (`files_done`, `state_counts`); `_on_watcher_settled`'s `final` is the full job dict INCLUDING `"progress"` (verified: watcher returns the `GET /jobs/{id}` payload).
- Produces: `FilesTab.maybe_auto_refresh(progress: dict | None) -> None`; `FilesTab._on_scrolled(value: int) -> None` (private, hook-tested); internal state `_last_progress_sig`, `_pending_refresh`, `_last_auto_refresh`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/gui/test_job_tabs.py` (a counting fetcher observes refreshes via `set_filter` fetch calls; drive time via monkeypatching `time.monotonic` inside the module under test):

```python
class _CountingFetcher:
    def __init__(self):
        self.fetches = 0

    def __call__(self, **kw):
        self.fetches += 1
        return []


def _progress(files_done, counts):
    return {"files_total": 10, "files_done": files_done, "files_failed": 0,
            "bytes_total": 0, "bytes_done": 0, "state_counts": counts}


def test_auto_refresh_fires_on_progress_change_at_top(qtbot):
    tab = FilesTab()
    qtbot.addWidget(tab)
    fetcher = _CountingFetcher()
    tab.attach(fetcher)
    baseline = fetcher.fetches                 # attach() does the first load
    tab.maybe_auto_refresh(_progress(1, {"verified": 1}))
    assert fetcher.fetches == baseline + 1
    # identical signature: no churn
    tab.maybe_auto_refresh(_progress(1, {"verified": 1}))
    assert fetcher.fetches == baseline + 1
    # None progress: no-op
    tab.maybe_auto_refresh(None)
    assert fetcher.fetches == baseline + 1


def test_auto_refresh_throttles_within_two_seconds(qtbot, monkeypatch):
    from mml_cloud_courier.gui import job_tabs as mod
    now = {"t": 1000.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: now["t"])
    tab = FilesTab()
    qtbot.addWidget(tab)
    fetcher = _CountingFetcher()
    tab.attach(fetcher)
    baseline = fetcher.fetches
    tab.maybe_auto_refresh(_progress(1, {"verified": 1}))
    assert fetcher.fetches == baseline + 1
    now["t"] += 0.5                            # inside the throttle window
    tab.maybe_auto_refresh(_progress(2, {"verified": 2}))
    assert fetcher.fetches == baseline + 1     # deferred, pending set
    now["t"] += 2.0                            # window elapsed; same sig tick flushes
    tab.maybe_auto_refresh(_progress(2, {"verified": 2}))
    assert fetcher.fetches == baseline + 2


def test_auto_refresh_defers_while_scrolled_and_flushes_on_return(qtbot):
    tab = FilesTab()
    qtbot.addWidget(tab)
    fetcher = _CountingFetcher()
    tab.attach(fetcher)
    baseline = fetcher.fetches
    tab.table.verticalScrollBar().setRange(0, 100)   # offscreen range is 0 otherwise
    tab.table.verticalScrollBar().setValue(40)       # user is browsing deep
    tab.maybe_auto_refresh(_progress(3, {"verified": 3}))
    assert fetcher.fetches == baseline               # deferred
    tab.table.verticalScrollBar().setValue(0)        # back to top -> flush
    assert fetcher.fetches == baseline + 1


def test_attach_resets_auto_refresh_state(qtbot):
    tab = FilesTab()
    qtbot.addWidget(tab)
    tab.attach(_CountingFetcher())
    tab.maybe_auto_refresh(_progress(1, {"verified": 1}))
    fetcher2 = _CountingFetcher()
    tab.attach(fetcher2)                       # new job
    baseline = fetcher2.fetches
    # same signature as the old job must still trigger: state was reset
    tab.maybe_auto_refresh(_progress(1, {"verified": 1}))
    assert fetcher2.fetches == baseline + 1
```

(If `job_tabs.py` does not yet import `time`, the second test's monkeypatch target comes with Step 3's implementation.)

- [ ] **Step 2: Run to verify failure.**

Run: `.venv\Scripts\python -m pytest tests/gui/test_job_tabs.py -q -o addopts=`
Expected: new tests FAIL (`maybe_auto_refresh` missing).

- [ ] **Step 3: Implement.** `job_tabs.py` — add `import time` to the module imports. In `FilesTab.__init__`, after the layout wiring:

```python
        self.table.verticalScrollBar().valueChanged.connect(self._on_scrolled)

        self._model: FileTableModel | None = None
        self._total: int | None = None
        self._last_progress_sig: tuple | None = None
        self._pending_refresh = False
        self._last_auto_refresh = 0.0
```

(the two pre-existing attribute lines move into this block unchanged). In `attach`, reset the policy state right after `self._total = None`:

```python
        self._last_progress_sig = None
        self._pending_refresh = False
        self._last_auto_refresh = 0.0
```

New methods on FilesTab:

```python
    # -- polite auto-refresh ------------------------------------------
    # File completions emit no events, so the watcher snapshot's progress
    # dict is the live signal. A refresh resets the virtualized table's
    # scroll position, so it only fires while the user is at the top;
    # changes that arrive mid-browse (or inside the throttle window) set
    # a pending flag flushed by a later tick or by scrolling back to top.

    _AUTO_REFRESH_SECONDS = 2.0

    def maybe_auto_refresh(self, progress: dict | None) -> None:
        if not progress or self._model is None:
            return
        sig = (progress.get("files_done"),
               tuple(sorted((progress.get("state_counts") or {}).items())))
        if sig != self._last_progress_sig:
            self._last_progress_sig = sig
            self._pending_refresh = True
        if not self._pending_refresh:
            return
        at_top = self.table.verticalScrollBar().value() == 0
        cooled = (time.monotonic() - self._last_auto_refresh
                  >= self._AUTO_REFRESH_SECONDS)
        if at_top and cooled:
            self._auto_refresh()

    def _on_scrolled(self, value: int) -> None:
        # Returning to the top flushes a deferred refresh; the throttle
        # never blocks this flush (the user asked for the top of the list).
        if value == 0 and self._pending_refresh and self._model is not None:
            self._auto_refresh()

    def _auto_refresh(self) -> None:
        self._pending_refresh = False
        self._last_auto_refresh = time.monotonic()
        self.refresh()
```

`main_window.py` — `_on_watcher_snapshot` gains one line at the top of the body:

```python
    def _on_watcher_snapshot(self, snap: dict) -> None:
        self.progress_tab.update_snapshot(snap)
        self.files_tab.maybe_auto_refresh(snap.get("progress"))
```

`_on_watcher_settled` routes the final state through the same policy:

```python
    def _on_watcher_settled(self, final) -> None:
        if final is None:
            return
        self._render_job(final)
        self.files_tab.maybe_auto_refresh(final.get("progress"))
        self._poke_rail()
```

- [ ] **Step 4: Run to verify pass.**

Run: `.venv\Scripts\python -m pytest tests/gui/test_job_tabs.py tests/gui/test_files_model.py tests/gui/test_main_window_smoke.py -q -o addopts=`
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/mml_cloud_courier/gui/job_tabs.py src/mml_cloud_courier/gui/main_window.py tests/gui/test_job_tabs.py
git commit -m "fix: files tab refreshes live from watcher progress, politely"
```

---

### Task 3: report.html per-file table

**Files:**
- Modify: `src/mml_cloud_courier/engine/report.py`
- Test: `tests/engine/test_report.py` (append)

**Interfaces:**
- Consumes: `write_report`'s existing `rows = repo.get_files(job_id)` (sqlite3.Row with all job_files columns); `_b64_or_empty`.
- Produces: `_MAX_FILES_SHOWN = 5000` module constant (tests shrink it); `_render_html(summary, failures, scan_error_events=(), rows=())` signature; `_human_bytes(n) -> str` local helper mirroring gui/format.py semantics.

- [ ] **Step 1: Write the failing tests.** Append to `tests/engine/test_report.py` (reuse the `finished_job` fixture):

```python
def test_html_report_lists_every_file_with_checksums(finished_job, tmp_path):
    db, job_id = finished_job
    paths = write_report(db, job_id, tmp_path / "report")
    html_text = paths.report_html.read_text(encoding="utf-8")
    assert "Files (5)" in html_text
    assert "f0.bin" in html_text and "f4.bin" in html_text
    assert crc32c_to_base64(11) in html_text          # verified file's crc
    assert "ab" * 32 in html_text                     # the one sha256
    assert "in use by EDITOR.EXE" in html_text        # detail column
    assert "1.0 KB" in html_text                      # human-readable size
    # self-containment invariant must survive the new table
    assert "http://" not in html_text and "https://" not in html_text


def test_html_files_table_caps_and_points_at_manifest(finished_job, tmp_path, monkeypatch):
    import mml_cloud_courier.engine.report as report_module
    monkeypatch.setattr(report_module, "_MAX_FILES_SHOWN", 3)
    db, job_id = finished_job
    paths = write_report(db, job_id, tmp_path / "report")
    html_text = paths.report_html.read_text(encoding="utf-8")
    assert "Files (5)" in html_text
    assert "f2.bin" in html_text
    assert "f3.bin" not in html_text and "f4.bin" not in html_text
    assert "and 2 more" in html_text
    assert "manifest.csv" in html_text
```

(`make_files` produces sizes of exactly `size=1000` → `_human_bytes(1000)` must render "1.0 KB", matching gui `human_bytes` semantics: 1000 B crosses to the KB unit.)

- [ ] **Step 2: Run to verify failure.**

Run: `.venv\Scripts\python -m pytest tests/engine/test_report.py -q -o addopts=`
Expected: new tests FAIL ("Files (5)" absent); existing report tests PASS.

- [ ] **Step 3: Implement.** In `report.py`, add beside `_MAX_FAILURES_SHOWN`:

```python
_MAX_FILES_SHOWN = 5000

_BYTE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def _human_bytes(n: int | float) -> str:
    """Mirror of gui/format.py human_bytes — the engine must not import gui
    (it pulls in Qt): decimal SI units, one decimal below 100."""
    value = float(n)
    for unit in _BYTE_UNITS:
        if value < 1000 or unit == _BYTE_UNITS[-1]:
            if unit == "B":
                return f"{int(value)} B"
            text = f"{value:.1f}" if value < 100 else f"{value:.0f}"
            return f"{text} {unit}"
        value /= 1000
```

In `write_report`, pass the rows through: `_render_html(summary, failures, scan_error_events, rows)`. Change `_render_html`'s signature to `def _render_html(summary: dict, failures, scan_error_events=(), rows=()) -> str:` and add, after the failures section construction:

```python
    shown_rows = list(rows[:_MAX_FILES_SHOWN])
    file_cells = "".join(
        "<tr>"
        f"<td><code>{esc(r['relative_path'])}</code></td>"
        f"<td class=\"num\">{esc(_human_bytes(r['size_bytes']))}</td>"
        f"<td>{esc(r['state'])}</td>"
        f"<td>{esc(r['error_message'] or '')}</td>"
        f"<td><code>{esc(_b64_or_empty(r['remote_crc32c']))}</code></td>"
        f"<td class=\"sha\"><code>{esc(r['sha256'] or '')}</code></td>"
        "</tr>"
        for r in shown_rows
    )
    files_more = (
        f"<p>… and {len(rows) - len(shown_rows)} more — the complete list is"
        " in manifest.csv beside this report.</p>"
        if len(rows) > len(shown_rows)
        else ""
    )
    files_html = (
        f"<h2>Files ({len(rows)})</h2>"
        "<table class=\"files\"><thead><tr>"
        "<th>Path</th><th>Size</th><th>State</th><th>Detail</th>"
        "<th>CRC32C</th><th>SHA-256</th>"
        "</tr></thead><tbody>" + file_cells + "</tbody></table>" + files_more
    )
```

Insert `{files_html}` into the returned HTML after the `{failures_html}` block, and extend the inline CSS with:

```
table.files {{ width: 100%; font-size: .85rem; }}
table.files th, table.files td {{ border-bottom: 1px solid #e2e8f0; padding: .25rem .5rem; text-align: left; vertical-align: top; }}
table.files td.num {{ white-space: nowrap; }}
table.files td.sha code {{ word-break: break-all; }}
```

(Hex colors are fine in engine/report.py — the no-hex rule covers gui/ only, and this file already uses hex.)

- [ ] **Step 4: Run to verify pass.**

Run: `.venv\Scripts\python -m pytest tests/engine/test_report.py -q -o addopts=`
Expected: PASS (all, including the pre-existing self-containment and tmp-file tests).

- [ ] **Step 5: Commit.**

```bash
git add src/mml_cloud_courier/engine/report.py tests/engine/test_report.py
git commit -m "feat: per-file table with checksums in report.html, capped with manifest pointer"
```

---

### Task 4: Full-suite verification (no new code)

- [ ] **Step 1:** `.venv\Scripts\python -m pytest -o addopts= -q` — record exact counts. Expected: baseline 709 + all new tests, 13 skipped, zero failures. Fix anything red in this task (superpowers:systematic-debugging first), one focused commit per fix.
- [ ] **Step 2:** `grep -rnE "#[0-9a-fA-F]{6}\b" src/mml_cloud_courier/gui --include="*.py" | grep -v theme.py` — must be empty.
- [ ] **Step 3:** Report recorded counts to the orchestrator.

---

## After Task 4 (orchestrator)

Manual smoke with the user from the worktree venv (`.venv\Scripts\python -m mml_cloud_courier.gui`): the live service already runs current code, so everything works immediately — watch a small live transfer's Files tab update (or archive-view an old job's columns), check centered SIZE/STATE, the CRC32C column + tooltip on a verified job, and generate/open a report to see the files table. Then superpowers:finishing-a-development-branch: merge `--no-ff`, push.

## Self-review notes (applied)

- Spec coverage: §2→Task 2, §3→Task 1, §4→Task 3, §5→each task + Task 4, §7→Task 0 + close-out. Alignment "SIZE right-align REPLACED" → Task 1 Step 3 rewrites the whole role block.
- Type consistency: `maybe_auto_refresh(progress: dict | None)` (Tasks 2 wiring both call sites); `_crc_b64_or_dash` (Task 1 only); `_render_html(..., rows=())` + `_MAX_FILES_SHOWN` (Task 3 only); `_progress()` test helper mirrors `asdict(JobProgress)` keys exactly.
- The scrolled-deep test sets the scrollbar range explicitly (offscreen ranges are 0 — the same trap the primitives round hit); the flush assertion rides the real `valueChanged` signal.
- The cap-note wording contains no "http" substring (self-containment tests).
