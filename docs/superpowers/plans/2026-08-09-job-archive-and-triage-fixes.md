# Job Archiving + Triage Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reversible job archiving (schema → service → GUI: hidden from the rail by default, a Show-archived toggle with a fifth collapsed group, archive/unarchive via rail context menu and a Summary-tab button) plus two shipped-defect fixes from the connections-redesign final review.

**Architecture:** `archived_at TEXT NULL` on jobs (v3 migration, crash-safe guard pattern); `list_jobs(include_archived=False)` keeps every existing caller lean; two POST endpoints mirroring the resume/pause patterns; the rail gains an `archived` group appended LAST (index math stable) routed by a new `group_for_job`; MainWindow owns the session-only Show-archived state and threads it through a new optional `fetch` callable on the poller.

**Tech Stack:** Python 3.12, SQLite, FastAPI, PySide6, pytest + pytest-qt (offscreen).

**Authoritative documents:** spec `docs/superpowers/specs/2026-08-09-job-archive-and-triage-fixes-design.md` (decisions and contracts; read before your task).

## Global Constraints

- Archivable statuses are EXACTLY `complete` and `cancelled` (server-enforced; `JobNotArchivable` → 409 with `f"job {job_id} is {status} and cannot be archived; only complete or cancelled jobs can"`).
- `list_jobs` default behavior (no args) must stay byte-identical for every existing caller: archived rows excluded. `include_archived` is a defaulted kwarg everywhere (repo, endpoint, ApiClient) — fakes in existing tests must keep working without edits.
- The profiles API stays frozen; archiving does NOT unblock profile deletion (FK + in-use count see archived rows) — documented limitation, no copy changes.
- `RAIL_GROUPS` gains `"archived"` appended LAST — `RAIL_GROUPS.index("completed")` must remain 3. Two existing tests are EXPLICITLY authorized to change and no others: `tests/gui/test_jobs_model.py` `assert model.rowCount() == 4` → 5 (in `test_sync_rail_places_jobs_and_roles`), and `tests/store/test_schema.py::test_fresh_database_is_version_2_with_validated_at` → version 3 (rename accordingly). The four contractual COPY_* strings and their tests are untouched (nothing here goes near them).
- All colors via theme tokens; zero 6-digit hex in gui/*.py outside theme.py (no new colors are needed — reuse existing objectNames).
- Qt gotchas: `setDefault`/`setAutoDefault(False)` discipline for dialog buttons (no dialogs here); `QTreeView.setCurrentIndex` auto-expands ancestors; custom QWidgets with QSS backgrounds need WA_StyledBackground (no new custom widgets here).
- Tests never touch the live install; QSettings isolation is autouse in tests/gui/conftest.py. Targeted runs: `.venv\Scripts\python -m pytest <files> -q -o addopts=`; full suite `-o addopts= -q` (never bare `-q` for counts on this host).
- Suite baseline on master 2311bf0: **688 passed, 13 skipped**.
- SDD conventions: every dispatch cds into the worktree FIRST and re-verifies `git rev-parse --show-toplevel` plus the expected parent commit before each commit; one commit per task; never amend; never bare `git stash`.
- The live install's DB migrates v2→v3 on the next service restart — nothing in this plan restarts the live service; note it in the merge report.

---

### Task 0: Worktree setup (no commit)

Executed once by the orchestrator before dispatching Task 1.

- [ ] **Step 1:** `git push origin master` from the main checkout (worktrees branch from origin/master).
- [ ] **Step 2:** EnterWorktree (suggested name: `job-archive`).
- [ ] **Step 3:** Provision (PowerShell, from the worktree root — note `tools\` does not exist in a fresh worktree; create it or the emulator suite silently skips):

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]" --quiet
New-Item -ItemType Directory -Force tools | Out-Null
Copy-Item C:\Users\pmaho\Documents\VibeCode\mml_cloud_transfer\tools\fake-gcs-server.exe tools\fake-gcs-server.exe
```

- [ ] **Step 4:** Baseline `.venv\Scripts\python -m pytest -o addopts= -q` — expect exactly **688 passed, 13 skipped**; record the counts.

---

### Task 1: Triage fixes — submission-wins filter + stale-card guard

**Files:**
- Modify: `src/mml_cloud_courier/gui/main_window.py` (pending-select block ~380-392; `show_jobs_for_profile` ~429-444)
- Modify: `src/mml_cloud_courier/gui/connection_dialogs.py` (`_delete_failed` ~1043)
- Test: `tests/gui/test_rail_filter.py` (append), `tests/gui/test_connection_manager.py` (append)

**Interfaces:**
- Consumes: existing `clear_profile_filter()`, `_find_rail_index`, `self.cards` list semantics in ConnectionsDialog.
- Produces: `MainWindow._clear_selection_and_tabs() -> None` — Tasks 5 and 6 call it. Behavior: clears rail selection, `_selected_job_id`/`_selected_status = None`, `_update_action_states()`, `watcher.stop()`, `progress_tab.reset()`, `errors_tab.load_groups([])`, `summary_tab.set_causes(None, None)`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/gui/test_rail_filter.py`:

```python
@pytest.mark.gui
def test_submitted_job_hidden_by_filter_clears_the_filter(qtbot, window):
    window._on_jobs(JOBS)                       # profiles 10, 10, 20
    window.show_jobs_for_profile(20, "PAM archive")
    assert window.rail_job_ids() == [3]
    # a poll tick arrives carrying a just-submitted job for another profile
    window._pending_select = 4
    window._on_jobs(JOBS + [_job(4, 10)])
    assert not window.filter_bar.isVisibleTo(window)     # submission wins
    assert sorted(window.rail_job_ids()) == [1, 2, 3, 4]
    qtbot.waitUntil(lambda: window.selected_job_id == 4, timeout=5000)
    assert window._pending_select is None


@pytest.mark.gui
def test_pending_job_not_yet_polled_leaves_filter_alone(qtbot, window):
    window._on_jobs(JOBS)
    window.show_jobs_for_profile(20, "PAM archive")
    window._pending_select = 99                  # poll has not seen it yet
    window._on_jobs(JOBS)
    assert window.filter_bar.isVisibleTo(window)          # filter intact
    assert window._pending_select == 99                   # still pending
```

Append to `tests/gui/test_connection_manager.py`:

```python
def test_delete_failure_after_refresh_ignores_the_dead_card(qtbot):
    client = FakeClient([profile(1)])
    dialog = ConnectionsDialog(client)
    qtbot.addWidget(dialog)
    wait_cards(qtbot, dialog, 1)
    old_card = dialog.cards[0]
    dialog._profiles_loaded([profile(1)])        # refresh replaces every card
    assert old_card not in dialog.cards
    # the late delete failure for the replaced card must be a clean no-op
    dialog._delete_failed(
        old_card,
        "409: profile 1 is used by 2 job(s) and cannot be deleted while they exist")
    assert not dialog.cards[0].region.isVisibleTo(dialog)
```

- [ ] **Step 2: Run to verify failure.**

Run: `.venv\Scripts\python -m pytest tests/gui/test_rail_filter.py tests/gui/test_connection_manager.py -q -o addopts=`
Expected: the two filter tests FAIL (filter stays visible / job unselected); the delete test FAILS (RuntimeError on the deleted card's C++ wrapper, or the refusal region shows on the dead card).

- [ ] **Step 3: Implement.** In `main_window.py`, extract the helper and use it in `show_jobs_for_profile` (replacing the identical inline block):

```python
    def _clear_selection_and_tabs(self) -> None:
        """The full deselection reset: mirrors what selecting a different
        job would tear down, so no tab keeps rendering a job the rail no
        longer shows."""
        self.rail_view.selectionModel().clearSelection()
        self._selected_job_id = None
        self._selected_status = None
        self._update_action_states()
        self.watcher.stop()
        self.progress_tab.reset()
        self.errors_tab.load_groups([])
        self.summary_tab.set_causes(None, None)
```

In `show_jobs_for_profile`, the `if self._selected_job_id is not None and ...` branch body becomes a single `self._clear_selection_and_tabs()` call.

In `_sync_rail_preserving_expansion`, replace the bare `if index is None: return` (after `index = self._find_rail_index(target)`) with:

```python
        index = self._find_rail_index(target)
        if index is None:
            if (pending is not None and self._profile_filter is not None
                    and any(job["id"] == pending for job in self._last_jobs)):
                # The just-submitted job exists but the profile filter hides
                # it. An explicit submission wins over view state -- the same
                # doctrine as the force-expand below -- so drop the filter;
                # clear_profile_filter re-syncs with the full list and this
                # method's pending branch then selects the job.
                self.clear_profile_filter()
            return
```

In `connection_dialogs.py`, `_delete_failed` gains the sibling guard as its first statement:

```python
    def _delete_failed(self, card, message) -> None:
        # refresh() deleteLater()s every card and rebuilds self.cards; a
        # delete that was in flight during that refresh must not touch the
        # now-dead card its callback still closes over.
        if card not in self.cards:
            return
```

- [ ] **Step 4: Run to verify pass.**

Run: `.venv\Scripts\python -m pytest tests/gui/test_rail_filter.py tests/gui/test_connection_manager.py tests/gui/test_main_window_smoke.py -q -o addopts=`
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/mml_cloud_courier/gui/main_window.py src/mml_cloud_courier/gui/connection_dialogs.py tests/gui/test_rail_filter.py tests/gui/test_connection_manager.py
git commit -m "fix: submission wins over the rail filter; guard the delete path's stale card"
```

---

### Task 2: Schema v3 + repository archive methods

**Files:**
- Modify: `src/mml_cloud_courier/store/schema.py` (SCHEMA_VERSION 12; jobs DDL 31-46; `apply_migrations` 107-133)
- Modify: `src/mml_cloud_courier/store/repository.py` (exception by `ProfileInUse` ~37; jobs section 64-202)
- Test: `tests/store/test_schema.py` (append + the one authorized rename), `tests/store/test_repository_service.py` (append)

**Interfaces:**
- Consumes: `_now()` (`datetime.now(UTC).isoformat(timespec="seconds")`), `get_job` LookupError pattern.
- Produces (Task 3 relies on these exactly): `class JobNotArchivable(Exception)` in repository; `JobRepository.archive_job(job_id: int) -> None`; `JobRepository.unarchive_job(job_id: int) -> None`; `JobRepository.list_jobs(include_archived: bool = False) -> list[sqlite3.Row]`; jobs rows now carry `archived_at` (None when active).

- [ ] **Step 1: Write the failing tests.** In `tests/store/test_schema.py`: rename `test_fresh_database_is_version_2_with_validated_at` to `test_fresh_database_is_version_3_with_validated_at_and_archived_at`, asserting version 3 and both columns (keep its existing structure, add `archived_at` to the jobs column check). Then append:

```python
def test_a_v2_database_gains_archived_at_in_place(tmp_path):
    """Build a database exactly as schema v2 wrote it (no jobs.archived_at,
    version=2), then connect(): the column appears, the version bumps, and
    existing rows survive with archived_at NULL."""
    db = tmp_path / "jobs.db"
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version (version) VALUES (2);
        CREATE TABLE jobs (
            id                 INTEGER PRIMARY KEY,
            name               TEXT NOT NULL,
            direction          TEXT NOT NULL,
            profile_id         INTEGER,
            source_root        TEXT NOT NULL,
            dest_prefix        TEXT NOT NULL,
            status             TEXT NOT NULL,
            audit_hash         INTEGER NOT NULL DEFAULT 0,
            scheduled_start_at TEXT,
            created_at         TEXT NOT NULL,
            started_at         TEXT,
            finished_at        TEXT,
            planned_files      INTEGER NOT NULL DEFAULT 0,
            planned_bytes      INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO jobs (name, direction, source_root, dest_prefix, status, created_at)
        VALUES ('legacy', 'upload', 'C:\\d', 'p', 'complete', '2026-08-09T00:00:00+00:00');
        """
    )
    raw.commit()
    raw.close()

    conn = connect(db)
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 3
        row = conn.execute("SELECT * FROM jobs WHERE name = 'legacy'").fetchone()
        assert row["archived_at"] is None       # new column, old row intact
    finally:
        conn.close()


def test_an_interrupted_v3_migration_recovers_on_the_next_connect(tmp_path):
    """Simulate a crash between the ALTER and the version bump: the column
    exists but the version still says 2. connect() must not re-ALTER (which
    would raise) and must catch the version up."""
    db = tmp_path / "jobs.db"
    conn = connect(db)          # fresh v3
    conn.execute("UPDATE schema_version SET version = 2")
    conn.commit()
    conn.close()

    conn = connect(db)
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 3
    finally:
        conn.close()
```

In `tests/store/test_repository_service.py` append (reuse that file's `repo` fixture and `_job` helper):

```python
def test_archive_only_terminal_completed_group_statuses(repo):
    from mml_cloud_courier.store.repository import JobNotArchivable
    job_id = _job(repo)
    for status in (JobStatus.PENDING, JobStatus.RUNNING, JobStatus.PAUSED,
                   JobStatus.STALLED, JobStatus.INCOMPLETE):
        repo.set_job_status(job_id, status)
        with pytest.raises(JobNotArchivable, match="only complete or cancelled"):
            repo.archive_job(job_id)
    repo.set_job_status(job_id, JobStatus.COMPLETE)
    repo.archive_job(job_id)
    assert repo.get_job(job_id)["archived_at"] is not None


def test_archive_is_idempotent_and_preserves_the_first_stamp(repo):
    job_id = _job(repo)
    repo.set_job_status(job_id, JobStatus.CANCELLED)
    repo.archive_job(job_id)
    first = repo.get_job(job_id)["archived_at"]
    repo.archive_job(job_id)
    assert repo.get_job(job_id)["archived_at"] == first


def test_unarchive_restores_and_is_idempotent(repo):
    job_id = _job(repo)
    repo.set_job_status(job_id, JobStatus.COMPLETE)
    repo.archive_job(job_id)
    repo.unarchive_job(job_id)
    assert repo.get_job(job_id)["archived_at"] is None
    repo.unarchive_job(job_id)                   # no-op, no raise
    with pytest.raises(LookupError):
        repo.archive_job(9999)
    with pytest.raises(LookupError):
        repo.unarchive_job(9999)


def test_list_jobs_excludes_archived_by_default(repo):
    keep = _job(repo)
    hide = _job(repo, name="hidden")
    repo.set_job_status(hide, JobStatus.COMPLETE)
    repo.archive_job(hide)
    assert [row["id"] for row in repo.list_jobs()] == [keep]
    assert [row["id"] for row in repo.list_jobs(include_archived=True)] == [keep, hide]
```

(If `_job` does not take a `name` kwarg, extend the helper minimally or insert the second job with the same pattern the helper uses — never weaken the assertions.)

- [ ] **Step 2: Run to verify failure.**

Run: `.venv\Scripts\python -m pytest tests/store/test_schema.py tests/store/test_repository_service.py -q -o addopts=`
Expected: new tests FAIL (no `archived_at` column / no `archive_job`); the renamed fresh-DB test FAILS on version 3.

- [ ] **Step 3: Implement.** `schema.py`: set `SCHEMA_VERSION = 3`; add `archived_at         TEXT` as the last column of the jobs CREATE TABLE in `_DDL`; extend `apply_migrations` after the `if version < 2:` block, same guard idiom:

```python
    if version < 3:
        # v2 -> v3: jobs.archived_at (archive = hide from the default list,
        # keep the row). Same crash-safe guard as v1 -> v2.
        columns = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
        if "archived_at" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN archived_at TEXT")
        conn.execute("UPDATE schema_version SET version = 3")
```

`repository.py`: next to `ProfileInUse`:

```python
class JobNotArchivable(Exception):
    """Archiving a job that is not in a completed-group status."""
```

In the jobs section (near `set_job_status`), with the archivable set as a module/class constant mirroring `_ACTIVE_STATUSES`:

```python
    _ARCHIVABLE_STATUSES = frozenset({"complete", "cancelled"})

    def archive_job(self, job_id: int) -> None:
        """Hide from the default list without deleting. Only completed-group
        statuses qualify; the first archive time survives re-archiving."""
        row = self.get_job(job_id)               # LookupError on a bogus id
        if row["status"] not in self._ARCHIVABLE_STATUSES:
            raise JobNotArchivable(
                f"job {job_id} is {row['status']} and cannot be archived;"
                " only complete or cancelled jobs can"
            )
        self._conn.execute(
            "UPDATE jobs SET archived_at = COALESCE(archived_at, ?) WHERE id = ?",
            (_now(), job_id),
        )

    def unarchive_job(self, job_id: int) -> None:
        self.get_job(job_id)                     # LookupError on a bogus id
        self._conn.execute(
            "UPDATE jobs SET archived_at = NULL WHERE id = ?", (job_id,)
        )
```

And `list_jobs` becomes:

```python
    def list_jobs(self, include_archived: bool = False) -> list[sqlite3.Row]:
        if include_archived:
            return self._conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        return self._conn.execute(
            "SELECT * FROM jobs WHERE archived_at IS NULL ORDER BY id"
        ).fetchall()
```

- [ ] **Step 4: Run to verify pass.**

Run: `.venv\Scripts\python -m pytest tests/store -q -o addopts=`
Expected: PASS (all store tests, including the untouched `test_jobs_with_status_and_list_jobs` — its jobs are unarchived).

- [ ] **Step 5: Commit.**

```bash
git add src/mml_cloud_courier/store/schema.py src/mml_cloud_courier/store/repository.py tests/store/test_schema.py tests/store/test_repository_service.py
git commit -m "feat: schema v3 archived_at + repository archive/unarchive/list filtering"
```

---

### Task 3: Service endpoints + ApiClient methods

**Files:**
- Modify: `src/mml_cloud_courier/service/app.py` (GET /jobs ~328-334; new POSTs beside resume_job ~469)
- Modify: `src/mml_cloud_courier/cli/service_client.py` (list_jobs ~54; new methods beside pause ~66)
- Test: `tests/service/test_api.py` (append)

**Interfaces:**
- Consumes: Task 2's `JobNotArchivable`, `archive_job`, `unarchive_job`, `list_jobs(include_archived=...)`; existing `_job_or_404`, `_row_dict`, `record_event`.
- Produces (Tasks 5-6 rely on these exactly): `GET /jobs?include_archived=<bool>` (default false); `POST /jobs/{job_id}/archive` → `{"archived": job_id}` | 404 | 409; `POST /jobs/{job_id}/unarchive` → `{"unarchived": job_id}` | 404; `ApiClient.list_jobs(include_archived: bool = False) -> list[dict]`, `ApiClient.archive_job(job_id: int) -> dict`, `ApiClient.unarchive_job(job_id: int) -> dict`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/service/test_api.py` (uses that file's `api` fixture and `_submit` helper; drive a job to `cancelled` with the existing action endpoints — no runner needed):

```python
def test_archive_lifecycle(api, tmp_path):
    client, _, _ = api
    job_id = _submit(client, tmp_path)
    # queued job: not archivable
    response = client.post(f"/jobs/{job_id}/archive")
    assert response.status_code == 409
    assert "only complete or cancelled jobs can" in response.json()["detail"]
    assert f"job {job_id} is pending" in response.json()["detail"]
    # cancel it -> archivable
    assert client.post(f"/jobs/{job_id}/cancel").status_code == 200
    assert client.post(f"/jobs/{job_id}/archive").json() == {"archived": job_id}
    # hidden from the default list, present with include_archived
    assert [j["id"] for j in client.get("/jobs").json()] == []
    listed = client.get("/jobs", params={"include_archived": "true"}).json()
    assert [j["id"] for j in listed] == [job_id]
    assert listed[0]["archived_at"] is not None
    # unarchive restores it
    assert client.post(f"/jobs/{job_id}/unarchive").json() == {"unarchived": job_id}
    assert [j["id"] for j in client.get("/jobs").json()] == [job_id]
    # 404s
    assert client.post("/jobs/999/archive").status_code == 404
    assert client.post("/jobs/999/unarchive").status_code == 404
```

- [ ] **Step 2: Run to verify failure.**

Run: `.venv\Scripts\python -m pytest tests/service/test_api.py -q -o addopts=`
Expected: the new test FAILS with 404/405 on the archive endpoint.

- [ ] **Step 3: Implement.** `app.py` — extend the import from `mml_cloud_courier.store.repository` with `JobNotArchivable` (it already imports `ProfileInUse`; match that line's style). `GET /jobs` gains the plain typed query param (house convention, no `Query` import):

```python
    @router.get("/jobs")
    def list_jobs(include_archived: bool = False) -> list[dict]:
        conn, repo = _open()
        try:
            return [_row_dict(row)
                    for row in repo.list_jobs(include_archived=include_archived)]
        finally:
            conn.close()
```

New handlers beside `resume_job`, mirroring its shape (including the user-action event, matching `"resumed_by_user"`):

```python
    @router.post("/jobs/{job_id}/archive")
    def archive_job(job_id: int) -> dict:
        conn, repo = _open()
        try:
            _job_or_404(repo, job_id)
            try:
                repo.archive_job(job_id)
            except JobNotArchivable as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
            repo.record_event(job_id, "archived_by_user")
            return {"archived": job_id}
        finally:
            conn.close()

    @router.post("/jobs/{job_id}/unarchive")
    def unarchive_job(job_id: int) -> dict:
        conn, repo = _open()
        try:
            _job_or_404(repo, job_id)
            repo.unarchive_job(job_id)
            repo.record_event(job_id, "unarchived_by_user")
            return {"unarchived": job_id}
        finally:
            conn.close()
```

`service_client.py` — replace `list_jobs` and add the actions beside `pause`:

```python
    def list_jobs(self, include_archived: bool = False) -> list[dict]:
        params = {"include_archived": "true"} if include_archived else None
        return self._check(
            self._session.get(f"{self._base}/jobs", params=params, timeout=30)
        )

    def archive_job(self, job_id: int) -> dict:
        return self._check(
            self._session.post(f"{self._base}/jobs/{job_id}/archive", timeout=30)
        )

    def unarchive_job(self, job_id: int) -> dict:
        return self._check(
            self._session.post(f"{self._base}/jobs/{job_id}/unarchive", timeout=30)
        )
```

- [ ] **Step 4: Run to verify pass.**

Run: `.venv\Scripts\python -m pytest tests/service tests/cli -q -o addopts=`
Expected: PASS (tests/cli proves the ApiClient change is backward-compatible with existing callers/fakes).

- [ ] **Step 5: Commit.**

```bash
git add src/mml_cloud_courier/service/app.py src/mml_cloud_courier/cli/service_client.py tests/service/test_api.py
git commit -m "feat: archive/unarchive endpoints and client methods; include_archived listing"
```

---

### Task 4: Rail model — the archived group

**Files:**
- Modify: `src/mml_cloud_courier/gui/jobs_model.py`
- Test: `tests/gui/test_jobs_model.py` (append + ONE authorized assertion update)

**Interfaces:**
- Consumes: jobs dicts now carrying `archived_at`.
- Produces (Task 5 relies on these exactly): `RAIL_GROUPS == ("needs_attention", "running", "queued", "completed", "archived")`; `GROUP_LABELS["archived"] == "Archived"`; `group_for_job(job: dict) -> str` (archived_at set → `"archived"`, else `group_for_status(job["status"])`); `sync_rail`/`_rail_signature` sensitive to `archived_at` changes.

- [ ] **Step 1: Write the failing tests.** In `tests/gui/test_jobs_model.py`, update the ONE authorized assertion in `test_sync_rail_places_jobs_and_roles`: `assert model.rowCount() == 4` → `== 5`. Then append:

```python
def test_archived_jobs_route_to_the_archived_group(qapp):
    from mml_cloud_courier.gui.jobs_model import group_for_job
    active = _job(1, "complete")
    archived = {**_job(2, "complete"), "archived_at": "2026-08-09T00:00:00+00:00"}
    assert group_for_job(active) == "completed"
    assert group_for_job(archived) == "archived"
    # regardless of status
    assert group_for_job({**_job(3, "cancelled"),
                          "archived_at": "2026-08-09T00:00:00+00:00"}) == "archived"

    model = build_rail_model()
    sync_rail(model, [active, archived])
    completed = model.item(RAIL_GROUPS.index("completed"))
    archived_group = model.item(RAIL_GROUPS.index("archived"))
    assert completed.rowCount() == 1 and completed.child(0).data(JOB_ID_ROLE) == 1
    assert archived_group.rowCount() == 1 and archived_group.child(0).data(JOB_ID_ROLE) == 2
    assert archived_group.text() == GROUP_LABELS["archived"]


def test_archiving_a_job_changes_the_rail_signature(qapp):
    model = build_rail_model()
    job = _job(1, "complete")
    assert sync_rail(model, [job]) is True
    assert sync_rail(model, [job]) is False              # unchanged: no-op
    archived = {**job, "archived_at": "2026-08-09T00:00:00+00:00"}
    assert sync_rail(model, [archived]) is True          # archive forces a rebuild
```

- [ ] **Step 2: Run to verify failure.**

Run: `.venv\Scripts\python -m pytest tests/gui/test_jobs_model.py -q -o addopts=`
Expected: new tests FAIL (`group_for_job` missing; rowCount 4).

- [ ] **Step 3: Implement.** In `jobs_model.py`:

```python
RAIL_GROUPS = ("needs_attention", "running", "queued", "completed", "archived")
GROUP_LABELS = {
    "needs_attention": "Needs attention",
    "running": "Running",
    "queued": "Queued",
    "completed": "Completed",
    "archived": "Archived",
}
```

Below `group_for_status`:

```python
def group_for_job(job: dict) -> str:
    # Archived is a shelf, not a lifecycle state: it wins over status.
    if job.get("archived_at"):
        return "archived"
    return group_for_status(job["status"])
```

In `_grouped_and_sorted`, bucket by `group_for_job(job)` instead of `group_for_status(job["status"])`. In `_rail_signature`, extend the per-job tuple with `job.get("archived_at")`:

```python
            tuple((job["id"], job["status"], job["name"],
                   job.get("scheduled_start_at"), job.get("archived_at"))
                 for job in buckets[group])
```

- [ ] **Step 4: Run to verify pass.**

Run: `.venv\Scripts\python -m pytest tests/gui/test_jobs_model.py tests/gui/test_main_window_smoke.py tests/gui/test_rail_filter.py -q -o addopts=`
Expected: PASS (`_sync_rail_preserving_expansion` iterates RAIL_GROUPS generically, so the fifth group needs no main-window change yet).

- [ ] **Step 5: Commit.**

```bash
git add src/mml_cloud_courier/gui/jobs_model.py tests/gui/test_jobs_model.py
git commit -m "feat: fifth archived rail group routed by group_for_job"
```

---

### Task 5: MainWindow — Show-archived toggle, context menu, archive handlers

**Files:**
- Modify: `src/mml_cloud_courier/gui/main_window.py`
- Modify: `src/mml_cloud_courier/gui/watcher.py` (`poll_loop` ~74-88), `src/mml_cloud_courier/gui/workers.py` (`JobsPoller.start` ~93)
- Test: `tests/gui/test_rail_archive.py` (new)

**Interfaces:**
- Consumes: Task 1's `_clear_selection_and_tabs()`; Task 3's `ApiClient.list_jobs(include_archived=)/archive_job/unarchive_job`; Task 4's `RAIL_GROUPS`/`group_for_job`.
- Produces (Task 6 relies on): `MainWindow._archive_job(job_id: int) -> None` (async; on success clears selection if it was the selected job, then re-polls); `MainWindow._unarchive_job(job_id: int) -> None`; `MainWindow._set_show_archived(on: bool) -> None`; `MainWindow._show_archived: bool`; `MainWindow._rail_menu_spec(index) -> list[tuple[str, str, bool]]` (testable menu composition: `(kind, label, checked)` where kind ∈ `"archive" | "unarchive" | "toggle_archived"`; checked is only meaningful for the toggle).

- [ ] **Step 1: Write the failing tests** (`tests/gui/test_rail_archive.py`):

```python
"""Show-archived toggle, rail context menu composition, archive handlers.
Fake jobs drive _on_jobs directly; the client fake records archive calls."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from mml_cloud_courier.gui.jobs_model import JOB_ID_ROLE, RAIL_GROUPS
from mml_cloud_courier.gui.main_window import MainWindow
from mml_cloud_courier.gui.session import discover_session


@pytest.fixture
def window(qtbot, gui_host):
    win = MainWindow(discover_session(), poll_interval=60)
    qtbot.addWidget(win)
    yield win
    win.shutdown()


def _job(job_id, status="complete", archived=None, profile_id=10):
    return {"id": job_id, "name": f"job-{job_id}", "status": status,
            "direction": "upload", "source_root": "C:\\d", "dest_prefix": "",
            "scheduled_start_at": None,
            "created_at": "2026-08-09T00:00:00+00:00",
            "archived_at": archived, "profile_id": profile_id, "progress": {}}


ARCHIVED_ROW = RAIL_GROUPS.index("archived")


@pytest.mark.gui
def test_archived_group_hidden_until_toggled(qtbot, window):
    window._on_jobs([_job(1)])
    assert window.rail_view.isRowHidden(ARCHIVED_ROW, window.rail_model.invisibleRootItem().index())
    window._set_show_archived(True)
    assert not window.rail_view.isRowHidden(ARCHIVED_ROW, window.rail_model.invisibleRootItem().index())
    assert not window.rail_view.isExpanded(window.rail_model.index(ARCHIVED_ROW, 0))
    window._set_show_archived(False)
    assert window.rail_view.isRowHidden(ARCHIVED_ROW, window.rail_model.invisibleRootItem().index())


@pytest.mark.gui
def test_menu_spec_per_row_kind(qtbot, window):
    window._set_show_archived(True)
    window._on_jobs([_job(1, "complete"), _job(2, "running"),
                     _job(3, "complete", archived="2026-08-09T00:00:00+00:00")])
    def index_for(job_id):
        idx = window._find_rail_index(job_id)
        assert idx is not None
        return idx
    kinds = [k for k, _l, _c in window._rail_menu_spec(index_for(1))]
    assert kinds == ["archive", "toggle_archived"]
    kinds = [k for k, _l, _c in window._rail_menu_spec(index_for(2))]
    assert kinds == ["toggle_archived"]           # running: not archivable
    kinds = [k for k, _l, _c in window._rail_menu_spec(index_for(3))]
    assert kinds == ["unarchive", "toggle_archived"]
    # a group header: toggle only, and its checked flag mirrors the state
    header_spec = window._rail_menu_spec(window.rail_model.index(0, 0))
    assert [k for k, _l, _c in header_spec] == ["toggle_archived"]
    assert header_spec[0][2] is True


@pytest.mark.gui
def test_archive_handler_calls_client_and_clears_selection(qtbot, window, monkeypatch):
    calls = []
    monkeypatch.setattr(window.client, "archive_job",
                        lambda job_id: calls.append(job_id) or {"archived": job_id},
                        raising=False)
    window._on_jobs([_job(1)])
    window.select_job(1)
    qtbot.waitUntil(lambda: window.selected_job_id == 1, timeout=5000)
    window._archive_job(1)
    qtbot.waitUntil(lambda: calls == [1], timeout=5000)
    qtbot.waitUntil(lambda: window.selected_job_id is None, timeout=5000)


@pytest.mark.gui
def test_toggle_off_with_archived_selection_clears_it(qtbot, window):
    window._set_show_archived(True)
    window._on_jobs([_job(1), _job(3, archived="2026-08-09T00:00:00+00:00")])
    window.select_job(3)
    qtbot.waitUntil(lambda: window.selected_job_id == 3, timeout=5000)
    window._set_show_archived(False)
    assert window.selected_job_id is None
```

- [ ] **Step 2: Run to verify failure.**

Run: `.venv\Scripts\python -m pytest tests/gui/test_rail_archive.py -q -o addopts=`
Expected: FAIL (`_set_show_archived` missing).

- [ ] **Step 3: Implement.**

`watcher.py` `poll_loop`: add an optional fetch callable (signature currently `poll_loop(client, *, stop, interval, on_jobs, on_down)` — adjust to the actual signature found in the file):

```python
def poll_loop(client, *, stop, interval, on_jobs, on_down, fetch=None):
    ...
    jobs = (fetch or client.list_jobs)()
```

(only the fetch line and parameter change; everything else stays).

`workers.py` `JobsPoller.start` gains `fetch=None` and passes it through to `poll_loop(..., fetch=fetch)`.

`main_window.py`:

- In `__init__` near the rail construction: `self._show_archived = False`; after the completed-collapse line, hide the archived header:

```python
        archived_index = self.rail_model.index(RAIL_GROUPS.index("archived"), 0)
        self.rail_view.setRowHidden(
            archived_index.row(), archived_index.parent(), True)
        self.rail_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.rail_view.customContextMenuRequested.connect(self._show_rail_menu)
```

- Poller start passes the live-state fetch (reads the flag at call time, so the toggle needs no restart):

```python
        self.poller.start(
            self.client, interval=self._poll_interval,
            fetch=lambda: self.client.list_jobs(
                include_archived=self._show_archived))
```

- `_poke_rail` becomes:

```python
    def _poke_rail(self) -> None:
        call_async(
            lambda: self.client.list_jobs(include_archived=self._show_archived),
            parent=self, on_done=self._on_jobs)
```

- The archive block (near the filter methods):

```python
    # -- archive ------------------------------------------------------

    def _set_show_archived(self, on: bool) -> None:
        self._show_archived = on
        archived_index = self.rail_model.index(RAIL_GROUPS.index("archived"), 0)
        self.rail_view.setRowHidden(
            archived_index.row(), archived_index.parent(), not on)
        if on:
            self.rail_view.collapse(archived_index)    # starts shelved, like Completed
        elif self._selected_job_id is not None:
            selected = next((job for job in self._last_jobs
                             if job["id"] == self._selected_job_id), None)
            if selected is not None and selected.get("archived_at"):
                self._clear_selection_and_tabs()
        self._poke_rail()

    def _rail_menu_spec(self, index) -> list[tuple[str, str, bool]]:
        """(kind, label, checked) triples for the rail context menu at
        `index` -- pure composition so tests can assert it without popping
        a QMenu."""
        spec: list[tuple[str, str, bool]] = []
        job_id = index.data(JOB_ID_ROLE)
        if job_id is not None:
            job = next((j for j in self._last_jobs if j["id"] == job_id), None)
            if job is not None:
                if job.get("archived_at"):
                    spec.append(("unarchive", "Unarchive job", False))
                elif job.get("status") in ("complete", "cancelled"):
                    spec.append(("archive", "Archive job", False))
        spec.append(("toggle_archived", "Show archived", self._show_archived))
        return spec

    def _show_rail_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu
        index = self.rail_view.indexAt(pos)
        job_id = index.data(JOB_ID_ROLE)
        menu = QMenu(self.rail_view)
        for kind, label, checked in self._rail_menu_spec(index):
            action = menu.addAction(label)
            if kind == "toggle_archived":
                action.setCheckable(True)
                action.setChecked(checked)
                action.triggered.connect(
                    lambda on, self=self: self._set_show_archived(on))
            elif kind == "archive":
                action.triggered.connect(
                    lambda _c=False, j=job_id: self._archive_job(j))
            elif kind == "unarchive":
                action.triggered.connect(
                    lambda _c=False, j=job_id: self._unarchive_job(j))
        menu.exec(self.rail_view.viewport().mapToGlobal(pos))

    def _archive_job(self, job_id: int) -> None:
        call_async(lambda: self.client.archive_job(job_id), parent=self,
                   on_done=lambda _r, j=job_id: self._archived(j),
                   on_failed=self._status_message)

    def _archived(self, job_id: int) -> None:
        if job_id == self._selected_job_id:
            self._clear_selection_and_tabs()
        self._poke_rail()

    def _unarchive_job(self, job_id: int) -> None:
        call_async(lambda: self.client.unarchive_job(job_id), parent=self,
                   on_done=lambda _r: self._poke_rail(),
                   on_failed=self._status_message)
```

(`Qt` is already imported in main_window.py — verify; add to the QtCore import if not.)

- [ ] **Step 4: Run to verify pass.**

Run: `.venv\Scripts\python -m pytest tests/gui/test_rail_archive.py tests/gui/test_rail_filter.py tests/gui/test_main_window_smoke.py tests/gui/test_watcher.py tests/gui/test_workers.py -q -o addopts=`
Expected: PASS (watcher/workers fakes unaffected — `fetch` is optional).

- [ ] **Step 5: Commit.**

```bash
git add src/mml_cloud_courier/gui/main_window.py src/mml_cloud_courier/gui/watcher.py src/mml_cloud_courier/gui/workers.py tests/gui/test_rail_archive.py
git commit -m "feat: show-archived toggle, rail context menu, archive handlers"
```

---

### Task 6: Summary-tab archive button

**Files:**
- Modify: `src/mml_cloud_courier/gui/job_tabs.py` (SummaryTab ~407-570)
- Modify: `src/mml_cloud_courier/gui/main_window.py` (SummaryTab construction ~174-177)
- Test: `tests/gui/test_job_tabs.py` (append, following its existing SummaryTab test patterns)

**Interfaces:**
- Consumes: Task 5's `MainWindow._archive_job`; SummaryTab's existing callback pattern (`on_open_report`, `on_resume`) and `update_job`.
- Produces: `SummaryTab(*, on_open_report, on_resume, on_archive, parent=None)`; `.archive_button` (QPushButton "Archive this job") visible iff `status in {"complete", "cancelled"}` and `archived_at` unset — derived in `update_job` (theme changes re-feed `update_job`, so visibility must be derived, not toggled ad hoc).

- [ ] **Step 1: Write the failing tests.** Append to `tests/gui/test_job_tabs.py` (construct SummaryTab exactly as that file's existing SummaryTab tests do, adding the new kwarg):

```python
def test_summary_archive_button_visibility_and_callback(qtbot):
    archived_calls = []
    tab = SummaryTab(on_open_report=lambda: None, on_resume=lambda: None,
                     on_archive=lambda: archived_calls.append(True))
    qtbot.addWidget(tab)
    assert tab.archive_button.text() == "Archive this job"

    tab.update_job({"id": 1, "status": "complete", "progress": {}})
    assert tab.archive_button.isVisibleTo(tab)
    tab.archive_button.click()
    assert archived_calls == [True]

    tab.update_job({"id": 1, "status": "cancelled", "progress": {}})
    assert tab.archive_button.isVisibleTo(tab)

    tab.update_job({"id": 1, "status": "running", "progress": {}})
    assert not tab.archive_button.isVisibleTo(tab)

    tab.update_job({"id": 1, "status": "complete", "progress": {},
                    "archived_at": "2026-08-09T00:00:00+00:00"})
    assert not tab.archive_button.isVisibleTo(tab)
```

(If existing SummaryTab constructions in this file or elsewhere pass only the two old kwargs, `on_archive` must have a safe default — use `on_archive=lambda: None` as the keyword default so no other test or call site breaks.)

- [ ] **Step 2: Run to verify failure.**

Run: `.venv\Scripts\python -m pytest tests/gui/test_job_tabs.py -q -o addopts=`
Expected: the new test FAILS (unexpected kwarg / no archive_button).

- [ ] **Step 3: Implement.** In `job_tabs.py`: module constant beside the other visibility sets:

```python
_ARCHIVE_VISIBLE = frozenset({"complete", "cancelled"})
```

`SummaryTab.__init__` signature: `def __init__(self, *, on_open_report, on_resume, on_archive=lambda: None, parent=None):`, stash `self._on_archive = on_archive`. Beside the existing buttons:

```python
        self.archive_button = QPushButton("Archive this job")
        self.archive_button.clicked.connect(lambda: self._on_archive())
        self.archive_button.hide()
```

and add it to `footer_row` after `resume_button`. In `update_job`, beside the existing visibility lines:

```python
        self.archive_button.setVisible(
            status in _ARCHIVE_VISIBLE and not job.get("archived_at"))
```

In `main_window.py`, the SummaryTab construction gains:

```python
        self.summary_tab = SummaryTab(
            on_open_report=self._on_open_report,
            on_resume=self._on_resume_from_summary,
            on_archive=self._on_archive_from_summary,
        )
```

with the handler beside `_on_resume_from_summary` (same template):

```python
    def _on_archive_from_summary(self) -> None:
        if self._selected_job_id is not None:
            self._archive_job(self._selected_job_id)
```

- [ ] **Step 4: Run to verify pass.**

Run: `.venv\Scripts\python -m pytest tests/gui/test_job_tabs.py tests/gui/test_rail_archive.py tests/gui/test_main_window_smoke.py -q -o addopts=`
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/mml_cloud_courier/gui/job_tabs.py src/mml_cloud_courier/gui/main_window.py tests/gui/test_job_tabs.py
git commit -m "feat: Summary-tab archive button for completed-group jobs"
```

---

### Task 7: Full-suite verification and recorded counts (no new code)

- [ ] **Step 1:** `.venv\Scripts\python -m pytest -o addopts= -q` — record exact counts. Expected: baseline 688 + all new tests passing, 13 skipped, zero failures. Fix anything red in this task (superpowers:systematic-debugging first), one focused commit per fix.
- [ ] **Step 2:** Confirm no contractual drift: `git diff master --stat -- tests/gui/test_connection_dialogs.py` must be empty; `git diff master -- src/mml_cloud_courier/gui/connection_dialogs.py | grep -E "^[-+]COPY_"` must be empty.
- [ ] **Step 3:** Report recorded counts to the orchestrator.

---

## After Task 7 (orchestrator)

Manual smoke check with the user from the worktree venv (`.venv\Scripts\python -m mml_cloud_courier.gui`). IMPORTANT expectation-setting: the GUI talks to the live SERVICE over HTTP, and the live service still runs the old code — its DB migrates v2→v3 and the new endpoints appear only when the service restarts onto merged code. So against the live service, archive actions will 404; the smoke check verifies the context menu / Show-archived toggle / Summary button render correctly and that the 404 surfaces as a status-bar message, not a crash. Full end-to-end archive behavior is covered by the suite's ephemeral hosts and goes live at the next service restart. Then superpowers:finishing-a-development-branch: merge `--no-ff`, push; the merge report notes the pending v2→v3 migration + new endpoints at next service restart.

## Self-review notes (applied)

- Spec coverage: §3→Task 1, §4→Task 1, §5→Task 2, §6→Task 2, §7→Task 3, §8→Task 4, §9→Tasks 5-6, §10→each task's tests + Task 7, §11→Task 0 + orchestrator close-out. CLI `run_status` raw-SQL branch intentionally untouched (spec non-goal; shows archived rows in direct-DB mode — acceptable, it is the raw database view).
- Type consistency: `JobNotArchivable` (Tasks 2/3), `list_jobs(include_archived=False)` (2/3/5), `group_for_job` (4/5), `_clear_selection_and_tabs` (1/5), `_rail_menu_spec` triples (5), `on_archive` default (6).
- The smoke-check caveat above (live service is v2 until restarted) is called out so nobody misreads a 404 as a defect.
