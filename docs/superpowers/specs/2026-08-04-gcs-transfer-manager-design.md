# MML Cloud Transfer — Design

**Date:** 2026-08-04
**Status:** Approved for planning
**Type:** Greenfield

## Problem

Researchers move multi-terabyte datasets between Windows workstations and a Google Cloud
Storage bucket. Two things go wrong today:

1. Transfers started in the evening do not survive the night. IT policy logs off idle
   sessions, and network or VPN connections drop.
2. When a transfer does finish, nobody can prove it finished *correctly*. There is no
   per-file record showing what was sent, what arrived, and whether the two match.

The users are not technical. They need to authenticate to a bucket, start a transfer,
and — most importantly — be told unambiguously in the morning whether it worked.

## Goals

- Transfers survive user logoff, network interruption, and service or machine restart.
- Every file carries a verifiable checksum record, and every job ends with an explicit
  COMPLETE or INCOMPLETE verdict.
- Resuming an interrupted transfer is one click and re-sends only what is missing.
- A non-technical user can configure a bucket connection, run a transfer, and understand
  any failure without reading a log file.

## Non-Goals (v1)

Deliberately excluded. Each is a plausible future addition, not an oversight.

- Bandwidth throttling and scheduled bandwidth windows
- Two-way sync, mirroring, or deletion propagation
- Multi-machine coordination or a shared job queue
- Non-Windows platforms
- Managing GCS object versioning, lifecycle, or storage classes
- Encryption beyond what GCS provides at rest and in transit

## Decisions Made

| Decision | Choice |
| --- | --- |
| Job model | Explicit one-shot jobs, both directions. No continuous sync. |
| Transfer engine | Custom Python engine on `google-cloud-storage` |
| Process model | Windows Service + thin Qt client |
| Verification | CRC32C end-to-end, whole-object verify, completeness audit; SHA-256 opt-in per job |
| Auth | Both service account keys and user OAuth |
| GUI | PySide6 desktop app, hybrid grouped-list / detail-pane layout |
| In scope | Job queue, scheduled start time, multiple bucket profiles |
| Python | 3.12 (see Risks) |

### Why a custom engine rather than wrapping `gcloud storage`

Wrapping the official CLI would inherit Google's tested parallelism and retry logic for
much less code. It was rejected because the central requirement is a trustworthy per-file
audit record, and the CLI's progress output is human-readable text, not a stable contract.
Producing the manifest would mean re-listing the bucket afterwards to reconstruct what the
CLI did — which is most of the custom engine anyway, built on a weaker foundation. It also
adds a Google Cloud SDK install dependency to every workstation and a second, separate
auth system.

Google Storage Transfer Service with on-prem agents was also considered and rejected: the
agents require Docker on locked-down Windows workstations, the agent-pool and IAM setup is
far outside non-technical-user territory, and the ad-hoc bidirectional job model fits it
poorly.

## Architecture

```text
┌─────────────────┐   HTTP + SSE over 127.0.0.1   ┌──────────────────────┐
│  Qt GUI client  │ ────────────────────────────► │  Windows Service     │
│  (user session) │ ◄──────────────────────────── │  (session 0)         │
└─────────────────┘      live progress events     │  ┌────────────────┐  │
        │                                          │  │ scheduler      │  │
        │ native folder picker,                    │  │ job worker     │  │
        │ browser OAuth flow                       │  │ FastAPI        │  │
        ▼                                          │  └───────┬────────┘  │
   (hands results to service)                      └──────────┼───────────┘
                                                              ▼
                                                    SQLite (WAL) + GCS
```

The service is the only component that touches GCS or reads source files. The GUI holds no
transfer state: closing it, logging off, or rebooting the workstation does not affect a
running job, and reopening it re-renders from the service's database. The overnight
requirement falls out of this structure rather than needing special handling.

### Why the engine cannot live in the GUI process

Windows terminates every process in a session at logoff, including detached ones. A
background process started from the interactive session would not survive the policy
logoff that motivates this project. A Windows Service runs in session 0 and does.

### IPC

FastAPI bound to `127.0.0.1`, authenticated with a bearer token stored in an
ACL-restricted file under `%ProgramData%`. Localhost is not access control on a multi-user
machine. Live progress streams over Server-Sent Events; everything else is REST.

