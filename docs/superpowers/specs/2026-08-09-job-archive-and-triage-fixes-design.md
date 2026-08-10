# Job archiving + connections-redesign triage fixes — design spec

Date: 2026-08-09
Status: approved (decisions taken by Peter Mahoney in session)
Baseline: master 7a27293 (connections redesign merged); suite 688 passed / 13 skipped.

## 1. Decisions taken during brainstorming

| # | Question | Decision |
| --- | --- | --- |
| 1 | Just-submitted job hidden by an active rail profile filter | **Submission wins**: auto-clear the filter and select the job (mirrors the existing force-expand doctrine for collapsed groups) |
| 2 | Which jobs are archivable | **Completed-group statuses only**: `complete` and `cancelled`; server-enforced |
| 3 | Where the Archive action lives | **Both**: rail right-click menu and a Summary-tab button |
| 4 | Seeing/undoing archives | **Toggle + Archived group**: a checkable "Show archived" in the rail menu; when on, polls include archived rows and a fifth collapsed "Archived" rail group appears with per-row Unarchive; off by default, nothing changes when off |

## 2. Scope

Two shipped-defect fixes (final-review triage of the connections redesign) plus one
feature (job archiving) spanning schema → repository → service API → client → GUI.

### Non-goals

- No CLI archive commands (API + GUI only this round).
- No job deletion (still does not exist anywhere).
- Archiving does **not** unblock profile deletion — the in-use count and the
  jobs→profiles FK still see archived rows. Known limitation, deliberately
  documented; the shipped delete-refusal copy stays as-is.
- The Show-archived toggle is session state, not persisted.

## 3. Triage fix A — submission wins over the profile filter

`gui/main_window.py`, `_sync_rail_preserving_expansion` (the pending-select block,
currently ~lines 380–392). When `pending is not None`, `_find_rail_index(pending)`
returns `None`, a profile filter is active, **and** the pending job exists in the
unfiltered `self._last_jobs` — the filter is what hides it — call
`self.clear_profile_filter()` and return. `clear_profile_filter` re-syncs with the
full list; the pending branch in that re-entrant call force-selects the job exactly
as it does for a collapsed group. If the job is not in `_last_jobs` (poll latency),
behavior is unchanged: pending stays set and the next poll resolves it. No recursion:
the re-entry runs with `_profile_filter is None`. The code comment cites the same
doctrine as the force-expand ("an explicit submission wins over view state").

## 4. Triage fix B — stale-card guard in the delete path

`gui/connection_dialogs.py`, `_delete_failed` (~line 1043) gets the identical guard
its sibling `_card_check_done` carries: `if card not in self.cards: return` before
touching the card, with the same rationale comment (refresh `deleteLater()`s every
card and rebuilds `self.cards`; membership is the liveness proxy).

## 5. Schema v3

- `SCHEMA_VERSION = 3`; jobs gain `archived_at TEXT NULL` (NULL = active; the
  timestamp doubles as the flag and preserves *when* it was archived).
- Migration v2→v3 follows the established crash-safe pattern (Plan 4): guarded by
  `PRAGMA table_info(jobs)` so a re-run after a crash between ALTER and version
  bump is a no-op.

## 6. Repository

- `archive_job(job_id: int) -> None` — `LookupError` on a bogus id (existing
  pattern); raises `JobNotArchivable(RuntimeError)` with
  `f"job {job_id} is {status} and cannot be archived; only complete or cancelled"
  f" jobs can"` unless status ∈ {`complete`, `cancelled`}; stamps
  `archived_at = COALESCE(archived_at, <now>)` using the repo's existing `_now()`
  convention (idempotent; first archive time preserved).
- `unarchive_job(job_id: int) -> None` — `LookupError` on a bogus id; clears
  `archived_at` (idempotent no-op when already active).
- `list_jobs(include_archived: bool = False)` — default filters
  `WHERE archived_at IS NULL`; `True` returns everything. Every existing caller
  keeps the no-arg lean behavior. `jobs_with_status`, scheduling, and
  `find_active_duplicate` are untouched — archivable statuses are terminal, so no
  interaction exists.

## 7. Service API + client

The jobs API is not under the frozen-profiles constraint. On the token-guarded
router:

- `GET /jobs?include_archived=<bool>` (default false) — passes through to
  `repo.list_jobs`; `archived_at` flows into payloads via the existing
  `_row_dict` (`SELECT *`).
