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
  could not clean up after itself. Preflight's delete probe is the real test.
- **Object versioning: supported either way.** The fixture's teardown lists with
  `versions=True` and deletes by explicit generation, so it removes noncurrent
  versions too. This matters: a plain delete on a versioned bucket merely adds
  another noncurrent version instead of removing the one you listed, and a
  live-only emptiness check would then report "clean" while the bytes remain.
  `afsc_mml_ccep` **has versioning enabled** — confirmed empirically on
  2026-08-05, since `storage.buckets.get` is denied and preflight cannot read it.
  To check any bucket without that permission: write an object, delete it, then
  `gcloud storage ls --all-versions` — a listed `name#generation` means versioning
  is on.

Recommended bucket lifecycle rules — the safety net for slice temp objects
orphaned by a hard crash (`AbortIncompleteMultipartUpload` does not apply;
these are ordinary composed-source objects). Adjusted here to the prefix this
run actually used (`scratch/`), plus a noncurrent-version rule since
versioning is enabled — without one, every delete this gate (or any operator)
performs accumulates a billable noncurrent version forever:

```json
{"lifecycle": {"rule": [
  {"action": {"type": "Delete"},
   "condition": {"age": 7, "matchesPrefix": ["scratch/mmlct-gate/"]}},
  {"action": {"type": "Delete"},
   "condition": {"isLive": false, "numNewerVersions": 1, "daysSinceNoncurrentTime": 7,
                 "matchesPrefix": ["scratch/mmlct-gate/", "scratch/mmlct-preflight/"]}}
]}}
```

**Warning — read before applying.** `gcloud storage buckets update --lifecycle-file`
**replaces the bucket's entire lifecycle configuration**; it does not merge. Both
rules above are scoped with `matchesPrefix` to the gate's own scratch paths
deliberately — the second rule (noncurrent-version cleanup) has no prefix filter
in the original spec draft, and applying that unscoped version to `afsc_mml_ccep`
would delete noncurrent versions **bucket-wide**, including under `data/` and
`marine-surveys-raw/`. If versioning is enabled there as an overwrite safety net
for live research data, an unscoped rule silently dismantles that net — this is
the only path in this entire gate by which anything outside the gate's own
prefix could be destroyed. Before applying, whoever holds bucket-admin must
first read the bucket's *current* lifecycle configuration and merge these two
rules into it — nobody on the operator account used for this gate can do that
enumeration themselves, since `storage.buckets.get` is denied (see Findings).
Applying this file as-is, without merging, would also delete any lifecycle
rules the bucket already has.

Apply with `gcloud storage buckets update gs://<bucket> --lifecycle-file=rule.json`.
Not applied for this run — `storage.buckets.get`/`bucketsUpdate` are outside
this account's grant (see Findings); recorded here as a follow-up for whoever
holds bucket-admin on `afsc_mml_ccep`.

## Run order

```powershell
pwsh tests/tools/preflight-gcs.ps1 -Bucket <bucket> -Prefix <scratch-folder>   # must exit 0
$env:MMLCT_TEST_BUCKET = "<bucket>"
$env:MMLCT_TEST_PREFIX = "<scratch-folder>"                       # omit for a dedicated bucket
.venv/Scripts/python -m pytest -m "real_bucket and not slow" -v   # 10 tests, ~1 min
.venv/Scripts/python -m pytest -m "real_bucket and slow" -v       # 1 test, uplink-bound
```

Run the fast suite first. When one of those is red the scale test cannot
succeed and would only cost time and bytes proving it.

**This run deviated from that order on the last line: the slow suite was not
run.** See "Task 7 — deferred" below for why and what is and is not proven as
a result.

## What each test proves

