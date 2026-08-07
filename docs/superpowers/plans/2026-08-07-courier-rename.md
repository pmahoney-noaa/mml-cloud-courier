# MML Cloud Courier Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the product and every identifier from MML Cloud Transfer (mmlct) to MML Cloud Courier (mmlcc) — clean break, no compatibility aliases — then migrate the live Windows service install.

**Architecture:** Six mechanical rename tasks in an isolated worktree, each ending with the full suite green at exactly the baseline counts, ordered so the tree installs and passes after every commit: package/pip identity first, then console-script names, env vars, Windows-service/display identity, transient wire identifiers, and finally docs + a rename note + full-tree verification. A seventh task (main session, user at keyboard for elevated steps) migrates the live service on merged master.

**Tech Stack:** Python 3.12, hatchling editable install, pytest (+fake-gcs-server emulator), pywin32 Windows service, PySide6 GUI.

**Decision record (2026-08-07):** product name AND all identifiers rename; pre-1.0, no external users, so no legacy-name support anywhere. Historical records under `docs/superpowers/{specs,plans,gates}` keep the old names.

## Canonical rename table

Every task consults this table; no other spellings are acceptable.

| Class | Old | New |
|---|---|---|
| pip / project name | `mml-cloud-transfer` | `mml-cloud-courier` |
| Python package | `mml_cloud_transfer` | `mml_cloud_courier` |
| Console scripts | `mmlct` / `mmlct-gui` / `mmlct-service` | `mmlcc` / `mmlcc-gui` / `mmlcc-service` |
| Env vars (all 7) | `MMLCT_{DATA_DIR, SERVICE_URL, TOKEN_FILE, OAUTH_CLIENT, TEST_BUCKET, TEST_PREFIX, FAKE_GCS}` | `MMLCC_*` same suffixes |
| Windows service name | `MMLCloudTransfer` | `MMLCloudCourier` |
| Service display name | `MML Cloud Transfer Service` | `MML Cloud Courier Service` |
| Default data dir | `%ProgramData%\MML Cloud Transfer` | `%ProgramData%\MML Cloud Courier` |
| Product display string | `MML Cloud Transfer` | `MML Cloud Courier` |
| pyproject description | `Verified, resumable file transfers…` | `MML Cloud Courier: verified, resumable file transfers between Windows workstations and Google Cloud Storage` |
| Preflight probe segment | `.mmlct-preflight` | `.mmlcc-preflight` |
| Release-gate segment | `mmlct-gate` | `mmlcc-gate` |
| Slice temp infix | `.mmlct.tmp/` | `.mmlcc.tmp/` |
| Audit metadata key | `mmlct-sha256` | `mmlcc-sha256` |
| Connectivity probe object | `mmlct-connectivity-probe` | `mmlcc-connectivity-probe` |
| Emulator client project | `"mmlct"` | `"mmlcc"` |
| Thread names | `mmlct-worker`, `mmlct-api`, `mmlct-gui-*` | `mmlcc-worker`, `mmlcc-api`, `mmlcc-gui-*` |
| DPAPI blob description | `MML Cloud Transfer credential` | `MML Cloud Courier credential` |
| Service class | `MmlctService` | `MmlccService` |

**Why the persisted-identifier renames are safe (clean break justified):**
- `.mmlct-preflight` probes and `mmlct-gate` objects are transient; both manual gates verified zero residue in the bucket at all versions.
- `mmlct-sha256` is write-only: `gcs/uploader.py` stamps it, tests assert it, **nothing ever reads it back** (verification recomputes hashes locally). Objects uploaded before the rename simply keep the old key as history.
- `.mmlct.tmp/` slice temps exist only while a sliced upload is in flight; Task 7's stale-active-job check runs before migration, so no job straddles the rename.
- `mmlct-connectivity-probe` is a metadata GET on a never-created name; the DPAPI description is a label, not key material; thread names and the emulator client project are process-local.

