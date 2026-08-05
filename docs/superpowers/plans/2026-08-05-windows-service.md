# MML Cloud Transfer — Plan 3: Windows Service

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Execution conventions (carried from Plans 1–2):** work in an isolated worktree; one commit per task; never amend; every subagent dispatch includes the working-directory guard (`git rev-parse --show-toplevel` must print the worktree root before any other command).

**Goal:** Jobs run inside a Windows Service and survive logoff, process kill, and machine restart: the CLI submits and watches jobs entirely over a token-authenticated local HTTP API, a FIFO worker runs one job at a time (honoring scheduled start times), startup recovery auto-resumes interrupted work, sustained network loss parks a job in `stalled` instead of failing the night, and SSE streams progress at ~2 events/second.

**Architecture:** A new `service/` package wraps the finished Plan 2 engine without changing its contracts: `config.py` (data dir + settings), `security.py` (ACL-restricted bearer-token file), `controller.py` (shared state between API and worker), `app.py` (FastAPI REST), `sse.py` (progress stream), `worker.py` (FIFO queue, scheduler eligibility, stalled probing, startup recovery), `host.py` (console host: uvicorn + worker threads), `windows_service.py` (pywin32 wrapper, session 0). The worker calls `engine.runner.run_job` exactly as the CLI does today; a new cooperative-stop hook (`EngineOptions.should_stop`) lets the service wind a run down in seconds, and a graceful stop deliberately reuses the crash-recovery path — stop is just a controlled interruption, resume is not a special mode. The plan opens with the five follow-ups queued by Plan 2's final review, because the service builds directly on the pieces they fix.

**Tech Stack:** Python 3.12, FastAPI + uvicorn (API), pywin32 (service host), requests (CLI client + SSE parsing by hand), httpx (dev, FastAPI TestClient transport), stdlib `sqlite3`/`threading`/`secrets`/`subprocess` (icacls), pytest, fake-gcs-server emulator.

**Spec:** [2026-08-04-gcs-transfer-manager-design.md](../specs/2026-08-04-gcs-transfer-manager-design.md) — Phase 3. *Done when:* the CLI drives jobs entirely over the local API and a job survives killing the service process and logging off. Auth profiles/DPAPI are Plan 4; the GUI is Plan 5. The Plan 2 release gate (`MMLCT_TEST_BUCKET` real-bucket suite + manual multi-GB kill-and-resume) remains open in parallel and does not block this plan.

## Global Constraints

These apply to every task. Do not restate them; do not violate them.

- **Python 3.12** (`py -3.12 -m venv`; interpreter `.venv/Scripts/python`). Run tests as `.venv/Scripts/python -m pytest ...`.
- **`core` stays pure**: no imports from `google.cloud.storage`, `google.auth`, `requests`, `fastapi`, PySide6, or `sqlite3` anywhere under `core/`.
- **`fastapi`/`uvicorn`/`pywin32` are imported ONLY under `service/`** (and in tests). `gcs` remains the only home of `google.cloud.storage`/`google.auth`/`requests` imports, except `cli/service_client.py`, which may import `requests` (it is an HTTP client by definition and touches no GCS API).
- **The API binds `127.0.0.1` only, and every route except `GET /health` requires the bearer token.** Token comparison uses `secrets.compare_digest`.
- **The service reuses `engine.runner.run_job` unchanged in contract** — the worker adds no second transfer code path. The CLI keeps its direct-engine mode (existing tests depend on it); service mode is additive behind `--service-url`.
- **Timestamps** are UTC ISO-8601 with seconds precision, matching `store.repository._now()` (`+00:00` offset). `jobs.scheduled_start_at` is normalized to this exact format at the API boundary so lexicographic SQL comparison is correct.
- **Fault-injection tests use real transport exception types** (`requests.exceptions.ConnectionError`, urllib3 classes) — never builtin `ConnectionResetError`. This masked a Critical in Plan 2.
- **Emulator tests** (marker `emulator`) skip cleanly when `tools/fake-gcs-server.exe` is absent (fetch: `pwsh tests/tools/get-fake-gcs-server.ps1`). Remember fake-gcs-server's resumable **status query finalizes truncated uploads** — do not write tests that query a deliberately truncated session and expect a 308 (the StatusQueryShim pattern in `tests/gcs/test_uploader_resumable.py` exists for that reason).
- **SQLite connections are thread-bound.** Any new thread (API handler, SSE generator, worker) opens its own connection via `store.db.connect` and closes it. Never share a connection across threads.
- **TDD throughout**: write the failing test, watch it fail, implement, watch it pass, commit.

## File Structure

```text
src/mml_cloud_transfer/
  core/errors.py             + TransferStopped                       (modify)
  core/slicing.py            + SizePolicy.parse classmethod          (modify)
  engine/runner.py           + run_finished on PAUSED, should_stop,
                               heartbeat call sites                  (modify)
  gcs/uploader.py            + should_stop in chunk loops            (modify)
  gcs/downloader.py          + progress cadence, should_stop         (modify)
  store/repository.py        + set_audit_hash, heartbeat, JobProgress,
                               queue/profile/paging queries          (modify)
  store/schema.py            + comment on bytes_transferred          (modify)
  cli/transfer_command.py    + set_audit_hash use, service transport (modify)
  cli/__main__.py            + --service-url/--token-file/--scheduled-at (modify)
  cli/service_client.py      ApiClient: REST + SSE over requests     (create)
  service/__init__.py                                                (create)
  service/config.py          ServiceConfig, load_config, data dir    (create)
  service/security.py        ensure_token / read_token / restrict_acl(create)
  service/controller.py      JobController: API<->worker handshake   (create)
  service/app.py             create_app: REST routes + auth          (create)
  service/sse.py             progress_events generator (~2/s)        (create)
  service/worker.py          QueueWorker: FIFO, scheduler, stalled,
                             startup recovery                        (create)
  service/host.py            ServiceHost + run_console               (create)
  service/__main__.py        python -m mml_cloud_transfer.service    (create)
  service/windows_service.py pywin32 wrapper + mmlct-service entry   (create)
docs/superpowers/specs/2026-08-04-gcs-transfer-manager-design.md     (modify: SHA-256 note)
pyproject.toml               + fastapi, uvicorn, pywin32, httpx(dev),
                               mmlct-service script                  (modify)
tests/
  engine/test_runner.py            + paused-event, stop tests        (modify)
  gcs/test_downloader_progress.py  cadence unit tests                (create)
  gcs/test_should_stop.py          stop in chunk loops               (create)
  store/test_repository.py         + set_audit_hash                  (modify)
  store/test_progress.py           heartbeat + job_progress          (create)
  store/test_repository_service.py queue/profile/paging queries      (create)
  core/test_slicing.py             + SizePolicy.parse                (modify)
  service/__init__.py                                               (create)
  service/conftest.py              free_port + running_host fixtures (create)
  service/test_config.py                                            (create)
  service/test_security.py                                          (create)
  service/test_controller.py                                        (create)
  service/test_api.py                                               (create)
  service/test_sse.py                                               (create)
  service/test_worker.py           unit (injected engine fns)       (create)
  service/test_worker_emulator.py  run_once end-to-end              (create)
  service/test_host.py                                              (create)
  service/test_windows_service.py  importorskip(pywin32)            (create)
  service/test_service_kill_resume.py  THE defining test            (create)
  cli/test_service_mode.py         CLI over the API                 (create)
```

---

### Task 1: Plan 2 follow-ups — `run_finished` on PAUSED, `JobRepository.set_audit_hash`, spec note on sliced SHA-256

Three review carry-forwards with no behavioral coupling to the service yet. (The remaining two follow-ups are Task 2.)

**Files:**
- Modify: `src/mml_cloud_transfer/engine/runner.py` (paused branch in `run_job`, ~line 428)
- Modify: `src/mml_cloud_transfer/store/repository.py` (add `set_audit_hash` next to `set_precondition`)
- Modify: `src/mml_cloud_transfer/cli/transfer_command.py` (replace raw SQL, ~lines 97–104)
- Modify: `docs/superpowers/specs/2026-08-04-gcs-transfer-manager-design.md` (Verification section)
- Test: `tests/engine/test_runner.py`, `tests/store/test_repository.py`

**Interfaces:**
- Consumes: existing `run_job`, `JobRepository`, `finish_job`, `record_event`.
- Produces: `JobRepository.set_audit_hash(job_id: int, enabled: bool) -> None` (raises `LookupError` on a bogus id); every run — including a paused one — now ends with a `run_finished` event whose detail is the final status value.

- [ ] **Step 1: Write the failing tests**

Append to `tests/engine/test_runner.py` (its existing `job` fixture, `opts` helper, `FakeApiError`, and imports are already in the file):

```python
def test_paused_run_still_records_run_finished(job, monkeypatch):
    db, job_id = job
    monkeypatch.setattr(
        runner, "upload_single_shot",
        lambda *a, **k: (_ for _ in ()).throw(FakeApiError(403)),
    )
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    run_job(db, job_id, ctx=None, options=opts())
    conn = connect(db)
    pairs = [(e["kind"], e["detail"]) for e in JobRepository(conn).get_events(job_id)]
    conn.close()
    assert ("run_finished", JobStatus.PAUSED.value) in pairs
```

Append to `tests/store/test_repository.py` (add `import pytest` and the `Direction`/`connect`/`JobRepository` imports if that file does not already have them):

```python
def test_set_audit_hash_flips_the_flag(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    repo = JobRepository(conn)
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root="s", dest_prefix=""
    )
    assert repo.get_job(job_id)["audit_hash"] == 0
    repo.set_audit_hash(job_id, True)
    assert repo.get_job(job_id)["audit_hash"] == 1
    repo.set_audit_hash(job_id, False)
    assert repo.get_job(job_id)["audit_hash"] == 0
    with pytest.raises(LookupError):
        repo.set_audit_hash(999, True)
    conn.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/engine/test_runner.py::test_paused_run_still_records_run_finished tests/store/test_repository.py::test_set_audit_hash_flips_the_flag -v`
Expected: FAIL — the event pair is absent; `JobRepository` has no attribute `set_audit_hash`.

- [ ] **Step 3: Implement**

In `engine/runner.py`, the paused branch of `run_job` becomes:

```python
        if paused:
            repo.finish_job(job_id, JobStatus.PAUSED)
            repo.record_event(job_id, "run_finished", JobStatus.PAUSED.value)
            return JobStatus.PAUSED
```

In `store/repository.py`, next to `set_precondition`:

```python
    def set_audit_hash(self, job_id: int, enabled: bool) -> None:
        self.get_job(job_id)  # LookupError on a bogus id, matching get_precondition
        self._conn.execute(
            "UPDATE jobs SET audit_hash = ? WHERE id = ?", (int(enabled), job_id)
        )
```

In `cli/transfer_command.py`, replace the raw-SQL block:

```python
    if direction is Direction.UPLOAD and args.audit_hash:
        conn = connect(args.db)
        try:
            JobRepository(conn).set_audit_hash(job_id, True)
        finally:
            conn.close()
```

In the spec's **Verification** section, replace the paragraph beginning `**Optional SHA-256** is computed in the same single read pass...` with:

```markdown
**Optional SHA-256** is computed in the same single read pass for single-shot and
single-session resumable files, so there it costs CPU but no additional I/O. Sliced
files (> 1 GiB) hash their slices in parallel and out of order, so the whole-file
SHA-256 requires one additional sequential read of the source after the slices
complete — enabling the audit hash roughly doubles local read I/O for the largest
files. It is stored in `job_files` and stamped into the object's custom metadata so
the hash travels with the object.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/engine/test_runner.py tests/store/test_repository.py tests/cli -v`
Expected: PASS (including the untouched CLI tests — the audit-hash path behaves identically).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "fix: run_finished event on paused runs; JobRepository.set_audit_hash; spec note on sliced SHA-256 second read"
```

---

### Task 2: Plan 2 follow-ups — download progress cadence and derived per-file byte progress

Two related fixes. (1) `_fetch_range` fires `on_progress` per 1 MiB chunk, and each mid-range callback opens a fresh SQLite connection in the runner (`_callback_repo` on a foreign thread) — throttle mid-range callbacks to a `progress_interval_bytes` cadence (default 32 MiB). (2) `job_files.bytes_transferred` flaps because concurrent slice callbacks overwrite it with per-slice counts — make `heartbeat` timestamp-only and derive byte progress from `file_slices`, which is per-slice and therefore correct under concurrency. `JobRepository.job_progress` becomes the single source the SSE stream and API read.

**Files:**
- Modify: `src/mml_cloud_transfer/gcs/downloader.py` (`_fetch_range`, `download_file`)
- Modify: `src/mml_cloud_transfer/store/repository.py` (`heartbeat`, new `JobProgress` + `job_progress`)
- Modify: `src/mml_cloud_transfer/engine/runner.py` (three `heartbeat` call sites)
- Modify: `src/mml_cloud_transfer/store/schema.py` (comment only, on `bytes_transferred`)
- Test: `tests/gcs/test_downloader_progress.py` (create), `tests/store/test_progress.py` (create)

**Interfaces:**
- Consumes: `SliceSpec` from `core.slicing`; existing `upsert_slice` semantics (callbacks already record per-slice `bytes_transferred`).
- Produces: `download_file(..., progress_interval_bytes: int = DOWNLOAD_PROGRESS_INTERVAL)` (module constant `DOWNLOAD_PROGRESS_INTERVAL = 32 * 1024 * 1024`); `_fetch_range(ctx, url, part_path, spec, on_progress, *, progress_interval_bytes)` (keyword-only tail; Task 3 adds `should_stop` beside it); `JobRepository.heartbeat(file_id: int) -> None` (timestamp only); `JobProgress` frozen dataclass with fields `files_total: int, files_done: int, files_failed: int, bytes_total: int, bytes_done: int, state_counts: dict[str, int]`; `JobRepository.job_progress(job_id: int) -> JobProgress`. Tasks 6–7 consume `job_progress` verbatim.

- [ ] **Step 1: Write the failing tests**

Create `tests/gcs/test_downloader_progress.py`:

```python
"""Mid-range progress callbacks are throttled: each one opens a SQLite
connection in the runner, so the cadence must amortize that cost over
many MiB. The final callback (carrying the range CRC) always fires."""

from types import SimpleNamespace

from mml_cloud_transfer.core.slicing import SliceSpec
from mml_cloud_transfer.gcs.downloader import _fetch_range

_MIB = 1024 * 1024


class _Response:
    status_code = 200
    headers: dict = {}
    text = ""

    def __init__(self, total: int):
        self._total = total

    def iter_content(self, chunk_size):
        sent = 0
        while sent < self._total:
            n = min(chunk_size, self._total - sent)
            yield b"\0" * n
            sent += n

    def json(self):
        return {}


def _ctx(total):
    session = SimpleNamespace(
        get=lambda url, headers=None, stream=False: _Response(total)
    )
    return SimpleNamespace(session=session)


def test_mid_range_progress_is_throttled(tmp_path):
    total = 8 * _MIB
    part = tmp_path / "f.part"
    part.write_bytes(b"\0" * total)
    calls = []
    _fetch_range(
        _ctx(total), "http://x", part,
        SliceSpec(index=0, offset=0, length=total),
        lambda idx, done, crc: calls.append((done, crc)),
        progress_interval_bytes=4 * _MIB,
    )
    mid = [done for done, crc in calls if crc is None]
    finals = [(done, crc) for done, crc in calls if crc is not None]
    assert mid == [4 * _MIB, 8 * _MIB]     # not one call per MiB
    assert len(finals) == 1 and finals[0][0] == total


def test_small_ranges_only_fire_the_final_callback(tmp_path):
    total = 2 * _MIB
    part = tmp_path / "f.part"
    part.write_bytes(b"\0" * total)
    calls = []
    _fetch_range(
        _ctx(total), "http://x", part,
        SliceSpec(index=0, offset=0, length=total),
        lambda idx, done, crc: calls.append((done, crc)),
        progress_interval_bytes=32 * _MIB,
    )
    assert len(calls) == 1 and calls[0][1] is not None
```

Create `tests/store/test_progress.py`:

```python
"""Byte progress is derived from file_slices (per-slice, correct under
concurrency); job_files.bytes_transferred is no longer written by
heartbeat because concurrent slice callbacks made it flap."""

from mml_cloud_transfer.core.models import Direction, PlannedFile, SliceState
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


