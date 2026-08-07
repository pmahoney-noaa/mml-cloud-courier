# Phase 5 Manual Gate — GUI

**Status: OPEN**

**Done-when under test (spec Phase 5):** a non-technical user can complete
setup, run a transfer, and resume an incomplete one without touching the CLI.

**Environment:** the LIVE service install (auto-start, `.\pmaho`, port
47821). The GUI reads the live token via its default discovery — no env
vars set. Bucket `afsc_mml_ccep`, prefix `scratch/phase5-gate/` (versioning
ON — all bucket cleanup MUST be version-aware). OAuth client: the Phase 4
gate's Testing-status client (note: its sign-ins die after ~7 days —
Finding 2 — which is fine for a gate run and exactly what the wizard copy
must disclose).

## A. Setup without the CLI

- [ ] A1. Launch `mmlct-gui`. The main window opens with no banner (service
      running) and the Phase 3/4 jobs visible in Completed.
- [ ] A2. Connections → New connection → the two path descriptions are
      shown; verify the sign-in copy names the ~7-day testing-status expiry
      and steers unattended use toward a key (spec recommendation,
      gate Finding 2).
- [ ] A3. Google sign-in path end-to-end: browser opens, NOAA account
      signs in, profile is created, the preflight capability summary reads
      in plain language ("This credential can list, read, write, compose
      and delete…"). **Do this before any transfer so the profile exists.**
- [ ] A4. Service-account key path: BLOCKED while no SA key exists (Phase 4
      gate section B still pending the admin ask). When a key arrives:
      browse to it, profile validates, the "you may delete the original
      file" message appears. Record either way.
- [ ] A5. Stop the service (elevated `Stop-Service MMLCloudTransfer`), open
      New connection again: both credential paths are disabled with the
      "start the service first" message — no browser ever opens (carried
      item 3, live). Restart the service.

## B. Run and resume without the CLI

- [ ] B1. New Transfer wizard: upload, the A3 profile, a ~200 MB local
      tree, prefix `scratch/phase5-gate/run1`. The review step's scan
      preview shows a live file/byte count before Finish.
- [ ] B2. Progress tab shows ~2 ticks/second, per-file rows, and — with at
      least one file over the slice threshold — slice-level progress
      ("slice N of M"). Rail shows the job under Running.
- [ ] B3. Close the window mid-run → tray balloon says transfers continue;
      reopen from the tray → progress re-renders from the service DB.
- [ ] B4. Kill the service mid-run (elevated `Stop-Service`): banner
      appears, status bar says reconnecting, the GUI does NOT die. Start
      the service via the banner button (UAC prompt) → job auto-resumes,
      watcher re-attaches on its own, run completes VERIFIED (carried
      item 5, live).
- [ ] B5. Force an INCOMPLETE: lock one source file exclusively (open it
      with `[System.IO.File]::Open(...)` in a PowerShell that stays up),
      run a second job over that tree. Errors tab groups the failure under
      "file is open in another program" with the suggested action; job
      lands in Needs attention.
- [ ] B6. Release the lock, Summary tab → Resume remaining → job completes
      COMPLETE. Open report → report.html opens in the browser.
- [ ] B7. Errors-tab group actions on a fresh locked-file failure: Retry
      these files (after unlocking) clears the group; on another, Stop
      retrying excludes it and the job's verdict reads INCOMPLETE with the
      exclusion visible in the report. Copy file list puts the paths on
      the clipboard.
- [ ] B8. Balloon notifications observed for complete and needs-attention
      transitions while the window sits in the tray.

## C. Settings and scheduled start

- [ ] C1. Settings dialog: set workers 4 → 6, Save, message says it takes
      effect at next service start; `settings.json` in the live data dir
      shows the value; set it back.
- [ ] C2. Wizard "Start later": schedule a small job 3 minutes out; rail
      shows it under Queued with the start time; it runs on time.

## Teardown

- [ ] Version-aware sweep of `scratch/phase5-gate/` (delete by explicit
      generation; verify zero versions remain).
- [ ] Remove gate profiles unless referenced by jobs (the in-use refusal
      is itself a pass), revoke the OAuth grant if a fresh one was made.
- [ ] Delete the local gate tree.