### Concurrency

One job runs at a time, taken from the queue. Parallelism happens *within* a job: a
process pool for chunked large-file transfers, a thread pool for batches of small files.
Serializing jobs keeps throughput predictable and progress reporting honest.

**Queue ordering** is FIFO by creation time. A job with a scheduled start is not eligible
until that time passes, after which it joins the queue normally — it does not preempt a
running job. If the machine or service was down at the scheduled time, the job becomes
eligible immediately at the next service start rather than being skipped, since a missed
overnight window should still run rather than silently disappear.

### Module boundaries

```text
mml_cloud_transfer/
  core/       pure logic — models, manifest planning, hashing, crc32c_combine,
              path normalization, error taxonomy. No GCS or GUI imports.
  store/      SQLite schema, migrations, repository, resume queries
  gcs/        authenticated client factory, uploader, downloader, verifier
  service/    Windows Service host, scheduler, job worker, FastAPI app
  auth/       profiles, DPAPI credential storage, OAuth flow helpers
  gui/        PySide6 client, API client, view-models, views
  cli/        headless control over the same service API
```

`core` has no I/O dependencies, so the correctness-critical logic is testable without a
network or a Windows service. The CLI exists for testing and for IT scripting; it is a
client of the same API the GUI uses, not a second code path.

## Data Model

SQLite in WAL mode. Every state transition is committed, so a process kill never loses
more than the in-flight chunk.

- **`profiles`** — named connections: project, bucket, auth type, credential reference,
  optional default prefix
- **`jobs`** — direction, profile, source root, destination prefix, status, scheduled
  start, options (audit hash on/off), rollup counters
- **`job_files`** — one row per file: relative path, size, mtime, state, local CRC32C,
  remote CRC32C, optional SHA-256, object generation, attempt count, last error,
  heartbeat timestamp
- **`file_slices`** — for large files: slice index, byte range, persisted resumable
  session URI, temp object name, state
- **`events`** — append-only timeline feeding both the GUI and the final report

**File states:** `pending` → `transferring` → `transferred` → `verified`, plus `failed`,
`skipped`, `changed`, and `quarantined`.

### Job lifecycle

Every job runs **scan → plan → transfer → verify**. The scan walks the source and writes
one `job_files` row per file before any bytes move, so totals and progress are accurate
from the start. On large trees the scan takes minutes and is reported as its own progress
phase.

## Transfer Strategy

Method is chosen by file size so that resume granularity matches the cost of losing
progress.

| Size | Method | Resume unit |
| --- | --- | --- |
| ≤ 8 MiB | Single-shot upload, CRC32C precomputed and server-verified | Whole file |
| 8 MiB – 1 GiB | One resumable session, URI persisted to `job_files` | Committed byte offset |
| > 1 GiB | Sliced parallel resumable uploads, then `compose` | Per-slice offset |

For the large path, slice size is `max(1 GiB, ceil(size / 32))`. This guarantees at most
32 components, so a single `compose` call suffices and no hierarchical composition is
needed. A 500 GB file becomes 32 slices of roughly 16 GiB, each uploaded in parallel via
its own resumable session and each independently resumable.

Temporary slice objects are deleted after a successful compose. A bucket lifecycle rule on
the temp prefix acts as a safety net for orphans left by a hard crash.

Downloads mirror this: ranged GETs into a `.part` file with completed ranges recorded in
`file_slices`, and an atomic rename only after whole-file verification.

### Conflict and skip rules

These rules apply identically in both directions, with "destination object" meaning a local
file on download.

- **Destination exists with matching size and CRC32C** → the file is marked `skipped`. This
  is the mechanism that makes re-running a job cheap and makes resume correct.
- **Destination exists with different content** → overwritten. Uploads use an
  `if_generation_match` precondition captured at plan time so two concurrent writers cannot
  silently clobber each other; a precondition failure is reported as a conflict rather than
  retried blindly.
- **Source file changed between scan and transfer** (size or mtime differs) → the file is
  marked `changed`, its planned checksum is discarded, and it is re-queued once with fresh
  metadata. If it changes again on that second attempt it is marked `failed` with a
  `source_changed` category, because a file being actively written cannot be transferred
  coherently. `changed` is therefore a transient state and never appears in a final report.
