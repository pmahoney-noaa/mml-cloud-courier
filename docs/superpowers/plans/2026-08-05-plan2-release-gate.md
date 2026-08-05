# MML Cloud Transfer — Plan 2 Release Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close Plan 2's open release gate — prove against real Google Cloud Storage the four behaviours `fake-gcs-server` cannot vouch for, and prove the interrupt-and-resume promise at real scale with the default size policy.

**Architecture:** Five artifacts, no `src/` changes except one error-taxonomy mapping. A read-only PowerShell preflight discovers what GCP resources exist and emits the commands to create what is missing. A session-scoped `real_bucket_ctx` fixture owns a unique run prefix and guarantees cleanup. Four fast protocol tests (~5 MB, under a minute) each isolate one unproven behaviour. One slow scale test (~2.6 GiB) drives the CLI through a real 3-slice upload, a mid-slice process kill, and a resume. A dated gate record captures what was run and what it proved.

**Tech Stack:** Python 3.12, pytest, `google-cloud-storage` ≥3.0, `google-auth`, `requests`, PowerShell 7 (`pwsh`), the `gcloud` CLI, real GCS.

**Spec:** [2026-08-05-plan2-release-gate-design.md](../specs/2026-08-05-plan2-release-gate-design.md)

## Global Constraints

These apply to every task. Do not restate them; do not violate them.

- **Python 3.12** — interpreter `.venv/Scripts/python`.
- **This is verification work on merged code.** Nothing under `src/` changes except Task 6's one-line error-taxonomy mapping. If a task tempts you to "fix" engine behaviour, stop and report the finding instead.
- **The test is the deliverable, so the TDD loop is inverted.** There is no implementation to make a test pass. The cycle for each gate test is: write it → run without `MMLCT_TEST_BUCKET` and confirm it **skips cleanly** (this is the RED-equivalent: it proves the marker and fixture gating work) → run with the bucket set and confirm it **passes** → commit. Task 6 is the exception and uses a real RED/GREEN cycle, because it changes `core`.
- **Never run a real-bucket test without cleanup in a `finally`.** A failed assertion must not leak billable objects. `tests/cli/test_interrupt_resume.py:145` is the existing precedent.
- **The gate runs inside a scratch folder of a bucket that holds real data.** `MMLCT_TEST_PREFIX` names that folder; every real-bucket run command in this plan sets it alongside `MMLCT_TEST_BUCKET` (omit it only for a bucket dedicated to the gate). No test may write outside the fixture's `run_prefix`, and no code may delete outside a path containing the `mmlct-gate/` segment the fixture builds itself. Task 2's guard enforces this; do not weaken or bypass it.
- **`core` stays pure** — no `google.cloud.storage`, `google.auth`, `requests`, or `sqlite3` imports under `core/`.
- **`google.cloud.storage` / `google.auth` / `requests` are imported only under `gcs/` and in test files.**
- **Marker discipline:** every test touching a real bucket is marked `real_bucket`; the 2.6 GiB one is additionally marked `slow`. A plain `.venv/Scripts/python -m pytest` on a machine with no `MMLCT_TEST_BUCKET` must stay green with these skipping.
- **The 2.6 GiB test never passes `--size-policy`.** Real 1 GiB slices and real session lifetimes are its entire reason to exist.
- **Timestamps** in the gate record are UTC ISO-8601.

## File Structure

```text
tests/tools/preflight-gcs.ps1             GCP discovery, read-only, emits fix commands   (create)
tests/conftest.py                         + real_bucket_ctx session fixture              (modify)
pyproject.toml                            + slow marker                                  (modify)
tests/gcs/test_real_bucket_protocol.py    4 fast protocol tests                          (create)
tests/cli/test_real_bucket_gate.py        1 slow scale test                              (create)
src/mml_cloud_transfer/core/errors.py     400-naming-crc32c -> CHECKSUM_MISMATCH         (modify)
tests/core/test_errors.py                 + 2 taxonomy tests                             (modify)
docs/superpowers/gates/
  2026-08-05-plan2-release-gate.md        runbook + results record                       (create)
```

---

### Task 1: Preflight discovery script

A read-only, idempotent script that answers "what do I have, and what do I run next". It reports; it never provisions. Someone with no GCP setup and someone with a fully configured bucket run the same command and get different amounts of output.

**Files:**
- Create: `tests/tools/preflight-gcs.ps1`

**Interfaces:**
- Consumes: nothing from this repo. Requires `gcloud` on PATH.
- Produces: an operator-facing script `preflight-gcs.ps1 -Bucket <name> [-Prefix <path>] [-Project <id>]`. Exit 0 when every check passes, 1 when any check fails. Warnings (non-STANDARD storage class, object versioning) do not affect the exit code; a retention policy does, because it breaks teardown. On success the script prints the `$env:MMLCT_TEST_BUCKET` assignment and, when `-Prefix` was given, the `$env:MMLCT_TEST_PREFIX` assignment.

- [ ] **Step 1: Write the script**

Create `tests/tools/preflight-gcs.ps1`:

```powershell
<#
.SYNOPSIS
  Read-only preflight for the Plan 2 release gate.

.DESCRIPTION
  Reports what GCP state exists and prints the exact command to fix anything
  missing. Creates no billable resources: the one object it writes is a
  permission probe that it deletes again.

  -Prefix names a scratch folder inside an existing bucket. The gate confines
  every object it writes to that folder, so an in-use bucket is a valid target.

.EXAMPLE
  pwsh tests/tools/preflight-gcs.ps1 -Bucket mmlct-gate-test

.EXAMPLE
  pwsh tests/tools/preflight-gcs.ps1 -Bucket my-research-bucket -Prefix scratch/mmlct
#>
param(
    [Parameter(Mandatory = $true)][string]$Bucket,
    [string]$Prefix,
    [string]$Project
)

# Normalise: no leading slash, exactly one trailing slash, or empty.
$PrefixPath = if ($Prefix) { $Prefix.Trim('/') + '/' } else { "" }

$ErrorActionPreference = "Stop"
$script:Failed = $false

function Report-Ok   ($msg) { Write-Host "  OK    $msg" -ForegroundColor Green }
function Report-Warn ($msg) { Write-Host "  WARN  $msg" -ForegroundColor Yellow }
function Report-Fail ($msg, $fix) {
    Write-Host "  FAIL  $msg" -ForegroundColor Red
    Write-Host "        fix: $fix" -ForegroundColor Cyan
    $script:Failed = $true
}

function Invoke-Gcloud {
    # Runs gcloud and returns [ExitCode, Output] without throwing.
    $output = & gcloud @args 2>&1 | Out-String
    return @($LASTEXITCODE, $output.Trim())
}

Write-Host "`nPlan 2 release-gate preflight" -ForegroundColor White
Write-Host "bucket: $Bucket"
Write-Host ("scratch prefix: " + $(if ($PrefixPath) { $PrefixPath } else { "(bucket root)" }))
Write-Host ""

# 1. gcloud present
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    Report-Fail "gcloud is not on PATH" "install from https://cloud.google.com/sdk/docs/install"
    Write-Host "`nStopping: every remaining check needs gcloud.`n"
    exit 1
}
Report-Ok "gcloud is installed"