- `POST /jobs/{job_id}/archive` — 200 `{"archived": job_id}`; 404 on `LookupError`
  (existing mapping); 409 with `str(exc)` on `JobNotArchivable` (mirrors the
  `ProfileInUse` mapping).
- `POST /jobs/{job_id}/unarchive` — 200 `{"unarchived": job_id}`; 404 on bogus id.

`ApiClient` gains `list_jobs(include_archived: bool = False)` (backward-compatible),
`archive_job(job_id) -> dict`, `unarchive_job(job_id) -> dict`.

## 8. Rail model

`gui/jobs_model.py`:

- `RAIL_GROUPS` becomes `("needs_attention", "running", "queued", "completed",
  "archived")` — appended LAST so existing index math (`RAIL_GROUPS.index(
  "completed")` at startup-collapse) is unchanged. `GROUP_LABELS["archived"] =
  "Archived"`.
- New routing: a job with `archived_at` set lands in `archived` regardless of
  status (a `group_for_job(job)` helper wrapping `group_for_status`); `sync_rail`
  and `_rail_signature` account for `archived_at` so archiving/unarchiving
  triggers a rebuild.

`MainWindow` hides the Archived header row (`rail_view.setRowHidden`) whenever
Show-archived is off; when first shown, the group starts collapsed (like
Completed's startup state).

## 9. GUI behavior

- **Rail context menu** (`customContextMenuRequested` on `rail_view`): "Archive
  job" on rows whose job is `complete`/`cancelled` and unarchived; "Unarchive job"
  on archived rows; a checkable "Show archived" always present. Menu composition
  lives in a unit-testable method (index → actions), with the QMenu popup as a
  thin shell over it.
- **Summary tab**: `SummaryTab` gains an `on_archive` callback (same pattern as
  `on_resume`) and an "Archive this job" button visible only when the displayed
  job is `complete`/`cancelled` with `archived_at` unset.
- Both entry points share one MainWindow handler: `call_async(archive_job)` →
  on success re-poll (`_poke_rail`). Errors surface via the existing
  status-message path.
- **Selection**: archiving the selected job — or toggling Show-archived off while
  an archived job is selected — clears selection and resets the tabs via a new
  shared `_clear_selection_and_tabs()` helper, extracted from the identical block
  in `show_jobs_for_profile` (fix A's area) so the reset conventions live once.
- **Toggle semantics**: flipping Show-archived triggers an immediate refetch with
  the new `include_archived` value, and the running poller respects the current
  value on every subsequent tick (fetch-callable or poller restart — behavior
  binding, mechanism free).

## 10. Tests

- Store: fresh v3 create; v2→v3 migration (column added, version bumped, re-run
  no-op); `archive_job` status enforcement (each non-archivable status → 
  `JobNotArchivable`), idempotent stamp preservation; `unarchive_job`;
  `list_jobs` default vs `include_archived=True`.
- Service (TestClient): archive a complete job → 200 and gone from default
  `GET /jobs`, present with `include_archived=true`; archive a running job → 409
  with the plain-language detail; unarchive → back in the default list; 404s.
- GUI: `group_for_job` routing; Archived group hidden/shown + collapsed default;
  context-menu composition per row type (archivable / archived / header / none);
  Summary-button visibility matrix; archive-clears-selection via the shared
  helper; toggle refetches with `include_archived`.
- Triage fixes: filtered-out pending job present in `_last_jobs` → filter clears,
  bar hides, job selected, pending consumed; pending job absent from `_last_jobs`
  → filter and pending stay; `_delete_failed` on a replaced card → no crash, no
  region on live cards.
- Full suite `-o addopts= -q`, counts recorded (baseline 688 passed / 13 skipped).

## 11. Execution & done criteria

Spec → plan (superpowers:writing-plans) → SDD in a fresh worktree (master already
pushed at 7a27293). House conventions as always: py 3.12 venv, `pip install -e
".[dev]"`, create `tools\` and copy fake-gcs-server.exe (fresh worktrees lack the
directory — emulator tests silently skip without it), verify the 688/13 baseline
before Task 1. Done when: full suite green with recorded counts; live service
restarted onto the merged code is NOT required this round (the live DB migrates
v2→v3 on next service restart — note it in the merge report); manual smoke check
with the user (archive/unarchive a real completed job is acceptable — it is
reversible by design); merged to master `--no-ff` and pushed.