def _job(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    repo = JobRepository(conn)
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root="s", dest_prefix="p"
    )
    repo.add_planned_files(job_id, [
        PlannedFile("a.bin", "s/a.bin", 100, 1),
        PlannedFile("b.bin", "s/b.bin", 1000, 1),
        PlannedFile("c.bin", "s/c.bin", 500, 1),
    ])
    return conn, repo, job_id


def test_heartbeat_updates_timestamp_only(tmp_path):
    conn, repo, job_id = _job(tmp_path)
    file_id = repo.get_files(job_id)[0]["id"]
    repo.heartbeat(file_id)
    row = repo.get_file(file_id)
    conn.close()
    assert row["heartbeat_at"] is not None
    assert row["bytes_transferred"] == 0


def test_job_progress_sums_done_sizes_and_inflight_slices(tmp_path):
    conn, repo, job_id = _job(tmp_path)
    a, b, c = (r["id"] for r in repo.get_files(job_id))
    repo.mark_verified(a, local_crc32c=1, remote_crc32c=1, generation=1)
    repo.mark_transferring(b)
    repo.upsert_slice(b, 0, offset=0, length=600,
                      state=SliceState.UPLOADED, bytes_transferred=600)
    repo.upsert_slice(b, 1, offset=600, length=400,
                      state=SliceState.UPLOADING, bytes_transferred=150)
    progress = repo.job_progress(job_id)
    conn.close()
    assert progress.files_total == 3
    assert progress.files_done == 1
    assert progress.files_failed == 0
    assert progress.bytes_total == 1600
    assert progress.bytes_done == 100 + 600 + 150
    assert progress.state_counts["pending"] == 1


def test_job_progress_ignores_slices_of_files_not_transferring(tmp_path):
    conn, repo, job_id = _job(tmp_path)
    b = repo.get_files(job_id)[1]["id"]
    repo.upsert_slice(b, 0, offset=0, length=600,
                      state=SliceState.UPLOADED, bytes_transferred=600)
    progress = repo.job_progress(job_id)  # b is pending, not transferring
    conn.close()
    assert progress.bytes_done == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/gcs/test_downloader_progress.py tests/store/test_progress.py -v`
Expected: FAIL — `_fetch_range` rejects `progress_interval_bytes`; `heartbeat` requires a second positional argument; `job_progress` does not exist.

- [ ] **Step 3: Implement**

`gcs/downloader.py` — add the constant and thread the parameter:

```python
DOWNLOAD_PROGRESS_INTERVAL = 32 * 1024 * 1024
```

```python
def _fetch_range(
    ctx: GcsContext,
    url: str,
    part_path: Path,
    spec: SliceSpec,
    on_progress: RangeProgressFn | None,
    *,
    progress_interval_bytes: int,
) -> int:
    """Stream one range into the part file; returns the range CRC32C."""
    crc = google_crc32c.Checksum()
    done = 0
    last_reported = 0
    response = ctx.session.get(
        url,
        headers={"Range": f"bytes={spec.offset}-{spec.offset + spec.length - 1}"},
        stream=True,
    )
    raise_for_status(response)
    # A separate handle per worker: seek + write is safe across threads
    # because ranges never overlap.
    with part_path.open("r+b") as fp:
        fp.seek(spec.offset)
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            fp.write(chunk)
            crc.update(chunk)
            done += len(chunk)
            if (
                on_progress is not None
                and done - last_reported >= progress_interval_bytes
            ):
                on_progress(spec.index, done, None)
                last_reported = done
    if done != spec.length:
        raise GcsHttpError(500, f"range {spec.index}: got {done} of {spec.length} bytes")
    range_crc = int.from_bytes(crc.digest(), "big")
    if on_progress is not None:
        on_progress(spec.index, done, range_crc)
    return range_crc
```

`download_file` gains `progress_interval_bytes: int = DOWNLOAD_PROGRESS_INTERVAL` in its signature and passes it through in the `pool.submit(_fetch_range, ...)` call — `pool.submit(fn, *args, **kwargs)` forwards keywords, so:

```python
        futures = {
            pool.submit(
                _fetch_range, ctx, url, part, spec, on_progress,
                progress_interval_bytes=progress_interval_bytes,
            ): spec
            for spec in to_fetch
        }
```

(`pool.submit(fn, *args, **kwargs)` forwards keywords — the dict-comprehension form above is fine as written.)

`store/repository.py` — heartbeat and the derivation (add `from dataclasses import dataclass` at the top):

```python
    def heartbeat(self, file_id: int) -> None:
        """Refresh the staleness timestamp. Byte progress lives in
        file_slices — a whole-file number written from concurrent slice
        callbacks flaps, so it is derived (job_progress), not stored."""
        self._conn.execute(
            "UPDATE job_files SET heartbeat_at = ? WHERE id = ?", (_now(), file_id)
        )
```

```python
@dataclass(frozen=True, slots=True)
class JobProgress:
    files_total: int
    files_done: int      # verified + skipped
    files_failed: int    # failed + quarantined
    bytes_total: int
    bytes_done: int      # sizes of done files + in-flight slice bytes
    state_counts: dict[str, int]
```

```python
    def job_progress(self, job_id: int) -> JobProgress:
        job = self.get_job(job_id)
        counts = {
            r["state"]: r["n"]
            for r in self._conn.execute(
                "SELECT state, COUNT(*) AS n FROM job_files"
                " WHERE job_id = ? GROUP BY state",
                (job_id,),
            )
        }
        done_bytes = self._conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) AS b FROM job_files"
            " WHERE job_id = ? AND state IN (?, ?)",
            (job_id, FileState.VERIFIED.value, FileState.SKIPPED.value),
        ).fetchone()["b"]
        inflight = self._conn.execute(
            "SELECT COALESCE(SUM(s.bytes_transferred), 0) AS b FROM file_slices s"
            " JOIN job_files f ON f.id = s.file_id"
            " WHERE f.job_id = ? AND f.state = ?",
            (job_id, FileState.TRANSFERRING.value),
        ).fetchone()["b"]
        return JobProgress(
            files_total=job["planned_files"],
            files_done=counts.get(FileState.VERIFIED.value, 0)
            + counts.get(FileState.SKIPPED.value, 0),
            files_failed=counts.get(FileState.FAILED.value, 0)
            + counts.get(FileState.QUARANTINED.value, 0),
            bytes_total=job["planned_bytes"],
            bytes_done=done_bytes + inflight,
            state_counts=counts,
        )