## Global Constraints

- Python 3.12 only: `py -3.12 -m venv .venv`; venv lives at the worktree root.
- Suite baseline must hold EXACTLY after every task: **526 passed, 1 skipped, 12 deselected (real_bucket)** — same tests, new names; no test added or removed.
- Known host quirk: `pytest -q` sometimes drops its final summary line. Cross-check with `.venv\Scripts\python -m pytest -v 2>&1 | Select-Object -Last 5`. NEVER estimate counts.
- Tests use ephemeral ports and temp data dirs (`MMLCC_DATA_DIR` after Task 3) and must never touch the live install (port 47821, `%ProgramData%`).
- Do NOT edit anything under `docs/superpowers/specs/`, `docs/superpowers/plans/`, or `docs/superpowers/gates/` — historical records keep the old names. (Task 6 moves one misplaced gate record INTO `gates/` without editing its content.)
- Clean break: never add fallback reads of old env vars, old metadata keys, or old service names.
- SDD commit discipline: every subagent dispatch `cd`s into the worktree as its FIRST command; immediately before each commit re-verify `git rev-parse --show-toplevel` prints the worktree root AND `git log -1 --format=%h` is the expected parent commit; one commit per task; never amend; never use bare `git stash`.
- `pyproject.toml` `addopts = "-q"` stays; run the suite as `.venv\Scripts\python -m pytest` from the worktree root.

## File Structure

No new source files; one moved package directory, two new/updated docs.

- Move: `src/mml_cloud_transfer/` → `src/mml_cloud_courier/` (git mv, Task 1)
- Modify (bulk, Tasks 1/3/4/5): all tracked files under `src/`, `tests/` (including `tests/tools/preflight-gcs.ps1`), plus `pyproject.toml`
- Modify (targeted): `pyproject.toml` (name, description, script names), `src/mml_cloud_courier/cli/__main__.py` (prog/description), `src/mml_cloud_courier/service/windows_service.py` (service identity), `src/mml_cloud_courier/gui/service_control.py`, `src/mml_cloud_courier/service/config.py` (data dir)
- Rewrite: `docs/gui.md` (Task 6)
- Create: `docs/courier-rename.md` (Task 6 — the rename note; final greps exclude it)
- Move: `docs/superpowers/2026-08-05-phase3-gate-record.md` → `docs/superpowers/gates/2026-08-05-phase3-gate-record.md` (Task 6, content untouched)

## The sweep script (used by Tasks 1, 3, 4, 5)

Each sweep task writes this to `$env:TEMP\courier_sweep.py` (overwriting is fine), edits ONLY the `PAIRS` list to that task's byte pairs, and runs it from the worktree root. It rewrites git-tracked files under `src/`, `tests/`, and `pyproject.toml` at the byte level (preserves encodings and CRLF/LF exactly), and never touches `docs/`:

```python
import subprocess
from pathlib import Path

PAIRS = [
    # (b"old", b"new") — set per task; see the task's Step for exact pairs
]
SCOPE = ["src", "tests", "pyproject.toml"]

files = subprocess.run(
    ["git", "ls-files", "--", *SCOPE],
    capture_output=True, text=True, check=True,
).stdout.splitlines()
changed = 0
for name in files:
    p = Path(name)
    data = p.read_bytes()
    new = data
    for old, repl in PAIRS:
        new = new.replace(old, repl)
    if new != data:
        p.write_bytes(new)
        changed += 1
print(f"rewrote {changed} files")
```

Run: `.venv\Scripts\python $env:TEMP\courier_sweep.py`

---

## Setup (main session, before Task 1 dispatches)

