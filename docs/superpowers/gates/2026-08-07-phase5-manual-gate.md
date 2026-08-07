# Phase 5 Manual Gate — GUI

**Status: CLOSED 2026-08-07 — ALL CHECKS PASS except A4 (BLOCKED, no SA key
obtainable; same pending admin ask as Phase 4 section B). Four live-fire
fixes and one improvement landed on master during the run.**

**Run:** 2026-08-07, operator `pmaho`, GUI + service on merged master
(merge 4ba6686) with live-fire fixes applied mid-gate. The spec's
done-when — "a non-technical user can complete setup, run a transfer, and
resume an incomplete one without touching the CLI" — is **demonstrated**,
with one scoped caveat: the Google sign-in path requires browsing to an
OAuth client JSON until Phase 6 packages a client ID (recorded at A2).

**Done-when under test (spec Phase 5):** a non-technical user can complete
setup, run a transfer, and resume an incomplete one without touching the CLI.

**Environment:** the LIVE service install (auto-start, `.\pmaho`, port
47821). The GUI reads the live token via its default discovery — no env
vars set. Bucket `afsc_mml_ccep`, prefix `scratch/phase5-gate/` (versioning
ON — all bucket cleanup version-aware). OAuth client: the Phase 4 gate's
Testing-status client (sign-ins die after ~7 days — Phase 4 Finding 2 —
fine for a gate run and exactly what the wizard copy must disclose).

## A. Setup without the CLI

- [x] A1. **PASS.** `mmlct-gui` launches; no banner (service running);
      Phase 3/4 jobs visible in Completed.
- [x] A2. **PASS, with the known Phase 5 scope gap recorded:** both path
      descriptions shown; sign-in copy names the ~7-day testing-status
      expiry and steers unattended use toward a key. **Observation:** the
      sign-in path first opens a file browser for the OAuth client JSON
      (`MMLCT_OAUTH_CLIENT` unset) — expected Phase 5 behavior; a
      non-technical user cannot complete this step until Phase 6 packages
      the client ID (already the plan's recorded scope decision).
- [x] A3. **PASS.** Google sign-in end-to-end: browser opened, NOAA
      account signed in, profile created, preflight capability summary in
      plain language.
- [ ] A4. **BLOCKED** — no service-account key obtainable (unchanged since
      the Phase 3/4 gates; bundled admin ask still pending). Re-run when a
      key exists. A3 proves the wizard/profile machinery either way.
- [x] A5. **PASS — carried item 3, live.** With the service stopped, New
      connection disables both credential paths with the "start the
      service first" message; no browser can open.

## B. Run and resume without the CLI

- [x] B1. **PASS.** Wizard upload (job 12, 51 files / ~2.2 GB incl. a
      2 GiB file) to `scratch/phase5-gate/run1`; scan preview showed live
      file/byte counts before Finish.
- [x] B2. **PASS.** ~2 ticks/s, per-file rows, slice-level progress shown
      as "slice N of 2" — 2 GiB file at the default 1 GiB slice policy
      (two-rung ladder is the deliberate default-policy outcome).
- [x] B3. **PASS.** Close-to-tray mid-run ballooned (first-close-only, by
      design); reopen re-rendered from the service DB.
- [x] B4. **PASS — carried item 5, live.** Service killed mid-run: banner
      + "reconnecting" status, GUI survived; banner Start button (UAC)
      restarted the service; job auto-resumed and the watcher re-attached
      unaided; run completed verified.
- [x] B5. **PASS with finding (fixed).** Locked-file job (13) grouped and
      landed in Needs attention — but under *permission denied*, not
      *file locked*: CPython's `open()` collapses ERROR_SHARING_VIOLATION
      into plain EACCES (winerror lost), so the taxonomy's FILE_LOCKED
      branch was unreachable for real reads. Plan 1-era engine bug, first
      exposed here. **Fixed at 11cdb12** (Win32 re-probe; affirmative
      32/33 only) — locked files now group correctly, carry the
      close-and-resume action, and retry as transient. Re-verification
      waived by the operator (covered by a real-lock unit test).
- [x] B6. **PASS.** Lock released → Summary → Resume remaining → COMPLETE
      (file skipped on CRC match); Open report opened report.html.
- [x] B7. **PASS.** Retry these files cleared a fresh locked group;
      Stop retrying excluded another (verdict INCOMPLETE with the
      exclusion visible); Copy file list reached the clipboard.
      **Bonus demonstration (job 14 events):** excluded files survive
      plain Resume *by design* (18:16:25 resume left the excluded file
      untouched; 18:16:41 Errors-tab Retry revived it → COMPLETE) — the
      full exclude/resume/retry lifecycle, live.
- [x] B8. **PASS.** Balloons for complete and needs-attention transitions
      while in the tray (transitions-only; no first-sight replay).

## C. Settings and scheduled start

- [x] C1. **PASS.** Workers 4 → 6 saved; "takes effect the next time the
      transfer service starts" shown; `settings.json` in the live data dir
      carried the staged value; reverted after the restart.
- [x] C2. **PASS.** Scheduled job (15) sat under Queued with its start
      time in the rail and ran on time. **Improvement from this check
      (landed at c577c14):** submission now records a
      `scheduled: waiting until <UTC> to start` event so the timeline —
      not just the rail — explains why a queued job is waiting.

## Teardown

- [x] Version-aware sweep of `scratch/phase5-gate/`: **51 versions deleted
      by explicit generation, 0 remain** (verified by a `versions=True`
      re-listing). Slice temps had already been swept by the engine.
- [x] Gate profile retained (jobs 12–15 reference it; the in-use refusal
      stands, same amendment as Phase 4). OAuth grant left to the
      operator: revoke at myaccount.google.com/permissions at leisure —
      the Testing-status token self-expires ~2026-08-14 regardless.
- [x] Local gate tree (`C:\phase5-gate-data`) deleted.

## Findings

1. **Dark-mode readability (fixed at 9227a0b).** Wizard pages were
   white-on-white (Windows Aero wizard style paints a white canvas under a
   dark palette) and the service banner white-on-pink (background set
   without a paired text color). Same latent bug fixed preemptively in the
   Summary verdict banner. Rule extracted: never set one half of a
   color pair.
2. **Errors tab omitted the taxonomy's suggested-action text (fixed at
   76f47bd).** The API delivered message + action; the tab rendered only
   the message. Now a guidance label under the tree shows the selected
   group's action, first group auto-selected.
3. **Locked files misclassified as permission_denied (fixed at 11cdb12).**
   See B5. Engine-side: live from the C1 restart onward.
4. **Excluded-vs-Resume asymmetry is by design** (see B7 bonus) — worth
   keeping in user docs: Summary "Resume remaining" retries what is still
   eligible; the Errors tab's "Retry these files" is the explicit revival
   of an excluded group.
5. **Queued jobs now explain themselves in the timeline** (c577c14, from
   C2 feedback): `scheduled: waiting until <UTC> to start`.
6. **Phase 6 inputs reaffirmed:** packaged OAuth client ID closes the A2
   observation; the installer owns the token-ACL grant; deferred-minor
   backlog from the Plan 5 final review stands.