| Test | Proves | Why the emulator cannot |
| --- | --- | --- |
| `test_run_prefix_is_unique_and_well_formed` | Runs cannot collide | — (fixture self-check) |
| `test_a_dirty_prefix_would_be_detected` | A `list_blobs(..., versions=True)` listing — the same call *shape* the fixture's collision check uses — still finds a probe object after its live version is deleted, which a live-only listing would miss. **It replicates that call shape inline; it does not invoke the fixture's actual collision check** in `real_bucket_ctx` setup (`tests/conftest.py`), so it cannot catch a regression that drops `versions=True` from the production check itself | — (fixture self-check; see Findings for why this replaced `test_the_run_prefix_starts_empty`, and for a second finding on the same test) |
| `test_objects_written_under_the_prefix_are_reachable` | Credentials can write and read | — (fixture self-check) |
| `test_status_query_returns_the_servers_committed_offset` | Resume reads the server's real committed offset | fake-gcs-server finalizes a truncated upload on the `bytes */total` probe |
| `test_compose_preserves_slice_order` | Layer 2 detects a mis-stitched object; `crc32c_combine` matches real compose | emulator compose is not the real implementation |
| `test_stale_precondition_is_a_conflict_on_real_gcs` | Concurrent writers cannot silently clobber each other | emulator precondition enforcement is unverified |
| `test_server_rejects_a_wrong_crc32c` | Layer 1 — GCS refuses a corrupted write | emulator does not validate CRC32C server-side |
| `test_real_bucket_round_trip` (`tests/cli/test_interrupt_resume.py`) | An upload-then-download round-trip reproduces the source bytes against real GCS. **No resume**: the test runs `transfer` then `transfer --direction download` only — no kill, no `mmlct resume`, no `--job-id` | shrunken test-only size policy, not the scale claim below |
| `test_delete_object_generation_scoping_on_a_versioned_bucket` (Finding 5) | A generation-less delete clears the live pointer (`get_meta()` returns `None`) but leaves a noncurrent version at the exact original generation; a generation-scoped delete removes the version outright; a mismatched generation touches nothing | fake-gcs-server ignores the `generation` query param on `DELETE` entirely, matched or not — only real GCS enforces it or shows the live/noncurrent split |
| `test_compose_slices_leaves_no_noncurrent_temp_versions` (Finding 5) | The production `upload_slice` + `compose_slices` path, run end-to-end, leaves zero noncurrent versions of its slice temps on a versioning-enabled bucket | same reason — compose and versioned-delete semantics are not faithfully emulated |
| `test_compose_slices_deletes_every_temp_with_an_explicit_generation` (`tests/gcs/test_compose_slices_generation_pinning.py`, Finding 5) | `compose_slices()` deletes every swept temp by an explicit, non-`None` generation and pins each compose source to its verified generation — a credential-free regression pin against silently reverting either half of the fix | — (no bucket needed; a stub client records calls. Exists precisely *because* the emulator and a live bucket both being unavailable in CI must not mean this regression goes unguarded) |

The ninth test in the spec's original table, `test_multi_gigabyte_kill_and_resume`
(the overnight promise at real 1 GiB slices), exists and is committed but was
**not run** as part of closing this gate. See "Task 7 — deferred."

## Task 7 — deferred, not passing

Task 7's scale test (`tests/cli/test_real_bucket_gate.py::test_multi_gigabyte_kill_and_resume`,
committed at `74e386d`, marked both `real_bucket` and `slow`) is **deferred**,
not part of this gate's passing result. This is a deliberate decision, not an
oversight — recorded here so the next reader does not mistake "the fast gate
is green" for "the whole gate is green."

Facts, all measured on this workstation on 2026-08-05:

- The test exists, is committed, and is marked `real_bucket and slow`.
- It is **not** collected by the gate command `-m "real_bucket and not slow"` —
  confirmed via `--collect-only`.
- A plain `pytest tests/cli/test_real_bucket_gate.py` run (no `MMLCT_TEST_BUCKET`)
  skips it in well under a second (observed: `1 skipped in 0.28s`, ~0.86s wall
  including interpreter startup) **without** generating its 2.6 GiB source
  tree — `real_bucket_ctx` is requested before `big_tree` in the test's
  fixture list, so the skip fires before the tree-building fixture runs.
- It has **never passed**. Blocker: this workstation's uplink measures
  0.12–0.15 MB/s (roughly 1 Mbps). A 32 MiB single-shot upload exceeded the
  120s socket timeout outright. At that rate, transferring 2.6 GiB is roughly
  a 6-hour run, and the default 8 MiB chunks (~68s each) sit close enough to
  the timeout that individual chunk uploads fail intermittently even when the
  overall transfer is progressing.