```

`engine/runner.py` — the three callback call sites `r.heartbeat(file_id, committed)` / `r.heartbeat(file_id, done)` become `r.heartbeat(file_id)`.

Grep for any other `heartbeat(` call sites passing bytes (`tests/store`, `tests/engine`) and update them to the one-argument signature.

`store/schema.py` — annotate the column (comment only; removing a SQLite column forces a table rebuild that is not worth it):

```sql
    -- Retained for schema stability; no longer written during transfer.
    -- Live byte progress is per-slice in file_slices.bytes_transferred
    -- and aggregated by JobRepository.job_progress.
    bytes_transferred INTEGER NOT NULL DEFAULT 0,
```

- [ ] **Step 4: Run the full suite to verify nothing regressed**

Run: `.venv/Scripts/python -m pytest`
Expected: PASS (236+ passed baseline plus the new tests; skips only for absent emulator/real-bucket env). The emulator download tests exercise the new cadence path end-to-end.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "fix: throttle download progress callbacks; derive byte progress from file_slices"
```

---

### Task 3: Cooperative stop — `TransferStopped` and `EngineOptions.should_stop`

The service must wind a run down in seconds (SCM stop, pause/cancel requests) without a second shutdown mechanism. Design: a `should_stop` callable checked between files, between retry attempts, and inside every chunk loop. When it fires, `TransferStopped` propagates up, `run_job` records a `run_stopped` event, sets the job back to `pending`, and returns — deliberately landing in the same state a crash would, so the existing resume path is the only recovery path. Stop latency is bounded by one 8 MiB upload chunk or one 1 MiB download chunk.

**Files:**
- Modify: `src/mml_cloud_transfer/core/errors.py` (add `TransferStopped`)
- Modify: `src/mml_cloud_transfer/gcs/uploader.py` (`upload_resumable`, `upload_slice`, `upload_sliced`)
- Modify: `src/mml_cloud_transfer/gcs/downloader.py` (`download_file`, `_fetch_range`)
- Modify: `src/mml_cloud_transfer/engine/runner.py` (`EngineOptions`, `_process_file`, `_transfer_once`, `run_job`)
- Test: `tests/engine/test_runner.py` (append), `tests/gcs/test_should_stop.py` (create)

**Interfaces:**
- Consumes: Task 2's `_fetch_range` keyword-only tail.
- Produces: `class TransferStopped(Exception)` in `core.errors`; `EngineOptions.should_stop: Callable[[], bool] | None = None`; `should_stop: Callable[[], bool] | None = None` keyword parameter on `upload_resumable`, `upload_slice`, `upload_sliced`, `download_file` (and `_fetch_range`); `run_job` returns `JobStatus.PENDING` after a stop, with the job status set to `pending` and a `run_stopped` event recorded. Tasks 8–10 rely on exactly this contract for graceful shutdown, pause, and cancel.

- [ ] **Step 1: Write the failing tests**

Append to `tests/engine/test_runner.py`:

```python
def test_stop_between_files_reenqueues_the_job(job, monkeypatch):
    db, job_id = job
    calls = {"n": 0}
    stop = {"flag": False}

    def one_then_stop(*a, **k):
        calls["n"] += 1
        stop["flag"] = True
        return verified(100)

    monkeypatch.setattr(runner, "upload_single_shot", one_then_stop)
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    status = run_job(
        db, job_id, ctx=None,
        options=opts(should_stop=lambda: stop["flag"]),
    )
    assert status is JobStatus.PENDING
    assert calls["n"] == 1                      # second file never attempted
    conn = connect(db)
    repo = JobRepository(conn)
    job_row = repo.get_job(job_id)
    kinds = [e["kind"] for e in repo.get_events(job_id)]
    conn.close()
    assert job_row["status"] == JobStatus.PENDING.value
    assert job_row["finished_at"] is None       # a stop is not a finish
    assert "run_stopped" in kinds
    states = files_by_state(db, job_id)
    assert FileState.VERIFIED.value in states.values()
    assert FileState.PENDING.value in states.values()


def test_stopped_file_is_not_marked_failed(job, monkeypatch):
    from mml_cloud_transfer.core.errors import TransferStopped

    db, job_id = job
    monkeypatch.setattr(
        runner, "upload_single_shot",
        lambda *a, **k: (_ for _ in ()).throw(TransferStopped("wind-down")),
    )
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    status = run_job(db, job_id, ctx=None, options=opts())
    assert status is JobStatus.PENDING
    states = files_by_state(db, job_id)
    assert FileState.FAILED.value not in states.values()
```

Create `tests/gcs/test_should_stop.py`:

```python
"""Cooperative stop reaches into the chunk loops, so stop latency is one
chunk, not one file. The upload test runs against the emulator; the
download test drives _fetch_range with an in-memory fake session."""

import os
from types import SimpleNamespace

import pytest

from mml_cloud_transfer.core.errors import TransferStopped
from mml_cloud_transfer.core.slicing import SliceSpec
from mml_cloud_transfer.gcs.client import make_context
from mml_cloud_transfer.gcs.downloader import _fetch_range
from mml_cloud_transfer.gcs.objects import get_meta
from mml_cloud_transfer.gcs.uploader import upload_resumable

_MIB = 1024 * 1024


@pytest.mark.emulator
def test_should_stop_aborts_between_upload_chunks(emulator, emulator_client, tmp_path):
    _, bucket = emulator_client
    ctx = make_context(bucket, emulator_endpoint=emulator.endpoint)
    src = tmp_path / "big.bin"
    src.write_bytes(os.urandom(600 * 1024))

    seen = []  # (session_uri, committed) pairs; initiate + one chunk = 2 entries

    with pytest.raises(TransferStopped):
        upload_resumable(
            ctx, str(src), "stop/big.bin", src.stat().st_size,
            precondition_generation=0, chunk_size=256 * 1024,
            on_progress=lambda uri, committed: seen.append((uri, committed)),
            should_stop=lambda: len(seen) >= 2,
        )
    assert len(seen) == 2                       # stopped before the second chunk
    assert get_meta(ctx, "stop/big.bin") is None  # never finalized


class _Response:
    status_code = 200
    headers: dict = {}
    text = ""

    def __init__(self, total: int):
        self._total = total

    def iter_content(self, chunk_size):
        sent = 0
        while sent < self._total:
            n = min(chunk_size, self._total - sent)
            yield b"\0" * n
            sent += n

    def json(self):
        return {}


def test_fetch_range_stops_between_chunks(tmp_path):
    total = 4 * _MIB
    part = tmp_path / "f.part"
    part.write_bytes(b"\0" * total)
    ctx = SimpleNamespace(session=SimpleNamespace(
        get=lambda url, headers=None, stream=False: _Response(total)
    ))
    chunks_written = {"n": 0}

    def should_stop():
        return chunks_written["n"] >= 1

    def counting_progress(idx, done, crc):
        chunks_written["n"] = done // _MIB

    with pytest.raises(TransferStopped):
        _fetch_range(
            ctx, "http://x", part,
            SliceSpec(index=0, offset=0, length=total),
            counting_progress,
            should_stop=should_stop,
            progress_interval_bytes=_MIB,
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/engine/test_runner.py -k stop tests/gcs/test_should_stop.py -v`
Expected: FAIL — `TransferStopped` does not exist; `EngineOptions` rejects `should_stop`; the gcs functions reject the keyword.

- [ ] **Step 3: Implement**

`core/errors.py` — add near the top, after the imports:

```python
class TransferStopped(Exception):
    """Cooperative stop: the caller's should_stop() returned True mid-run.

    Not part of the error taxonomy — never passed to classify(), never
    recorded against a file. The interrupted state is identical to a crash,
    so the ordinary resume path is the only recovery path.
    """
```

`gcs/uploader.py` — add `should_stop: Callable[[], bool] | None = None` as a keyword parameter to `upload_resumable`, `upload_slice`, and `upload_sliced` (import `TransferStopped` from `core.errors`). At the top of each chunk `while` loop in `upload_resumable` and `upload_slice`:

```python
        while offset < size_bytes:          # (spec.length in upload_slice)
            if should_stop is not None and should_stop():
                raise TransferStopped(object_name)
            data = fp.read(...)
            ...
```

In `upload_sliced`: check once before the pool block (`if should_stop is not None and should_stop(): raise TransferStopped(object_name)`) and pass `should_stop=should_stop` to each `pool.submit(upload_slice, ...)` call — a slice worker raising `TransferStopped` propagates through `future.result()` exactly like any other failure.

`gcs/downloader.py` — `download_file` gains `should_stop: Callable[[], bool] | None = None` and forwards it; `_fetch_range` gains `should_stop` keyword-only beside `progress_interval_bytes`, checked per chunk:

```python
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if should_stop is not None and should_stop():
                raise TransferStopped(f"range {spec.index}")
            fp.write(chunk)
            ...
```

`engine/runner.py`:

1. `EngineOptions` gains `should_stop: Callable[[], bool] | None = None`.
2. `_process_file`'s retry loop — check before each attempt, and never let the generic handler classify a stop:

```python
        delays = iter(options.retry.delays(rng))
        for attempt in range(options.retry.max_attempts):
            if options.should_stop is not None and options.should_stop():
                raise TransferStopped(row["relative_path"])
            repo.mark_transferring(file_id)
            try:
                _transfer_once(ctx, db_path, repo, job, row, options)
                return
            except TransferStopped:
                # Not a failure: the service asked us to wind down. The file
                # stays `transferring`; the next run picks it up by state.
                raise
            except Exception as exc:
                ...existing handler unchanged...
```

3. `_transfer_once` passes `should_stop=options.should_stop` into the `upload_resumable`, `upload_sliced`, and `download_file` calls (not `upload_single_shot` — a ≤ 8 MiB file is already bounded).
4. `run_job` — a `stopped` flag mirroring `paused`:

```python
        paused = False
        stopped = False
        for _pass in range(2):  # second pass picks up files marked `changed`
            ...
            with ThreadPoolExecutor(max_workers=options.file_workers) as pool:
                futures = [...]
                for future in futures:
                    try:
                        future.result()
                    except JobPaused as exc:
                        repo.record_event(job_id, "run_paused", str(exc)[:200])
                        pool.shutdown(cancel_futures=True)
                        paused = True
                        break
                    except TransferStopped:
                        repo.record_event(job_id, "run_stopped")
                        pool.shutdown(cancel_futures=True)
                        stopped = True
                        break
            if paused or stopped:
                break

        if stopped:
            # Deliberately NOT finish_job: a stop is an interruption, not an
            # outcome. `pending` + run_stopped puts the job exactly where the
            # startup-recovery / resume path expects to find it.
            repo.set_job_status(job_id, JobStatus.PENDING)
            return JobStatus.PENDING

        if paused:
            repo.finish_job(job_id, JobStatus.PAUSED)
            repo.record_event(job_id, "run_finished", JobStatus.PAUSED.value)
            return JobStatus.PAUSED
```

(The `run_finished`-on-paused lines are Task 1's; keep them.) Import `TransferStopped` from `core.errors` in all three modules.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/engine tests/gcs -v`
Expected: PASS, including the emulator stop test if the emulator binary is present.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: cooperative stop (TransferStopped + EngineOptions.should_stop) through engine and chunk loops"
```

---

### Task 4: `SizePolicy.parse`, service configuration, and the ACL-restricted token file

Foundation the rest of the service stands on: where the data directory lives, what `settings.json` may override, and how the bearer token is created. `SizePolicy.parse` moves the CLI's policy-string parsing into `core` so `settings.json` can reuse it.

**Files:**
- Modify: `src/mml_cloud_transfer/core/slicing.py` (add `SizePolicy.parse` classmethod)
- Modify: `src/mml_cloud_transfer/cli/transfer_command.py` (`parse_size_policy` delegates to it)
- Create: `src/mml_cloud_transfer/service/__init__.py` (empty)
- Create: `src/mml_cloud_transfer/service/config.py`
- Create: `src/mml_cloud_transfer/service/security.py`
- Create: `tests/service/__init__.py` (empty)
- Test: `tests/core/test_slicing.py` (append), `tests/service/test_config.py`, `tests/service/test_security.py`

**Interfaces:**
- Consumes: existing `SizePolicy` dataclass (fields `single_shot_max`, `resumable_max`, `min_slice`, `max_components`).
- Produces: `SizePolicy.parse(text: str) -> SizePolicy` (classmethod; `"a,b,c"` integers, `max_components=32`, `ValueError` on anything else); `DEFAULT_PORT = 47821`; `default_data_dir() -> Path` (`MMLCT_DATA_DIR` env override, else `%ProgramData%\MML Cloud Transfer`); frozen `ServiceConfig` dataclass with field `data_dir: Path` and fields/defaults `host="127.0.0.1"`, `port=DEFAULT_PORT`, `auto_resume_on_startup=True`, `poll_interval=1.0`, `stall_probe_interval=60.0`, `sse_interval=0.5`, `file_workers=4`, `size_policy: SizePolicy | None = None`, plus properties `db_path`, `reports_dir`, `token_path`, `settings_path`, `base_url`; `load_config(data_dir=None, *, port=None) -> ServiceConfig` (reads `settings.json`); `ensure_token(path: Path) -> str`; `read_token(path: Path) -> str`; `restrict_acl(path: Path) -> None`. Every later task consumes `ServiceConfig`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_slicing.py` (add `import pytest` and the `SizePolicy` import if absent):

```python
def test_size_policy_parse_round_trips():
    policy = SizePolicy.parse("65536,262144,262144")
    assert policy.single_shot_max == 65536
    assert policy.resumable_max == 262144
    assert policy.min_slice == 262144
    assert policy.max_components == 32


def test_size_policy_parse_rejects_garbage():
    with pytest.raises(ValueError):
        SizePolicy.parse("1,2")
    with pytest.raises(ValueError):
        SizePolicy.parse("a,b,c")
```

Create `tests/service/test_config.py`:

```python
import json

from mml_cloud_transfer.service.config import (
    DEFAULT_PORT,
    default_data_dir,
    load_config,
)


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("MMLCT_DATA_DIR", str(tmp_path))
    assert default_data_dir() == tmp_path


def test_defaults(tmp_path):
    config = load_config(tmp_path)
    assert config.host == "127.0.0.1"
    assert config.port == DEFAULT_PORT
    assert config.db_path == tmp_path / "jobs.db"
    assert config.reports_dir == tmp_path / "reports"
    assert config.token_path == tmp_path / "api_token"
    assert config.auto_resume_on_startup is True
    assert config.size_policy is None
    assert config.base_url == f"http://127.0.0.1:{DEFAULT_PORT}"


def test_settings_json_overrides(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({
        "port": 5555,
        "auto_resume_on_startup": False,
        "file_workers": 2,
        "size_policy": "1,2,3",
        "poll_interval": 0.1,
    }), encoding="utf-8")
    config = load_config(tmp_path)
    assert config.port == 5555
    assert config.auto_resume_on_startup is False
    assert config.file_workers == 2
    assert config.size_policy.min_slice == 3
    assert config.poll_interval == 0.1


def test_port_argument_beats_settings(tmp_path):
    (tmp_path / "settings.json").write_text('{"port": 5555}', encoding="utf-8")
    assert load_config(tmp_path, port=6666).port == 6666
```

Create `tests/service/test_security.py`:

```python
import subprocess
import sys

import pytest

from mml_cloud_transfer.service.security import ensure_token, read_token


def test_ensure_token_is_stable_and_round_trips(tmp_path):
    path = tmp_path / "deep" / "api_token"
    first = ensure_token(path)
    second = ensure_token(path)
    assert first == second == read_token(path)
    assert len(first) >= 32


def test_read_token_rejects_empty_file(tmp_path):
    path = tmp_path / "api_token"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        read_token(path)


@pytest.mark.skipif(sys.platform != "win32", reason="ACLs are Windows-only")
def test_token_file_acl_drops_inheritance(tmp_path):
    path = tmp_path / "api_token"
    ensure_token(path)
    out = subprocess.run(
        ["icacls", str(path)], capture_output=True, text=True, check=True
    ).stdout
    assert "(I)" not in out          # no inherited ACEs survive
    assert "SYSTEM" in out or "S-1-5-18" in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/core/test_slicing.py tests/service -v`
Expected: FAIL — `SizePolicy` has no `parse`; `mml_cloud_transfer.service` does not exist.

- [ ] **Step 3: Implement**

`core/slicing.py` — inside the `SizePolicy` dataclass:

```python
    @classmethod
    def parse(cls, text: str) -> "SizePolicy":
        """Parse 'single_shot_max,resumable_max,min_slice' (bytes, integers)."""
        parts = text.split(",")
        if len(parts) != 3:
            raise ValueError(
                "size policy must be 'single_shot_max,resumable_max,min_slice'"
            )
        single, resumable, min_slice = (int(p) for p in parts)
        return cls(
            single_shot_max=single, resumable_max=resumable,
            min_slice=min_slice, max_components=32,
        )
```

`cli/transfer_command.py` — `parse_size_policy` becomes a delegating one-liner (kept because tests and `--size-policy` wiring import it by name):

```python
def parse_size_policy(text: str) -> SizePolicy:
    return SizePolicy.parse(text)
```

Create `service/__init__.py` (empty) and `service/config.py`:

```python
"""Service configuration: the data directory layout and settings.json.

Everything the host, worker, and API must agree on lives here. The data
directory defaults to %ProgramData%\\MML Cloud Transfer; tests and console
runs point MMLCT_DATA_DIR (or --data-dir) at a temp directory instead.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

from mml_cloud_transfer.core.slicing import SizePolicy

DEFAULT_PORT = 47821


def default_data_dir() -> Path:
    env = os.environ.get("MMLCT_DATA_DIR")
    if env:
        return Path(env)
    return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "MML Cloud Transfer"


@dataclass(frozen=True)
class ServiceConfig:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    auto_resume_on_startup: bool = True
    poll_interval: float = 1.0
    stall_probe_interval: float = 60.0
    sse_interval: float = 0.5
    file_workers: int = 4
    size_policy: SizePolicy | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobs.db"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def token_path(self) -> Path:
        return self.data_dir / "api_token"

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


_SETTINGS_KEYS = {
    "host": str,
    "port": int,
    "auto_resume_on_startup": bool,
    "poll_interval": float,
    "stall_probe_interval": float,
    "sse_interval": float,
    "file_workers": int,
}


def load_config(
    data_dir: str | os.PathLike[str] | None = None, *, port: int | None = None
) -> ServiceConfig:
    root = Path(data_dir) if data_dir is not None else default_data_dir()
    config = ServiceConfig(data_dir=root)
    settings_file = config.settings_path
    if settings_file.exists():
        raw = json.loads(settings_file.read_text(encoding="utf-8"))
        updates = {
            key: cast(raw[key]) for key, cast in _SETTINGS_KEYS.items() if key in raw
        }
        if raw.get("size_policy"):
            updates["size_policy"] = SizePolicy.parse(raw["size_policy"])
        config = replace(config, **updates)
    if port is not None:
        config = replace(config, port=port)
    return config
```

Create `service/security.py`:

```python
"""The API bearer token: a file under the data directory, ACL-restricted.

Localhost is not access control on a multi-user machine (spec). The ACL is
cut to SYSTEM, Administrators, and the account that created the file — the
account the service runs as. Which additional principal the GUI's user gets
is an installer decision (Phase 6), not made here.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path

_SYSTEM_SID = "*S-1-5-18"
_ADMINISTRATORS_SID = "*S-1-5-32-544"


def _current_account() -> str:
    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME", "")
    return f"{domain}\\{user}" if domain else user


def restrict_acl(path: Path) -> None:
    """Drop inherited ACEs; grant only SYSTEM, Administrators, this account."""
    if sys.platform != "win32":
        return  # ACLs are Windows-only; POSIX dev runs skip this
    subprocess.run(
        [
            "icacls", str(path), "/inheritance:r",
            "/grant:r", f"{_SYSTEM_SID}:(F)",
            "/grant:r", f"{_ADMINISTRATORS_SID}:(F)",
            "/grant:r", f"{_current_account()}:(F)",
        ],
        check=True, capture_output=True, text=True,
    )


def ensure_token(path: Path) -> str:
    """Create (if missing) and return the API bearer token.

    The empty file is ACL-restricted *before* the secret is written, so the
    token bytes never exist under a permissive ACL.
    """
    if path.exists():
        return read_token(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    restrict_acl(path)
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    return token


def read_token(path: Path) -> str:
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError(f"token file is empty: {path}")
    return token
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/core/test_slicing.py tests/service tests/cli -v`
Expected: PASS (CLI size-policy tests still green through the delegation).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: SizePolicy.parse, service config, and ACL-restricted bearer-token file"
```

---

### Task 5: Repository queries for the queue, profiles, paging, and events

Everything the service layer asks SQLite that Plan 2 did not. Profiles reuse the existing `profiles` table (Phase 4 will replace ad-hoc rows with real managed profiles; the schema does not change). The scheduler *is* `next_eligible_job`: a job whose `scheduled_start_at` has passed is simply eligible — which is also, with zero extra code, the "missed windows run at next service start" rule.

**Files:**
- Modify: `src/mml_cloud_transfer/store/repository.py`
- Test: `tests/store/test_repository_service.py` (create)

**Interfaces:**
- Consumes: existing `create_job`, `_now()` timestamp format.
- Produces (all on `JobRepository`): `list_jobs() -> list[Row]`; `jobs_with_status(status: JobStatus) -> list[Row]`; `next_eligible_job(now: str) -> Row | None`; `get_files_page(job_id, *, state: str | None = None, limit: int = 500, offset: int = 0) -> list[Row]`; `events_after(job_id: int, after_id: int = 0) -> list[Row]`; `count_failures(job_id: int, category: ErrorCategory) -> int`; `create_profile(*, name, bucket, auth_type, credential_ref=None, project_id="", default_prefix="") -> int`; `get_profile(profile_id: int) -> Row` (LookupError if absent); `find_profile(*, bucket, auth_type, credential_ref) -> Row | None` (NULL-safe on `credential_ref`); `get_or_create_profile(*, bucket, auth_type, credential_ref=None) -> int`. `auth_type` values used in Phase 3: `"adc"`, `"key_file"`, `"emulator"` (with `credential_ref` = key path or emulator endpoint respectively).

- [ ] **Step 1: Write the failing tests**

Create `tests/store/test_repository_service.py`:

```python
"""Queue, profile, paging, and event-cursor queries the service layer runs."""

import pytest

from mml_cloud_transfer.core.errors import ErrorCategory
from mml_cloud_transfer.core.models import Direction, FileState, JobStatus, PlannedFile
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


@pytest.fixture
def repo(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    yield JobRepository(conn)
    conn.close()


def _job(repo, *, scheduled=None, status=None):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root="s",
        dest_prefix="", scheduled_start_at=scheduled,
    )
    if status is not None:
        repo.set_job_status(job_id, status)
    return job_id


def test_next_eligible_is_fifo_by_id(repo):
    first = _job(repo)
    _job(repo)
    assert repo.next_eligible_job("2026-08-05T12:00:00+00:00")["id"] == first


def test_scheduled_jobs_wait_for_their_time(repo):
    job_id = _job(repo, scheduled="2026-08-05T22:00:00+00:00")
    assert repo.next_eligible_job("2026-08-05T21:59:59+00:00") is None
    assert repo.next_eligible_job("2026-08-05T22:00:00+00:00")["id"] == job_id


def test_missed_windows_are_eligible_not_skipped(repo):
    # The service was down at the scheduled time; at next start "now" is
    # simply later, so the job runs instead of silently disappearing.
    job_id = _job(repo, scheduled="2026-08-01T03:00:00+00:00")
    assert repo.next_eligible_job("2026-08-05T09:00:00+00:00")["id"] == job_id


def test_non_pending_jobs_are_not_picked(repo):
    _job(repo, status=JobStatus.PAUSED)
    _job(repo, status=JobStatus.COMPLETE)
    _job(repo, status=JobStatus.CANCELLED)
    assert repo.next_eligible_job("2026-08-05T12:00:00+00:00") is None


def test_jobs_with_status_and_list_jobs(repo):
    running = _job(repo, status=JobStatus.RUNNING)
    _job(repo)
    assert [j["id"] for j in repo.jobs_with_status(JobStatus.RUNNING)] == [running]
    assert len(repo.list_jobs()) == 2


def test_get_or_create_profile_is_null_safe_on_credential_ref(repo):
    a = repo.get_or_create_profile(bucket="b", auth_type="adc", credential_ref=None)
    b = repo.get_or_create_profile(bucket="b", auth_type="adc", credential_ref=None)
    c = repo.get_or_create_profile(
        bucket="b", auth_type="key_file", credential_ref="k.json"
    )
    assert a == b
    assert a != c
    assert repo.get_profile(c)["credential_ref"] == "k.json"
    with pytest.raises(LookupError):
        repo.get_profile(999)


def test_files_page_state_filter_and_failure_counts(repo):
    job_id = _job(repo)
    repo.add_planned_files(
        job_id, [PlannedFile(f"f{i:02d}", "s", 1, 1) for i in range(7)]
    )
    page = repo.get_files_page(job_id, limit=3, offset=3)
    assert [r["relative_path"] for r in page] == ["f03", "f04", "f05"]
    first = repo.get_files(job_id)[0]["id"]
    repo.mark_failed(first, ErrorCategory.NETWORK, "boom")
    failed = repo.get_files_page(job_id, state=FileState.FAILED.value, limit=10)
    assert [r["id"] for r in failed] == [first]
    assert repo.count_failures(job_id, ErrorCategory.NETWORK) == 1
    assert repo.count_failures(job_id, ErrorCategory.QUOTA) == 0


def test_events_after_cursor(repo):
    job_id = _job(repo)
    repo.record_event(job_id, "one")
    repo.record_event(job_id, "two")
    all_events = repo.events_after(job_id, 0)
    assert [e["kind"] for e in all_events] == ["one", "two"]
    after = repo.events_after(job_id, all_events[0]["id"])
    assert [e["kind"] for e in after] == ["two"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/store/test_repository_service.py -v`
Expected: FAIL — none of the methods exist yet.

- [ ] **Step 3: Implement**

Add to `store/repository.py` (jobs section):

```python
    def list_jobs(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()

    def jobs_with_status(self, status: JobStatus) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY id", (status.value,)
        ).fetchall()

    def next_eligible_job(self, now: str) -> sqlite3.Row | None:
        """FIFO by id among pending jobs whose scheduled start has passed.

        String comparison is correct only because both sides use the
        _now() format (UTC ISO-8601, seconds, +00:00). The API layer owns
        normalizing user-supplied schedules into that format. A missed
        window needs no special case: its schedule is simply <= now.
        """
        return self._conn.execute(
            "SELECT * FROM jobs WHERE status = ?"
            " AND (scheduled_start_at IS NULL OR scheduled_start_at <= ?)"
            " ORDER BY id LIMIT 1",
            (JobStatus.PENDING.value, now),
        ).fetchone()
```

Paging/events/failure counts (reporting section):

```python
    def get_files_page(
        self, job_id: int, *, state: str | None = None,
        limit: int = 500, offset: int = 0,
    ) -> list[sqlite3.Row]:
        if state is None:
            return self._conn.execute(
                "SELECT * FROM job_files WHERE job_id = ?"
                " ORDER BY id LIMIT ? OFFSET ?",
                (job_id, limit, offset),
            ).fetchall()
        return self._conn.execute(
            "SELECT * FROM job_files WHERE job_id = ? AND state = ?"
            " ORDER BY id LIMIT ? OFFSET ?",
            (job_id, state, limit, offset),
        ).fetchall()

    def events_after(self, job_id: int, after_id: int = 0) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM events WHERE job_id = ? AND id > ? ORDER BY id",
            (job_id, after_id),
        ).fetchall()

    def count_failures(self, job_id: int, category: ErrorCategory) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) AS n FROM job_files WHERE job_id = ?"
            " AND state = ? AND error_category = ?",
            (job_id, FileState.FAILED.value, category.value),
        ).fetchone()["n"]
