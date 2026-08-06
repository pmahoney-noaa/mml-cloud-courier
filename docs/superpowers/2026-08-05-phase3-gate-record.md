# Phase 3 Manual Gate — Record

**Date started:** 2026-08-05
**Branch state:** master @ e150c0b (Plan 3 merged; suite 322 passed, 2 skipped)
**Machine:** Windows 11 Pro, Python 3.12 venv at repo root

| # | Check | Result | Notes |
| --- | --- | --- | --- |
| A1 | `mmlct-service install` (elevated) succeeds | **PASS** | After remove+install cycle for the python.exe-hosting fix (676e9a0) |
| A2 | `sc qc MMLCloudTransfer` shows AUTO_START | **PASS** | START_TYPE: 2 AUTO_START |
| A3 | `sc qfailure MMLCloudTransfer` shows restart/5000, restart/5000, restart/30000, reset 86400 | **PASS** | Verified 2026-08-05 15:3x |
| A4 | `mmlct-service start` → service RUNNING | **PASS** | Took THREE root-cause fixes — see Findings 1–2 below (pythonservice DLL search; pythonservice venv-root site-packages; icacls 1332 on workgroup LocalSystem). Now STATE: 4 RUNNING hosted by the venv's python.exe. |
| A5 | `/health` responds on 127.0.0.1:47821 | **PASS** | status ok, version 0.1.0, active_job_id null |
| A6 | Token file created; `icacls` shows no inherited ACEs, SYSTEM(+Administrators) only | **PASS** | Elevated view: NT AUTHORITY\SYSTEM:(F) + BUILTIN\Administrators:(F) (user-confirmed), no (I) entries. Non-elevated icacls: Access denied — the ACL denies even reads to filtered-admin tokens, confirming the lockdown is real. |
| A7 | Token readable from user session (after explicit user grant) | **PASS** | Pre-grant: non-elevated access DENIED (correct — UAC filtered token). After elevated `/grant "MATRIX\pmaho:(R)"`: non-elevated `mmlct status --service-url` printed "No jobs." **Phase 6 decision input:** the installer must add an explicit per-user (or group) read grant for the GUI; Administrators membership alone is not enough under UAC. |
| B1 | Bucket + credentials the service can use unattended | **ADAPTED** | Service-account key unobtainable near-term. Decision: run the service as the user's account (`MATRIX\pmaho`) so it sees the user's ADC — the spec's own named-account configuration ("LocalSystem is the default only for local-disk-only installations"). Session-0 logoff survival is unaffected. Reusing the release-gate bucket `afsc_mml_ccep` (versioning ON; buckets.get denied) under `scratch/phase3-gate/*` prefixes. LocalSystem+key remains the packaged default; Phase 4's DPAPI/OAuth credential store is the permanent fix for service-owned credentials. |
| C1 | Multi-GB job submitted via `mmlct transfer --service-url` runs | **PASS** | 2026-08-06: job 1, 201 files / 2.51 GiB → `scratch/phase3-gate/run1`, COMPLETE in 118 s (~23 MB/s), 0 failed, ADC via `.\pmaho` service. Worker wrote summary.json/manifest.csv/report.html; CLI SSE watch exited 0. Preflight passed same session (write/compose/delete OK; expected metadata WARNs). |
| C2 | **Logoff survival**: job progresses/completes across a full logoff | **PASS** | 2026-08-06: job 2 (201 files / 2.51 GiB → `scratch/phase3-gate/run2`), run_started 13:03:46Z; user signed out moments later; job reached COMPLETE at 13:05:49Z with 0 failures, audit 201 checked / 0 mismatches, report written by the worker — all with no user session. Corroboration: `quser` shows sign-back-in at 13:11Z, 5.5 min after finish. **The design's core promise, demonstrated.** |
| C3 | Kill pythonservice.exe mid-transfer → SCM auto-restarts → job auto-resumes → COMPLETE, checksums match | pending | |
| C4 | Network disabled ~5 min mid-run → job `stalled` → recovers on reconnect → COMPLETE | pending | |

## Applied lessons from the Plan 2 release gate (docs/superpowers/gates/2026-08-05-plan2-release-gate.md)

1. **Preflight before bytes.** Run `pwsh tests/tools/preflight-gcs.ps1 -Bucket afsc_mml_ccep -Prefix scratch` before C1; expect (and accept) metadata WARNs — `storage.buckets.get` is denied and the write/compose/delete probes are the real check. It also validates that the exact ADC the user-account service will use can do everything the runs need.
2. **Versioning changes every cleanup.** `afsc_mml_ccep` has versioning ON: a plain delete archives, it does not remove. All Stage C teardown uses `gcloud storage rm --all-versions` and all emptiness verification uses `gcloud storage ls --all-versions --recursive`. A live-only "clean" is not clean.
3. **Crash-orphaned slice temps do not self-clean.** The compose-time sweep (Finding 5 fix) only runs on a job that reaches a successful compose. Any Stage C run abandoned INCOMPLETE leaves `<object>.mmlct.tmp/<nnnn>` versions that nothing will ever remove (no lifecycle rule is applied, and none can match the infix). Teardown must explicitly check for and purge `*.mmlct.tmp*` at all versions.
4. **Measure the uplink first.** The same 2.6 GiB run was a non-finishing ~6 h ordeal at ~1 Mbps and a 3m47s pass at ~100 Mbps, with 8 MiB chunks brushing the 120 s socket timeout on the slow link. Before C1: quick throughput check; if the link is slow that day, the logoff test (C2) can still run — slow is realistic overnight behavior — but C3/C4 iterations should use the small-file tree, not the 2.5 GiB file.
5. **Time the C3 kill by slice state, not by feel.** The release gate killed when `file_slices` showed a live `session_uri` with `0 < bytes_transferred < length_bytes` — a provable mid-sliced-file kill. Stage C does the same by querying the service's `jobs.db` read-only during the run.
6. **One writer per destination, by discipline.** Known residual (release-gate follow-up 3): the per-job lock does not stop a second `transfer` to the same destination from creating a second job. Stage C uses a fresh `scratch/phase3-gate/runN` prefix per run, never re-issues `transfer` for a prefix in flight, and never mixes direct-mode CLI transfers with service jobs on the same prefix. (Within the service, the FIFO worker serializes jobs anyway.)
7. **Manual runs have no pytest teardown.** The release gate's stranded-bytes incident happened when a dying session skipped session-scoped cleanup. Everything Stage C writes is cleaned explicitly at the end against checklist items, not implicitly.
8. **Default size policy is the point.** C-runs use no `--size-policy`, so the 2.5 GiB file slices at real 1 GiB boundaries (3 components) — the exact configuration the release gate proved end-to-end, now exercised through the service instead of the CLI runner.

## Findings / anomalies

1. **pythonservice.exe hosting structurally broken here** (fixed, 676e9a0): per-user Python → python312.dll invisible to LocalSystem (Event 7009); venv-root relocation → no site-packages → `ModuleNotFoundError: servicemanager` (Event 14). Service now hosted by the venv's python.exe via PrepareToHostSingle.
2. **icacls account-name grant fails on workgroup machines** (fixed, 6e45d4a): LocalSystem presents `WORKGROUP\MACHINE$`; icacls error 1332 in `restrict_acl` crashed startup (traceback in MMLCloudTransfer Event 3). Grants now use the process token's SID (`whoami /user`), which needs no name mapping; empty half-created token files self-heal on the next start.
3. Phase 6 implication: both findings vanish under PyInstaller onedir packaging + installer-managed ACLs, validating those plan choices — but the installer must still grant by SID, not name.
