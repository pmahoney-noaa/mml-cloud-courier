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
| C1 | Multi-GB job submitted via `mmlct transfer --service-url` runs | pending | |
| C2 | **Logoff survival**: job progresses/completes across a full logoff | pending | The property the design exists for |
| C3 | Kill pythonservice.exe mid-transfer → SCM auto-restarts → job auto-resumes → COMPLETE, checksums match | pending | |
| C4 | Network disabled ~5 min mid-run → job `stalled` → recovers on reconnect → COMPLETE | pending | |

## Findings / anomalies

1. **pythonservice.exe hosting structurally broken here** (fixed, 676e9a0): per-user Python → python312.dll invisible to LocalSystem (Event 7009); venv-root relocation → no site-packages → `ModuleNotFoundError: servicemanager` (Event 14). Service now hosted by the venv's python.exe via PrepareToHostSingle.
2. **icacls account-name grant fails on workgroup machines** (fixed, 6e45d4a): LocalSystem presents `WORKGROUP\MACHINE$`; icacls error 1332 in `restrict_acl` crashed startup (traceback in MMLCloudTransfer Event 3). Grants now use the process token's SID (`whoami /user`), which needs no name mapping; empty half-created token files self-heal on the next start.
3. Phase 6 implication: both findings vanish under PyInstaller onedir packaging + installer-managed ACLs, validating those plan choices — but the installer must still grant by SID, not name.