```

Profiles (new section):

```python
    # ---- profiles -------------------------------------------------------

    def create_profile(
        self, *, name: str, bucket: str, auth_type: str,
        credential_ref: str | None = None, project_id: str = "",
        default_prefix: str = "",
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO profiles (name, project_id, bucket, auth_type,"
            " credential_ref, default_prefix, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, project_id, bucket, auth_type, credential_ref,
             default_prefix, _now()),
        )
        return int(cursor.lastrowid)

    def get_profile(self, profile_id: int) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"no profile with id {profile_id}")
        return row

    def find_profile(
        self, *, bucket: str, auth_type: str, credential_ref: str | None
    ) -> sqlite3.Row | None:
        # "IS ?" is SQLite's NULL-safe equality, so an ADC profile
        # (credential_ref NULL) is found rather than duplicated.
        return self._conn.execute(
            "SELECT * FROM profiles WHERE bucket = ? AND auth_type = ?"
            " AND credential_ref IS ? ORDER BY id LIMIT 1",
            (bucket, auth_type, credential_ref),
        ).fetchone()

    def get_or_create_profile(
        self, *, bucket: str, auth_type: str, credential_ref: str | None = None
    ) -> int:
        row = self.find_profile(
            bucket=bucket, auth_type=auth_type, credential_ref=credential_ref
        )
        if row is not None:
            return int(row["id"])
        count = self._conn.execute(
            "SELECT COUNT(*) AS n FROM profiles"
        ).fetchone()["n"]
        return self.create_profile(
            name=f"{bucket} [{auth_type} {count + 1}]",  # profiles.name is UNIQUE
            bucket=bucket, auth_type=auth_type, credential_ref=credential_ref,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/store -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: repository queries for job queue, profiles, file paging, and event cursors"
```

---

### Task 6: `JobController` and the FastAPI REST app

The API process-internal handshake plus every REST route. The controller is the one piece of shared mutable state between the API threads and the worker thread: which job is active, and whether a pause/cancel has been requested for it. REST handlers are synchronous, open a SQLite connection per request, and never touch GCS — submission validates only what the service itself can check instantly (local paths), so `POST /jobs` returns in milliseconds.

**Files:**
- Create: `src/mml_cloud_transfer/service/controller.py`
- Create: `src/mml_cloud_transfer/service/app.py`
- Modify: `pyproject.toml` (add `fastapi`, `uvicorn` to dependencies; `httpx` to dev)
- Test: `tests/service/test_controller.py`, `tests/service/test_api.py`

**Interfaces:**
- Consumes: `ServiceConfig`, `ensure_token`, `JobRepository` (Tasks 4–5), `job_progress` (Task 2), `write_report` from `engine.report`.
- Produces: `JobController` with `job_started(job_id) -> threading.Event` (arms a fresh stop event, clears intent), `job_finished() -> str | None` (returns and clears the intent: `"pause"`, `"cancel"`, or None), `request(job_id, intent) -> bool` (False unless job_id is the active job; sets the stop event), `active_job_id` property, and `service_stop: threading.Event`; `create_app(config: ServiceConfig, controller: JobController) -> FastAPI` serving: `GET /health` (open), and token-guarded `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/files`, `GET /jobs/{id}/events`, `POST /jobs/{id}/pause`, `POST /jobs/{id}/resume`, `POST /jobs/{id}/cancel`, `POST /jobs/{id}/report`. `POST /jobs` accepts `{name, direction, source_root, dest_prefix?, bucket, credentials_path?, emulator_endpoint?, audit_hash?, scheduled_start_at?}` and returns `201 {"job_id": N, "scheduled_start_at": normalized|null}`. Tasks 7–12 consume all of this.

- [ ] **Step 1: Write the failing tests**

Create `tests/service/test_controller.py`:

```python
from mml_cloud_transfer.service.controller import JobController


def test_request_only_reaches_the_active_job():
    controller = JobController()
    assert controller.request(1, "pause") is False   # nothing active
    stop = controller.job_started(1)
    assert controller.active_job_id == 1
    assert controller.request(2, "pause") is False   # wrong job
    assert not stop.is_set()
    assert controller.request(1, "cancel") is True
    assert stop.is_set()
    assert controller.job_finished() == "cancel"
    assert controller.active_job_id is None
    assert controller.job_finished() is None         # intent consumed


def test_each_job_gets_a_fresh_stop_event():
    controller = JobController()
    first = controller.job_started(1)
    controller.request(1, "pause")
    controller.job_finished()
    second = controller.job_started(2)
    assert first.is_set()
    assert not second.is_set()
```

Create `tests/service/test_api.py`:

```python
"""REST surface. No worker thread: lifecycle actions on a *running* job are
tested by arming the controller directly, exactly as the worker does."""

import pytest
from fastapi.testclient import TestClient

from mml_cloud_transfer.core.models import JobStatus
from mml_cloud_transfer.service.app import create_app
from mml_cloud_transfer.service.config import load_config
from mml_cloud_transfer.service.controller import JobController
from mml_cloud_transfer.service.security import read_token
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


@pytest.fixture
def api(tmp_path):
    config = load_config(tmp_path / "data")
    controller = JobController()
    app = create_app(config, controller)
    client = TestClient(app)
    client.headers.update(
        {"Authorization": f"Bearer {read_token(config.token_path)}"}
    )
    return client, config, controller


def _submit(client, tmp_path, **overrides):
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    payload = {
        "name": "j", "direction": "upload", "source_root": str(src),
        "dest_prefix": "p", "bucket": "b",
    }
    payload.update(overrides)
    response = client.post("/jobs", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["job_id"]


def test_routes_require_the_bearer_token(api):
    client, config, _ = api
    bare = TestClient(client.app)
    assert bare.get("/health").status_code == 200          # health is open
    assert bare.get("/jobs").status_code == 401
    assert bare.post("/jobs", json={}).status_code == 401
    wrong = TestClient(client.app)
    wrong.headers.update({"Authorization": "Bearer nope"})
    assert wrong.get("/jobs").status_code == 401


def test_submit_upload_validates_the_source_folder(api, tmp_path):
    client, _, _ = api
    response = client.post("/jobs", json={
        "name": "j", "direction": "upload",
        "source_root": str(tmp_path / "missing"), "bucket": "b",
    })
    assert response.status_code == 400
    assert "not found" in response.json()["detail"]


def test_submit_creates_job_and_profile(api, tmp_path):
    client, config, _ = api
    job_id = _submit(client, tmp_path, emulator_endpoint="http://127.0.0.1:1")
    conn = connect(config.db_path)
    repo = JobRepository(conn)
    job = repo.get_job(job_id)
    profile = repo.get_profile(job["profile_id"])
    conn.close()
    assert job["status"] == JobStatus.PENDING.value
    assert profile["auth_type"] == "emulator"
    assert profile["credential_ref"] == "http://127.0.0.1:1"
    detail = client.get(f"/jobs/{job_id}").json()
    assert detail["status"] == "pending"
    assert detail["progress"]["files_total"] == 0
    assert client.get("/jobs").json()[0]["id"] == job_id


def test_schedule_is_normalized_to_utc_seconds(api, tmp_path):
    client, _, _ = api
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    response = client.post("/jobs", json={
        "name": "j", "direction": "upload", "source_root": str(src),
        "bucket": "b", "scheduled_start_at": "2027-01-01T09:30:00+02:00",
    })
    assert response.status_code == 201
    assert response.json()["scheduled_start_at"] == "2027-01-01T07:30:00+00:00"
    bad = client.post("/jobs", json={
        "name": "j", "direction": "upload", "source_root": str(src),
        "bucket": "b", "scheduled_start_at": "tonight",
    })
    assert bad.status_code == 422


def test_pause_resume_cancel_on_queued_jobs(api, tmp_path):
    client, _, _ = api
    job_id = _submit(client, tmp_path)
    assert client.post(f"/jobs/{job_id}/pause").json()["status"] == "paused"
    assert client.post(f"/jobs/{job_id}/resume").json()["status"] == "pending"
    assert client.post(f"/jobs/{job_id}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/jobs/{job_id}/resume").json()["status"] == "pending"
    assert client.post(f"/jobs/{job_id}/resume").status_code == 409
    assert client.post("/jobs/999/pause").status_code == 404


def test_lifecycle_on_a_running_job_goes_through_the_controller(api, tmp_path):
    client, config, controller = api
    job_id = _submit(client, tmp_path)
    conn = connect(config.db_path)
    JobRepository(conn).set_job_status(job_id, JobStatus.RUNNING)
    conn.close()
    stop = controller.job_started(job_id)
    assert client.post(f"/jobs/{job_id}/pause").json()["status"] == "stopping"
    assert stop.is_set()
    assert controller.job_finished() == "pause"


def test_report_endpoint_writes_the_three_files(api, tmp_path):
    client, _, _ = api
    job_id = _submit(client, tmp_path)
    paths = client.post(f"/jobs/{job_id}/report").json()
    from pathlib import Path
    assert Path(paths["report_html"]).exists()
    assert Path(paths["summary_json"]).exists()
    assert Path(paths["manifest_csv"]).exists()
```

- [ ] **Step 2: Install new dependencies, run the tests to verify they fail**

Add to `pyproject.toml` `dependencies`: `"fastapi>=0.111"`, `"uvicorn>=0.30"`. Add to `dev`: `"httpx>=0.27"`. Then:

Run: `.venv/Scripts/python -m pip install -e .[dev]` and `.venv/Scripts/python -m pytest tests/service/test_controller.py tests/service/test_api.py -v`
Expected: FAIL — `controller`/`app` modules do not exist.

- [ ] **Step 3: Implement**

Create `service/controller.py`:

```python
"""Shared state between the API (which receives pause/cancel requests) and
the worker (which owns the running job). One job runs at a time, so one
active id, one stop event, one pending intent."""

from __future__ import annotations

import threading


class JobController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_job_id: int | None = None
        self._intent: str | None = None
        self._stop_event = threading.Event()
        self.service_stop = threading.Event()

    def job_started(self, job_id: int) -> threading.Event:
        """Arm a fresh stop event for this job; returns it for the run."""
        with self._lock:
            self._active_job_id = job_id
            self._intent = None
            self._stop_event = threading.Event()
            return self._stop_event

    def job_finished(self) -> str | None:
        """Clear the active job; return the pending intent, if any."""
        with self._lock:
            intent = self._intent
            self._intent = None
            self._active_job_id = None
            return intent

    def request(self, job_id: int, intent: str) -> bool:
        """Ask the active job to stop with this intent. False if not active."""
        with self._lock:
            if self._active_job_id != job_id:
                return False
            self._intent = intent
            self._stop_event.set()
            return True

    @property
    def active_job_id(self) -> int | None:
        with self._lock:
            return self._active_job_id
```

Create `service/app.py`:

```python
"""The service REST API. Binds only through the host (127.0.0.1); every
route except /health requires the bearer token from the ACL-restricted
token file. Handlers are synchronous (FastAPI runs them on a threadpool)
and open a fresh SQLite connection per request — connections are
thread-bound and requests land on arbitrary pool threads."""

from __future__ import annotations

import importlib.metadata
import os
import secrets
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from mml_cloud_transfer.core.models import Direction, JobStatus
from mml_cloud_transfer.engine.report import write_report
from mml_cloud_transfer.service.config import ServiceConfig
from mml_cloud_transfer.service.controller import JobController
from mml_cloud_transfer.service.security import ensure_token
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

try:
    VERSION = importlib.metadata.version("mml-cloud-transfer")
except importlib.metadata.PackageNotFoundError:  # frozen/odd environments
    VERSION = "0"


class JobSubmission(BaseModel):
    name: str = Field(min_length=1)
    direction: Literal["upload", "download"]
    source_root: str = Field(min_length=1)
    dest_prefix: str = ""
    bucket: str = Field(min_length=1)
    credentials_path: str | None = None
    emulator_endpoint: str | None = None
    audit_hash: bool = False
    scheduled_start_at: str | None = None


def _normalize_schedule(text: str) -> str:
    """To the exact _now() format so SQL string comparison is ordering."""
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"scheduled_start_at is not ISO-8601: {text!r}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()  # naive input means local wall-clock time
    return parsed.astimezone(UTC).isoformat(timespec="seconds")


def _require_token(request: Request) -> None:
    header = request.headers.get("authorization", "")
    token = request.app.state.token
    if not header.startswith("Bearer ") or not secrets.compare_digest(
        header[len("Bearer "):], token
    ):
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")


def _row_dict(row) -> dict:
    return {key: row[key] for key in row.keys()}


def create_app(config: ServiceConfig, controller: JobController) -> FastAPI:
    app = FastAPI(title="MML Cloud Transfer", version=VERSION)
    app.state.config = config
    app.state.controller = controller
    app.state.token = ensure_token(config.token_path)

    router = APIRouter(dependencies=[Depends(_require_token)])

    def _open():
        conn = connect(config.db_path)
        return conn, JobRepository(conn)

    def _job_or_404(repo: JobRepository, job_id: int):
        try:
            return repo.get_job(job_id)
        except LookupError:
            raise HTTPException(
                status_code=404, detail=f"no job with id {job_id}"
            ) from None

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": VERSION,
            "active_job_id": controller.active_job_id,
        }

    @router.post("/jobs", status_code=201)
    def submit_job(submission: JobSubmission) -> dict:
        if submission.direction == "upload":
            if not os.path.isdir(submission.source_root):
                raise HTTPException(status_code=400, detail=(
                    "source folder not found or not readable by the service"
                    f" account: {submission.source_root}"
                ))
        else:
            # Spec: reachability is tested at creation, under the service's
            # own identity. Creating the destination folder IS that test.
            try:
                os.makedirs(submission.source_root, exist_ok=True)
            except OSError as exc:
                raise HTTPException(status_code=400, detail=(
                    f"destination folder cannot be created by the service: {exc}"
                )) from exc

        scheduled = (
            _normalize_schedule(submission.scheduled_start_at)
            if submission.scheduled_start_at else None
        )
        if submission.emulator_endpoint:
            auth_type, credential_ref = "emulator", submission.emulator_endpoint
        elif submission.credentials_path:
            auth_type, credential_ref = "key_file", submission.credentials_path
        else:
            auth_type, credential_ref = "adc", None

        conn, repo = _open()
        try:
            profile_id = repo.get_or_create_profile(
                bucket=submission.bucket, auth_type=auth_type,
                credential_ref=credential_ref,
            )
            job_id = repo.create_job(
                name=submission.name,
                direction=Direction(submission.direction),
                source_root=submission.source_root,
                dest_prefix=submission.dest_prefix,
                profile_id=profile_id,
                audit_hash=submission.audit_hash,
                scheduled_start_at=scheduled,
            )
            repo.record_event(
                job_id, "job_submitted", f"direction={submission.direction}"
            )
        finally:
            conn.close()
        return {"job_id": job_id, "scheduled_start_at": scheduled}

    @router.get("/jobs")
    def list_jobs() -> list[dict]:
        conn, repo = _open()
        try:
            return [_row_dict(row) for row in repo.list_jobs()]
        finally:
            conn.close()

    @router.get("/jobs/{job_id}")
    def get_job(job_id: int) -> dict:
        conn, repo = _open()
        try:
            job = _job_or_404(repo, job_id)
            return {**_row_dict(job), "progress": asdict(repo.job_progress(job_id))}
        finally:
            conn.close()

    @router.get("/jobs/{job_id}/files")
    def get_files(
        job_id: int, state: str | None = None, limit: int = 500, offset: int = 0
    ) -> list[dict]:
        limit = max(1, min(limit, 5000))
        conn, repo = _open()
        try:
            _job_or_404(repo, job_id)
            return [
                _row_dict(r)
                for r in repo.get_files_page(
                    job_id, state=state, limit=limit, offset=offset
                )
            ]
        finally:
            conn.close()

    @router.get("/jobs/{job_id}/events")
    def get_events(job_id: int, after_id: int = 0) -> list[dict]:
        conn, repo = _open()
        try:
            _job_or_404(repo, job_id)
            return [_row_dict(e) for e in repo.events_after(job_id, after_id)]
        finally:
            conn.close()

    @router.post("/jobs/{job_id}/pause")
    def pause_job(job_id: int) -> dict:
        conn, repo = _open()
        try:
            job = _job_or_404(repo, job_id)
            status = job["status"]
            if status == JobStatus.RUNNING.value:
                if controller.request(job_id, "pause"):
                    return {"status": "stopping"}
                raise HTTPException(status_code=409, detail=(
                    "job is marked running but nothing is active;"
                    " restart the service to recover it"
                ))
            if status == JobStatus.STALLED.value:
                # Mid-stall the worker still owns the job; otherwise flip
                # the row directly and the stall loop notices and exits.
                if controller.request(job_id, "pause"):
                    return {"status": "stopping"}
                repo.set_job_status(job_id, JobStatus.PAUSED)
                repo.record_event(job_id, "paused_by_user")
                return {"status": JobStatus.PAUSED.value}
            if status == JobStatus.PENDING.value:
                repo.set_job_status(job_id, JobStatus.PAUSED)
                repo.record_event(job_id, "paused_by_user")
                return {"status": JobStatus.PAUSED.value}
            raise HTTPException(
                status_code=409, detail=f"cannot pause a {status} job"
            )
        finally:
            conn.close()

    @router.post("/jobs/{job_id}/resume")
    def resume_job(job_id: int) -> dict:
        resumable = {
            JobStatus.PAUSED.value, JobStatus.STALLED.value,
            JobStatus.INCOMPLETE.value, JobStatus.CANCELLED.value,
        }
        conn, repo = _open()
        try:
            job = _job_or_404(repo, job_id)
            if job["status"] not in resumable:
                raise HTTPException(
                    status_code=409, detail=f"cannot resume a {job['status']} job"
                )
            repo.set_job_status(job_id, JobStatus.PENDING)
            repo.record_event(job_id, "resumed_by_user")
            return {"status": JobStatus.PENDING.value}
        finally:
            conn.close()

    @router.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: int) -> dict:
        conn, repo = _open()
        try:
            job = _job_or_404(repo, job_id)
            status = job["status"]
            if status == JobStatus.RUNNING.value:
                if controller.request(job_id, "cancel"):
                    return {"status": "stopping"}
                raise HTTPException(status_code=409, detail=(
                    "job is marked running but nothing is active;"
                    " restart the service to recover it"
                ))
            if status == JobStatus.STALLED.value and controller.request(
                job_id, "cancel"
            ):
                return {"status": "stopping"}
            if status in (
                JobStatus.PENDING.value, JobStatus.PAUSED.value,
                JobStatus.STALLED.value,
            ):
                repo.set_job_status(job_id, JobStatus.CANCELLED)
                repo.record_event(job_id, "cancelled_by_user")
                return {"status": JobStatus.CANCELLED.value}
            raise HTTPException(
                status_code=409, detail=f"cannot cancel a {status} job"
            )
        finally:
            conn.close()

    @router.post("/jobs/{job_id}/report")
    def make_report(job_id: int) -> dict:
        conn, repo = _open()
        try:
            job = _job_or_404(repo, job_id)
            bucket = None
            if job["profile_id"] is not None:
                bucket = repo.get_profile(job["profile_id"])["bucket"]
        finally:
            conn.close()
        paths = write_report(
            config.db_path, job_id,
            config.reports_dir / f"job-{job_id}", bucket=bucket,
        )
        return {
            "summary_json": str(paths.summary_json),
            "manifest_csv": str(paths.manifest_csv),
            "report_html": str(paths.report_html),
        }

    app.include_router(router)
    return app
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/service -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: JobController and FastAPI app with bearer-token auth and job lifecycle routes"
```

---

### Task 7: SSE progress streaming

`GET /jobs/{id}/stream` emits one `progress` event per tick (default every 0.5 s — the spec's "roughly twice per second"), each carrying the `job_progress` snapshot plus events appended since the previous tick. The stream ends by itself after the tick that shows a terminal status (`complete`, `incomplete`, `paused`, `cancelled` — **not** `stalled`, so a watching client sees a stall recover). The generator is framework-free and tested directly; the route is a thin `StreamingResponse` wrapper.

**Files:**
- Create: `src/mml_cloud_transfer/service/sse.py`
- Modify: `src/mml_cloud_transfer/service/app.py` (add the route)
- Test: `tests/service/test_sse.py`

**Interfaces:**
- Consumes: `JobRepository.job_progress` (Task 2), `events_after` (Task 5).
- Produces: `TERMINAL_STREAM_STATUSES: frozenset[str]`; `snapshot(repo, job_id, after_event_id) -> dict` (keys `job_id`, `status`, `progress` (asdict of `JobProgress`), `events` (list of `{id, at, kind, detail}`)); `format_sse(data: dict) -> str` (`"event: progress\ndata: <json>\n\n"`); `progress_events(db_path, job_id, *, interval=0.5, max_ticks=None, sleep=time.sleep) -> Iterator[str]`. Task 11's CLI `--service-url` watch loop parses exactly this shape.

- [ ] **Step 1: Write the failing tests**

Create `tests/service/test_sse.py`:

```python
"""The generator is tested directly (injected sleep, capped ticks); one
endpoint test proves the HTTP wrapper streams and terminates."""

