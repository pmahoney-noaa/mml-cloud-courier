# Plan 2 Release Gate — Storage and Data Transfer Evaluation

**Date:** 2026-08-05
**Status:** Approved for planning
**Type:** Test/verification work on merged code
**Parent spec:** [2026-08-04-gcs-transfer-manager-design.md](2026-08-04-gcs-transfer-manager-design.md) — the Testing section's real-bucket release gate
**Parent plan:** [2026-08-04-transfer-engine.md](../plans/2026-08-04-transfer-engine.md) — Phase Complete, "Release gate (spec requirement)"

## Problem

Plan 2 is merged: `mmlct transfer` moves a tree across all three size paths, `mmlct resume`
continues an interrupted job, and every job ends with an audited COMPLETE/INCOMPLETE
verdict. All of it is proven against `fake-gcs-server` and in-process stubs. None of it has
ever touched Google Cloud Storage.

The spec anticipated this and called it out as a release gate rather than a nicety:

> Emulators do not faithfully implement `compose` or resumable-session semantics, which is
> exactly the machinery this design depends on [...] **The real-bucket suite is a release
> gate, not optional.**

The gate today is one test — `test_real_bucket_round_trip` in
`tests/cli/test_interrupt_resume.py` — which skips without `MMLCT_TEST_BUCKET` and, when it
does run, uses the tiny size policy `65536,262144,262144`. It never exercises a 1 GiB slice,
a long-lived session, or a resume.

Worse, the emulator is actively wrong in the one place the whole design rests on.
`tests/gcs/test_uploader_resumable.py` documents it:

> fake-gcs-server's memory backend finalizes an incomplete upload when it receives the
> `bytes */total` probe (verified by direct protocol probe: 200 + truncated object instead
> of 308).

Every resume test therefore substitutes a `StatusQueryShim` that answers the status query
locally with a hand-built 308. **"Ask the server what it actually committed" — the sentence
the overnight-transfer promise is built on — has never been executed against a server that
implements it.** If real GCS disagrees with our reading of the protocol, no test in the repo
would notice.

## Goals

- Prove, against real GCS, the four behaviours the emulator cannot vouch for: resumable
  status-query semantics, `compose` ordering and combined-CRC arithmetic, `if_generation_match`
  enforcement, and server-side CRC32C rejection.
- Prove the defining interrupt-and-resume promise at real scale with the **default** size
  policy — real 1 GiB slices, real session lifetimes — not shrunken test thresholds.
- Make the gate runnable by someone who does not yet know what GCP resources they have.
- Guarantee the gate leaks no billable objects, including on failure.
- Leave a durable, dated record of what was run and what it proved.

## Non-Goals

- Throughput or performance measurement. Numbers observed in passing may be recorded as
  context, but nothing asserts on them and no tuning conclusions are drawn here.
- Provisioning GCP resources automatically. The preflight *emits* commands; a human runs them.
- CI integration. The gate is operator-run against a real billable bucket.
- A shipped `mmlct preflight` subcommand. The spec promises per-profile preflight permission
  checks; that is Plan 4's profile work, and pulling it forward would widen a plan we are
  trying to close.
- Fixing anything the gate finds. Findings are recorded; fixes are separate work, except
  where a fix is a one-line taxonomy mapping (see Expected Findings).

## Architecture

Five artifacts, in dependency order:

```text
tests/tools/preflight-gcs.ps1              discovery — what exists, what to run next
tests/conftest.py                          + real_bucket_ctx fixture (session-scoped)
pyproject.toml                             + slow marker
tests/gcs/test_real_bucket_protocol.py     4 fast protocol tests  (~5 MB, <1 min)
tests/cli/test_real_bucket_gate.py         1 slow scale test      (~2.6 GiB, uplink-bound)
docs/superpowers/gates/
  2026-08-05-plan2-release-gate.md         runbook + results record
```

Nothing under `src/` changes. This is verification work on merged code; if the gate finds a
defect, the fix is a separate commit with its own test.

### Why a layered suite rather than one end-to-end test

A single mega-test that transfers everything, kills, resumes, and asserts COMPLETE would be
less code and one bucket run. It was rejected because real-bucket iterations are slow and
billable, which is exactly the condition under which failure isolation pays for itself. A
red mega-test says "something in 2.6 GiB of machinery broke" and charges another full run
per debugging hypothesis. The four protocol tests cost pennies and seconds, and each names
the layer that broke.

## Component 1 — Preflight discovery

`tests/tools/preflight-gcs.ps1`, read-only and idempotent. It reports; it does not
provision.