- **Empty directories** are not represented. GCS has no directories, and recreating them on
  download is not attempted.
- **Symlinks, junctions, and reparse points** are skipped and reported, rather than
  followed. This avoids cycles and duplicated data.

### Resume is not a special mode

The worker always selects files not yet `verified` or `skipped` and continues. A job
interrupted by a power cut resumes by exactly the same path as one the user paused. Files
left in `transferring` by a crash are detected via a stale heartbeat and reset to
resumable on service startup.

## Verification

Three layers, each catching something the others cannot.

**Layer 1 — in-flight.** CRC32C is computed as bytes stream off disk and sent with the
upload. GCS rejects a corrupted write server-side and the client raises `DataCorruption`.
Costs nothing extra, since the bytes are being read anyway.

**Layer 2 — whole-object.** After finalize, or after `compose` for sliced files, the
object's `crc32c`, `size`, and `generation` are fetched and compared against locally
computed whole-file values. This layer exists because per-slice checksums do not prove the
assembled object is correct — a compose that stitched slices in the wrong order would pass
Layer 1 and fail here. A file becomes `verified` only after Layer 2 passes.

Obtaining the whole-file CRC32C without re-reading the file requires combining the slice
CRC32Cs arithmetically. This is a small GF(2) operation implemented in
`core/crc32c_combine.py` and property-tested against straight full-file hashes.

**Layer 3 — completeness audit.** At job end the destination prefix is re-listed and
reconciled against the manifest: every planned file present, at the expected size and
checksum, none missing. This is the layer that catches files silently skipped due to a
permissions error, a file lock, or an over-long path — the most common real-world
"the transfer failed."

The scan records size and mtime; a source file that changes between scan and transfer is
marked `changed` and reported rather than shipped silently.

**Optional SHA-256** is computed in the same single read pass, so enabling it costs CPU but
no additional I/O. It is stored in `job_files` and stamped into the object's custom
metadata so the hash travels with the object.

### Why not read-back verification

Re-downloading every object to re-hash it doubles transfer time and egress cost. It
defends only against corruption at rest in GCS, which Google already re-validates
continuously, and a one-time check at transfer time gives no protection against future
rot. The three layers above cover every failure mode that read-back would catch, at no
extra bandwidth.

### Why CRC32C rather than MD5

MD5 is unavailable on composite and multipart objects, and is not supported by chunked
download at all. It is a dead end for the large-file path. CRC32C is stored by GCS for
every object and is the algorithm the client library validates against. SHA-256 covers the
audit and chain-of-custody case where an external party must verify independently.

## Validation Summary

Every job writes a report folder containing:

- `summary.json` — machine-readable job result
- `manifest.csv` — one row per file: path, object name, size, local CRC32C, remote CRC32C,
  optional SHA-256, final state, timestamps, error
- `report.html` — self-contained, so it can be emailed without broken attachments

The summary records job identity, profile and bucket, direction, start and end times,
duration, counts by state, total bytes, average throughput, and an explicit verdict.

**Verdict rule:** a job is **COMPLETE** only if every planned file is `verified` or
deliberately `skipped` *and* the Layer 3 audit reconciles. Anything else is **INCOMPLETE**,
with failures grouped by cause and a one-click "Resume remaining" action. The tool never
reports an ambiguous "done"; that ambiguity is what makes users distrust large transfers.

## Authentication and Credentials

Both paths converge on a credential the *service* can use while nobody is logged on.

**Service account key.** The user browses to the `.json` in the setup wizard. The GUI hands
it to the service, which validates it by performing a real operation against the target
bucket, so a wrong key or missing IAM grant surfaces during setup rather than overnight.
The user is then told they may delete the original file.

**User OAuth.** The browser flow must run in the interactive session, so the GUI performs
it using the installed-app flow with a loopback redirect, then hands the refresh token to
the service. The service refreshes access tokens autonomously thereafter. This ships an
OAuth desktop client ID with the application; for installed apps the accompanying secret is
not genuinely secret, which is standard but stated here explicitly.