import json

import pytest
from fastapi.testclient import TestClient

from mml_cloud_transfer.core.models import Direction, JobStatus
from mml_cloud_transfer.service.app import create_app
from mml_cloud_transfer.service.config import load_config
from mml_cloud_transfer.service.controller import JobController
from mml_cloud_transfer.service.security import read_token
from mml_cloud_transfer.service.sse import format_sse, progress_events
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


@pytest.fixture
def db_with_job(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    repo = JobRepository(conn)
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root="s", dest_prefix=""
    )
    repo.record_event(job_id, "job_submitted")
    yield db, conn, repo, job_id
    conn.close()


def _parse(event_text):
    lines = event_text.strip().splitlines()
    assert lines[0] == "event: progress"
    return json.loads(lines[1][len("data: "):])


def test_stream_ticks_then_terminates_on_terminal_status(db_with_job):
    db, conn, repo, job_id = db_with_job
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        if len(slept) == 2:                      # finish during the 2nd wait
            repo.set_job_status(job_id, JobStatus.COMPLETE)

    events = [
        _parse(e) for e in progress_events(db, job_id, interval=0.5, sleep=sleep)
    ]
    assert events[0]["status"] == "pending"
    assert events[-1]["status"] == "complete"    # final tick emitted, then closed
    assert all(s == 0.5 for s in slept)


def test_events_are_cursored_not_repeated(db_with_job):
    db, conn, repo, job_id = db_with_job

    def sleep(seconds):
        repo.record_event(job_id, "tick")
        if sleep.calls == 1:
            repo.set_job_status(job_id, JobStatus.CANCELLED)
        sleep.calls += 1

    sleep.calls = 0
    ticks = [
        _parse(e) for e in progress_events(db, job_id, interval=0.5, sleep=sleep)
    ]
    seen = [event["id"] for tick in ticks for event in tick["events"]]
    assert seen == sorted(set(seen)), "an event id was repeated across ticks"
    assert ticks[0]["events"][0]["kind"] == "job_submitted"


def test_max_ticks_caps_a_never_ending_job(db_with_job):
    db, conn, repo, job_id = db_with_job
    ticks = list(progress_events(db, job_id, interval=0.0, max_ticks=3, sleep=lambda s: None))
    assert len(ticks) == 3


def test_stream_endpoint_serves_one_terminal_event(tmp_path):
    config = load_config(tmp_path / "data")
    app = create_app(config, JobController())
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {read_token(config.token_path)}"})

    conn = connect(config.db_path)
    repo = JobRepository(conn)
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root="s", dest_prefix=""
    )
    repo.set_job_status(job_id, JobStatus.COMPLETE)
    conn.close()

    assert client.get("/jobs/999/stream").status_code == 404
    with client.stream("GET", f"/jobs/{job_id}/stream") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())     # terminates: job is terminal
    assert "event: progress" in body
    assert '"status": "complete"' in body or '"status":"complete"' in body
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/service/test_sse.py -v`
Expected: FAIL — `service.sse` does not exist; the stream route 404s differently (route missing).

- [ ] **Step 3: Implement**

Create `service/sse.py`:

```python
"""Server-Sent Events: one progress snapshot per tick, ~2/second.

Ticks continue while the client stays connected and the job is live; the
tick that shows a terminal status is emitted and then the stream closes.
`stalled` is deliberately NOT terminal so a watcher sees recovery happen.
The generator owns one read-only SQLite connection for its lifetime (it
runs on a single thread) and closes it on the way out — including when
the client disconnects and the generator is garbage-collected mid-yield.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict

from mml_cloud_transfer.core.models import JobStatus
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

TERMINAL_STREAM_STATUSES = frozenset({
    JobStatus.COMPLETE.value,
    JobStatus.INCOMPLETE.value,
    JobStatus.PAUSED.value,
    JobStatus.CANCELLED.value,
})


def snapshot(repo: JobRepository, job_id: int, after_event_id: int) -> dict:
    job = repo.get_job(job_id)
    return {
        "job_id": job_id,
        "status": job["status"],
        "progress": asdict(repo.job_progress(job_id)),
        "events": [
            {"id": e["id"], "at": e["at"], "kind": e["kind"], "detail": e["detail"]}
            for e in repo.events_after(job_id, after_event_id)
        ],
    }


def format_sse(data: dict) -> str:
    return f"event: progress\ndata: {json.dumps(data)}\n\n"


def progress_events(
    db_path,
    job_id: int,
    *,
    interval: float = 0.5,
    max_ticks: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[str]:
    conn = connect(db_path)
    try:
        repo = JobRepository(conn)
        last_event_id = 0
        ticks = 0
        while True:
            data = snapshot(repo, job_id, last_event_id)
            if data["events"]:
                last_event_id = data["events"][-1]["id"]
            yield format_sse(data)
            ticks += 1
            if data["status"] in TERMINAL_STREAM_STATUSES:
                return
            if max_ticks is not None and ticks >= max_ticks:
                return
            sleep(interval)
    finally:
        conn.close()
```

Add the route to `service/app.py` (inside `create_app`, with the other routes; add `from fastapi.responses import StreamingResponse` and `from mml_cloud_transfer.service.sse import progress_events` to the imports):

```python
    @router.get("/jobs/{job_id}/stream")
    def stream_job(job_id: int) -> StreamingResponse:
        conn, repo = _open()
        try:
            _job_or_404(repo, job_id)
        finally:
            conn.close()
        return StreamingResponse(
            progress_events(config.db_path, job_id, interval=config.sse_interval),
            media_type="text/event-stream",
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/service -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: SSE progress streaming with throttled ticks and event cursoring"
```

---

### Task 8: `QueueWorker` — FIFO pickup, scan-if-needed, run, report, intents

The worker loop: one job at a time, FIFO by id among eligible jobs. On pickup it scans if the manifest is empty (local scan for uploads, `scan_remote` for downloads), builds the `GcsContext` from the job's profile, runs `run_job` with the controller's stop event wired into `should_stop`, writes the report on a finished run, and translates a pause/cancel intent afterward. Every collaborator is injectable so the unit tests run without any network; one emulator test proves `run_once` end-to-end.

**Files:**
- Create: `src/mml_cloud_transfer/service/worker.py`
- Test: `tests/service/test_worker.py`, `tests/service/test_worker_emulator.py`

**Interfaces:**
- Consumes: `next_eligible_job`, `get_profile` (Task 5); `run_job`/`EngineOptions.should_stop` (Task 3); `JobController` (Task 6); `run_scan` from `cli.scan_command`; `scan_remote` from `engine.runner`; `write_report`; `make_context`.
- Produces: `QueueWorker(config, controller, *, make_context_fn=make_context, run_job_fn=run_job, run_scan_fn=run_scan, scan_remote_fn=scan_remote, write_report_fn=write_report, get_meta_fn=get_meta, sleep=time.sleep, now=_now)` with `run_once() -> bool` (handled at most one job) and `run_forever() -> None` (loop until `controller.service_stop`). Task 9 extends this class (`startup_recovery`, stalled handling); Task 10 threads it into the host.

- [ ] **Step 1: Write the failing tests**

Create `tests/service/test_worker.py`:

```python
"""Worker unit tests: every engine collaborator is injected, no network.
The injected run_job_fn writes DB state exactly as the real one would."""

import pytest

from mml_cloud_transfer.core.models import Direction, JobStatus, PlannedFile
from mml_cloud_transfer.service.config import load_config
from mml_cloud_transfer.service.controller import JobController
from mml_cloud_transfer.service.worker import QueueWorker
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


@pytest.fixture
def config(tmp_path):
    return load_config(tmp_path / "data")


def _submit(config, *, name="j", direction=Direction.UPLOAD,
            scheduled=None, planned=True):
    conn = connect(config.db_path)
    try:
        repo = JobRepository(conn)
        profile_id = repo.get_or_create_profile(
            bucket="b", auth_type="adc", credential_ref=None
        )
        job_id = repo.create_job(
            name=name, direction=direction, source_root="s", dest_prefix="p",
            profile_id=profile_id, scheduled_start_at=scheduled,
        )
        if planned:
            repo.add_planned_files(job_id, [PlannedFile("a.bin", "s/a.bin", 3, 1)])
    finally:
        conn.close()
    return job_id


def _status(config, job_id):
    conn = connect(config.db_path)
    try:
        return JobRepository(conn).get_job(job_id)["status"]
    finally:
        conn.close()


def _worker(config, controller=None, **kw):
    controller = controller or JobController()
    kw.setdefault("make_context_fn", lambda bucket, **k: object())
    kw.setdefault("write_report_fn", lambda *a, **k: None)
    return QueueWorker(config, controller, **kw), controller


def test_fifo_pickup_and_schedule_eligibility(config):
    first = _submit(config, name="first")
    second = _submit(config, name="later", scheduled="2100-01-01T00:00:00+00:00")
    ran = []

    def fake_run_job(db_path, job_id, ctx, *, options):
        ran.append(job_id)
        conn = connect(db_path)
        try:
            JobRepository(conn).finish_job(job_id, JobStatus.COMPLETE)
        finally:
            conn.close()
        return JobStatus.COMPLETE

    worker, _ = _worker(config, run_job_fn=fake_run_job)
    assert worker.run_once() is True
    assert worker.run_once() is False        # the year-2100 job is not eligible
    assert ran == [first]
    assert _status(config, second) == JobStatus.PENDING.value


def test_upload_without_manifest_is_scanned_first(config):
    _submit(config, planned=False)
    order = []

    def fake_scan(**kwargs):
        order.append(("scan", kwargs["job_id"]))

    def fake_run_job(db_path, job_id, ctx, *, options):
        order.append(("run", job_id))
        conn = connect(db_path)
        try:
            JobRepository(conn).finish_job(job_id, JobStatus.COMPLETE)
        finally:
            conn.close()
        return JobStatus.COMPLETE

    worker, _ = _worker(config, run_scan_fn=fake_scan, run_job_fn=fake_run_job)
    worker.run_once()
    assert [kind for kind, _ in order] == ["scan", "run"]


def test_cancel_intent_lands_after_the_stopped_run(config):
    job_id = _submit(config)

    def fake_run_job(db_path, job_id, ctx, *, options):
        worker_controller.request(job_id, "cancel")   # user clicks mid-run
        assert options.should_stop()                  # wired to the stop event
        conn = connect(db_path)
        try:
            repo = JobRepository(conn)
            repo.set_job_status(job_id, JobStatus.PENDING)  # engine stop path
        finally:
            conn.close()
        return JobStatus.PENDING

    worker, worker_controller = _worker(config, run_job_fn=fake_run_job)
    worker.run_once()
    assert _status(config, job_id) == JobStatus.CANCELLED.value


def test_pause_intent_lands_after_the_stopped_run(config):
    job_id = _submit(config)

    def fake_run_job(db_path, job_id, ctx, *, options):
        worker_controller.request(job_id, "pause")
        conn = connect(db_path)
        try:
            JobRepository(conn).set_job_status(job_id, JobStatus.PENDING)
        finally:
            conn.close()
        return JobStatus.PENDING

    worker, worker_controller = _worker(config, run_job_fn=fake_run_job)
    worker.run_once()
    assert _status(config, job_id) == JobStatus.PAUSED.value


def test_intent_never_downgrades_a_finished_job(config):
    job_id = _submit(config)

    def fake_run_job(db_path, job_id, ctx, *, options):
        worker_controller.request(job_id, "cancel")   # arrives too late
        conn = connect(db_path)
        try:
            JobRepository(conn).finish_job(job_id, JobStatus.COMPLETE)
        finally:
            conn.close()
        return JobStatus.COMPLETE

    worker, worker_controller = _worker(config, run_job_fn=fake_run_job)
    worker.run_once()
    assert _status(config, job_id) == JobStatus.COMPLETE.value


def test_report_written_for_finished_runs(config):
    _submit(config)
    reported = []

    def fake_run_job(db_path, job_id, ctx, *, options):
        conn = connect(db_path)
        try:
            JobRepository(conn).finish_job(job_id, JobStatus.INCOMPLETE)
        finally:
            conn.close()
        return JobStatus.INCOMPLETE

    worker, _ = _worker(
        config, run_job_fn=fake_run_job,
        write_report_fn=lambda db, job_id, out, bucket=None: reported.append(job_id),
    )
    worker.run_once()
    assert len(reported) == 1


def test_worker_crash_pauses_the_job_not_the_service(config):
    job_id = _submit(config)

    def exploding_context(bucket, **kw):
        raise RuntimeError("boom")

    worker, _ = _worker(config, make_context_fn=exploding_context)
    assert worker.run_once() is True        # handled: did work, didn't raise
    assert _status(config, job_id) == JobStatus.PAUSED.value
    conn = connect(config.db_path)
    kinds = [e["kind"] for e in JobRepository(conn).events_after(job_id, 0)]
    conn.close()
    assert "worker_error" in kinds
```

Create `tests/service/test_worker_emulator.py`:

```python
"""run_once end-to-end: a real tiny upload through the real engine."""

import os

import pytest

from mml_cloud_transfer.core.models import Direction, JobStatus
from mml_cloud_transfer.service.config import load_config
from mml_cloud_transfer.service.controller import JobController
from mml_cloud_transfer.service.worker import QueueWorker
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


@pytest.mark.emulator
def test_run_once_completes_a_real_upload(emulator, emulator_client, tmp_path):
    _, bucket = emulator_client
    config = load_config(tmp_path / "data")
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.bin").write_bytes(os.urandom(4096))
    (src / "b.bin").write_bytes(os.urandom(2048))

    conn = connect(config.db_path)
    repo = JobRepository(conn)
    profile_id = repo.get_or_create_profile(
        bucket=bucket, auth_type="emulator", credential_ref=emulator.endpoint
    )
    job_id = repo.create_job(
        name="e2e", direction=Direction.UPLOAD, source_root=str(src),
        dest_prefix="night", profile_id=profile_id,
    )
    conn.close()

    worker = QueueWorker(config, JobController())
    assert worker.run_once() is True

    conn = connect(config.db_path)
    repo = JobRepository(conn)
    job = repo.get_job(job_id)
    states = {r["state"] for r in repo.get_files(job_id)}
    conn.close()
    assert job["status"] == JobStatus.COMPLETE.value
    assert states == {"verified"}
    assert (config.reports_dir / f"job-{job_id}" / "report.html").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/service/test_worker.py -v`
Expected: FAIL — `service.worker` does not exist.

- [ ] **Step 3: Implement**

Create `service/worker.py`:

```python
"""The FIFO job worker: one job at a time, scan-if-needed, run, report.