| Check | Mechanism | On failure |
| --- | --- | --- |
| `gcloud` present | `gcloud version` | print install URL, stop |
| Authenticated | `gcloud auth list` | print `gcloud auth login` |
| ADC available | `gcloud auth application-default print-access-token` | print `gcloud auth application-default login` |
| Project configured | `gcloud config get project` | print `gcloud config set project <id>` |
| Bucket exists | `gcloud storage buckets describe gs://$Bucket` | print the exact `buckets create` command |
| Storage class STANDARD | same describe | warn: minimum-storage-duration charges on temp slices |
| Write / delete / compose permitted | probe object under `mmlct-preflight/`, composed then deleted | name the missing IAM role |
| `*.mmlct.tmp/` lifecycle rule | `buckets describe --format=json` | print the rule JSON and `buckets update` command |

Parameters: `-Bucket <name>` (required), `-Project <id>` (optional, defaults to the
configured project). Exit code 0 when every check passes, 1 if any check fails. Warnings
— currently only the non-STANDARD storage class — are printed but do not affect the exit
code, since a Nearline bucket is a cost problem, not a correctness one. The final line on
success prints the environment assignment to copy:
`$env:MMLCT_TEST_BUCKET = "<name>"`.

Provisioning is emitted rather than executed on purpose: a script that silently creates
billable cloud resources on a machine whose state it has just admitted it does not know is
the wrong default.

The permission probe cleans up its own objects in a `finally`.

## Component 2 — Fixture and cost containment

A session-scoped `real_bucket_ctx` fixture in `tests/conftest.py`, beside the existing
`emulator` fixture and following its skip idiom.

- Yields `(GcsContext, run_prefix)`.
- Skips when `MMLCT_TEST_BUCKET` is unset, with the message
  `set MMLCT_TEST_BUCKET (and ADC credentials) to run the release gate`.
- `run_prefix` is `mmlct-gate/<YYYYmmddTHHMMSSZ>-<uuid8>/`, unique per session, so
  concurrent runs and abandoned runs never collide.
- Teardown, in a `finally`, deletes every object under `run_prefix` — including
  `<name>.mmlct.tmp/<nnnn>` slice temps, which fall under the prefix by construction
  (`slice_temp_name` in `gcs/uploader.py:259`) — then re-lists and fails the session if
  anything survives. `tests/cli/test_interrupt_resume.py:145` already learned this lesson
  per-test; the fixture generalizes it so no individual test can leak.

A `slow` marker is added to `pyproject.toml` alongside `emulator` and `real_bucket`:

```toml
"slow: multi-gigabyte; minutes, not seconds",
```

so `-m "real_bucket and not slow"` gives a sub-minute protocol-only gate during iteration.

**Cost and runtime.** One full gate run stores ~2.6 GiB for a few minutes and issues a few
thousand Class-A operations. The scale test never downloads, so there is no egress. Total
well under $0.05. Runtime is uplink-bound: roughly 45 seconds at 500 Mbps, about 7 minutes
at 50 Mbps, plus generation and hashing.

## Component 3 — Protocol tests

`tests/gcs/test_real_bucket_protocol.py`. All four marked `real_bucket`, all small and fast.

**`test_status_query_returns_the_servers_committed_offset`.** The `StatusQueryShim` killer.
Initiate a 1 MiB session with `initiate_upload`, PUT one 256 KiB chunk with `put_chunk`,
then call `query_offset(ctx.session, uri, total)` and assert `committed == 262144` **and
`finalized is None`**. The second assertion is exactly what fake-gcs-server gets wrong. Then
complete the upload via `upload_resumable(..., session_uri=uri)` and assert Layer 2 passes
and the finalized object is the full 1 MiB. This is the most important test in the gate.

**`test_compose_preserves_slice_order`.** A 3 MiB source under
`SizePolicy(single_shot_max=64*1024, resumable_max=1024*1024, min_slice=1024*1024,
max_components=32)`, so `plan_slices` yields three 1 MiB components, each block carrying its
index in its first 16 bytes. Assert the composed
object's CRC equals `crc32c_combine.combine_all` over the slice CRCs *and* equals a fresh
`hash_file` of the source; assert the temp objects are gone after compose. Then compose the
same components in reverse order into a scratch object name and assert its CRC differs from
the correct one — without that, a compose that ignored order could pass this test vacuously
and we would never know Layer 2 discriminates at all. The scratch object is deleted in a
`finally`.

**`test_stale_precondition_is_a_conflict_on_real_gcs`.** Upload an object, then upload
different content to the same name with `precondition_generation=0`; assert the raised
exception classifies as `ErrorCategory.CONFLICT`. Precondition enforcement is currently
asserted only against the emulator, and Plan 2 Task 5 Step 4 explicitly anticipated that it
might need to move here.

**`test_server_rejects_a_wrong_crc32c`.** Layer 1. Preset a deliberately wrong
`blob.crc32c`, upload, and assert the write is rejected and no object exists afterwards.
The test asserts the rejection and records `classify(exc).category` (see Expected Findings).

## Component 4 — Scale test

`tests/cli/test_real_bucket_gate.py::test_multi_gigabyte_kill_and_resume`, marked
`real_bucket` and `slow`.

**Tree** (~2.6 GiB, generated into `tmp_path`):