**Storage.** Credentials are encrypted with DPAPI at machine scope and written to an
ACL-restricted directory under `%ProgramData%`, readable only by the service account and
Administrators.

**Security limits, stated plainly.** Machine-scope encryption means the file ACL is the
real access control; the encryption protects against a stolen backup or disk image, not
against someone who already holds local admin on the machine. A stored refresh token lets
that machine act as that Google user until the token is revoked. This is inherent to the
requirement that transfers continue after logoff.

**Recommendation surfaced in the UI:** use service accounts with least-privilege IAM —
object-level access to a single bucket — for anything unattended and recurring. Reserve
user OAuth for interactive or short-lived setups.

Each profile runs a preflight permission check appropriate to the job direction, producing
messages like "this credential can list and read but cannot write to `gs://bucket/prefix`".

## Path Handling

The service runs under its own identity and therefore does not inherit the user's mapped
drive letters, share credentials, or VPN connection.

- Source paths are stored and used in UNC form. The GUI's folder picker returns a path such
  as `Z:\imaging\run47`; the GUI resolves it to its UNC equivalent and displays both.
- Before a job can be queued, the service tests reachability of both source and destination
  under its own identity. Unreachable paths are rejected at creation time with an
  explanation, never discovered mid-transfer.
- Filesystem access uses `\\?\`-prefixed paths so files beyond 260 characters do not fail.

Data is expected on local disks or LAN shares. The service runs as a domain account granted
share access; LocalSystem is the default only for local-disk-only installations. Data
reachable solely over a per-user VPN is outside what the service can access after logoff.

## GUI

**Main window.** A left rail grouped by status with **Needs attention** pinned at the top,
followed by Running, Queued, and a collapsed Completed group. The right pane shows the
selected job across four tabs: Progress, Files, Errors, Summary. Failures are visible from
the rail; detail is one click away without crowding the list.

**New Transfer wizard**, four steps: direction → connection → source and destination →
options and review. The review step runs a scan preview so the user sees "8,412 files,
6.6 TB" before committing. Options include job name, scheduled start time, and the SHA-256
audit hash checkbox.

**Connection setup** branches to service account key or Google sign-in, validates against
the real bucket, and reports the preflight result in plain language.

**Errors tab groups by cause, not by file.** Three thousand failures from one expired
credential must read as one problem. Categories — permission denied, file locked, path too
long, checksum mismatch, network, quota — expand to their files and carry group-level
actions: retry this group, skip permanently, copy list.

**Progress.** The service pushes SSE updates throttled to roughly twice per second. The
file table is virtualized so million-row jobs do not stall the UI. For a large file in
flight the panel shows slice-level progress ("slice 23 of 32, 12 verified"), which is what
makes resume legible to a user.

**Tray.** Closing the window minimizes to tray and stops nothing, with a balloon
notification when a job finishes or needs attention. If the service is not running, the GUI
shows a banner with a start action rather than empty widgets. Advanced settings —
concurrency, slice size — live in a Settings dialog off the default path.

## Error Handling

**One bad file never ends a job.** Transient failures (429, 5xx, connection resets) retry
with exponential backoff and jitter up to five attempts per file within a single job run;
the file is then marked `failed` with a category and the job continues. Attempt counts are
persisted, and a resume grants a fresh allowance of five, so a file that failed only
because the network was down overnight is retried the next time the job runs.

**Terminal failures are treated by kind.** A 401 or 403 means the credential is wrong, so
the job pauses immediately and escalates rather than failing ten thousand files identically.
A 404 on download is terminal for that file alone.

**Sustained network loss** moves a job to `stalled`: still alive, retrying on a slow
cadence. A VPN blip at 1am costs minutes, not the night.

**Error taxonomy** is a first-class enum in `core` mapping exceptions to a category, a
plain-language message, and a suggested action. This single mapping feeds the grouped
Errors tab, tray notifications, and the report — one source of truth for what went wrong
and what to do about it.

**Recovery.** The service is registered auto-start with restart-on-failure. On startup it
recovers jobs left in `running`, resets files stranded by a stale heartbeat, and resumes
(controlled by an auto-resume-on-startup setting, default on). A file that fails repeatedly
across multiple resume attempts is `quarantined` so the job can still reach a terminal
verdict instead of looping.

## Testing

**`core`** — fast unit tests with no I/O. Property tests for `crc32c_combine` against
straight full-file hashes; tests for slice-size arithmetic, path normalization, manifest
planning, and the error taxonomy.

**`store`** — tests against real temporary SQLite databases, including crash-recovery
simulation: kill mid-transaction, reopen, assert the job is resumable.

**`gcs`** — emulator tests (fake-gcs-server) cover broad happy paths. Emulators do not
faithfully implement `compose` or resumable-session semantics, which is exactly the
machinery this design depends on, so a smaller suite gated behind an environment variable
runs against a real bucket for compose, session resumption, and checksum behaviour. **The
real-bucket suite is a release gate, not optional.**

**Fault injection** — a transport wrapper that injects connection resets, 503 responses,
and truncated bodies at configurable points, so resume can be tested without physically
interrupting anything.

**The defining test** — interrupt-and-resume: run a job over a synthetic tree containing a
multi-gigabyte file, kill the service process mid-transfer, restart it, and assert the job
completes with every checksum matching. This proves the core promise and runs in CI against
the emulator plus manually against a real bucket.

**GUI** — thin by design. Unit tests for the API client and view-models, a pytest-qt smoke
test for the main window. No full UI automation.

## Packaging and Deployment

PyInstaller produces two executables, service and GUI. An Inno Setup installer:

- installs to Program Files
- registers the Windows Service with auto-start and restart-on-failure
- creates the `%ProgramData%` directories with restrictive ACLs
- prompts for the domain account the service runs as, required for network share access,
  defaulting to LocalSystem for local-disk-only installations
- creates a Start Menu shortcut for the GUI

Rotating logs are written to `%ProgramData%`; service lifecycle events go to the Windows
Event Log. A "collect diagnostics" button in the GUI zips logs and a schema dump, excluding
credentials.

## Delivery Phases

Each phase ends with something demonstrable, and the CLI exists from the first phase so the
engine is provable long before any GUI work begins.

1. **`core` + `store`** — models, manifest planning, hashing, `crc32c_combine`, SQLite
   schema and repository. No network. *Done when:* `mmlct scan <path>` writes a complete
   manifest and the unit suite passes, including the CRC32C combination property tests.
2. **Transfer engine** — `gcs` uploader, downloader, and verifier across all three size
   paths, with resume and all three verification layers. Development uses Application
   Default Credentials or a local key file directly; profiles come later. *Done when:* the
   CLI runs a real multi-gigabyte job end-to-end, produces a report, and passes the
   interrupt-and-resume test against a real bucket.
3. **Service** — FastAPI app, job queue, scheduler, Windows Service host, startup recovery.
   *Done when:* the CLI drives jobs entirely over the local API and a job survives killing
   the service process and logging off.
4. **Auth and profiles** — both credential paths, DPAPI storage, preflight permission and
   path-reachability checks. *Done when:* a profile created from either credential type
   works unattended after logoff.
5. **GUI** — PySide6 client against the finished API. *Done when:* a non-technical user can
   complete setup, run a transfer, and resume an incomplete one without touching the CLI.
6. **Packaging** — PyInstaller executables, Inno Setup installer, service registration,
   diagnostics bundle. *Done when:* a clean Windows machine goes from installer to
   completed verified transfer.

## Risks

**Python version.** The development machine has Python 3.14.5, but PySide6 and
`google-crc32c` wheel availability on 3.14 is likely thin. The project pins Python 3.12 to
avoid compiling C extensions on Windows. Revisit once wheels land.

**Cold-storage temp objects.** If a destination bucket is Nearline or colder, temporary
slice objects incur minimum-storage-duration charges even though they are deleted
immediately after compose. On Standard buckets this is free. The GUI warns when a profile
targets a non-Standard bucket.

**Compose component limit.** The slice-size formula keeps component count at or below 32
so a single compose call always suffices. If the exact GCS limit differs from the assumed
32, only the constant changes, not the design.

**Service identity and shares.** If IT cannot grant the service account access to the
required shares, the design's overnight guarantee does not hold for those sources. Confirm
share access during deployment planning, not after install.