- The one attempt made died at approximately 1% of `big.bin`. Because the
  pytest session itself died (not a clean test failure), the session-scoped
  `real_bucket_ctx` teardown never ran, and 8 MiB of slice data was stranded
  in the bucket; it was located and purged manually, not by the fixture.
- What that partial run **did** confirm against real GCS, and which stands as
  partial validation even though the test did not complete:
  - The default size policy sliced the 2.5 GiB `big.bin` into exactly 3
    slices — 1 GiB, 1 GiB, 0.5 GiB — as designed.
  - `mid.bin` (64 MiB) routed to the single-session resumable path, not the
    sliced path, as designed.
  - All four resumable sessions (3 slices + `mid.bin`) initiated successfully
    against real GCS.
  - Per-slice `bytes_transferred` was persisted to SQLite as bytes actually
    moved, i.e. the DB bookkeeping this design depends on for resume tracks
    real progress at real scale, not just at test-shrunk scale.
- What remains **unproven**:
  - `compose` of components at or above 1 GiB (the largest compose exercised
    by the passing fast-gate test is far smaller).
  - Kill-and-resume across a session with a multi-minute-plus lifetime.
  - The report/verdict path (COMPLETE/INCOMPLETE audit) at real scale.

**Consequently, this gate executed ZERO kill-and-resume against real GCS at
any scale.** `test_real_bucket_round_trip` (see the table above) is an
upload-then-download round-trip with no interruption and no resume call.
Task 7 — the only test in this suite that kills a transfer mid-flight and
resumes it against real GCS — never completed. The closest evidence this gate
has for the resume path is `test_status_query_returns_the_servers_committed_offset`,
which proves in-process session continuation only (the server's committed
offset is read correctly and a resumed upload does not resend already-committed
bytes) — it does not kill a process, does not go through `mmlct resume`, and
proves nothing about session survival across a real interruption. Whether
kill-and-resume actually works at any scale against real GCS remains unverified
by this gate.

This is an environment limitation (uplink bandwidth), not a defect found in
the product. Running the scale test to completion needs either a faster
uplink than this workstation has, or a deliberately extended timeout budget
and a multi-hour window; both are follow-up work, not part of closing this
gate today.

## Results