# 2. Authenticated
$code, $accounts = Invoke-Gcloud auth list --filter=status:ACTIVE --format="value(account)"
if ($code -ne 0 -or -not $accounts) {
    Report-Fail "no active gcloud account" "gcloud auth login"
} else {
    Report-Ok "authenticated as $accounts"
}

# 3. Application Default Credentials — this is what the tests actually use
$code, $null = Invoke-Gcloud auth application-default print-access-token
if ($code -ne 0) {
    Report-Fail "Application Default Credentials are not set up" `
                "gcloud auth application-default login"
} else {
    Report-Ok "Application Default Credentials work"
}

# 4. Project
if (-not $Project) {
    $code, $Project = Invoke-Gcloud config get-value project
    if ($code -ne 0 -or -not $Project -or $Project -eq "(unset)") { $Project = $null }
}
if (-not $Project) {
    Report-Fail "no project configured" "gcloud config set project <project-id>"
} else {
    Report-Ok "project is $Project"
}

# 5. Bucket exists, and its storage class
$code, $describe = Invoke-Gcloud storage buckets describe "gs://$Bucket" --format=json
if ($code -ne 0) {
    Report-Fail "bucket gs://$Bucket not found or not readable" `
                "gcloud storage buckets create gs://$Bucket --location=us-central1 --default-storage-class=STANDARD --uniform-bucket-level-access"
} else {
    Report-Ok "bucket gs://$Bucket exists"
    $meta = $describe | ConvertFrom-Json
    if ($meta.default_storage_class -ne "STANDARD") {
        Report-Warn ("storage class is $($meta.default_storage_class) — temp slice objects " +
                     "will incur minimum-storage-duration charges (spec: cold-storage risk)")
    } else {
        Report-Ok "storage class is STANDARD"
    }

    # 6. Object versioning — teardown's deletes would become noncurrent versions,
    #    so the gate's bytes would keep billing and "the bucket is clean" would
    #    be false even though every assertion passed.
    if ($meta.versioning_enabled -eq $true) {
        Report-Warn ("object versioning is ENABLED — the gate's deletes leave noncurrent " +
                     "versions that keep billing; add a noncurrent-version lifecycle rule " +
                     "or expect to purge them manually")
    } else {
        Report-Ok "object versioning is disabled"
    }

    # 7. Retention policy / bucket lock — this one is fatal rather than costly:
    #    deletes are refused, so the fixture's teardown cannot clean up and its
    #    emptiness assertion fails the whole session.
    if ($meta.retention_policy) {
        Report-Fail ("bucket has a retention policy " +
                     "($($meta.retention_policy.retention_period)s) — the gate cannot " +
                     "delete what it writes") `
                    "run the gate against a bucket without a retention policy"
    } else {
        Report-Ok "no retention policy"
    }

    # 8. Lifecycle safety net for orphaned slice temps
    $rules = $meta.lifecycle_config.rule
    $hasTmpRule = $false
    foreach ($rule in $rules) {
        if ($rule.condition.matches_prefix -and
            ($rule.condition.matches_prefix -join " ") -match "mmlct") { $hasTmpRule = $true }
    }
    if (-not $hasTmpRule) {
        Report-Warn "no lifecycle rule covering mmlct-gate/ orphans — see the gate record for the JSON"
    } else {
        Report-Ok "lifecycle rule covering mmlct objects is present"
    }
}

# 9. Write / read / compose / delete permission probe, inside the scratch prefix
if (-not $script:Failed) {
    $probePrefix = "$PrefixPath" + "mmlct-preflight/$([guid]::NewGuid().ToString('N').Substring(0,8))"
    $tmp = Join-Path $env:TEMP "mmlct-probe.bin"
    try {
        Set-Content -Path $tmp -Value "mmlct preflight probe" -NoNewline
        $code, $out = Invoke-Gcloud storage cp $tmp "gs://$Bucket/$probePrefix/a.bin"
        if ($code -ne 0) {
            Report-Fail "cannot write to gs://$Bucket" `
                        "grant roles/storage.objectAdmin on this bucket to $accounts"
        } else {
            Report-Ok "write succeeded"
            $code, $null = Invoke-Gcloud storage cp $tmp "gs://$Bucket/$probePrefix/b.bin"
            $code, $out = Invoke-Gcloud storage objects compose `
                "gs://$Bucket/$probePrefix/a.bin" "gs://$Bucket/$probePrefix/b.bin" `
                "gs://$Bucket/$probePrefix/composed.bin"
            if ($code -ne 0) {
                Report-Fail "compose is not permitted" `
                            "grant roles/storage.objectAdmin (compose needs create + get)"
            } else {
                Report-Ok "compose succeeded"
            }
        }
    } finally {
        Invoke-Gcloud storage rm --recursive "gs://$Bucket/$probePrefix" | Out-Null
        Remove-Item $tmp -ErrorAction SilentlyContinue
        Report-Ok "probe objects cleaned up"
    }
}

Write-Host ""
if ($script:Failed) {
    Write-Host "Preflight FAILED — fix the items above and run again.`n" -ForegroundColor Red
    exit 1
}
Write-Host "Preflight passed. Run the gate with:" -ForegroundColor Green
Write-Host ""
Write-Host "  `$env:MMLCT_TEST_BUCKET = `"$Bucket`""
if ($PrefixPath) {
    Write-Host "  `$env:MMLCT_TEST_PREFIX = `"$($PrefixPath.TrimEnd('/'))`""
}
Write-Host ""
exit 0
```

- [ ] **Step 2: Verify the missing-argument path**

Run: `pwsh tests/tools/preflight-gcs.ps1`
Expected: PowerShell prompts for the mandatory `-Bucket` parameter (or errors in a non-interactive shell). Press Ctrl+C. This confirms the parameter is genuinely mandatory.

- [ ] **Step 3: Run it for real**

Run: `pwsh tests/tools/preflight-gcs.ps1 -Bucket <your-bucket-name> -Prefix <scratch-folder>`
Expected: a checklist. Whatever the outcome, every FAIL line is followed by a runnable `fix:` command. Work through them until the script exits 0.

Two supported shapes:
- **A bucket dedicated to this gate** — omit `-Prefix`; the gate writes at the bucket root. Prefer single-region STANDARD, so teardown is unambiguous.
- **A scratch folder inside an existing, in-use bucket** — pass `-Prefix`; every object the gate writes lands under it. This is the expected deployment here, and it is why the teardown guard in Task 2 exists.

Record the final state in your report: which checks passed, which needed fixing, the bucket's region and storage class, and whether versioning or a retention policy is in play.

- [ ] **Step 4: Commit**

```bash
git add tests/tools/preflight-gcs.ps1
git commit -m "test: add read-only GCP preflight for the Plan 2 release gate"
```

---

### Task 2: The `real_bucket_ctx` fixture and the `slow` marker

Every subsequent task depends on this. One session-scoped fixture that owns a unique prefix and guarantees the bucket is left clean — so no individual test can leak billable objects, however it fails.

**Files:**
- Modify: `tests/conftest.py` (append the fixture)
- Modify: `pyproject.toml` (add the `slow` marker)
- Test: `tests/gcs/test_real_bucket_fixture.py` (create)

**Interfaces:**
- Consumes: `make_context` from `gcs.client`; `list_prefix`, `delete_object` from `gcs.objects`.
- Produces: pytest fixture `real_bucket_ctx` (session-scoped), yielding `tuple[GcsContext, str]` — the context and a trailing-slash-terminated `run_prefix` of the form `<base/>mmlct-gate/<YYYYmmddTHHMMSSZ>-<uuid8>/`, where `base` is the optional `MMLCT_TEST_PREFIX`. Skips with an actionable message when `MMLCT_TEST_BUCKET` is unset. Teardown deletes everything under `run_prefix` and fails the session if anything survives. Also produces the module-level helper `_gate_run_prefix(base: str) -> str` so the guard is testable without a bucket. Marker `slow` is registered.

**Why the guard exists:** teardown recursively deletes everything under `run_prefix`. That is safe only while the prefix is ours by construction. `MMLCT_TEST_PREFIX` points the gate into a bucket that holds real data, so a typo in that variable is the difference between deleting scratch and deleting someone's imaging run. The `mmlct-gate/` segment is therefore **mandatory and never operator-supplied**, and teardown asserts it before the first delete.

- [ ] **Step 1: Add the marker**

In `pyproject.toml`, extend the existing `markers` list under `[tool.pytest.ini_options]` with a third entry, leaving the other two exactly as they are:

```toml
markers = [
    "emulator: requires fake-gcs-server (tools/fake-gcs-server.exe); skipped when absent",
    "real_bucket: requires MMLCT_TEST_BUCKET and real credentials; release gate",
    "slow: multi-gigabyte; minutes, not seconds",
]
```

- [ ] **Step 2: Write the failing test**

Create `tests/gcs/test_real_bucket_fixture.py`:

```python
"""Self-checks for the release-gate fixture.

These are cheap (a few bytes) and run first, so the fixture's prefix
construction and cleanup are proven before the 2.6 GiB test trusts them.
"""

import re

import pytest

from mml_cloud_transfer.gcs.objects import get_meta, list_prefix

from tests.conftest import _gate_run_prefix

PREFIX_SHAPE = re.compile(
    r"^(?:[^/]+/)*mmlct-gate/\d{8}T\d{6}Z-[0-9a-f]{8}/$"
)


def test_the_gate_segment_is_never_operator_supplied():
    """No MMLCT_TEST_PREFIX value can produce a prefix without mmlct-gate/.

    Teardown recursively deletes everything under the run prefix. This is the
    assertion standing between a typo in that variable and someone's data.
    Runs without a bucket, so it guards every machine, not just the gate host.
    """
    for base in ("", "/", "scratch", "scratch/", "/scratch/mmlct/", "a/b/c"):
        prefix = _gate_run_prefix(base)
        assert "/mmlct-gate/" in f"/{prefix}", prefix
        assert PREFIX_SHAPE.match(prefix), prefix
        assert not prefix.startswith("/"), prefix


def test_a_prefix_confines_the_run_to_the_scratch_folder():
    assert _gate_run_prefix("scratch/mmlct").startswith("scratch/mmlct/mmlct-gate/")
    assert _gate_run_prefix("").startswith("mmlct-gate/")


@pytest.mark.real_bucket
def test_run_prefix_is_unique_and_well_formed(real_bucket_ctx):
    ctx, run_prefix = real_bucket_ctx
    assert PREFIX_SHAPE.match(run_prefix), run_prefix
    assert ctx.bucket, "the context must name the bucket under test"


@pytest.mark.real_bucket
def test_the_run_prefix_starts_empty(real_bucket_ctx):
    ctx, run_prefix = real_bucket_ctx
    # Anything here would mean a prefix collision with another run.
    assert [m.name for m in list_prefix(ctx, run_prefix)] == []


@pytest.mark.real_bucket
def test_objects_written_under_the_prefix_are_reachable(real_bucket_ctx):
    ctx, run_prefix = real_bucket_ctx
    name = f"{run_prefix}reachable.bin"
    ctx.client.bucket(ctx.bucket).blob(name).upload_from_string(b"probe")
    meta = get_meta(ctx, name)
    assert meta is not None
    assert meta.size == 5
    # Deliberately not deleted — the fixture's teardown must remove it. If
    # teardown is broken, the next session's emptiness check fails loudly.
```

- [ ] **Step 3: Run to verify it skips, then fails**

Run (with `MMLCT_TEST_BUCKET` **unset**): `.venv/Scripts/python -m pytest tests/gcs/test_real_bucket_fixture.py -v`
Expected: a collection ERROR — `ImportError: cannot import name '_gate_run_prefix' from 'tests.conftest'`. This is the RED state.

- [ ] **Step 4: Implement the fixture**

Append to `tests/conftest.py`:

```python
#: The one path segment an operator can never supply. Teardown deletes
#: everything under the run prefix, so this segment is what makes that
#: deletion safe -- see _gate_run_prefix and the guard in real_bucket_ctx.
GATE_SEGMENT = "mmlct-gate"


def _gate_run_prefix(base: str) -> str:
    """Build a unique run prefix under `base`, always inside GATE_SEGMENT.

    `base` is the operator's MMLCT_TEST_PREFIX -- a scratch folder inside a
    bucket that may hold real data. It is normalised, never trusted, and can
    never displace the gate segment.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run = f"{GATE_SEGMENT}/{stamp}-{uuid.uuid4().hex[:8]}/"
    base = base.strip().strip("/")
    return f"{base}/{run}" if base else run


@pytest.fixture(scope="session")
def real_bucket_ctx():
    """The release gate's context: a real bucket and a unique run prefix.

    Session-scoped so one prefix covers the whole gate run. Teardown deletes
    everything under the prefix -- including <name>.mmlct.tmp/<nnnn> slice
    temps, which live under it by construction (gcs.uploader.slice_temp_name)
    -- and fails the session if anything survives, so a leak surfaces as a red
    test rather than a surprise bill.

    MMLCT_TEST_PREFIX confines the run to a scratch folder, so an in-use
    bucket is a valid target.
    """
    bucket = os.environ.get("MMLCT_TEST_BUCKET")
    if not bucket:
        pytest.skip(
            "set MMLCT_TEST_BUCKET (and ADC credentials) to run the release gate — "
            "see docs/superpowers/gates/2026-08-05-plan2-release-gate.md"
        )

    from mml_cloud_transfer.gcs.client import make_context
    from mml_cloud_transfer.gcs.objects import delete_object, list_prefix

    run_prefix = _gate_run_prefix(os.environ.get("MMLCT_TEST_PREFIX", ""))
    ctx = make_context(bucket)
    try:
        yield ctx, run_prefix
    finally:
        # The guard. Everything below deletes recursively, and MMLCT_TEST_PREFIX
        # points into a bucket that may hold real data -- so refuse to delete
        # anything whose path is not demonstrably ours. A typo fails a test
        # instead of destroying data.
        assert f"/{GATE_SEGMENT}/" in f"/{run_prefix}", (
            f"refusing to delete under {run_prefix!r} — it is not a gate prefix"
        )
        for meta in list(list_prefix(ctx, run_prefix)):
            delete_object(ctx, meta.name)
        survivors = [m.name for m in list_prefix(ctx, run_prefix)]
        assert not survivors, f"release gate leaked objects: {survivors}"
```

and extend the existing import block at the top of the file with the datetime import (`os`, `uuid`, and `pytest` are already imported):

```python
from datetime import datetime, timezone
```

- [ ] **Step 5: Verify it skips cleanly without a bucket**

Run (with `MMLCT_TEST_BUCKET` **unset**): `.venv/Scripts/python -m pytest tests/gcs/test_real_bucket_fixture.py -v`
Expected: **2 passed, 3 skipped**. The two guard tests need no bucket — that is deliberate, so the assertion protecting the operator's data is verified on every machine, not only the gate host.

- [ ] **Step 6: Verify it passes against the real bucket**

Run:
```powershell
$env:MMLCT_TEST_BUCKET = "<your-bucket>"
$env:MMLCT_TEST_PREFIX = "<your-scratch-folder>"   # omit if the bucket is dedicated
.venv/Scripts/python -m pytest tests/gcs/test_real_bucket_fixture.py -v
```
Expected: 5 passed.

Then run it a **second** time. Expected: 5 passed again — `test_the_run_prefix_starts_empty` passing on the second run is the proof that the first run's teardown actually deleted `reachable.bin`.

Confirm by eye that the objects appeared under your scratch folder and nowhere else:
`gcloud storage ls --recursive "gs://$env:MMLCT_TEST_BUCKET/$env:MMLCT_TEST_PREFIX/"`

- [ ] **Step 7: Confirm the default suite is unaffected**

Run (with `MMLCT_TEST_BUCKET` **unset**): `.venv/Scripts/python -m pytest`
Expected: the existing suite green, plus the 2 guard tests passing and the 3 real-bucket tests skipping.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml tests/conftest.py tests/gcs/test_real_bucket_fixture.py
git commit -m "test: add real-bucket gate fixture with guaranteed prefix cleanup"
```

---

### Task 3: The resumable status-query test

The most important test in the gate. `tests/gcs/test_uploader_resumable.py:47-56` documents that fake-gcs-server finalizes a truncated upload when it receives the `bytes */total` probe, so every resume test substitutes a `StatusQueryShim` that answers with a hand-built 308. "Ask the server what it actually committed" has never run against a server that implements it.

**Files:**
- Create: `tests/gcs/test_real_bucket_protocol.py`

**Interfaces:**
- Consumes: `real_bucket_ctx` (Task 2); `initiate_upload`, `put_chunk`, `query_offset`, `PutResult` from `gcs.resumable`; `upload_resumable` from `gcs.uploader`; `hash_file` from `core.hashing`; `get_meta` from `gcs.objects`.
- Produces: `tests/gcs/test_real_bucket_protocol.py` with module constants `CHUNK = 256 * 1024` and `TOTAL = 1024 * 1024`, and a `blocks(count, seed)` helper reused by Task 4.

- [ ] **Step 1: Write the test**

Create `tests/gcs/test_real_bucket_protocol.py`:

```python
"""Release gate: the four behaviours fake-gcs-server cannot vouch for.

Small and fast -- a few megabytes, under a minute. Run these before the
2.6 GiB scale test; when one of them is red, the scale test cannot succeed
and would only cost time and bytes proving it.
"""

import random

import pytest

from mml_cloud_transfer.core.hashing import hash_file
from mml_cloud_transfer.gcs.objects import get_meta
from mml_cloud_transfer.gcs.resumable import (
    initiate_upload,
    put_chunk,
    query_offset,
)
from mml_cloud_transfer.gcs.uploader import upload_resumable

CHUNK = 256 * 1024
TOTAL = 1024 * 1024


def blocks(count: int, seed: int) -> bytes:
    """`count` distinct 256 KiB blocks: block N starts with N, big-endian.

    Distinct blocks matter. If every block were identical, a compose that
    stitched slices in the wrong order would produce a byte-identical object
    and the order test below would pass while proving nothing.
    """
    template = random.Random(seed).randbytes(CHUNK)
    return b"".join(n.to_bytes(16, "big") + template[16:] for n in range(count))


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "session.bin"
    path.write_bytes(blocks(4, seed=1))  # 1 MiB
    return path


@pytest.mark.real_bucket
def test_status_query_returns_the_servers_committed_offset(real_bucket_ctx, source):
    """The StatusQueryShim killer.

    fake-gcs-server answers the 'bytes */total' probe with 200 and a
    truncated object. Real GCS must answer 308 with the committed Range.
    Every resume in this codebase depends on that distinction.
    """
    ctx, run_prefix = real_bucket_ctx
    name = f"{run_prefix}status-query.bin"

    uri = initiate_upload(ctx, name, TOTAL)
    assert uri.startswith("http"), uri

    first = put_chunk(ctx.session, uri, source.read_bytes()[:CHUNK], 0, TOTAL)
    assert first.committed == CHUNK
    assert first.finalized is None, "a 256 KiB chunk of a 1 MiB upload must not finalize"

    # The assertion the emulator cannot make: an out-of-band status query
    # reports the server's committed prefix without finalizing anything.
    status = query_offset(ctx.session, uri, TOTAL)
    assert status.finalized is None, (
        "the status probe finalized the upload — resume would silently truncate files"
    )
    assert status.committed == CHUNK

    # And the real resume path completes it, hashing the committed prefix
    # locally rather than re-sending it.
    result = upload_resumable(
        ctx, str(source), name, TOTAL,
        precondition_generation=None, session_uri=uri, chunk_size=CHUNK,
    )
    assert result.state == "verified"
    assert result.bytes_sent == TOTAL - CHUNK, "the committed prefix must not be re-sent"

    meta = get_meta(ctx, name)
    assert meta is not None
    assert meta.size == TOTAL
    assert meta.crc32c == hash_file(source).crc32c
```

- [ ] **Step 2: Verify it skips cleanly**

Run (with `MMLCT_TEST_BUCKET` **unset**): `.venv/Scripts/python -m pytest tests/gcs/test_real_bucket_protocol.py -v`
Expected: 1 skipped.

- [ ] **Step 3: Run it against the real bucket**

Run:
```powershell
$env:MMLCT_TEST_BUCKET = "<your-bucket>"
.venv/Scripts/python -m pytest tests/gcs/test_real_bucket_protocol.py -v
```
Expected: PASS.

**If it fails, stop and report before touching anything.** This is the single highest-risk unknown in the plan, and a red result means resume is wrong in production today. Capture the exact assertion, the response status, and the raw `Range` header value. Do not "fix" it inside this task — the finding is the deliverable, and the fix needs its own design conversation.

- [ ] **Step 4: Commit**

```bash
git add tests/gcs/test_real_bucket_protocol.py
git commit -m "test: prove real GCS resumable status-query semantics"
```

---

### Task 4: The compose-ordering test

Layer 2 exists because "a compose that stitched slices in the wrong order would pass Layer 1 and fail here" (spec, Verification). That claim has only ever been checked against an emulator whose compose is not the real implementation. This task checks it against real GCS **and** checks that the check itself can fail.

**Files:**
- Modify: `tests/gcs/test_real_bucket_protocol.py` (append)

**Interfaces:**
- Consumes: `blocks` and `real_bucket_ctx` from Task 3; `SizePolicy`, `plan_slices` from `core.slicing`; `upload_slice`, `compose_slices`, `slice_temp_name` from `gcs.uploader`; `combine_all` from `core.crc32c_combine`; `crc32c_from_base64`, `hash_file` from `core.hashing`; `list_prefix`, `delete_object` from `gcs.objects`.
- Produces: module constant `COMPOSE_POLICY`.

- [ ] **Step 1: Write the test**

Append to `tests/gcs/test_real_bucket_protocol.py` (and extend the import block at the top with the new names):

```python
from mml_cloud_transfer.core.crc32c_combine import combine_all
from mml_cloud_transfer.core.hashing import crc32c_from_base64
from mml_cloud_transfer.core.slicing import SizePolicy, plan_slices
from mml_cloud_transfer.gcs.objects import delete_object, list_prefix
from mml_cloud_transfer.gcs.uploader import compose_slices, slice_temp_name, upload_slice

#: 3 MiB under this policy -> exactly three 1 MiB components.
COMPOSE_POLICY = SizePolicy(
    single_shot_max=64 * 1024,
    resumable_max=1024 * 1024,
    min_slice=1024 * 1024,
    max_components=32,
)
THREE_MIB = 3 * 1024 * 1024


@pytest.fixture
def composable(tmp_path):
    path = tmp_path / "composable.bin"
    path.write_bytes(blocks(12, seed=2))  # 3 MiB of distinct 256 KiB blocks
    return path


@pytest.mark.real_bucket
def test_compose_preserves_slice_order(real_bucket_ctx, composable):
    ctx, run_prefix = real_bucket_ctx
    name = f"{run_prefix}composed.bin"
    reversed_name = f"{run_prefix}composed-reversed.bin"

    specs = plan_slices(THREE_MIB, policy=COMPOSE_POLICY)
    assert len(specs) == 3, "the policy must produce three components"

    crcs = []
    metas = []
    for spec in specs:
        crc, meta = upload_slice(ctx, str(composable), name, spec, chunk_size=CHUNK)
        crcs.append(crc)
        metas.append(meta)

    combined = combine_all([(c, s.length) for c, s in zip(crcs, specs)])
    assert combined == hash_file(composable).crc32c, (
        "crc32c_combine over the slices must equal a straight whole-file hash"
    )

    bucket = ctx.client.bucket(ctx.bucket)
    try:
        # Compose the SAME components in the wrong order. If this produced the
        # same CRC, the correct-order assertion below would be vacuous and
        # Layer 2 could not detect a mis-stitched object at all.
        wrong = bucket.blob(reversed_name)
        wrong.compose([bucket.blob(m.name) for m in reversed(metas)])
        wrong.reload()
        assert crc32c_from_base64(wrong.crc32c) != combined, (
            "reversed compose produced the expected CRC — Layer 2 cannot detect order"
        )
    finally:
        delete_object(ctx, reversed_name)

    result = compose_slices(
        ctx, name, metas, combined, THREE_MIB, precondition_generation=0
    )
    assert result.state == "verified"
    assert result.remote_crc32c == combined

    leftovers = [m.name for m in list_prefix(ctx, f"{name}.mmlct.tmp/")]
    assert leftovers == [], f"compose left temp objects behind: {leftovers}"
    assert slice_temp_name(name, 0) == f"{name}.mmlct.tmp/0000"
```

- [ ] **Step 2: Verify it skips cleanly**

Run (with `MMLCT_TEST_BUCKET` **unset**): `.venv/Scripts/python -m pytest tests/gcs/test_real_bucket_protocol.py -v`
Expected: 2 skipped.

- [ ] **Step 3: Run it against the real bucket**

Run:
```powershell
$env:MMLCT_TEST_BUCKET = "<your-bucket>"
.venv/Scripts/python -m pytest tests/gcs/test_real_bucket_protocol.py -v
```
Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/gcs/test_real_bucket_protocol.py
git commit -m "test: prove real GCS compose order and combined-CRC arithmetic"
```

---

### Task 5: The precondition test

`if_generation_match` is what stops two writers silently clobbering each other, and a 412 must read as `conflict` rather than being blindly retried. Plan 2 Task 5 Step 4 explicitly anticipated that the emulator might not enforce it and that the assertion would have to move here.

**Files:**
- Modify: `tests/gcs/test_real_bucket_protocol.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 3-4; `classify`, `ErrorCategory` from `core.errors`; `upload_single_shot` from `gcs.uploader`.
- Produces: nothing new for later tasks.

- [ ] **Step 1: Write the test**

Append to `tests/gcs/test_real_bucket_protocol.py` (extending the import block):

```python
from mml_cloud_transfer.core.errors import ErrorCategory, classify
from mml_cloud_transfer.gcs.uploader import upload_single_shot


@pytest.mark.real_bucket
def test_stale_precondition_is_a_conflict_on_real_gcs(real_bucket_ctx, tmp_path):
    ctx, run_prefix = real_bucket_ctx
    name = f"{run_prefix}precondition.bin"

    first = tmp_path / "first.bin"
    first.write_bytes(b"the original content")
    second = tmp_path / "second.bin"
    # Different content, or the skip rule fires before any precondition does.
    second.write_bytes(b"entirely different content")

    created = upload_single_shot(ctx, str(first), name, precondition_generation=0)
    assert created.state == "verified"

    # precondition_generation=0 means "this object must not exist". It does.
    with pytest.raises(Exception) as excinfo:
        upload_single_shot(ctx, str(second), name, precondition_generation=0)
    assert classify(excinfo.value).category is ErrorCategory.CONFLICT

    # The rejected write must not have replaced anything.
    meta = get_meta(ctx, name)
    assert meta is not None
    assert meta.generation == created.generation

    # Under the correct generation the same write succeeds.
    replaced = upload_single_shot(
        ctx, str(second), name, precondition_generation=created.generation
    )
    assert replaced.state == "verified"
    assert replaced.generation != created.generation
```

- [ ] **Step 2: Verify it skips cleanly**

Run (with `MMLCT_TEST_BUCKET` **unset**): `.venv/Scripts/python -m pytest tests/gcs/test_real_bucket_protocol.py -v`
Expected: 3 skipped.

- [ ] **Step 3: Run it against the real bucket**

Run:
```powershell
$env:MMLCT_TEST_BUCKET = "<your-bucket>"
.venv/Scripts/python -m pytest tests/gcs/test_real_bucket_protocol.py -v
```
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/gcs/test_real_bucket_protocol.py
git commit -m "test: prove real GCS enforces if_generation_match as a conflict"
```

---

### Task 6: Layer-1 server-side CRC rejection, and the taxonomy gap it exposes

Layer 1 is "GCS rejects a corrupted write server-side". This is the one task with a real RED/GREEN cycle, because the expected finding is a `core` defect: `core/errors.py:125-138` maps 401, 403, 404, 412, 429, 408 and 5xx and nothing else, so GCS's **400** on a checksum mismatch falls through to `UNKNOWN` — "An unexpected error occurred." A corrupted-write rejection reading as an unexpected error defeats the taxonomy's entire purpose.

The `DataCorruption` special-case at `core/errors.py:162` does not cover this path: it catches the client library's own validation, and we deliberately pass `checksum=None` because we set `blob.crc32c` ourselves.

**Files:**
- Modify: `src/mml_cloud_transfer/core/errors.py`
- Modify: `tests/core/test_errors.py` (append)
- Modify: `tests/gcs/test_real_bucket_protocol.py` (append)

**Interfaces:**
- Consumes: `crc32c_to_base64` from `core.hashing`.
- Produces: `_from_http_status(code: int, message: str = "") -> ErrorCategory | None` — the existing private helper gains a second parameter, defaulted so nothing else breaks.

- [ ] **Step 1: Write the failing core tests**

Append to `tests/core/test_errors.py`:

```python
def test_a_400_naming_crc32c_is_a_checksum_mismatch():
    """GCS rejects a write whose declared CRC32C does not match the bytes.

    That is Layer 1 doing its job, and the user must be told the copy was
    corrupted -- not that something unexpected happened.
    """

    class BadRequest(Exception):
        code = 400

    exc = BadRequest(
        "Provided CRC32C hash 'AAAAAA==' doesn't match calculated CRC32C hash 'zzzzzz=='."
    )
    assert classify(exc).category is ErrorCategory.CHECKSUM_MISMATCH


def test_a_400_about_anything_else_stays_unknown():
    """Only checksum 400s are reclassified; a malformed request is not."""

    class BadRequest(Exception):
        code = 400

    assert classify(BadRequest("Invalid argument.")).category is ErrorCategory.UNKNOWN
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/core/test_errors.py -v`
Expected: `test_a_400_naming_crc32c_is_a_checksum_mismatch` FAILS — the category is `UNKNOWN`. The second test passes already.

- [ ] **Step 3: Implement the mapping**

In `src/mml_cloud_transfer/core/errors.py`, change `_from_http_status` to take the message and match on it:

```python
def _from_http_status(code: int, message: str = "") -> ErrorCategory | None:
    if code in (401, 403):
        return ErrorCategory.CREDENTIAL
    if code == 404:
        return ErrorCategory.NOT_FOUND
    if code == 412:
        return ErrorCategory.CONFLICT
    if code == 429:
        return ErrorCategory.QUOTA
    if code == 408:
        return ErrorCategory.NETWORK
    if 500 <= code <= 599:
        return ErrorCategory.NETWORK
    # GCS reports a rejected checksum as a 400 whose message names the hash.
    # Only that shape is reclassified — a plain 400 is still UNKNOWN, because
    # a malformed request is not a corrupted transfer.
    if code == 400 and any(
        token in message.lower() for token in ("crc32c", "checksum", "md5")
    ):
        return ErrorCategory.CHECKSUM_MISMATCH
    return None
```

and pass the message at the single call site in `classify`:

```python
        category = _from_http_status(code, str(exc))
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/core/test_errors.py -v`
Expected: PASS, including every pre-existing test in the file.

- [ ] **Step 5: Write the real-bucket test**

Append to `tests/gcs/test_real_bucket_protocol.py` (extending the import block with `crc32c_to_base64`):

```python
@pytest.mark.real_bucket
def test_server_rejects_a_wrong_crc32c(real_bucket_ctx, source):
    """Layer 1: GCS must refuse a write whose declared CRC32C is wrong."""
    ctx, run_prefix = real_bucket_ctx
    name = f"{run_prefix}corrupt.bin"

    blob = ctx.client.bucket(ctx.bucket).blob(name)
    blob.crc32c = crc32c_to_base64(0xDEADBEEF)  # deliberately not the file's CRC

    with pytest.raises(Exception) as excinfo:
        blob.upload_from_filename(str(source), checksum=None, if_generation_match=0)

    assert classify(excinfo.value).category is ErrorCategory.CHECKSUM_MISMATCH, (
        f"unexpected classification for {type(excinfo.value).__name__}: "
        f"{excinfo.value}"
    )
    assert get_meta(ctx, name) is None, "a rejected write must leave no object"
```

- [ ] **Step 6: Run it against the real bucket**

Run:
```powershell
$env:MMLCT_TEST_BUCKET = "<your-bucket>"
.venv/Scripts/python -m pytest tests/gcs/test_real_bucket_protocol.py -v
```
Expected: 4 passed.

Two outcomes are acceptable, and which one you got goes in your report:
- The exception is a 400 naming the hash → Step 3's mapping is what makes this pass. Expected case.
- The exception is `DataCorruption` (the client library validated before sending) → it was already classified correctly by `core/errors.py:162`, and Step 3's mapping is defensive rather than load-bearing. Keep it; the core tests still justify it.

If the classification is anything else, report the exception type and message verbatim rather than widening the token list to force a pass.

- [ ] **Step 7: Run the whole suite**

Run (with `MMLCT_TEST_BUCKET` **unset**): `.venv/Scripts/python -m pytest`
Expected: green, with the gate tests skipping. The two new `core` tests run and pass everywhere.

- [ ] **Step 8: Commit**

```bash
git add src/mml_cloud_transfer/core/errors.py tests/core/test_errors.py tests/gcs/test_real_bucket_protocol.py
git commit -m "fix: classify a checksum 400 as checksum_mismatch, proven against real GCS"
```

---

### Task 7: The multi-gigabyte kill-and-resume

The spec's defining test, at real scale with the **default** size policy: real 1 GiB slices, real session lifetimes, a real process death mid-slice.

**Files:**
- Create: `tests/cli/test_real_bucket_gate.py`

**Interfaces:**
- Consumes: `real_bucket_ctx` (Task 2); `connect` from `store.db`; `JobRepository` from `store.repository`; `FileState`, `JobStatus` from `core.models`; `hash_file` from `core.hashing`; `get_meta`, `list_prefix` from `gcs.objects`.
- Produces: nothing for later tasks.

- [ ] **Step 1: Write the test**

Create `tests/cli/test_real_bucket_gate.py`:

```python
"""Release gate: the defining test at real scale.

Unlike tests/cli/test_interrupt_resume.py, this passes NO --size-policy. The
default thresholds mean big.bin is cut into real 1 GiB slices, each with its
own long-lived resumable session -- the conditions an overnight transfer
actually meets, and the ones shrunken test thresholds cannot reproduce.
"""

import csv
import json
import os
import random
import shutil
import subprocess
import sys
import time

import pytest

from mml_cloud_transfer.core.hashing import hash_file
from mml_cloud_transfer.core.models import FileState, JobStatus
from mml_cloud_transfer.gcs.objects import get_meta, list_prefix
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

MIB = 1024 * 1024
BIG_BYTES = 2560 * MIB      # 2.5 GiB -> 3 slices: 1 GiB, 1 GiB, 0.5 GiB
MID_BYTES = 64 * MIB        # one resumable session
SMALL_COUNT = 8
SMALL_BYTES = 1 * MIB       # single-shot
FILE_COUNT = SMALL_COUNT + 2
NEEDED_FREE_BYTES = 6 * 1024**3
KILL_DEADLINE_SECONDS = 900


def _cli(*args):
    return [sys.executable, "-m", "mml_cloud_transfer.cli", *args]


def _write_blocks(path, block_count: int, seed: int) -> None:
    """Write `block_count` 1 MiB blocks, block N tagged with N.

    Distinct blocks are the point: identical ones would let a compose that
    stitched slices out of order produce a byte-identical object. Cheap to
    generate -- one PRNG block, then a per-block header.
    """
    template = random.Random(seed).randbytes(MIB)
    with open(path, "wb") as fp:
        for n in range(block_count):
            fp.write(n.to_bytes(16, "big") + template[16:])


@pytest.fixture(scope="module")
def big_tree(tmp_path_factory):
    root = tmp_path_factory.mktemp("gate-src")
    free = shutil.disk_usage(root).free
    if free < NEEDED_FREE_BYTES:
        pytest.skip(
            f"needs {NEEDED_FREE_BYTES // 1024**3} GiB free on {root.drive or root}, "
            f"found {free // 1024**3} GiB"
        )
    _write_blocks(root / "big.bin", BIG_BYTES // MIB, seed=11)
    _write_blocks(root / "mid.bin", MID_BYTES // MIB, seed=12)
    for n in range(SMALL_COUNT):
        _write_blocks(root / f"small-{n:02d}.bin", SMALL_BYTES // MIB, seed=100 + n)
    return root


def _slice_in_flight(db_path) -> bool:
    """True once some slice has a live session and a partial commit."""
    if not db_path.exists():
        return False
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM file_slices "
            "WHERE session_uri IS NOT NULL "
            "  AND bytes_transferred > 0 "
            "  AND bytes_transferred < length_bytes"
        ).fetchone()
        return row["n"] > 0
    except Exception:
        # The schema may not exist yet in the first moments of the scan.
        return False
    finally:
        conn.close()


@pytest.mark.real_bucket
@pytest.mark.slow
def test_multi_gigabyte_kill_and_resume(real_bucket_ctx, big_tree, tmp_path):
    ctx, run_prefix = real_bucket_ctx
    prefix = f"{run_prefix}scale"
    db = tmp_path / "gate.db"

    proc = subprocess.Popen(
        _cli(
            "transfer", "--db", str(db), "--bucket", ctx.bucket,
            "--name", "release-gate", "--source", str(big_tree), "--prefix", prefix,
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + KILL_DEADLINE_SECONDS
        while time.monotonic() < deadline:
            if _slice_in_flight(db):
                break
            if proc.poll() is not None:
                pytest.fail("transfer finished before any slice was mid-flight")
            time.sleep(1.0)
        else:
            pytest.fail(
                f"no slice reached a partial commit within "
                f"{KILL_DEADLINE_SECONDS}s — is the uplink saturated?"
            )
    finally:
        proc.kill()
        proc.wait(timeout=30)

    conn = connect(db)
    try:
        repo = JobRepository(conn)
        job = conn.execute("SELECT * FROM jobs ORDER BY id").fetchone()
        job_id = job["id"]
        counts = repo.count_by_state(job_id)
    finally:
        conn.close()
    assert job["status"] != JobStatus.COMPLETE.value
    assert counts.get(FileState.VERIFIED, 0) < FILE_COUNT, (
        "the kill landed after everything finished — it proves nothing"
    )

    resumed = subprocess.run(
        _cli(
            "resume", "--db", str(db), "--job-id", str(job_id),
            "--bucket", ctx.bucket, "--report-dir", str(tmp_path / "report"),
        ),
        capture_output=True, text=True, timeout=3600,
    )
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr

    conn = connect(db)
    try:
        repo = JobRepository(conn)
        assert repo.get_job(job_id)["status"] == JobStatus.COMPLETE.value
        rows = repo.get_files(job_id)
    finally:
        conn.close()
    assert len(rows) == FILE_COUNT
    assert all(
        r["state"] in (FileState.VERIFIED.value, FileState.SKIPPED.value) for r in rows
    ), [dict(r) for r in rows if r["state"] not in
        (FileState.VERIFIED.value, FileState.SKIPPED.value)]

    # The sliced file end-to-end: the composed object matches a fresh hash of
    # the source, which is the whole promise of Layer 2 over real slices.
    meta = get_meta(ctx, f"{prefix}/big.bin")
    assert meta is not None
    assert meta.size == BIG_BYTES
    assert meta.crc32c == hash_file(big_tree / "big.bin").crc32c

    # No orphaned slice temps anywhere under the run.
    orphans = [m.name for m in list_prefix(ctx, prefix) if ".mmlct.tmp/" in m.name]
    assert orphans == [], f"slice temp objects survived compose: {orphans}"

    # The report is the artifact a user is handed in the morning.
    summary = json.loads((tmp_path / "report" / "summary.json").read_text("utf-8"))
    assert summary["verdict"] == "COMPLETE"
    with (tmp_path / "report" / "manifest.csv").open(encoding="utf-8") as fp:
        assert len(list(csv.DictReader(fp))) == FILE_COUNT
```

- [ ] **Step 2: Verify it skips cleanly**

Run (with `MMLCT_TEST_BUCKET` **unset**): `.venv/Scripts/python -m pytest tests/cli/test_real_bucket_gate.py -v`
Expected: 1 skipped. Confirm it skipped **without** generating 2.6 GiB — the fixture must not run. If the run took more than a second or `tmp_path` filled up, the skip is happening in the wrong place; fix the ordering before continuing.

- [ ] **Step 3: Run it against the real bucket**

Run:
```powershell
$env:MMLCT_TEST_BUCKET = "<your-bucket>"
.venv/Scripts/python -m pytest tests/cli/test_real_bucket_gate.py -v -s --durations=0
```
Expected: 1 passed. Runtime is uplink-bound — roughly 2 minutes at 500 Mbps, 15+ minutes at 50 Mbps, plus about 30 seconds of file generation.

Record in your report: total duration, the observed count of `job_files`/`file_slices` rows at kill time, and whether the resume re-sent any already-committed bytes (visible in the report's byte totals).

If the kill deadline expires, the most likely cause is that the transfer finished the small files and `big.bin` had not yet started a slice. Raise `KILL_DEADLINE_SECONDS`, or add `--workers 1` to the `transfer` invocation so file dispatch is serialized — but **never** add `--size-policy`.

- [ ] **Step 4: Confirm the bucket is clean**

Run: `gcloud storage ls --recursive "gs://$env:MMLCT_TEST_BUCKET/$env:MMLCT_TEST_PREFIX/mmlct-gate/"` (drop the `$env:MMLCT_TEST_PREFIX/` segment if the bucket is dedicated).
Expected: nothing. The fixture's teardown deletes the whole run prefix and asserts emptiness, so this is a belt-and-braces check that the assertion is real.

If the bucket has object versioning enabled, also check for noncurrent versions — `gcloud storage ls --all-versions --recursive` — because deletes there leave versions behind and the 2.6 GiB keeps billing.

- [ ] **Step 5: Commit**

```bash
git add tests/cli/test_real_bucket_gate.py
git commit -m "test: add multi-gigabyte default-policy kill-and-resume release gate"
```

---

### Task 8: The gate record

The artifact that lets someone say "Plan 2 shipped" with evidence, and that Plan 3 extends at `2026-08-05-windows-service.md:3769`.

**Files:**
- Create: `docs/superpowers/gates/2026-08-05-plan2-release-gate.md`

**Interfaces:**
- Consumes: results from Tasks 1-7.
- Produces: the durable gate record.

- [ ] **Step 1: Write the record**

Create `docs/superpowers/gates/2026-08-05-plan2-release-gate.md`:

````markdown
# Plan 2 Release Gate — Record

**Design:** [../specs/2026-08-05-plan2-release-gate-design.md](../specs/2026-08-05-plan2-release-gate-design.md)
**Plan:** [../plans/2026-08-05-plan2-release-gate.md](../plans/2026-08-05-plan2-release-gate.md)

Plan 2's spec makes the real-bucket suite a release gate, not an option:
emulators do not faithfully implement `compose` or resumable-session
semantics, which is exactly the machinery the design depends on.

## Prerequisites

Either shape works:

- **A dedicated bucket** — single-region STANDARD, gate writes at the root.
- **A scratch folder in an existing bucket** — set `MMLCT_TEST_PREFIX`; every
  object the gate writes lands under it, and teardown refuses to delete
  anything outside a `mmlct-gate/` segment it built itself.

Also required:

- Application Default Credentials on this machine with `roles/storage.objectAdmin`
  on that bucket.
- 6 GiB free disk for the scale test's source tree.
- **No retention policy or bucket lock** — deletes would be refused and the gate
  could not clean up after itself. Preflight fails on this.
- Object versioning **disabled**, ideally. With it on, the gate's deletes leave
  noncurrent versions that keep billing, and "the bucket is clean" is not true
  even when every assertion passes. Preflight warns.

Recommended bucket lifecycle rule — the safety net for slice temp objects
orphaned by a hard crash (`AbortIncompleteMultipartUpload` does not apply;
these are ordinary composed-source objects). Adjust the prefix to match your
`MMLCT_TEST_PREFIX`:

```json
{"lifecycle": {"rule": [
  {"action": {"type": "Delete"},
   "condition": {"age": 7, "matchesPrefix": ["<scratch-folder>/mmlct-gate/"]}}
]}}
```

Apply with `gcloud storage buckets update gs://<bucket> --lifecycle-file=rule.json`.

## Run order

```powershell
pwsh tests/tools/preflight-gcs.ps1 -Bucket <bucket> -Prefix <scratch-folder>   # must exit 0
$env:MMLCT_TEST_BUCKET = "<bucket>"
$env:MMLCT_TEST_PREFIX = "<scratch-folder>"                       # omit for a dedicated bucket
.venv/Scripts/python -m pytest -m "real_bucket and not slow" -v   # 7 tests, <1 min
.venv/Scripts/python -m pytest -m "real_bucket and slow" -v       # 1 test, uplink-bound
```

Run the fast suite first. When one of those is red the scale test cannot
succeed and would only cost time and bytes proving it.

## What each test proves

| Test | Proves | Why the emulator cannot |
| --- | --- | --- |
| `test_run_prefix_is_unique_and_well_formed` | Runs cannot collide | — (fixture self-check) |
| `test_the_run_prefix_starts_empty` | The previous run's teardown worked | — (fixture self-check) |
| `test_objects_written_under_the_prefix_are_reachable` | Credentials can write and read | — (fixture self-check) |
| `test_status_query_returns_the_servers_committed_offset` | Resume reads the server's real committed offset | fake-gcs-server finalizes a truncated upload on the `bytes */total` probe |
| `test_compose_preserves_slice_order` | Layer 2 detects a mis-stitched object; `crc32c_combine` matches real compose | emulator compose is not the real implementation |
| `test_stale_precondition_is_a_conflict_on_real_gcs` | Concurrent writers cannot silently clobber each other | emulator precondition enforcement is unverified |
| `test_server_rejects_a_wrong_crc32c` | Layer 1 — GCS refuses a corrupted write | emulator does not validate CRC32C server-side |
| `test_multi_gigabyte_kill_and_resume` | The overnight promise: real 1 GiB slices survive process death | requires real scale and real session lifetimes |

## Results

| Field | Value |
| --- | --- |
| Date (UTC) | _(fill in)_ |
| Bucket | _(fill in)_ |
| Scratch prefix (`MMLCT_TEST_PREFIX`) | _(fill in, or "bucket root")_ |
| Region / storage class | _(fill in)_ |
| Versioning / retention policy | _(fill in)_ |
| Uplink (observed) | _(fill in)_ |
| Preflight | _(pass/fail)_ |
| Fast suite (`real_bucket and not slow`) | _(N passed, duration)_ |
| Scale test (`real_bucket and slow`) | _(pass/fail, duration)_ |
| Bytes re-sent on resume | _(fill in)_ |
| Run by | _(fill in)_ |

## Findings

_(One entry per surprise. Empty is a valid result.)_

## Teardown

- [ ] `gcloud storage ls --recursive "gs://<bucket>/<prefix>/mmlct-gate/"` returns nothing
- [ ] `gcloud storage ls --recursive "gs://<bucket>/<prefix>/mmlct-preflight/"` returns nothing
- [ ] Nothing outside `<prefix>/` was created or modified — compare against the
      folder listing you took before the run
- [ ] If versioning is enabled: `gcloud storage ls --all-versions --recursive` under
      `<prefix>/` returns nothing, or the noncurrent versions were purged
- [ ] Lifecycle rule in place (or noted as deliberately absent)
- [ ] Local 2.6 GiB source tree removed (pytest's `tmp_path` cleanup handles this;
      confirm if the run was interrupted)
````

- [ ] **Step 2: Run the whole gate and fill in the results**

Run, in order:
```powershell
pwsh tests/tools/preflight-gcs.ps1 -Bucket <bucket> -Prefix <scratch-folder>
$env:MMLCT_TEST_BUCKET = "<bucket>"
$env:MMLCT_TEST_PREFIX = "<scratch-folder>"
.venv/Scripts/python -m pytest -m "real_bucket and not slow" -v
.venv/Scripts/python -m pytest -m "real_bucket and slow" -v
```
Expected: preflight exit 0; 7 passed; 1 passed.

Before the first run, take a listing of the scratch folder's parent so the teardown checklist has something to compare against.

Fill in the Results table and the Findings section with what actually happened. **Do not write "pass" for anything you did not watch pass.**

- [ ] **Step 3: Confirm the default suite is still green**

Run (with `MMLCT_TEST_BUCKET` **unset**): `.venv/Scripts/python -m pytest`
Expected: green. The only skips are `real_bucket`/`slow` and, on a machine without the binary, `emulator`.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/gates/
git commit -m "docs: record the Plan 2 release-gate run"
```

---

## Phase Complete

Plan 2's release gate is closed. The four behaviours the emulator cannot vouch for are proven against real GCS, the interrupt-and-resume promise holds at real scale with real 1 GiB slices, and the run is on the record with its findings.

**Definition of done:**

- `pwsh tests/tools/preflight-gcs.ps1 -Bucket <name>` exits 0.
- `pytest -m "real_bucket and not slow" -v` — 7 passed, 0 skipped (3 fixture self-checks + 4 protocol tests).
- `pytest -m "real_bucket and slow" -v` — 1 passed.
- `pytest` with no marker selection — green, gate tests skipping, the 2 prefix-guard tests passing.
- The gate record is filled in and committed; findings are either empty or filed as follow-ups.
- No objects remain under `<prefix>/mmlct-gate/` or `<prefix>/mmlct-preflight/`, and nothing outside `<prefix>/` was touched.

**Carry-forward:** Plan 3's manual step at `2026-08-05-windows-service.md:3769` extends this gate with the service-hosted equivalent — the same kill-and-resume driven through the local API, surviving a service restart and a logoff. It should append to this same record rather than starting a new one.