- [ ] **S1: Create the worktree.** Use EnterWorktree (branch name `courier-rename`). Known quirk: if creation errors with a working-tree-resolution complaint, the worktree WAS still created — enter it by path with the exact casing git reports (`git worktree list`).
- [ ] **S2: Create the worktree venv.** From the worktree root: `py -3.12 -m venv .venv` then `.venv\Scripts\python -m pip install -e ".[dev]"`.
- [ ] **S3: Copy the emulator binary.** `Copy-Item "C:\Users\pmaho\Documents\VibeCode\mml_cloud_transfer\tools\fake-gcs-server.exe" "<worktree>\tools\fake-gcs-server.exe"` (create `tools\` first if git didn't materialize it: `New-Item -ItemType Directory -Force tools`).
- [ ] **S4: Verify the baseline.** `.venv\Scripts\python -m pytest` → exactly **526 passed, 1 skipped, 12 deselected**. If the summary line is missing, cross-check with `-v` per Global Constraints. Do not dispatch Task 1 until this matches.

---

### Task 1: Package + pip identity

**Files:**
- Move: `src/mml_cloud_transfer/` → `src/mml_cloud_courier/`
- Modify: every tracked file under `src/` and `tests/` containing `mml_cloud_transfer` (~115 files, imports and docstrings), `pyproject.toml` (name, description, `packages`, script module paths)

**Interfaces:**
- Consumes: nothing (first task).
- Produces: importable package `mml_cloud_courier`; pip project `mml-cloud-courier`. Console script NAMES remain `mmlct`/`mmlct-gui`/`mmlct-service` until Task 2 (their module targets update here so they keep working).

- [ ] **Step 1: Move the package directory**

```powershell
git mv src/mml_cloud_transfer src/mml_cloud_courier
```

- [ ] **Step 2: Sweep imports and module paths**

Sweep script with:

```python
PAIRS = [(b"mml_cloud_transfer", b"mml_cloud_courier")]
```

This also rewrites `pyproject.toml`'s `packages = ["src/mml_cloud_courier"]` and the three script targets' module paths (e.g. `mmlct = "mml_cloud_courier.cli.__main__:main"`).

- [ ] **Step 3: Rename the pip project and description (manual edits, not covered by the sweep)**

In `pyproject.toml` `[project]`:

```toml
name = "mml-cloud-courier"
description = "MML Cloud Courier: verified, resumable file transfers between Windows workstations and Google Cloud Storage"
```

- [ ] **Step 4: Reinstall (old dist out, new dist in)**

```powershell
.venv\Scripts\python -m pip uninstall -y mml-cloud-transfer
.venv\Scripts\python -m pip install -e ".[dev]"
```

(Without the uninstall, the stale `mml-cloud-transfer` editable hooks linger in site-packages.)

- [ ] **Step 5: Verify no stragglers**

```powershell
git grep -n "mml_cloud_transfer" -- ':!docs/superpowers'
```

Expected: exactly ONE hit — `docs/gui.md:3` (`python -m mml_cloud_transfer.gui`; Task 6 rewrites that file). Also `git grep -n "mml-cloud-transfer" -- ':!docs/superpowers'` → no hits.

- [ ] **Step 6: Full suite**

`.venv\Scripts\python -m pytest` → exactly 526 passed, 1 skipped, 12 deselected (cross-check with `-v` if the summary line is missing).

- [ ] **Step 7: Commit**

```powershell
git add -A
git commit -m "refactor: rename package mml_cloud_transfer -> mml_cloud_courier (pip: mml-cloud-courier)"
```

---

### Task 2: Console-script names + CLI-facing command strings

**Files:**
- Modify: `pyproject.toml:26-31` (script names), `src/mml_cloud_courier/cli/__main__.py:40` (prog/description), `src/mml_cloud_courier/cli/transfer_command.py:162,194`, `src/mml_cloud_courier/service/app.py:282`, `src/mml_cloud_courier/cli/profile_command.py:1`, `src/mml_cloud_courier/gui/__main__.py:1`, `src/mml_cloud_courier/service/windows_service.py:1`, plus any test asserting these hint strings (grep in Step 3)

**Interfaces:**
- Consumes: package `mml_cloud_courier` (Task 1).
- Produces: console scripts `mmlcc`, `mmlcc-gui`, `mmlcc-service`; argparse `prog="mmlcc"`. Tasks 6–7 rely on these exact names.

- [ ] **Step 1: Rename the entry points in `pyproject.toml`**

```toml
[project.scripts]
mmlcc = "mml_cloud_courier.cli.__main__:main"
mmlcc-service = "mml_cloud_courier.service.windows_service:main"

[project.gui-scripts]
mmlcc-gui = "mml_cloud_courier.gui.__main__:main"
```

- [ ] **Step 2: Update prog and CLI description**

`cli/__main__.py:40` → `parser = argparse.ArgumentParser(prog="mmlcc", description="MML Cloud Courier")`

- [ ] **Step 3: Update every command-hint string**

Find them all (run from worktree root; expected sites listed below, but trust the grep):

```powershell
git grep -n "mmlct" -- src tests pyproject.toml
```

Rewrite the CLI/command-name hits (leave env vars `MMLCT_` for Task 3 and wire identifiers `.mmlct-preflight` / `mmlct-gate` / `.mmlct.tmp` / `mmlct-sha256` / `mmlct-connectivity-probe` / `project="mmlct"` / thread names for Tasks 3/5):
- `cli/transfer_command.py:162` → `'mmlcc status --service-url ...'`
- `cli/transfer_command.py:194` → `mmlcc resume --db ...`
- `service/app.py:282` → `(mmlcc resume --job-id ...)`
- `cli/profile_command.py:1` docstring → `"""mmlcc profile subcommands.`
- `gui/__main__.py:1` docstring → ``"""GUI entry point: `mmlcc-gui`."""``
- `service/windows_service.py:1` docstring → `"""pywin32 Windows Service wrapper: mmlcc-service install|start|stop|remove.`
- Matching test assertions on these strings (the grep will show them, e.g. in `tests/cli/test_transfer_cli.py`).

- [ ] **Step 4: Reinstall to regenerate entry points**

```powershell
.venv\Scripts\python -m pip install -e ".[dev]"
```

- [ ] **Step 5: Verify the new scripts exist and old ones are gone**

```powershell
.venv\Scripts\mmlcc.exe --help
Test-Path .venv\Scripts\mmlct.exe   # expect False
```

`mmlcc --help` must print `usage: mmlcc ...`.

- [ ] **Step 6: Full suite** — exactly 526 passed, 1 skipped, 12 deselected.

- [ ] **Step 7: Commit**

```powershell
git add -A
git commit -m "refactor: console scripts mmlct* -> mmlcc*; CLI prog and command hints"
```

---

### Task 3: Environment variables MMLCT_* → MMLCC_*

**Files:**
- Modify (via sweep): `src/mml_cloud_courier/{service/config.py, service/__main__.py, gui/session.py, gui/connection_dialogs.py, auth/oauth_flow.py, cli/__main__.py, cli/profile_command.py}`, `tests/{conftest.py, gui/conftest.py, gui/test_session.py, service/test_config.py, auth/test_oauth_flow.py, cli/test_profile_cli.py, cli/test_transfer_cli.py, cli/test_service_mode.py, gcs/test_real_bucket_fixture.py, tools/preflight-gcs.ps1}`, `pyproject.toml:45` (real_bucket marker text)

**Interfaces:**
- Consumes: nothing new.
- Produces: env vars `MMLCC_DATA_DIR`, `MMLCC_SERVICE_URL`, `MMLCC_TOKEN_FILE`, `MMLCC_OAUTH_CLIENT`, `MMLCC_TEST_BUCKET`, `MMLCC_TEST_PREFIX`, `MMLCC_FAKE_GCS`. Task 7 recreates the user-scoped OAuth var under the new name.

- [ ] **Step 1: Sweep**

```python
PAIRS = [(b"MMLCT_", b"MMLCC_")]
```

- [ ] **Step 2: Verify**

```powershell
git grep -n "MMLCT_" -- ':!docs/superpowers'
```

Expected: hits ONLY in `docs/gui.md` (rewritten in Task 6). None in `src/`, `tests/`, `pyproject.toml`.

- [ ] **Step 3: Full suite** — exactly 526 passed, 1 skipped, 12 deselected. (Tests set/unset these vars via monkeypatch; the sweep renames both the setters and the readers, so behavior is unchanged.)

- [ ] **Step 4: Commit**

```powershell
git add -A
git commit -m "refactor: env vars MMLCT_* -> MMLCC_*"
```

---

### Task 4: Windows service identity, data dir, product display strings

**Files:**
- Modify (via sweep + one manual edit): `src/mml_cloud_courier/service/windows_service.py:27-28,40` , `src/mml_cloud_courier/gui/service_control.py:8`, `src/mml_cloud_courier/service/config.py:4,24`, `src/mml_cloud_courier/service/__main__.py:15`, `src/mml_cloud_courier/service/app.py:156`, `src/mml_cloud_courier/service/host.py:50,114`, `src/mml_cloud_courier/gui/{main_window.py:64,281, tray.py:26,36,57,102, session.py:33, __main__.py:16}`, `src/mml_cloud_courier/auth/dpapi.py:22`, `tests/service/test_windows_service.py:16`

**Interfaces:**
- Consumes: nothing new.
- Produces: `SERVICE_NAME = "MMLCloudCourier"` (both `windows_service.py` and `service_control.py`), `DISPLAY_NAME = "MML Cloud Courier Service"`, `default_data_dir()` → `%ProgramData%\MML Cloud Courier`. Task 7's install/verify depends on these exact values.

- [ ] **Step 1: Sweep**

```python
PAIRS = [
    (b"MMLCloudTransfer", b"MMLCloudCourier"),
    (b"MML Cloud Transfer", b"MML Cloud Courier"),
]
```

This covers both `SERVICE_NAME` constants, `DISPLAY_NAME`, the data dir in `config.py:24`, every window/tray/banner/session string, the FastAPI title, host prints, the DPAPI description, and the `test_windows_service.py` assertion.

- [ ] **Step 2: Rename the service class (manual — different casing, not in the sweep)**

`windows_service.py`: `class MmlctService(...)` → `class MmlccService(...)`, and the factory's `return MmlctService` → `return MmlccService`.

- [ ] **Step 3: Verify**

```powershell
git grep -nE "MMLCloudTransfer|MML Cloud Transfer|Mmlct" -- ':!docs/superpowers'
```

Expected: hits ONLY in `docs/gui.md` (Task 6).

- [ ] **Step 4: Full suite** — exactly 526 passed, 1 skipped, 12 deselected.

- [ ] **Step 5: Commit**

```powershell
git add -A
git commit -m "refactor: service MMLCloudCourier, data dir 'MML Cloud Courier', display strings"
```

---

### Task 5: Transient wire identifiers (probe segment, gate segment, slice temps, metadata key, misc)

All remaining lowercase `mmlct` occurrences in `src/` and `tests/` are exactly this task's targets, so one sweep pair finishes the job. The probe segment moves with BOTH skip-rule call sites in the same commit: `engine/runner.py:105` and `service/app.py:674` both consume `PROBE_SEGMENT` from `auth/preflight.py:25` — the constant is the single source of truth, so changing it changes both gates atomically; the tests that hard-code the literal move here too.

**Files:**
- Modify (via sweep): `src/mml_cloud_courier/{auth/preflight.py:25,95, gcs/uploader.py:84,133,264,363,364, gcs/client.py:55,80, service/worker.py:298, service/host.py:60,63, gui/workers.py:48,77,103}`, `tests/{conftest.py:119,141, auth/test_preflight.py:47, service/test_profiles_api.py:314, engine/test_runner_emulator.py:101, gcs/test_uploader_sliced.py:36,37,59,123, gcs/test_compose_slices_generation_pinning.py:27, gcs/test_uploader_single_shot.py:56, gcs/test_uploader_resumable.py:160, gcs/test_real_bucket_protocol.py:151,153,300, cli/test_real_bucket_gate.py:164, gcs/test_real_bucket_fixture.py:16,21,29,35,36, tools/preflight-gcs.ps1}`

**Interfaces:**
- Consumes: nothing new.
- Produces: `PROBE_SEGMENT = ".mmlcc-preflight"`, `GATE_SEGMENT = "mmlcc-gate"`, slice temp names `<object>.mmlcc.tmp/<nnnn>`, metadata `{"mmlcc-sha256": ...}`. No later task consumes these directly; the final grep (Task 6) requires them.

- [ ] **Step 1: Pre-sweep audit** — confirm the remaining lowercase hits are all expected classes (probe/gate/tmp/sha256/connectivity/project/thread names/ps1 examples):

```powershell
git grep -n "mmlct" -- src tests pyproject.toml
```

If anything outside those classes appears, STOP and report it — do not sweep blind.

- [ ] **Step 2: Sweep**

```python
PAIRS = [(b"mmlct", b"mmlcc")]
```

(Safe by construction: Tasks 1–4 already consumed every other `mmlct` spelling; Step 1 proved what remains.)

- [ ] **Step 3: Verify zero lowercase hits**

```powershell
git grep -in "mmlct" -- src tests pyproject.toml
```

Expected: no output (`-i` also proves no `MMLCT` residue).

- [ ] **Step 4: Full suite** — exactly 526 passed, 1 skipped, 12 deselected. The emulator tests exercise the new probe/gate/temp names end-to-end (upload, compose, sweep, preview skip-rule, preflight cleanup).

- [ ] **Step 5: Commit**

```powershell
git add -A
git commit -m "refactor: wire identifiers -> .mmlcc-preflight, mmlcc-gate, .mmlcc.tmp, mmlcc-sha256"
```

---

### Task 6: Docs, rename note, full-tree verification

**Files:**
- Rewrite: `docs/gui.md`
- Create: `docs/courier-rename.md`
- Move: `docs/superpowers/2026-08-05-phase3-gate-record.md` → `docs/superpowers/gates/2026-08-05-phase3-gate-record.md` (content untouched — it is a gate record that predates the `gates/` directory; moving it puts it under the history carve-out so the final grep passes without rewriting history)

**Interfaces:**
- Consumes: every new name from Tasks 1–5 (see canonical table).
- Produces: the rename note the done-criteria grep excludes; a fully-clean tree.

- [ ] **Step 1: Rewrite `docs/gui.md`** — full replacement:

```markdown
# MML Cloud Courier — GUI notes

Launch: `mmlcc-gui` (or `python -m mml_cloud_courier.gui`). Requires the
`gui` extra (`pip install -e ".[gui]"`; dev installs already include it).

The GUI is a thin client of the Windows service. It keeps no transfer
state: closing it (which minimizes to the tray) or logging off does not
affect running jobs; reopening re-renders everything from the service.

Discovery: the service URL and bearer token come from the service data
directory (`%ProgramData%\MML Cloud Courier`, or `MMLCC_DATA_DIR`), with
`MMLCC_SERVICE_URL` / `MMLCC_TOKEN_FILE` as explicit overrides (used by
tests). If the token is unreadable the window says so; the account needs
read access to the data directory (the installer will own this grant in
Phase 6).

Banner "service is not running": the Start button asks for elevation via
UAC (`sc start MMLCloudCourier`). Everything else in the GUI works
unelevated.

OAuth client configuration for the Google sign-in path comes from
`MMLCC_OAUTH_CLIENT` or a browsed `client_secret_*.json` until Phase 6
packages a client ID.
```

- [ ] **Step 2: Move the misplaced gate record**

```powershell
git mv docs/superpowers/2026-08-05-phase3-gate-record.md docs/superpowers/gates/2026-08-05-phase3-gate-record.md
```

- [ ] **Step 3: Create `docs/courier-rename.md`**

```markdown
# Renamed: MML Cloud Transfer → MML Cloud Courier (2026-08-07)

Decided 2026-08-07, executed the same week: the product AND every
identifier renamed in one clean break — pre-1.0, no external users, no
compatibility aliases.

| | Old | New |
|---|---|---|
| pip name | mml-cloud-transfer | mml-cloud-courier |
| package | mml_cloud_transfer | mml_cloud_courier |
| console scripts | mmlct / mmlct-gui / mmlct-service | mmlcc / mmlcc-gui / mmlcc-service |
| env vars | MMLCT_* | MMLCC_* |
| Windows service | MMLCloudTransfer | MMLCloudCourier |
| data dir | %ProgramData%\MML Cloud Transfer | %ProgramData%\MML Cloud Courier |
| probe segment | .mmlct-preflight | .mmlcc-preflight |
| gate segment | mmlct-gate | mmlcc-gate |
| slice temp infix | .mmlct.tmp/ | .mmlcc.tmp/ |
| audit metadata key | mmlct-sha256 | mmlcc-sha256 |

Historical records under `docs/superpowers/{specs,plans,gates}` keep the
old names on purpose — they document what actually happened. Objects
uploaded before the rename keep their `mmlct-sha256` metadata key; nothing
reads that key back (verification recomputes hashes locally).

The live service was reinstalled as MMLCloudCourier and the data
directory renamed in place (same volume, ACLs and machine-scope DPAPI
blobs unaffected); job history 1–15 carried over intact.
```

- [ ] **Step 4: Final full-tree verification (the done-criteria greps)**

```powershell
git grep -inE "mml_cloud_transfer|mml-cloud-transfer|mmlct" -- ':!docs/superpowers/specs' ':!docs/superpowers/plans' ':!docs/superpowers/gates' ':!docs/courier-rename.md'
git grep -nE "MML Cloud Transfer|MMLCloudTransfer" -- ':!docs/superpowers/specs' ':!docs/superpowers/plans' ':!docs/superpowers/gates' ':!docs/courier-rename.md'
```

Expected: BOTH commands print nothing. (git grep searches tracked files only, so `.venv`, `.git`, caches, and the emulator exe are naturally excluded.)

- [ ] **Step 5: Full suite, both ways**

`.venv\Scripts\python -m pytest` AND the `-v` cross-check → exactly 526 passed, 1 skipped, 12 deselected.

- [ ] **Step 6: Commit**

```powershell
git add -A
git commit -m "docs: gui.md under new names; courier rename note; file phase3 gate record under gates/"
```

---

## Merge (main session)

After Task 6 passes review: use superpowers:finishing-a-development-branch to merge `courier-rename` into `master`. Do not start Task 7 until the merge is on `master` and the user is present — Task 7 is the only task that touches the live install.

---

### Task 7: Live install migration (merged master; user at the keyboard for every elevated step)

The live service `MMLCloudTransfer` (LocalSystem-adjacent `.\pmaho`, auto-start) is hosted by the MAIN repo's `.venv\Scripts\python.exe` running `src\mml_cloud_transfer\service\windows_service.py` by absolute path. The merge deletes that path, so reinstalling the service is mandatory, not optional. Order matters: the stop/remove uses the OLD entry points, so it happens BEFORE the merge lands in the main venv.

**Files:** none in-repo (live-machine state only).

**Interfaces:**
- Consumes: `mmlcc` / `mmlcc-service` / `mmlcc-gui` entry points (Task 2), `MMLCloudCourier` + `MML Cloud Courier` data dir (Task 4).

- [ ] **Step 1: Stale-active-job check (old CLI, service still running).** `.venv\Scripts\mmlct.exe status` against the live service (port 47821). All jobs (1–15) must be in terminal states — none active/queued/paused mid-transfer. If any are active, wait for completion or have the user decide; do NOT proceed with a job in flight (protects both the DB and any `.mmlct.tmp` slice temps).
- [ ] **Step 2 (ELEVATED — user):** stop and remove the old service:

```powershell
sc.exe stop MMLCloudTransfer
.venv\Scripts\mmlct-service.exe remove    # fallback: sc.exe delete MMLCloudTransfer
sc.exe query MMLCloudTransfer             # expect: service does not exist
```

- [ ] **Step 3: Merge + reinstall in the main venv** (unelevated):

```powershell
git merge courier-rename        # per finishing-a-development-branch
.venv\Scripts\python -m pip uninstall -y mml-cloud-transfer
.venv\Scripts\python -m pip install -e ".[dev]"
Remove-Item -Recurse -Force src\mml_cloud_transfer -ErrorAction SilentlyContinue   # stray __pycache__ leftover only; the tracked files moved in the merge
.venv\Scripts\mmlcc.exe --help  # sanity: new entry point resolves
```

- [ ] **Step 4 (ELEVATED — user): rename the data directory** (service is stopped; same-volume rename preserves SID-based ACLs; machine-scope DPAPI blobs are path-independent):

```powershell
Rename-Item "C:\ProgramData\MML Cloud Transfer" "MML Cloud Courier"
```

- [ ] **Step 5 (ELEVATED — user): install and start the new service** (install auto-adds `--startup auto` and the restart-on-failure policy):

```powershell
.venv\Scripts\mmlcc-service.exe install
sc.exe start MMLCloudCourier
sc.exe qc MMLCloudCourier       # verify: AUTO_START, correct ImagePath (venv python + new module path)
```

- [ ] **Step 6: Verify the migrated install:**

```powershell
Invoke-RestMethod http://127.0.0.1:47821/health
.venv\Scripts\mmlcc.exe profile list
.venv\Scripts\mmlcc.exe status          # jobs 1-15 present on the migrated DB
```

Then launch `mmlcc-gui`: window title "MML Cloud Courier", full job history visible, no token/banner errors. Confirm the c577c14 scheduled-event feature is live (first service restart since it merged): a scheduled job's timeline shows the "waiting until" event.

- [ ] **Step 7: Migrate the user-scoped OAuth env var (if present):**

```powershell
$old = [Environment]::GetEnvironmentVariable("MMLCT_OAUTH_CLIENT", "User")
if ($old) {
  [Environment]::SetEnvironmentVariable("MMLCC_OAUTH_CLIENT", $old, "User")
  [Environment]::SetEnvironmentVariable("MMLCT_OAUTH_CLIENT", $null, "User")
}
```

- [ ] **Step 8: Confirm settings carry-over.** `Get-Content "C:\ProgramData\MML Cloud Courier\settings.json"` still shows `"file_workers": 6` (adopted; carried as-is by the directory rename — no edit).

---

## Out of scope (deliberate)

- **Historical records** under `docs/superpowers/{specs,plans,gates}` keep every old name — they are the record of what happened.
- **The repo folder name** `C:\Users\pmaho\Documents\VibeCode\mml_cloud_transfer` stays: the venv's `pyvenv.cfg` and the reinstalled service's ImagePath bake in absolute paths, so renaming the folder would break both. Revisit when the private GitHub repo (`pmahoney-noaa/mml-cloud-courier`) is created.
- **Deferred after this lands:** custom icon set (whale-fluke-into-cloud), private GitHub repo, Phase 6 packaging under the new name.

## Done when

- Full suite green under the new names: exactly 526 passed, 1 skipped, 12 deselected.
- `mmlcc-gui` launches against the reinstalled `MMLCloudCourier` service with jobs 1–15 intact.
- The two Task 6 Step 4 greps return nothing — old names survive ONLY under `docs/superpowers/{specs,plans,gates}` and `docs/courier-rename.md`.
