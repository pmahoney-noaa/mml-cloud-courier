# Phase 3 Manual Gate — Record

**Date started:** 2026-08-05
**Branch state:** master @ e150c0b (Plan 3 merged; suite 322 passed, 2 skipped)
**Machine:** Windows 11 Pro, Python 3.12 venv at repo root

| # | Check | Result | Notes |
| --- | --- | --- | --- |
| A1 | `mmlct-service install` (elevated) succeeds | pending | |
| A2 | `sc qc MMLCloudTransfer` shows AUTO_START | pending | |
| A3 | `sc qfailure MMLCloudTransfer` shows restart/5000, restart/5000, restart/30000, reset 86400 | pending | |
| A4 | `mmlct-service start` → service RUNNING | in progress | 1053 root-caused: per-user Python install → `python312.dll` invisible to LocalSystem's DLL search (Event 7009, process never connected). postinstall had placed pywintypes312.dll in System32 (necessary, not sufficient). Fix: python312.dll/python3.dll/vcruntime140*.dll copied beside `.venv\pythonservice.exe`. **Packaging finding for Phase 6:** installer must guarantee runtime DLLs resolve for the service identity (PyInstaller onedir sidesteps this entirely). |
| A5 | `/health` responds on 127.0.0.1:47821 | pending | |
| A6 | Token file created; `icacls` shows no inherited ACEs, SYSTEM(+Administrators) only | pending | THE untested path: icacls under LocalSystem |
| A7 | Token readable from user session (after explicit user grant) | pending | Finding feeds Phase 6 installer ACL decision |
| B1 | Non-prod project + STANDARD bucket + service-account key provisioned | pending | Shared with Plan 2 release gate |
| C1 | Multi-GB job submitted via `mmlct transfer --service-url` runs | pending | |
| C2 | **Logoff survival**: job progresses/completes across a full logoff | pending | The property the design exists for |
| C3 | Kill pythonservice.exe mid-transfer → SCM auto-restarts → job auto-resumes → COMPLETE, checksums match | pending | |
| C4 | Network disabled ~5 min mid-run → job `stalled` → recovers on reconnect → COMPLETE | pending | |

## Findings / anomalies

(fill in as observed)
