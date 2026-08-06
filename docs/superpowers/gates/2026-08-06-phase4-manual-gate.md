# Phase 4 Manual Gate — Auth and Profiles

**Status: OPEN**

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

## A. OAuth profile (blocked only on creating a client ID — do first)

- [ ] A1. Create a desktop OAuth client: Google Cloud Console > APIs &
      Services > Credentials > Create credentials > OAuth client ID >
      Application type "Desktop app". Download the JSON;
      `$env:MMLCT_OAUTH_CLIENT = "<path>"`. (Any project the operator can
      use works; the client ID only identifies the app, not the bucket.)
- [ ] A2. `mmlct profile login --name gate-oauth --bucket afsc_mml_ccep
      --prefix scratch/phase4-gate --service-url http://127.0.0.1:47821`
      — browser opens, sign in as the operator account. Expect: profile
      created; summary says the credential can list, read, write, compose
      and delete.
- [ ] A3. At-rest check (elevated): the newest file under
      `%ProgramData%\MML Cloud Transfer\credentials\` contains NO plaintext
      (`findstr /I "refresh_token" <file>` finds nothing), and
      `icacls <file>` shows no `(I)` entries.
- [ ] A4. `mmlct transfer --profile gate-oauth --name p4-oauth --source
      C:\gate-data-small --service-url http://127.0.0.1:47821` — then
      **sign out immediately**. Sign back in after the expected duration:
      job COMPLETE, audit clean, report written. Corroborate the logoff
      window with `quser` (Phase 3 C2 technique).
- [ ] A5. Restart the service (`mmlct-service restart` or sc) and run a
      second job with the same profile: the stored credential survives a
      service restart (machine-scope DPAPI, not session-bound).
- [ ] A6. Revoke the app's access at myaccount.google.com/permissions,
      then submit a job with the profile: submission is refused with the
      capability summary (or the job pauses with a CREDENTIAL error if
      revocation lands mid-run) — and NOT a stall loop. This is the
      invalid_grant path of Task 1 live.

## B. Service-account key profile (blocked until a key is obtainable)

- [ ] B1. `mmlct profile add-key --name gate-key --bucket <bucket>
      --key-file <key.json> --service-url http://127.0.0.1:47821` —
      expect validation summary + "you may delete the original file".
- [ ] B2. Repeat A3–A5 with `gate-key`.
- [ ] B3. Record here if no key was obtainable and B stayed blocked; the
      OAuth path (A) alone satisfies "either credential type" only if B is
      re-run when a key exists — the spec says both paths converge, and A
      proves the DPAPI/unattended machinery either way.

## C. Automated real-bucket pass

- [ ] C1. `$env:MMLCT_TEST_BUCKET="afsc_mml_ccep";
      $env:MMLCT_TEST_PREFIX="scratch"` then
      `.venv/Scripts/python -m pytest -m "real_bucket and not slow" -v` —
      all pass (now includes the Task 6 preflight probe test).

## Teardown

- [ ] Remove gate profiles: `mmlct profile remove --name gate-oauth ...`
      (and `gate-key`); confirm their blobs are gone from
      `%ProgramData%\MML Cloud Transfer\credentials\`.
- [ ] `gcloud storage ls --all-versions --recursive
      "gs://afsc_mml_ccep/scratch/phase4-gate/**"` (or the ADC
      version-aware listing if gcloud needs reauth) — matches no objects.
- [ ] Local gate data dirs removed.
- [ ] Revoke the gate OAuth grant at myaccount.google.com/permissions if
      the profile is not being kept.