The worker owns no transfer logic — it wires engine.runner.run_job to the
queue, the controller, and the report writer. Task 9 adds startup recovery
and the stalled slow-retry loop.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from mml_cloud_transfer.cli.scan_command import run_scan
from mml_cloud_transfer.core.models import Direction, JobStatus
from mml_cloud_transfer.engine.report import write_report
from mml_cloud_transfer.engine.runner import EngineOptions, run_job, scan_remote
from mml_cloud_transfer.gcs.client import make_context
from mml_cloud_transfer.gcs.objects import get_meta
from mml_cloud_transfer.service.config import ServiceConfig
from mml_cloud_transfer.service.controller import JobController
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

_INTENTS = {
    "pause": (JobStatus.PAUSED, "paused_by_user"),
    "cancel": (JobStatus.CANCELLED, "cancelled_by_user"),
}


def _now() -> str:
    """Must match store.repository._now — schedules compare as strings."""
    return datetime.now(UTC).isoformat(timespec="seconds")


class QueueWorker:
    def __init__(
        self,
        config: ServiceConfig,
        controller: JobController,
        *,
        make_context_fn=make_context,
        run_job_fn=run_job,
        run_scan_fn=run_scan,
        scan_remote_fn=scan_remote,
        write_report_fn=write_report,
        get_meta_fn=get_meta,
        sleep=time.sleep,
        now=_now,
    ) -> None:
        self._config = config
        self._controller = controller
        self._make_context = make_context_fn
        self._run_job = run_job_fn
        self._run_scan = run_scan_fn
        self._scan_remote = scan_remote_fn
        self._write_report = write_report_fn
        self._get_meta = get_meta_fn
        self._sleep = sleep
        self._now = now

    # ---- loop -----------------------------------------------------------

    def run_forever(self) -> None:
        while not self._controller.service_stop.is_set():
            if not self.run_once():
                self._sleep(self._config.poll_interval)

    def run_once(self) -> bool:
        """Pick up and fully handle at most one job. Never raises."""
        picked = self._pick()
        if picked is None:
            return False
        job, profile = picked
        stop_event = self._controller.job_started(job["id"])
        try:
            self._handle(job, profile, stop_event)
        finally:
            intent = self._controller.job_finished()
            self._apply_intent(job["id"], intent)
        return True

    # ---- steps ----------------------------------------------------------

    def _pick(self):
        conn = connect(self._config.db_path)
        try:
            repo = JobRepository(conn)
            job = repo.next_eligible_job(self._now())
            if job is None:
                return None
            if job["profile_id"] is None:
                repo.set_job_status(job["id"], JobStatus.PAUSED)
                repo.record_event(
                    job["id"], "worker_error", "job has no connection profile"
                )
                return dict(job), None
            return dict(job), dict(repo.get_profile(job["profile_id"]))
        finally:
            conn.close()

    def _handle(self, job, profile, stop_event) -> None:
        job_id = job["id"]
        if profile is None:
            return  # _pick already paused it
        try:
            ctx = self._context(profile)
            if job["planned_files"] == 0 and job["started_at"] is None:
                if Direction(job["direction"]) is Direction.UPLOAD:
                    self._run_scan(
                        db_path=self._config.db_path,
                        source_root=job["source_root"],
                        dest_prefix=job["dest_prefix"],
                        job_name=job["name"],
                        job_id=job_id,
                        policy=self._config.size_policy,
                    )
                else:
                    self._scan_remote(
                        ctx, self._config.db_path, job_id,
                        policy=self._config.size_policy,
                    )
            if stop_event.is_set() or self._controller.service_stop.is_set():
                return
            status = self._run_job(
                self._config.db_path, job_id, ctx,
                options=self._options(stop_event),
            )
            if status in (
                JobStatus.COMPLETE, JobStatus.INCOMPLETE, JobStatus.PAUSED
            ):
                self._report(job_id, profile)
        except Exception as exc:  # a worker crash must not kill the service
            self._record_failure(job_id, exc)

    def _context(self, profile):
        auth_type = profile["auth_type"]
        if auth_type == "emulator":
            return self._make_context(
                profile["bucket"], emulator_endpoint=profile["credential_ref"]
            )
        if auth_type == "key_file":
            return self._make_context(
                profile["bucket"], credentials_path=profile["credential_ref"]
            )
        return self._make_context(profile["bucket"])

    def _options(self, stop_event) -> EngineOptions:
        return EngineOptions(
            policy=self._config.size_policy,
            file_workers=self._config.file_workers,
            should_stop=lambda: (
                stop_event.is_set() or self._controller.service_stop.is_set()
            ),
        )

    def _report(self, job_id: int, profile) -> None:
        self._write_report(
            self._config.db_path, job_id,
            self._config.reports_dir / f"job-{job_id}",
            bucket=profile["bucket"],
        )

    def _apply_intent(self, job_id: int, intent: str | None) -> None:
        if intent not in _INTENTS:
            return
        target, event = _INTENTS[intent]
        conn = connect(self._config.db_path)
        try:
            repo = JobRepository(conn)
            # Only a run that actually stopped (engine put it back to
            # `pending`, or Task 9 left it `stalled`) takes the intent; a
            # run that finished first must keep its real outcome.
            if repo.get_job(job_id)["status"] in (
                JobStatus.PENDING.value, JobStatus.STALLED.value
            ):
                repo.set_job_status(job_id, target)
                repo.record_event(job_id, event)
        finally:
            conn.close()

    def _record_failure(self, job_id: int, exc: Exception) -> None:
        conn = connect(self._config.db_path)
        try:
            repo = JobRepository(conn)
            repo.set_job_status(job_id, JobStatus.PAUSED)  # needs attention
            repo.record_event(job_id, "worker_error", str(exc)[:500])
        finally:
            conn.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/service -v`
Expected: PASS (the emulator test runs when the binary is present, otherwise skips).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: FIFO queue worker with scan-if-needed, stop wiring, reports, and pause/cancel intents"
```

---

### Task 9: Startup recovery and the `stalled` slow-cadence retry state

Two behaviors on `QueueWorker`. **Recovery:** at service start, jobs left `running` or `stalled` by a dead process get `reset_stale_transfers(stale_after_seconds=0)` (no transfer can possibly be in flight at startup, so zero staleness is correct) and become `pending` when auto-resume is on (default), `paused` when off. **Stalled:** a run that ends `INCOMPLETE` with at least one `network`-category failure, where a connectivity probe then fails, parks the job in `stalled` and probes on a slow cadence (`stall_probe_interval`, default 60 s) instead of burning the night. Probing — not blind re-running — matters: each re-run costs up to 5 per-file attempts against the 15-attempt quarantine budget, so the worker re-enqueues only once the bucket answers again. A probe that fails with a *credential* error counts as reachable: the re-run will then pause the job through the engine's own escalation, which is the correct outcome. A stalled job intentionally holds the single worker (the spec runs one job at a time; a network outage would stall any successor too), and the loop exits when the stop event fires or the job's status is changed externally (pause/cancel via API).

**Files:**
- Modify: `src/mml_cloud_transfer/service/worker.py`
- Test: `tests/service/test_worker.py` (append)

**Interfaces:**
- Consumes: `jobs_with_status`, `count_failures` (Task 5), `reset_stale_transfers` (existing), `classify` from `core.errors`, injected `get_meta_fn`.
- Produces: `QueueWorker.startup_recovery() -> None`; stalled handling inside `_handle`; `_probe(ctx) -> bool`. Task 10 calls `startup_recovery()` before starting the loop; Task 12's kill test depends on it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/service/test_worker.py` (add `import requests` and `from mml_cloud_transfer.core.errors import ErrorCategory` and `from mml_cloud_transfer.core.models import FileState, PlannedFile` to its imports as needed):

```python
def _fail_network(config, job_id):
    conn = connect(config.db_path)
    try:
        repo = JobRepository(conn)
        file_id = repo.get_files(job_id)[0]["id"]
        repo.mark_failed(file_id, ErrorCategory.NETWORK, "conn reset")
    finally:
        conn.close()


def test_startup_recovery_resumes_interrupted_jobs(config):
    job_id = _submit(config)
    conn = connect(config.db_path)
    repo = JobRepository(conn)
    repo.set_job_status(job_id, JobStatus.RUNNING)
    file_id = repo.get_files(job_id)[0]["id"]
    repo.mark_transferring(file_id)          # fresh heartbeat — a crash relic
    conn.close()

    worker, _ = _worker(config)
    worker.startup_recovery()

    conn = connect(config.db_path)
    repo = JobRepository(conn)
    job = repo.get_job(job_id)
    state = repo.get_files(job_id)[0]["state"]
    kinds = [e["kind"] for e in repo.events_after(job_id, 0)]
    conn.close()
    assert job["status"] == JobStatus.PENDING.value
    assert state == FileState.PENDING.value  # 0s staleness beat the heartbeat
    assert "recovered_at_startup" in kinds


def test_startup_recovery_honours_auto_resume_off(config, tmp_path):
    import json as jsonlib
    (config.data_dir).mkdir(parents=True, exist_ok=True)
    config.settings_path.write_text(
        jsonlib.dumps({"auto_resume_on_startup": False}), encoding="utf-8"
    )
    from mml_cloud_transfer.service.config import load_config as reload
    config = reload(config.data_dir)
    job_id = _submit(config)
    conn = connect(config.db_path)
    JobRepository(conn).set_job_status(job_id, JobStatus.RUNNING)
    conn.close()

    worker, _ = _worker(config)
    worker.startup_recovery()
    assert _status(config, job_id) == JobStatus.PAUSED.value


def test_incomplete_with_unreachable_network_stalls_then_requeues(config):
    job_id = _submit(config)
    probes = {"n": 0}
    slept = []

    def fake_run_job(db_path, job_id, ctx, *, options):
        _fail_network(config, job_id)
        conn = connect(db_path)
        try:
            JobRepository(conn).finish_job(job_id, JobStatus.INCOMPLETE)
        finally:
            conn.close()
        return JobStatus.INCOMPLETE

    def fake_get_meta(ctx, name):
        probes["n"] += 1
        if probes["n"] <= 2:
            # A REAL transport exception type — never builtin ConnectionResetError.
            raise requests.exceptions.ConnectionError("network is down")
        return None                       # 404 == server answered == reachable

    worker, _ = _worker(
        config, run_job_fn=fake_run_job, get_meta_fn=fake_get_meta,
        sleep=slept.append,
    )
    worker.run_once()

    conn = connect(config.db_path)
    repo = JobRepository(conn)
    job = repo.get_job(job_id)
    kinds = [e["kind"] for e in repo.events_after(job_id, 0)]
    conn.close()
    assert job["status"] == JobStatus.PENDING.value   # re-queued, ready to re-run
    assert "job_stalled" in kinds
    assert "job_unstalled" in kinds
    assert slept                                      # it waited between probes
    assert all(s == config.stall_probe_interval for s in slept)


def test_incomplete_with_reachable_network_does_not_stall(config):
    job_id = _submit(config)
    reported = []

    def fake_run_job(db_path, job_id, ctx, *, options):
        _fail_network(config, job_id)
        conn = connect(db_path)
        try:
            JobRepository(conn).finish_job(job_id, JobStatus.INCOMPLETE)
        finally:
            conn.close()
        return JobStatus.INCOMPLETE

    worker, _ = _worker(
        config, run_job_fn=fake_run_job,
        get_meta_fn=lambda ctx, name: None,           # probe succeeds
        write_report_fn=lambda db, job_id, out, bucket=None: reported.append(job_id),
    )
    worker.run_once()
    assert _status(config, job_id) == JobStatus.INCOMPLETE.value
    assert reported == [job_id]


def test_cancel_during_stall_wins(config):
    job_id = _submit(config)

    def fake_run_job(db_path, job_id, ctx, *, options):
        _fail_network(config, job_id)
        conn = connect(db_path)
        try:
            JobRepository(conn).finish_job(job_id, JobStatus.INCOMPLETE)
        finally:
            conn.close()
        return JobStatus.INCOMPLETE

    def sleeping_cancel(seconds):
        worker_controller.request(job_id, "cancel")   # user cancels mid-stall

    worker, worker_controller = _worker(
        config, run_job_fn=fake_run_job,
        get_meta_fn=lambda ctx, name: (_ for _ in ()).throw(
            requests.exceptions.ConnectionError("still down")
        ),
        sleep=sleeping_cancel,
    )
    worker.run_once()
    assert _status(config, job_id) == JobStatus.CANCELLED.value
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/service/test_worker.py -v`
Expected: FAIL — no `startup_recovery`; INCOMPLETE runs never stall.