| File | Size | Default-policy path |
| --- | --- | --- |
| `big.bin` | 2.5 GiB | sliced — 3 real slices (1 GiB, 1 GiB, 0.5 GiB), one `compose` |
| `mid.bin` | 64 MiB | one resumable session |
| `small-00..07.bin` | 1 MiB each | single-shot |

Generated in 1 MiB blocks whose first 16 bytes encode the block index, with the remainder
from a seeded PRNG. Cheap to produce, and order-sensitive: identical blocks would let a
mis-stitched compose pass unnoticed.

**No `--size-policy` flag is passed.** That is the entire point of this test — real 1 GiB
slices, real session lifetimes, real default thresholds. The automated suite everywhere else
shrinks them.

**Sequence.** Launch `mmlct transfer` as a subprocess. Poll `file_slices` until some row has
a non-null `session_uri` and `0 < bytes_transferred < length_bytes` — the true
"mid-sliced-file" condition, not a timer — then `proc.kill()`. Fail the test if that state
is not reached within 15 minutes.
Assert the job is not COMPLETE and `big.bin` is not `verified`. Run `mmlct resume` and
require exit code 0.

**Final assertions.** Job status COMPLETE; every file `verified` or `skipped`; `big.bin`'s
remote CRC32C equals a fresh local `hash_file`; **zero surviving objects under any
`*.mmlct.tmp/` prefix**; `summary.json` verdict COMPLETE; `manifest.csv` row count equals
the planned file count.

**Guards.** Skip with an actionable message if free disk on `tmp_path`'s drive is under
6 GiB. The subprocess kill runs in a `finally` so a hung transfer cannot orphan a process.

## Component 5 — Gate record

`docs/superpowers/gates/2026-08-05-plan2-release-gate.md`, created by this work and filled
in when the gate is run:

1. Prerequisites and the preflight command.
2. Run order: preflight → `-m "real_bucket and not slow"` → `-m "real_bucket and slow"`.
3. What each test proves, in one line each, so a red result is interpretable without
   reading the test.
4. A results table: date, bucket, region, storage class, uplink, per-phase duration,
   pass/fail, findings.
5. Teardown checklist: confirm the gate prefix is empty, confirm no `mmlct-preflight/`
   residue, note whether the lifecycle rule is in place.

Plan 3 already references this gate — `2026-08-05-windows-service.md:3769` extends it with
the service-hosted equivalent — so it wants to be a durable artifact rather than terminal
scrollback.

## Expected Findings

Stated in advance so a red result is a confirmation rather than a scramble.

**GCS's 400 on a checksum mismatch will classify as `UNKNOWN`, not `CHECKSUM_MISMATCH`.**
`core/errors.py:125-138` maps 401, 403, 404, 412, 429, 408 and 5xx, and nothing else; 400
falls through to `UNKNOWN`. The `DataCorruption` special-case at `core/errors.py:162` only
catches the client library's own raise, which is not the path taken when we preset
`blob.crc32c` and pass `checksum=None`. Test 4 records the observed category. If it is
`UNKNOWN`, the fix is a one-line addition to `_from_http_status` — in scope, with a `core`
unit test alongside it, since leaving a corrupted-write rejection reading as "an unexpected
error occurred" defeats the error taxonomy's entire purpose.

**The resumable status query is the highest-risk unknown.** If real GCS's 308 `Range`
semantics differ from the shimmed assumption in any way — inclusive-vs-exclusive end,
absent header on a zero-byte commit — resume is wrong today and nothing in the suite knows.
Test 1 is designed so that failure is unambiguous and cheap to reproduce.

## Testing Strategy

The gate is itself test code, so "how do we test the test" matters.

- Every gate test skips cleanly without `MMLCT_TEST_BUCKET`, so a plain `pytest` run stays
  green on any machine and CI is unaffected.
- The fast protocol suite runs first and shakes out the fixture — prefix construction,
  cleanup, credential wiring — for pennies, before the slow test commits 2.6 GiB to it.
- The fixture's cleanup assertion is self-checking: it re-lists after deleting and fails the
  session on any survivor, so a leak surfaces as a red test rather than a surprise bill.
- The reverse-order compose assertion in test 2 and the block-index encoding in the scale
  test both exist to prevent vacuous passes.

## Definition of Done

- `pwsh tests/tools/preflight-gcs.ps1 -Bucket <name>` exits 0 on the operator's machine.
- `pytest -m "real_bucket and not slow" -v` — 7 passed, 0 skipped (4 protocol tests plus
  3 fixture self-checks).
- `pytest -m "real_bucket and slow" -v` — 1 passed.
- `pytest` with no marker selection — green, with `real_bucket` and `slow` tests skipping.
- The gate record is filled in, committed, and its findings section is either empty or lists
  filed follow-ups.
- The bucket contains no objects under the gate prefix.

Plan 2 is shippable at that point. Plan 3's service-hosted kill-and-resume
(`2026-08-05-windows-service.md:3769`) extends this gate rather than replacing it.