| Field | Value |
| --- | --- |
| Date (UTC) | 2026-08-05 |
| Bucket | `afsc_mml_ccep` |
| Scratch prefix (`MMLCT_TEST_PREFIX`) | `scratch` |
| Region / storage class | Unverified — `storage.buckets.get` denied (see Findings) |
| Versioning / retention policy | Versioning: **enabled**, confirmed empirically (write + delete + `gcloud storage ls --all-versions` still lists `name#generation`). Retention policy: unverified by metadata read; preflight's delete probe (the practical retention check) passed, so no retention lock is blocking deletes. |
| Uplink (observed) | 0.12–0.15 MB/s (~1 Mbps), from the Task 7 attempt |
| Preflight | Pass — exit 0, with expected metadata warnings (see Findings) |
| Fast suite (`real_bucket and not slow`) | **10 passed**, 0 failed, 0 skipped — run twice for repeatability: 42.08s then 41.33s (post-Finding-5-completion runs, this record; the earlier `8 passed … 62.62s then 39.94s` in Finding 2 was measured before `test_delete_object_generation_scoping_on_a_versioned_bucket` and `test_compose_slices_leaves_no_noncurrent_temp_versions` existed and is retained there for history, not as this gate's outcome) |
| Scale test (`real_bucket and slow`) | **Not run — deferred.** See "Task 7 — deferred" above. |
| Bytes re-sent on resume | 0 of the committed prefix — status-query test observed `put308 committed=262144`, then a resumed upload sending only `bytes_sent=786432` of 1048576 (i.e. the already-committed 262144 bytes were not re-sent) |
| Run by | Claude (Task 8), operator account `peter.mahoney@noaa.gov`, project `ggn-nmfs-afscinf-infra-01` |

## Findings

### 1. `test_the_run_prefix_starts_empty` was unsound by construction — fixed during this run

While executing the documented gate command for the first time end-to-end
(`pytest -m "real_bucket and not slow" -v`, with no file/order overrides),
the run failed 1 of 8: `test_the_run_prefix_starts_empty`
(`tests/gcs/test_real_bucket_fixture.py`) found the shared run prefix
non-empty and failed.

Root cause: `real_bucket_ctx` is a **session-scoped** fixture — one
`run_prefix` for the whole pytest session. `test_real_bucket_round_trip`
(`tests/cli/test_interrupt_resume.py`, confined to that shared prefix by an
earlier commit, `7a03ff6`) writes objects under `run_prefix/round-trip/` and
deliberately leaves them for the session's teardown to remove, by design —
the same pattern the fixture file's own
`test_objects_written_under_the_prefix_are_reachable` uses. `pytest`'s
default collection order is alphabetical by path, and `tests/cli/` sorts
before `tests/gcs/`, so `test_real_bucket_round_trip` always ran first,
always left its two files behind, and `test_the_run_prefix_starts_empty`
always then found the prefix dirty. This was deterministic, not a flake — it
would happen on every machine, every run, with the code as committed.

It was never caught earlier because Task 2 validated the fixture file in
isolation (`pytest tests/gcs/test_real_bucket_fixture.py`, not the full `-m`
selector), and Task 6 never ran the real-bucket suite against a live bucket
at all. This was the first time the documented gate command had actually been
executed end-to-end — which is exactly what a release gate is for: it did not
just validate GCS behavior, it also caught a defect in its own harness that
no amount of code review of the individual commits had surfaced.

The defect was in the test's premise, not its luck: a test asserting global
emptiness of a *session-scoped, shared* prefix can only ever be sound for
whichever test happens to execute first. Ordering was never guaranteed by
anything in the suite (no ordering plugin, no explicit dependency).

Fix applied (both files in `tests/`, nothing under `src/`):

- Moved the actual collision check into `real_bucket_ctx`'s setup, in
  `tests/conftest.py`, immediately before the fixture's `yield` and before
  its `try:` (so a dirty prefix is detected — and asserted on — before
  teardown could delete objects that are not this run's). It lists with
  `versions=True`, for the same reason teardown does: a noncurrent version
  under the prefix is still a collision.
- Replaced `test_the_run_prefix_starts_empty` with
  `test_a_dirty_prefix_would_be_detected` in
  `tests/gcs/test_real_bucket_fixture.py`. It no longer asserts anything
  about the shared prefix's global state; instead it writes a probe object
  under a sub-path it alone owns (`<run_prefix>collision-check/probe.bin`),
  asserts a `versions=True` listing of that sub-path sees it, and asserts a
  listing of a sibling sub-path it never wrote to
  (`<run_prefix>collision-check-absent/`) is empty. This proves the listing
  mechanism the fixture's own check relies on actually discriminates dirty
  from clean, and it cannot be broken by collection order because it never
  reasons about anything outside sub-paths it owns.
- `tests/cli/test_interrupt_resume.py` was **not** changed — it was behaving
  correctly; the shared-prefix assumption was the defect, not the round-trip
  test's choice to lean on session teardown.

Verification after the fix, both against the exact documented command with
default collection order (no reordering, no file-list override):

- Run 1: `8 passed, 262 deselected, 1 warning in 37.49s`
- Run 2 (repeatability + proof teardown left nothing behind for the fresh
  run to collide with): `8 passed, 262 deselected, 1 warning in 40.40s`

### 2. `test_a_dirty_prefix_would_be_detected` (Finding 1's replacement) did not actually prove version-awareness — fixed on code review

Code review of Finding 1's fix caught a second, more subtle defect in the
same test before it shipped. `test_a_dirty_prefix_would_be_detected` used
`list_prefix()`, a plain live-only listing, to check a probe object it had
written but never deleted. That proves "an object that exists is listed" —
never in doubt — not that the check is version-aware. The production
collision check it exists to protect lists with `versions=True` specifically
so a *noncurrent* version under a colliding prefix is also caught. A
regression that dropped `versions=True` from the production check would have
left this test green, on a bucket where that exact regression matters most
(`afsc_mml_ccep` has versioning enabled) — silently reintroducing the class
of bug this plan had already hit twice (once in the fixture's original
teardown, once in Finding 1's `test_the_run_prefix_starts_empty`).

Fix: rewrote the test to call the exact listing shape the production check
uses (`ctx.client.list_blobs(..., versions=True)`, not `list_prefix`), and
made the assertion decisive by deleting the probe's live object before
asserting the listing still finds it — a state only a `versions=True`
listing reports correctly; a live-only listing would report the sub-path
empty at that point, which is exactly the failure mode Finding 1's version of
the test could not have caught.

That fix immediately surfaced a **third**, independent bug, caught by running
it rather than by inspection: the first attempt deleted the probe via
`blob.delete()` on the same `Blob` object returned by `upload_from_string()`.
The `google-cloud-storage` client's `Blob.delete()` forwards
`generation=self.generation`, and that object's `.generation` was already
populated from the upload response — so the call was a **generation-scoped**
delete, which permanently purges that exact version rather than clearing
only the live pointer. Verified directly (a throwaway script against
`afsc_mml_ccep`, cleaned up after): deleting that way left a `versions=True`
listing of the object's prefix empty, i.e. the object was gone outright, not
archived. Deleting through a *fresh* `Blob` handle from the same path (no
generation known locally) instead performs a live-pointer delete, and the
same listing then correctly showed one noncurrent version. This distinction —
generation-scoped delete purges a specific version outright; a delete with no
known generation only clears the live pointer and archives the rest — is not
obvious from the client library's signature and is easy to get backwards
silently, since both calls succeed and neither raises.

The rewritten test now: writes a probe under `<run_prefix>collision-check/`,
deletes it through a fresh `Blob` handle, asserts a `versions=True` listing
of that sub-path is still non-empty (the decisive check), and asserts a
`versions=True` listing of an untouched sibling sub-path
(`<run_prefix>collision-check-absent/`) is empty. It still leaves the
noncurrent version for the fixture's teardown to sweep, and still reasons
only about sub-paths it owns, so it remains order-independent. The now-unused
`list_prefix` import was dropped.

**Residual gap, stated plainly:** this rewritten test still does not invoke
the fixture's actual collision check (the `versions=True` listing inside
`real_bucket_ctx` setup in `tests/conftest.py`). It duplicates that same call
shape inline in the test file instead. That proves the *mechanism* — a
`versions=True` listing genuinely sees noncurrent versions a live-only listing
would miss — but it does not prove the *production check* still uses that
mechanism. If someone edited `real_bucket_ctx` and dropped `versions=True`
from its own listing call, this test would keep passing; nothing in this
suite would catch that regression. Follow-up: hoist the listing into a shared
helper (e.g. `_list_including_noncurrent(ctx, prefix)`) used by both the
production check and this test, so the test exercises the real code path
instead of a parallel copy of it.

Verification after this fix, again against the exact documented command with
default collection order:

- Run 1: `8 passed, 262 deselected, 1 warning in 62.62s`
- Run 2 (repeatability): `8 passed, 262 deselected, 1 warning in 39.94s`
- Full suite, no bucket env vars: `260 passed, 10 skipped in 9.79s`
- `gcloud storage ls --all-versions --recursive "gs://afsc_mml_ccep/scratch/mmlct-gate/**"`
  — matched no objects

### 3. Preflight cannot read bucket metadata — expected, not a misconfiguration

`storage.buckets.get` is denied for the operator account on `afsc_mml_ccep`.
Preflight therefore cannot read storage class, versioning, retention policy,
or lifecycle configuration directly, and emits `WARN` lines naming exactly
what went unverified as a result. This is the least-privilege shape the
parent spec recommends (an operator should not need bucket-admin merely to
run transfers), not a bug in preflight or a misconfigured bucket. Preflight's
write/compose/delete probes are the practical substitute: they confirm the
account can actually do everything the gate needs, without needing to read
metadata to know it.

### 4. Storage class and retention policy remain formally unverified

Consequence of Finding 3: storage class and retention policy could not be
read from bucket metadata. The preflight delete probe (successfully deleting
a probe object) is the practical retention check — a retention lock would
have refused the delete — and it passed, so no retention policy is blocking
this gate's cleanup. Storage class was not independently checked; if a
future run needs to confirm it is STANDARD (rather than, say, an
inadvertently-configured cold class incurring early-deletion charges), that
needs either bucket-admin access or a request to whoever holds it.

### 5. `delete_object()` is a live-pointer delete, so sliced uploads double-bill storage indefinitely on a versioning-enabled destination — FIXED

**Status: fixed**, commit `13c5a9344e29`. Recorded here because this record
is what someone will consult to answer "can Plan 2 ship?" — this was a real
product defect the run surfaced, not a gate artifact.

`src/mml_cloud_transfer/gcs/objects.py::delete_object()` deletes through a
fresh `Blob` handle with no generation set — the same live-pointer delete
shape identified in Finding 2, except here it is in production code, not a
test. Its only production caller is `compose_slices()`
(`src/mml_cloud_transfer/gcs/uploader.py`), which sweeps the per-slice temp
objects after a successful compose.

On a **versioning-enabled destination bucket**, that live-pointer delete does
not remove the swept temp — it archives it as a noncurrent version, which
keeps billing. Because a sliced upload writes its full content twice (once
per slice as a temp object, then again as the composed object), this means a
500 GB sliced file costs **1 TB** of storage indefinitely, and a 20 TB dataset
costs **40 TB** — until something else purges those noncurrent versions. It is
also invisible in normal operation: live listings show nothing under the
object's name, and the job's audit reports `COMPLETE`, because compose and
delete both returned success.

**The parent spec's suggested `*.mmlct.tmp/` lifecycle rule does not mitigate
this, for two independent reasons:**

1. `slice_temp_name()` (`src/mml_cloud_transfer/gcs/uploader.py`) puts
   `.mmlct.tmp/` in the **middle** of the object name —
   `<object_name>.mmlct.tmp/<nnnn>` — not as a shared prefix. GCS lifecycle
   `matchesPrefix` conditions match only literal prefixes; there is no
   wildcard support. Because `<object_name>` varies per file, the temps for
   different files share no common prefix, so a lifecycle rule of the shape
   the spec describes (`matchesPrefix: ["*.mmlct.tmp/"]` or similar) cannot
   actually be written against this naming scheme.
2. Even if a matching rule could be written, a lifecycle `Delete` action
   applied to a **live** object in a versioning-enabled bucket only archives
   it — same as the code path in question — so the underlying bytes would
   keep billing as a noncurrent version regardless.

**The first pass of the fix was one line.** `compose_slices()` already held
each slice temp's `ObjectMeta` (which carries `.generation`) before calling
`delete_object()` on it — the generation was already in hand, it just was
not being passed through. `delete_object()` (`src/mml_cloud_transfer/gcs/objects.py`)
now takes an optional `generation` keyword and calls
`bucket.delete_blob(name, generation=generation)` instead of deleting through
a fresh, generation-less `Blob` handle; the default (`generation=None`)
remains a live-pointer delete, so existing callers are unaffected.

**A subsequent review found that pass incomplete and it was extended.**
Sweeping only the generations captured in `slice_metas` misses two cases: a
retry after `ChecksumMismatch` (the runner calls `repo.clear_slices(file_id)`
and re-uploads every slice, overwriting each temp — the prior generations
are already noncurrent by the time this run's sweep executes, and it never
learns about them), and a generation mismatch on delete, which 404s and is
silently swallowed by `ignore_missing=True`. `compose_slices()`
(`src/mml_cloud_transfer/gcs/uploader.py`) now sweeps by listing, not by the
handles it already held: after compose succeeds and Layer 2 verifies, it
calls `ctx.client.list_blobs(ctx.bucket, prefix=f"{object_name}.mmlct.tmp/",
versions=True)` and deletes every version it finds by that version's own
generation. Everything under the temp prefix at that point is garbage by
definition, so a total sweep is correct, not merely convenient. Separately,
`compose_slices()` now also pins each compose *source* to the generation
`ObjectMeta` recorded (`bucket.blob(meta.name, generation=meta.generation)`)
rather than a fresh, generation-less handle that would read whatever
happens to be live — so compose fails fast on a replaced temp instead of
silently composing different bytes than the ones just verified.

This is pinned by four tests:

- `tests/gcs/test_objects.py::test_delete_object_with_explicit_generation_removes_it`
  (emulator) — confirms the `generation` parameter is wired through
  `delete_object()` and does not raise. It does **not** prove generation
  semantics: fake-gcs-server ignores the `generation` query param on `DELETE`
  entirely, matched or not, so this test passes identically whether the
  kwarg is honored, ignored, or dropped. It is a wiring smoke test, nothing
  more — the real-bucket tests below carry the actual proof.
- `tests/gcs/test_real_bucket_protocol.py::test_delete_object_generation_scoping_on_a_versioned_bucket`
  (real bucket, the decisive test) — proves the actual contrast that
  motivated the fix: a generation-less delete on `afsc_mml_ccep` clears the
  live pointer (`get_meta()` returns `None`) but leaves a noncurrent version
  behind at the exact generation originally uploaded (`list_blobs(...,
  versions=True)` non-empty, and that version's `.generation` matches),
  while a generation-scoped delete of an equivalent object removes it outright
  (the same listing is empty). It also proves a mismatched generation leaves
  the object untouched. (The wrong-generation half of this proof could not be
  written against fake-gcs-server — confirmed empirically that the emulator
  does not enforce the `generation` query param on `DELETE` at all, matched
  or not, so only real GCS can show it.)
- `tests/gcs/test_real_bucket_protocol.py::test_compose_slices_leaves_no_noncurrent_temp_versions`
  (real bucket, product-level proof) — runs the actual `upload_slice` +
  `compose_slices` path used in production and asserts a `versions=True`
  listing of the swept `*.mmlct.tmp/` temps is empty. Before the fix this
  failed, showing the three temps surviving as noncurrent versions —
  reproducing the defect directly.
- `tests/gcs/test_compose_slices_generation_pinning.py::test_compose_slices_deletes_every_temp_with_an_explicit_generation`
  (credential-free, no bucket, no emulator) — the regression pin. A reviewer
  mutation-tested the tree behind this fix and found that reverting
  `compose_slices()`'s sweep to a generation-less delete, and separately
  making `delete_object()` silently drop the `generation` kwarg, **both**
  left the entire 338-test bucket-free suite green — meaning nothing short
  of the real-bucket suite would have caught either regression, and CI does
  not run the real-bucket suite. This test uses a stub `google.cloud.storage`
  client that records every call and asserts `compose_slices()` deletes each
  swept temp by its exact generation (never `None`) and pins each compose
  source to its verified generation. Verified to go red under both mutations
  described above, and green with the tree intact.

**Severity was: does not block this branch**, but must be resolved before
Plan 2 ships to any customer bucket with versioning enabled, since that is
exactly the configuration this gate ran against (`afsc_mml_ccep`) and exactly
the configuration under which the defect was both active and invisible. Stated
precisely: the systematic 2x storage cost on a clean run is eliminated — every
run that reaches a successful compose now sweeps and hard-deletes every
version under the temp prefix, which also closes the retry/overwrite and
generation-mismatch gaps described above, not just the case where
`slice_metas` still holds the live generation. What is **not** claimed: this
sweep only runs after a successful compose, so temps orphaned by a crash
*before* compose (process killed mid-upload, machine loss, etc.) are still
uncleared by this code path — those still depend on the operator-owned
lifecycle rule discussed above (and its documented limitation: `.mmlct.tmp/`
is an infix, not a prefix, so no single `matchesPrefix` rule covers every
file's temps). No test in this gate exercises that crash-before-compose case
against real GCS.

**Verification of the completed fix (totalizing sweep + pinned compose
sources + regression pin), same date:**

- `tests/gcs/test_compose_slices_generation_pinning.py` (the new unit test):
  RED with `compose_slices()`'s sweep call reverted to drop the `generation`
  kwarg (`AssertionError`, e.g. `('...0000', None) != ('...0000', 1001)`);
  RED again, independently, with `delete_object()` edited to drop the kwarg
  before it reaches `delete_blob()` (same assertion, same failure shape);
  GREEN with the tree intact — `1 passed`.
- Full suite, no bucket env vars: `339 passed, 12 skipped in 26.81s` (was
  `338 passed, 12 skipped`; +1 is the new unit test above).
- `-m "real_bucket and not slow" -v`, with `MMLCT_TEST_BUCKET`/`MMLCT_TEST_PREFIX`
  set: `10 passed, 341 deselected` — run twice, `42.08s` then `41.33s`.
- `gcloud storage ls --all-versions --recursive "gs://afsc_mml_ccep/scratch/**"`:
  one object listed, the `scratch/` folder placeholder itself; zero objects
  with `mmlct` in the name at any version.

## Follow-ups

Ranked by what would most reduce risk before Plan 2 ships broadly. Former
item 1 (`delete_object()` generation scoping, Finding 5) is fixed — see
Finding 5 above — and has been removed from this list. None of the remaining
items were fixed in this gate; they are recorded here so they are not lost.

1. **Give `tests/gcs/test_uploader_sliced.py` distinct per-slice content.**
   Its `source` fixture writes `bytes(range(256)) * 4096`, so every slice is
   byte-identical. A mis-ordered `compose` (e.g. slices 1 and 2 swapped) would
   still produce a byte-correct object and pass every existing assertion —
   no test anywhere in this suite can currently catch a slice-ordering
   regression in `upload_sliced`.
2. **Hoist the versions-aware listing into a shared helper** (Finding 2's
   residual gap) so `test_a_dirty_prefix_would_be_detected` exercises the
   same code path as the production collision check in `real_bucket_ctx`,
   rather than a parallel copy of its call shape.
3. **Add a `pytest_collection_modifyitems` auto-skip for `real_bucket`** so
   real-bucket tests fail fast with a clear message (rather than erroring on
   missing credentials/env) when `MMLCT_TEST_BUCKET` is unset, and are
   trivially excludable in CI.
4. **Run Task 7 on a real uplink.** This gate's uplink (0.12–0.15 MB/s) made
   the scale test infeasible; it needs either a faster connection or a
   multi-hour timeout budget, neither of which was available for this run.
5. **Consider whether Layer 1 (in-flight CRC32C) should exist above 8 MiB at
   all.** `initiate_upload` sets no CRC32C and `put_chunk` sends no checksum
   header, so there is no in-flight checksum for any file over 8 MiB today —
   Layer 2 (post-compose `crc32c_combine` verification) still catches
   corruption, but only after the fact, not during upload.

## Teardown

Use `--all-versions` on every check. Without it a versioned bucket reports clean
while the bytes are still there — the exact failure this gate had before the
fixture was made version-aware.

- [x] `gcloud storage ls --all-versions --recursive "gs://afsc_mml_ccep/scratch/mmlct-gate/**"` — matched no objects (`ERROR: (gcloud.storage.ls) One or more URLs matched no objects.`)
- [x] `gcloud storage ls --all-versions --recursive "gs://afsc_mml_ccep/scratch/mmlct-preflight/**"` — matched no objects (same error/result)
- [x] `gcloud storage ls --all-versions --recursive "gs://afsc_mml_ccep/mmlct-test/**"` — matched no objects (same error/result)
      (bucket root — catches a regression of the legacy round-trip test's old unconfined prefix)
- [x] Nothing outside `scratch/` was created or modified — `gcloud storage ls "gs://afsc_mml_ccep/scratch/"` was the only populated path touched by this run; no other top-level object was written.
- [ ] Lifecycle rule: **not applied.** `storage.buckets.get`/`bucketsUpdate` are outside this
      operator account's grant (Finding 3). The recommended rule JSON above is on record for
      whoever holds bucket-admin on `afsc_mml_ccep`.
- [x] Local 2.6 GiB source tree: never created. Task 7's scale test was deferred and its
      `big_tree` fixture never ran (confirmed: the run skipped in 0.28s, before the tree-building
      fixture would execute). Nothing to clean up.