- [ ] **Step 3: Implement**

In `service/worker.py`, add imports `from mml_cloud_transfer.core.errors import ErrorCategory, classify`, then:

```python
    # ---- startup recovery ----------------------------------------------

    def startup_recovery(self) -> None:
        """Jobs left running/stalled by a dead service become pending (or
        paused when auto-resume is off). Zero staleness on the heartbeat
        reset is correct here and only here: at startup, no transfer can
        possibly be in flight."""
        conn = connect(self._config.db_path)
        try:
            repo = JobRepository(conn)
            for status in (JobStatus.RUNNING, JobStatus.STALLED):
                for job in repo.jobs_with_status(status):
                    repo.reset_stale_transfers(job["id"], stale_after_seconds=0)
                    if self._config.auto_resume_on_startup:
                        repo.set_job_status(job["id"], JobStatus.PENDING)
                        repo.record_event(
                            job["id"], "recovered_at_startup", "auto-resume"
                        )
                    else:
                        repo.set_job_status(job["id"], JobStatus.PAUSED)
                        repo.record_event(
                            job["id"], "recovered_at_startup",
                            "paused (auto-resume off)",
                        )
        finally:
            conn.close()
```

In `_handle`, replace the post-run block:

```python
            status = self._run_job(
                self._config.db_path, job_id, ctx,
                options=self._options(stop_event),
            )
            if (
                status is JobStatus.INCOMPLETE
                and self._network_failed(job_id)
                and not self._probe(ctx)
            ):
                self._stall(job_id, ctx, stop_event)
                return
            if status in (
                JobStatus.COMPLETE, JobStatus.INCOMPLETE, JobStatus.PAUSED
            ):
                self._report(job_id, profile)
```

New methods:

```python
    # ---- stalled --------------------------------------------------------

    def _network_failed(self, job_id: int) -> bool:
        conn = connect(self._config.db_path)
        try:
            repo = JobRepository(conn)
            return repo.count_failures(job_id, ErrorCategory.NETWORK) > 0
        finally:
            conn.close()

    def _probe(self, ctx) -> bool:
        """Can we reach the bucket at all? A 404 (get_meta -> None) is a
        SUCCESSFUL probe — the server answered. Only network-class failures
        mean unreachable; e.g. a credential failure is 'reachable', so the
        re-run escalates it properly (the job pauses)."""
        try:
            self._get_meta(ctx, "mmlct-connectivity-probe")
        except Exception as exc:
            return classify(exc).category is not ErrorCategory.NETWORK
        return True

    def _stall(self, job_id: int, ctx, stop_event) -> None:
        conn = connect(self._config.db_path)
        try:
            repo = JobRepository(conn)
            repo.set_job_status(job_id, JobStatus.STALLED)
            repo.record_event(
                job_id, "job_stalled",
                "sustained network failure; probing on slow cadence",
            )
        finally:
            conn.close()
        while not (stop_event.is_set() or self._controller.service_stop.is_set()):
            self._sleep(self._config.stall_probe_interval)
            if stop_event.is_set() or self._controller.service_stop.is_set():
                return  # intent (if any) is applied by run_once's finally
            conn = connect(self._config.db_path)
            try:
                repo = JobRepository(conn)
                if repo.get_job(job_id)["status"] != JobStatus.STALLED.value:
                    return  # paused/cancelled externally while we slept
                if self._probe(ctx):
                    repo.set_job_status(job_id, JobStatus.PENDING)
                    repo.record_event(
                        job_id, "job_unstalled", "network is back; re-queued"
                    )
                    return
            finally:
                conn.close()
```

Note the attempt-budget reasoning for the reviewer: the stall loop re-enqueues rather than calling `run_job` itself, so a re-run happens only via the ordinary queue path, at most once per successful probe. While the network is fully down, zero per-file attempts are consumed. If probes succeed but transfers keep failing, cumulative attempts walk to quarantine (15) and the job reaches a terminal INCOMPLETE — self-terminating by the spec's own rule.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/service -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: startup recovery (auto-resume) and stalled slow-cadence probing"
```

---

### Task 10: Hosts — console runner, `python -m mml_cloud_transfer.service`, pywin32 service

Three entry points around one `ServiceHost`: tests and the kill test run the console host; developers run `python -m mml_cloud_transfer.service`; production installs `mmlct-service` (pywin32, session 0, auto-start, restart-on-failure via `sc failure`). Graceful stop = set `controller.service_stop` + ask uvicorn to exit; the engine's `should_stop` (Task 3) winds the running job down onto the recovery path within one chunk.

**Files:**
- Create: `src/mml_cloud_transfer/service/host.py`
- Create: `src/mml_cloud_transfer/service/__main__.py`
- Create: `src/mml_cloud_transfer/service/windows_service.py`
- Modify: `pyproject.toml` (add `pywin32` dependency, `mmlct-service` script)
- Test: `tests/service/conftest.py` (create), `tests/service/test_host.py`, `tests/service/test_windows_service.py`

**Interfaces:**
- Consumes: `create_app`, `JobController`, `QueueWorker.startup_recovery`/`run_forever`, `ServiceConfig`.
- Produces: `ServiceHost(config)` with `.start()` (recovery + worker thread + uvicorn thread, non-blocking), `.wait_ready(timeout=15.0)`, `.stop(timeout=30.0)`, and attributes `.config`, `.controller`, `.app`, `.worker`; `run_console(config)` (blocking, Ctrl+C to stop); `python -m mml_cloud_transfer.service [--data-dir DIR] [--port N]`; `mmlct-service [install|start|stop|remove|debug]` with `_svc_name_ = "MMLCloudTransfer"`. Test fixture `running_host` in `tests/service/conftest.py` (yields `(host, config, token)`), plus helper `free_port()`. Tasks 11–12 consume the fixture and the `-m` entry point.

- [ ] **Step 1: Write the failing tests**

Create `tests/service/conftest.py`:

```python
"""Service-level fixtures: an in-process host on an ephemeral port."""

import socket

import pytest

from mml_cloud_transfer.service.config import load_config
from mml_cloud_transfer.service.security import read_token


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def running_host(tmp_path):
    from mml_cloud_transfer.service.host import ServiceHost

    config = load_config(tmp_path / "data", port=free_port())
    host = ServiceHost(config)
    host.start()
    host.wait_ready()
    yield host, config, read_token(config.token_path)
    host.stop()
```

Create `tests/service/test_host.py`:

```python
import requests

from mml_cloud_transfer.core.models import JobStatus


def test_host_serves_health_and_guards_jobs(running_host):
    host, config, token = running_host
    health = requests.get(f"{config.base_url}/health", timeout=5)
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert requests.get(f"{config.base_url}/jobs", timeout=5).status_code == 401
    jobs = requests.get(
        f"{config.base_url}/jobs",
        headers={"Authorization": f"Bearer {token}"}, timeout=5,
    )
    assert jobs.status_code == 200
    assert jobs.json() == []


def test_host_stop_joins_its_threads(running_host):
    host, config, token = running_host
    host.stop()
    assert all(not t.is_alive() for t in host.threads)
    assert host.controller.service_stop.is_set()
```

Create `tests/service/test_windows_service.py`:

```python
import pytest

win32serviceutil = pytest.importorskip(
    "win32serviceutil", reason="pywin32 is Windows-only"
)


def test_service_class_shape():
    from mml_cloud_transfer.service.windows_service import (
        DISPLAY_NAME,
        SERVICE_NAME,
        _build_service_class,
    )

    cls = _build_service_class()
    assert cls._svc_name_ == SERVICE_NAME == "MMLCloudTransfer"
    assert cls._svc_display_name_ == DISPLAY_NAME
    assert issubclass(cls, win32serviceutil.ServiceFramework)
```

- [ ] **Step 2: Install pywin32, run the tests to verify they fail**

Add to `pyproject.toml` `dependencies`: `"pywin32>=306; sys_platform == 'win32'"`. Add to `[project.scripts]`: `mmlct-service = "mml_cloud_transfer.service.windows_service:main"`. Then:

Run: `.venv/Scripts/python -m pip install -e .[dev]` and `.venv/Scripts/python -m pytest tests/service/test_host.py tests/service/test_windows_service.py -v`
Expected: FAIL — `service.host` / `service.windows_service` do not exist.

- [ ] **Step 3: Implement**

Create `service/host.py`:

```python
"""One process, three threads: uvicorn, the queue worker, and the caller.

The Windows Service wrapper and `python -m mml_cloud_transfer.service`
both drive ServiceHost; tests run it in-process on an ephemeral port.
"""

from __future__ import annotations

import threading
import time

import uvicorn

from mml_cloud_transfer.service.app import create_app
from mml_cloud_transfer.service.config import ServiceConfig
from mml_cloud_transfer.service.controller import JobController
from mml_cloud_transfer.service.worker import QueueWorker


class ServiceHost:
    def __init__(self, config: ServiceConfig) -> None:
        self.config = config
        self.controller = JobController()
        self.app = create_app(config, self.controller)
        self.worker = QueueWorker(config, self.controller)
        self._server = uvicorn.Server(
            uvicorn.Config(
                self.app, host=config.host, port=config.port, log_level="warning"
            )
        )
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        """Recovery first, then worker and API threads. Non-blocking."""
        self.worker.startup_recovery()
        self.threads = [
            threading.Thread(
                target=self.worker.run_forever, name="mmlct-worker", daemon=True
            ),
            threading.Thread(
                target=self._server.run, name="mmlct-api", daemon=True
            ),
        ]
        for thread in self.threads:
            thread.start()

    def wait_ready(self, timeout: float = 15.0) -> None:
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"API server did not start on {self.config.base_url}"
                    " (port in use?)"
                )
            time.sleep(0.05)

    def stop(self, timeout: float = 30.0) -> None:
        """Graceful: the running job winds down via should_stop within one
        chunk and lands on the recovery path. Safe to call twice."""
        self.controller.service_stop.set()
        self._server.should_exit = True
        for thread in self.threads:
            thread.join(timeout=timeout)


def run_console(config: ServiceConfig) -> None:
    host = ServiceHost(config)
    host.start()
    host.wait_ready()
    print(f"MML Cloud Transfer service on {config.base_url} (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        host.stop()
```

Create `service/__main__.py`:

```python
"""Console-mode entry point: python -m mml_cloud_transfer.service"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from mml_cloud_transfer.service.config import load_config
from mml_cloud_transfer.service.host import run_console


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mml_cloud_transfer.service")
    parser.add_argument("--data-dir", default=None,
                        help="Data directory (default: %%ProgramData%%\\MML Cloud Transfer,"
                             " or MMLCT_DATA_DIR)")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    run_console(load_config(args.data_dir, port=args.port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Create `service/windows_service.py`:

```python
"""pywin32 Windows Service wrapper: mmlct-service install|start|stop|remove.

Runs in session 0, which is the point — jobs survive user logoff. The
class is built inside a factory so importing this module (e.g. on a
machine without pywin32) stays cheap and safe; console-mode development
uses `python -m mml_cloud_transfer.service` instead.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence

SERVICE_NAME = "MMLCloudTransfer"
DISPLAY_NAME = "MML Cloud Transfer Service"


def _build_service_class():
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil

    from mml_cloud_transfer.service.config import load_config
    from mml_cloud_transfer.service.host import ServiceHost

    class MmlctService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = DISPLAY_NAME
        _svc_description_ = (
            "Verified, resumable file transfers to Google Cloud Storage."
        )

        def __init__(self, args):
            super().__init__(args)
            self._stop = win32event.CreateEvent(None, 0, 0, None)
            self._host = None

        def SvcDoRun(self):
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, ""),
            )
            self._host = ServiceHost(load_config())
            self._host.start()
            self._host.wait_ready(timeout=60)
            win32event.WaitForSingleObject(self._stop, win32event.INFINITE)

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            if self._host is not None:
                self._host.stop()
            win32event.SetEvent(self._stop)

    return MmlctService


