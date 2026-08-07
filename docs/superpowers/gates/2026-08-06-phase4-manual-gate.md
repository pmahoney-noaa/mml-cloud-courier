# Phase 4 Manual Gate — Auth and Profiles

**Status: SECTIONS A AND C CLOSED — ALL CHECKS PASS (2026-08-06); SECTION B BLOCKED (no service-account key obtainable)**

**Run:** 2026-08-06 local (job timestamps 2026-08-07T01:40–01:58Z), operator
`pmaho`, service on merged master @ e25b69a. The spec's done-when — "a
profile created from either credential type works unattended after logoff"
— is **demonstrated for the OAuth credential type** (A4, job #9). Per B3
below, the gate fully closes only when B is re-run with a real key; A
proves the DPAPI/unattended machinery either way.

**Done-when under test (spec Phase 4):** a profile created from either
credential type works unattended after logoff.

**Environment:** the LIVE service install (auto-start, running as
`.\pmaho`, port 47821, data in `%ProgramData%\MML Cloud Transfer`) — this
gate deliberately runs against the live install; it is the deployment
being certified. Bucket `afsc_mml_ccep`, prefix `scratch/phase4-gate/`
(versioning ON; `storage.buckets.get` denied — preflight metadata is
object-level only, so no WARNs are expected from the product preflight).
All bucket cleanup MUST be version-aware (`--all-versions` / delete by
explicit generation) — a live-only "clean" is not clean.

## Step 0 (added during execution): restart onto the merged code

- [x] Stale-active-job check before restart: `mmlct status` showed jobs
      1–8 (Phase 3 gate) all `complete` — nothing for the new
      duplicate-destination guard to collide with.
- [x] `Restart-Service MMLCloudTransfer` (elevated) — first service start
      on merged master e25b69a, and therefore the live database's v1→v2
      migration moment.
- [x] Merged-code liveness + live migration proof in one check:
      `mmlct profile list` returned the Phase 3 legacy ad-hoc profile
      `afsc_mml_ccep [adc 1] … last check: never` — the `/profiles` route
      only exists post-merge, and `last check: never` renders
      `validated_at`, a column that only exists post-migration, correctly
      NULL on the pre-migration row. Old row intact, new column added, on
      the production database.

## A. OAuth profile

- [x] A1. Desktop OAuth client created in a **personal GCP project**
      (org-internal client not creatable: operator lacks OAuth-config
      permissions in the NOAA org — see Findings 1–2). Consent screen in
      **Testing** publishing status; `peter.mahoney@noaa.gov` added as a
      test user (Console: Google Auth Platform → Audience → Test users;
      the first sign-in attempt without it failed with the expected
      `Error 403: access_denied`). `MMLCT_OAUTH_CLIENT` set to the
      downloaded client JSON.
- [x] A2. `mmlct profile login --name gate-oauth --bucket afsc_mml_ccep
      --prefix scratch/phase4-gate ...` — **PASS** after the test-user
      fix. Browser sign-in as the NOAA account succeeded; profile
      `gate-oauth` (auth_type `oauth_user`) created; capability summary:
      can list, read, write, compose and delete. **Finding 1: NOAA
      Workspace policy did NOT block the external Testing-status app** —
      user OAuth is viable for this org, at least for tester-listed
      accounts.
- [x] A3. At-rest + ACL check — **PASS** (performed non-elevated, which
      itself is evidence: the service runs as `.\pmaho`, so the operator's
      own grant is expected — see Finding 3). Blob
      `credentials\cred-aa62eea08a63.dpapi` (704 bytes, DPAPI provider
      header `01000000`): byte scan finds NO `refresh_token`,
      `client_secret`, `authorized_user`, or `1//` token prefix. `icacls`:
      no `(I)` entries anywhere; directory grants exactly
      `MATRIX\pmaho:(OI)(CI)(F)`, `BUILTIN\Administrators:(OI)(CI)(F)`,
      `NT AUTHORITY\SYSTEM:(OI)(CI)(F)`; blob grants the same three
      principals `(F)`, non-inherited.
- [x] A4. **PASS — the done-when, demonstrated.** Job #9
      (`p4-oauth-run1`, 40 files / 480 MiB → `scratch/phase4-gate/run1`):
      submitted 01:40:28Z, operator signed out immediately after
      submission per procedure; COMPLETE at **01:47:07Z** (6m38s,
      ~1.26 MB/s on that evening's link), 40/40 verified, audit clean,
      report written by the worker. `quser` after sign-back-in: console
      logon 6:51 PM (≈01:51Z) — **four minutes after the job finished**,
      corroborating that the run completed with no interactive session.
      The service refreshed the OAuth access token autonomously from the
      DPAPI-stored refresh token mid-run.
      **Bonus demonstration (job #10):** an accidental resubmission of the
      identical transfer at 01:50:44Z was correctly *allowed* by the
      duplicate-destination guard (job #9 was COMPLETE — only active jobs
      block) and finished in **8 seconds with 40/40 skipped** (size+CRC
      match) — the spec's cheap-re-run mechanism, live. The profile's
      `validated_at` (01:50:44Z) matches that submission's preflight
      stamp, confirming per-submission preflight stamping.
- [x] A5. Service restarted (elevated `Restart-Service`), then job #11
      (`p4-oauth-run2` → `scratch/phase4-gate/run2`, distinct prefix so
      files genuinely transfer rather than skip): **COMPLETE, 40/40
      verified** — the credential decrypts and refreshes in a fresh
      service process. Machine-scope DPAPI, not session-bound.
- [x] A6. **PASS — clean refusal, no stall loop.** App access removed at
      myaccount.google.com/permissions (the *grant* on the NOAA account —
      not the Console tester list, which does not invalidate issued
      tokens). Resubmission (`p4-oauth-run3`) was refused at submission
      time with the capability summary (exit 1, no traceback); **no job
      row was created** (job list ends at #11), i.e. the
      submission-preflight caught the dead grant before any state was
      made. This is Task 1's invalid_grant → CREDENTIAL path, live.

## B. Service-account key profile (blocked until a key is obtainable)

- [ ] B1. `mmlct profile add-key ...` — **BLOCKED 2026-08-06:** no
      service-account key obtainable (unchanged since Phase 3 gate B1).
      Admin request pending: bundled ask for (a) an org-internal OAuth
      desktop client (no verification, no 7-day expiry, immune to
      external-app policy) and (b) a least-privilege SA key
      (`roles/storage.objectAdmin` on `afsc_mml_ccep`).
- [ ] B2. Repeat A3–A5 with `gate-key` — blocked on B1.
- [x] B3. Recorded: B stayed blocked; section A closes the
      DPAPI/unattended machinery either way. Re-run B when a key exists.

## C. Automated real-bucket pass

- [x] C1. **PASS — 11 passed** (`-m "real_bucket and not slow"`,
      MMLCT_TEST_BUCKET=afsc_mml_ccep, MMLCT_TEST_PREFIX=scratch): the 10
      release-gate protocol tests plus
      `test_preflight_against_the_real_bucket_leaves_no_versions` — first
      live proof that the product preflight's cleanup is version-aware on
      the real versioned bucket. Two warnings, both benign: the
      pre-existing FastAPI/Starlette testclient deprecation (tracked since
      Plan 4 Task 2; predates the branch) and google-auth's
      "user credentials without a quota project" notice (environmental —
      object-level GCS ops need no quota project; same ADC shape as both
      prior gates).

## Teardown

- [x] `mmlct profile remove --name gate-oauth` → **409 refusal, BY
      DESIGN** (jobs #9–#11 reference the profile; deletion refuses while
      any job does — itself a live demonstration of the in-use guard).
      **Amendment to this checklist:** gate profiles used by gate jobs are
      *retained*, not removed; `gate-oauth`'s credential is revoked (A6),
      so the stored blob is inert. `gate-key` never existed (B blocked).
- [x] Bucket sweep, version-aware, via the ADC client (gcloud CLI not
      used — its token needs interactive reauth): all versions under
      `scratch/phase4-gate/` deleted by explicit generation — **80
      versions deleted, 0 remain** (verified by a `versions=True`
      re-listing). No `.mmlct-preflight` residue at any version — the
      product preflight cleaned up after itself throughout the gate.
- [x] Local gate data removed (`C:\gate-data-small`).
- [x] OAuth grant already revoked in A6. The Testing-status client in the
      personal project may be deleted at the operator's leisure (or kept
      for a future B-section run alongside the admin ask).

## Findings

1. **NOAA Workspace does not block external Testing-status OAuth apps for
   tester-listed accounts.** The decisive unknown going in. Sign-in as
   `peter.mahoney@noaa.gov` succeeded once the account was on the app's
   test-user list. User OAuth is therefore a viable credential path for
   this org — with caveat 2.
2. **Testing-status refresh tokens expire after 7 days.** The gate's
   profile would have died ~2026-08-14 regardless of A6. A durable OAuth
   deployment needs one of: the app published to production (unverified
   warning at sign-in, tokens persist), an org-internal client (admins
   only — requested), or the SA-key path. Phase 5's connection-setup
   wizard copy should reflect this; the packaged client ID decision is
   Phase 6's.
3. **Bridge-config ACL reality, stated plainly:** because the live service
   runs as `.\pmaho` (spec-sanctioned named-account config), the
   credential blob's ACL necessarily grants that same user — so the
   operator's own non-elevated processes can read (and, machine-scope,
   decrypt) the blob. This is inherent to the named-account config, not a
   defect; the packaged LocalSystem default plus installer-managed ACLs
   (Phase 6) restores the stronger boundary. The ACL is otherwise exactly
   as designed: no inheritance, three principals, grants applied by SID.
4. **`Error 403: access_denied` at sign-in = not on the tester list**
   (Testing-status app). Fixed in the Console under Google Auth Platform →
   Audience → Test users. Distinct from an admin "Access blocked" screen,
   which never appeared.
5. **CLI runbook notes for Phase 5:** (a) `--db` is required by
   `status`/`transfer`/`resume` even in service mode where it is unused —
   make it optional when `--service-url` is set; (b) every Windows
   sign-out kills the shell, so session variables and the `mmlct` alias
   must be re-established after each logoff cycle — a non-issue once the
   GUI exists, but worth a note in any CLI runbook.