def _configure_restart_on_failure() -> None:
    """Spec: auto-start with restart-on-failure. sc's argument style is
    'name= value' with the space required."""
    subprocess.run(
        ["sc", "failure", SERVICE_NAME, "reset=", "86400",
         "actions=", "restart/5000/restart/5000/restart/30000"],
        check=False, capture_output=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    import win32serviceutil

    args = list(sys.argv if argv is None else [sys.argv[0], *argv])
    if "install" in args and "--startup" not in args:
        args[args.index("install"):args.index("install")] = ["--startup", "auto"]
    win32serviceutil.HandleCommandLine(_build_service_class(), argv=args)
    if "install" in args:
        _configure_restart_on_failure()
    return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/service -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: ServiceHost console runner, module entry point, and pywin32 Windows Service wrapper"
```

---

### Task 11: CLI as a client of the service API

`transfer`, `resume`, `status`, and `report` gain `--service-url` (default from `MMLCT_SERVICE_URL`) and `--token-file`; when a URL is present they go over HTTP: submit → watch the SSE stream → fetch the report → exit code by verdict. `transfer` additionally gains `--scheduled-at` (service mode only — direct mode has no scheduler and must reject it). Direct-engine mode is untouched. SSE parsing is hand-rolled over `requests.iter_lines` — the protocol here is one event type with one `data:` line.

**Files:**
- Create: `src/mml_cloud_transfer/cli/service_client.py`
- Modify: `src/mml_cloud_transfer/cli/__main__.py` (options)
- Modify: `src/mml_cloud_transfer/cli/transfer_command.py` (service-mode branches)
- Test: `tests/cli/test_service_mode.py` (create)

**Interfaces:**
- Consumes: the API surface (Task 6), SSE shape (Task 7), `running_host` fixture (Task 10), `read_token` from `service.security`, `load_config` from `service.config`.
- Produces: `ServiceError(status_code, detail)`; `ApiClient(base_url, token, *, session=None)` with `health()`, `submit_job(payload) -> int`, `list_jobs()`, `get_job(job_id)`, `pause(job_id)`, `resume(job_id)`, `cancel(job_id)`, `report(job_id) -> dict`, `stream(job_id) -> Iterator[dict]` (yields parsed `progress` payloads until the server closes); CLI flags `--service-url`, `--token-file`, `--scheduled-at`. The Plan 5 GUI will reuse `ApiClient` unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/cli/test_service_mode.py`:

```python
"""The CLI drives jobs entirely over the local API (Phase 3 gate). The
running_host fixture lives in tests/service/conftest.py, so these tests
import it explicitly."""

import io
import os
from contextlib import redirect_stdout

import pytest

from mml_cloud_transfer.cli.__main__ import main
from mml_cloud_transfer.cli.service_client import ApiClient, ServiceError
from mml_cloud_transfer.core.models import JobStatus
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

pytest_plugins = ["tests.service.conftest"]


def _run(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(argv)
    return code, out.getvalue()


def test_sse_parser_yields_progress_payloads():
    class _Response:
        status_code = 200

        def iter_lines(self, decode_unicode=False):
            yield "event: progress"
            yield 'data: {"status": "running", "n": 1}'
            yield ""
            yield "event: progress"
            yield 'data: {"status": "complete", "n": 2}'
            yield ""

    class _Session:
        headers = {}

        def get(self, url, stream=False, timeout=None):
            return _Response()

    client = ApiClient("http://x", "t", session=_Session())
    events = list(client.stream(1))
    assert [e["status"] for e in events] == ["running", "complete"]


@pytest.mark.emulator
def test_transfer_over_the_service_api(emulator, emulator_client, running_host, tmp_path):
    _, bucket = emulator_client
    host, config, token = running_host
    token_file = config.token_path
    src = tmp_path / "src"
    src.mkdir()
    for n in range(5):
        (src / f"f{n}.bin").write_bytes(os.urandom(4096))

    code, out = _run([
        "transfer", "--db", "unused-in-service-mode",
        "--name", "api-job", "--source", str(src), "--prefix", "night",
        "--bucket", bucket, "--emulator-endpoint", emulator.endpoint,
        "--service-url", config.base_url, "--token-file", str(token_file),
    ])
    assert code == 0, out
    assert "COMPLETE" in out
    assert "Report:" in out

    conn = connect(config.db_path)
    repo = JobRepository(conn)
    job = repo.list_jobs()[-1]
    states = {r["state"] for r in repo.get_files(job["id"])}
    conn.close()
    assert job["status"] == JobStatus.COMPLETE.value
    assert states == {"verified"}

    code, out = _run([
        "status", "--db", "unused",
        "--service-url", config.base_url, "--token-file", str(token_file),
    ])
    assert code == 0
    assert "api-job" in out


def test_scheduled_at_is_rejected_in_direct_mode(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    code, out = _run([
        "transfer", "--db", str(tmp_path / "jobs.db"),
        "--name", "j", "--source", str(src), "--bucket", "b",
        "--scheduled-at", "2100-01-01T00:00",
    ])
    assert code == 2
    assert "--service-url" in out


@pytest.mark.emulator
def test_scheduled_submit_returns_immediately(emulator, emulator_client, running_host, tmp_path):
    _, bucket = emulator_client
    host, config, token = running_host
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.bin").write_bytes(b"x")

    code, out = _run([
        "transfer", "--db", "unused", "--name", "tonight",
        "--source", str(src), "--bucket", bucket,
        "--emulator-endpoint", emulator.endpoint,
        "--scheduled-at", "2100-01-01T00:00:00+00:00",
        "--service-url", config.base_url, "--token-file", str(config.token_path),
    ])
    assert code == 0
    assert "2100-01-01" in out
    client = ApiClient(config.base_url, token)
    assert client.list_jobs()[-1]["status"] == JobStatus.PENDING.value
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/cli/test_service_mode.py -v`
Expected: FAIL — `cli.service_client` does not exist; `--service-url`/`--scheduled-at` are unknown arguments.

- [ ] **Step 3: Implement**

Create `cli/service_client.py`:

```python
"""HTTP client for the service API — the CLI's transport when --service-url
is given, and the GUI's transport in Plan 5. SSE parsing is hand-rolled:
the stream carries one event type with a single data: line per event."""

from __future__ import annotations

import json
from collections.abc import Iterator

import requests


class ServiceError(Exception):
    """The API said no: carries the HTTP status and the server's detail."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class ApiClient:
    def __init__(
        self, base_url: str, token: str, *, session: requests.Session | None = None
    ):
        self._base = base_url.rstrip("/")
        self._session = session if session is not None else requests.Session()
        self._session.headers["Authorization"] = f"Bearer {token}"

    def _check(self, response):
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", "")
            except ValueError:
                detail = getattr(response, "text", "")
            raise ServiceError(response.status_code, str(detail))
        return response.json()

    def health(self) -> dict:
        return self._check(self._session.get(f"{self._base}/health", timeout=10))

    def submit_job(self, payload: dict) -> int:
        response = self._session.post(f"{self._base}/jobs", json=payload, timeout=30)
        return int(self._check(response)["job_id"])

    def list_jobs(self) -> list[dict]:
        return self._check(self._session.get(f"{self._base}/jobs", timeout=30))

    def get_job(self, job_id: int) -> dict:
        return self._check(
            self._session.get(f"{self._base}/jobs/{job_id}", timeout=30)
        )

    def pause(self, job_id: int) -> dict:
        return self._check(
            self._session.post(f"{self._base}/jobs/{job_id}/pause", timeout=30)
        )

    def resume(self, job_id: int) -> dict:
        return self._check(
            self._session.post(f"{self._base}/jobs/{job_id}/resume", timeout=30)
        )

    def cancel(self, job_id: int) -> dict:
        return self._check(
            self._session.post(f"{self._base}/jobs/{job_id}/cancel", timeout=30)
        )

    def report(self, job_id: int) -> dict:
        return self._check(
            self._session.post(f"{self._base}/jobs/{job_id}/report", timeout=600)
        )

    def stream(self, job_id: int) -> Iterator[dict]:
        """Yield each SSE progress payload until the server closes the
        stream (which it does after a terminal tick)."""
        response = self._session.get(
            f"{self._base}/jobs/{job_id}/stream", stream=True, timeout=(10, 65)
        )
        if response.status_code >= 400:
            raise ServiceError(response.status_code, "stream refused")
        for line in response.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                yield json.loads(line[len("data:"):].strip())
```

`cli/__main__.py` — add a helper and wire it into the four subcommands (`transfer`, `resume`, `status`, `report`); `--scheduled-at` goes on `transfer` only:

```python
def add_service_options(sub):
    sub.add_argument(
        "--service-url",
        default=os.environ.get("MMLCT_SERVICE_URL"),
        help="Drive this command through the service API at this URL",
    )
    sub.add_argument(
        "--token-file", default=None,
        help="Bearer-token file (default: the service data directory's api_token)",
    )
```

```python
    transfer.add_argument(
        "--scheduled-at", default=None,
        help="Queue the job to start at this ISO-8601 time (requires --service-url)",
    )
    add_service_options(transfer)
    ...
    add_service_options(resume)
    add_service_options(status)
    add_service_options(report)
```

(Add `import os` to `cli/__main__.py`. `status` and `report` already exist as parsers; just add the call for each.)

`cli/transfer_command.py` — service-mode branches at the top of each command function, plus the watch loop (new imports: `from mml_cloud_transfer.cli.service_client import ApiClient, ServiceError`, `from mml_cloud_transfer.service.security import read_token`, `from mml_cloud_transfer.service.config import load_config`):

```python
def _api_client(args) -> ApiClient:
    token_path = (
        Path(args.token_file) if args.token_file else load_config().token_path
    )
    return ApiClient(args.service_url, read_token(token_path))


def _watch(client: ApiClient, job_id: int) -> JobStatus:
    """Print progress lines until the server closes the stream, then
    return the job's final status."""
    last_line = ""
    final_status = None
    for event in client.stream(job_id):
        final_status = event["status"]
        progress = event["progress"]
        line = (
            f"[{event['status']}] "
            f"{progress['files_done']}/{progress['files_total']} files, "
            f"{progress['bytes_done']}/{progress['bytes_total']} bytes"
        )
        if line != last_line:
            print(line)
            last_line = line
        for entry in event["events"]:
            if entry["kind"] in ("job_stalled", "job_unstalled", "run_paused"):
                print(f"  ! {entry['kind']}: {entry['detail'] or ''}")
    if final_status is None:
        raise ServiceError(0, "stream ended without any event")
    return JobStatus(final_status)


def _finish_via_service(client: ApiClient, job_id: int, status: JobStatus) -> int:
    report = client.report(job_id)
    print(f"Job {job_id}: {status.value.upper()}")
    print(f"Report: {report['report_html']}")
    return 0 if status is JobStatus.COMPLETE else 1


def run_transfer_via_service(args) -> int:
    client = _api_client(args)
    job_id = client.submit_job({
        "name": args.name,
        "direction": args.direction,
        "source_root": args.source,
        "dest_prefix": args.prefix,
        "bucket": args.bucket,
        "credentials_path": args.credentials,
        "emulator_endpoint": args.emulator_endpoint,
        "audit_hash": args.audit_hash,
        "scheduled_start_at": args.scheduled_at,
    })
    print(f"Job {job_id} submitted")
    if args.scheduled_at:
        print(f"Scheduled to start at {args.scheduled_at}; check progress with"
              f" 'mmlct status --service-url {args.service_url}'")
        return 0
    return _finish_via_service(client, job_id, _watch(client, job_id))
```

Then at the top of the existing functions:

```python
def run_transfer(args) -> int:
    if args.scheduled_at and not args.service_url:
        raise ValueError(
            "--scheduled-at requires the service; pass --service-url"
        )
    if args.service_url:
        return run_transfer_via_service(args)
    ...existing body unchanged...


def run_resume(args) -> int:
    if args.service_url:
        client = _api_client(args)
        client.resume(args.job_id)
        return _finish_via_service(client, args.job_id, _watch(client, args.job_id))
    ...existing body unchanged...


def run_status(args) -> int:
    if args.service_url:
        client = _api_client(args)
        jobs = client.list_jobs()
        if not jobs:
            print("No jobs.")
            return 0
        for job in jobs:
            print(
                f"#{job['id']} {job['name']} [{job['direction']}] {job['status']}"
                f" — {display_path(job['source_root'])} ->"
                f" {job['dest_prefix'] or '(root)'}"
            )
        return 0
    ...existing body unchanged...


def run_report_cmd(args) -> int:
    if args.service_url:
        client = _api_client(args)
        print(f"Report: {client.report(args.job_id)['report_html']}")
        return 0
    ...existing body unchanged...
```

`ValueError` already maps to exit code 2 in `cli/__main__.py`'s transfer/resume dispatch — the `--scheduled-at`-without-service error rides that path. Also wrap the service-mode dispatch so `ServiceError` and `requests.ConnectionError` print a friendly message ("service not reachable at <url> — is it running?") and return 1; do this in `cli/__main__.py`'s dispatch `try` block by adding the two exception types.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/cli -v`
Expected: PASS (service-mode tests skip without the emulator; the parser and direct-mode tests always run).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: CLI drives transfer/resume/status/report over the service API with SSE watch"
```

---

### Task 12: The defining test — a job survives killing the service — and final verification

Phase 3's gate, mirroring Plan 2's `test_interrupt_resume` but one level up: the *service process* dies, and on restart the job resumes with **no resume command** — startup recovery does it. Then the full-suite verification for the whole plan.

**Files:**
- Test: `tests/service/test_service_kill_resume.py` (create)

**Interfaces:**
- Consumes: `python -m mml_cloud_transfer.service` (Task 10), the REST surface (Task 6), settings.json overrides (Task 4), startup recovery (Task 9), `free_port` from `tests/service/conftest.py`.
- Produces: the executable proof of the phase's core promise.

- [ ] **Step 1: Write the test**

Create `tests/service/test_service_kill_resume.py`:

```python
"""Phase 3's defining test: kill the service mid-transfer, restart it, and
the job completes with every checksum matching — with NO resume call.
The console host runs as a real subprocess so the kill is real process
death; auto-resume (default on) must do the rest."""

import json
import os
import subprocess
import sys
import time

import pytest
import requests

from mml_cloud_transfer.core.hashing import hash_file
from mml_cloud_transfer.core.models import JobStatus
from mml_cloud_transfer.gcs.client import make_context
from mml_cloud_transfer.gcs.objects import get_meta
from mml_cloud_transfer.service.security import read_token
from mml_cloud_transfer.store.db import connect

from tests.service.conftest import free_port


def _start_service(data_dir, port):
    return subprocess.Popen(
        [
            sys.executable, "-m", "mml_cloud_transfer.service",
            "--data-dir", str(data_dir), "--port", str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_healthy(base_url, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if requests.get(f"{base_url}/health", timeout=1).ok:
                return
        except requests.ConnectionError:
            pass
        time.sleep(0.2)
    raise AssertionError("service did not become healthy in time")


@pytest.mark.emulator
def test_job_survives_service_kill(emulator, emulator_client, tmp_path):
    _, bucket = emulator_client
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "settings.json").write_text(json.dumps({
        "file_workers": 1,
        "size_policy": "65536,262144,262144",   # tiny thresholds: all 3 paths
        "poll_interval": 0.2,
    }), encoding="utf-8")

    src = tmp_path / "src"
    src.mkdir()
    for n in range(40):
        (src / f"small-{n:02d}.bin").write_bytes(os.urandom(8_192))
    (src / "medium.bin").write_bytes(os.urandom(200 * 1024))
    (src / "big.bin").write_bytes(os.urandom(600 * 1024))

    port = free_port()
    base = f"http://127.0.0.1:{port}"

    proc = _start_service(data_dir, port)
    try:
        _wait_healthy(base)
        headers = {
            "Authorization": f"Bearer {read_token(data_dir / 'api_token')}"
        }
        job_id = requests.post(f"{base}/jobs", json={
            "name": "overnight", "direction": "upload",
            "source_root": str(src), "dest_prefix": "night",
            "bucket": bucket, "emulator_endpoint": emulator.endpoint,
        }, headers=headers, timeout=30).json()["job_id"]

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = requests.get(
                f"{base}/jobs/{job_id}", headers=headers, timeout=5
            ).json()
            if job["progress"]["files_done"] >= 3:
                break
            time.sleep(0.2)
        else:
            pytest.fail("transfer made no visible progress within 60s")
    finally:
        proc.kill()          # real, unceremonious process death
        proc.wait(timeout=15)

    db = data_dir / "jobs.db"
    conn = connect(db)
    row = conn.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    done_before = conn.execute(
        "SELECT COUNT(*) AS n FROM job_files WHERE state IN ('verified', 'skipped')"
    ).fetchone()["n"]
    conn.close()
    assert row["status"] != JobStatus.COMPLETE.value
    assert done_before < 42, "kill happened too late to prove anything"

    # Restart. NO resume call: startup recovery must re-enqueue and the
    # worker must finish the job on its own.
    proc = _start_service(data_dir, port)
    try:
        _wait_healthy(base)
        headers = {
            "Authorization": f"Bearer {read_token(data_dir / 'api_token')}"
        }
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            job = requests.get(
                f"{base}/jobs/{job_id}", headers=headers, timeout=5
            ).json()
            if job["status"] in (
                JobStatus.COMPLETE.value, JobStatus.INCOMPLETE.value,
                JobStatus.PAUSED.value,
            ):
                break
            time.sleep(0.5)
        else:
            pytest.fail("job did not reach a terminal status after restart")
        assert job["status"] == JobStatus.COMPLETE.value
    finally:
        proc.kill()
        proc.wait(timeout=15)

    conn = connect(db)
    rows = conn.execute(
        "SELECT state FROM job_files WHERE job_id = ?", (job_id,)
    ).fetchall()
    conn.close()
    assert len(rows) == 42
    assert {r["state"] for r in rows} <= {"verified", "skipped"}

    # Spot-check the sliced file end-to-end: remote CRC == fresh local hash.
    ctx = make_context(bucket, emulator_endpoint=emulator.endpoint)
    meta = get_meta(ctx, "night/big.bin")
    assert meta is not None
    assert meta.crc32c == hash_file(src / "big.bin").crc32c

    # The report was written by the worker, not by any client call.
    assert (data_dir / "reports" / f"job-{job_id}" / "report.html").exists()
```

- [ ] **Step 2: Run the defining test**

Run: `.venv/Scripts/python -m pytest tests/service/test_service_kill_resume.py -v`
Expected: PASS (requires the emulator binary). If the kill consistently lands after all 42 files finish, lower the progress threshold from 3 to 1 or add more small files — the mid-flight assertion (`done_before < 42`) is the proof and must hold.

- [ ] **Step 3: Full-suite verification**

Run: `.venv/Scripts/python -m pytest`
Expected: everything passes — the Plan 2 baseline (236 passed + 2 skipped) plus every test added by Tasks 1–12; the only skips are `real_bucket` (release gate, needs `MMLCT_TEST_BUCKET`) and, on a machine without the binary, `emulator`. Fix anything red before committing.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "test: service survives kill and auto-resumes to COMPLETE (Phase 3 defining test)"
```

---

## Phase 3 gate — what "done" means beyond CI

Automated (this plan): the full suite including `test_job_survives_service_kill` is green.

Manual, before Phase 3 ships (extends the still-open Plan 2 release gate; needs an elevated prompt and a real bucket):

1. `mmlct-service install` then `mmlct-service start` (elevated). Verify `sc qc MMLCloudTransfer` shows AUTO_START and `sc qfailure MMLCloudTransfer` shows the restart actions.
2. Submit a multi-GB job via `mmlct transfer --service-url http://127.0.0.1:47821 ...` against the real bucket (`MMLCT_TEST_BUCKET` credentials).
3. **Log off the Windows session** while the job runs. Log back in; `mmlct status --service-url ...` must show the job progressed or completed — this is the one property no automated test in this suite can prove.
4. Kill the service process from Task Manager mid-transfer; confirm the SCM restarts it (restart-on-failure) and the job completes with a COMPLETE report and matching checksums.
5. Pull a network cable / disable the adapter mid-run for ~5 minutes; confirm the job goes `stalled`, then recovers and completes after reconnection.

Record the results in the PR description alongside the Plan 2 real-bucket gate results.
