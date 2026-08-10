# Phase 6 — Packaging and Installer — Design

**Date:** 2026-08-10
**Status:** Approved (brainstorm 2026-08-10; installer tech, service-account
handling, and end-state all user-selected)
**Sequencing:** Second sub-project of the round icons → packaging → README.
Consumes the icon sub-project's committed `mmlcc.ico`.

## Goal

A single `setup.exe` that installs MML Cloud Courier — service, GUI, CLI — on a
Windows workstation, upgrades it in place, and uninstalls it cleanly, proven by a
manual gate on this machine ending in **permanent cutover** of the live install
to the packaged build.

## Binding constraints (violations are plan defects)

- **PyInstaller onedir.** Onefile is forbidden: onedir kills the hosting-DLL
  failure class found in Phase 3.
- **The service is hosted in the packaged interpreter.** `pythonservice.exe` is
  structurally broken for per-user-Python venvs (Phase 3, fix 676e9a0); the SCM
  ImagePath must point at our own packaged `mmlcc-service.exe`.
- **ACLs are granted by SID, never account names** (6e45d4a lesson): the
  installer grants the installing user's SID on the data dir via `icacls`.
- **Any fresh service registration lands on LocalSystem** — restoring the
  `.\pmaho` log-on on this machine is a documented, gate-checked step
  (rename-round lesson). Design mitigation: upgrades never re-register.
- **Tests never touch the live install** (port 47821,
  `%ProgramData%\MML Cloud Courier`); the QSettings isolation fixtures stay
  authoritative. Packaging adds no test that violates this.
- **OAuth client config is never bundled.** It remains the `MMLCC_OAUTH_CLIENT`
  env var or a browsed file. Bundling would share one client and inherit its
  Testing-status 7-day refresh-token expiry. The packaged-client decision
  (org-internal client + SA key) is **gated on the pending admin ask** and is
  surfaced to the user as an open decision point, not resolved by this design.
- **The repo folder stays `mml_cloud_transfer`** (venv `pyvenv.cfg` + live
  service ImagePath bake absolute paths).

## Decisions (user-selected)

1. **Installer technology: Inno Setup.** Single `setup.exe`, Pascal scripting for
   service/icacls steps, Apps-list entry, standard upgrade/uninstall UX. Adds one
   dev tool (ISCC.exe) invoked by the build script.
2. **Service account: docs + upgrade-preserves.** First install registers the
   service as LocalSystem (the sanctioned packaged default; LocalSystem + SA key
   is the packaged credential story, DPAPI/OAuth profiles the permanent fix).
   Upgrades stop → replace files → start **without re-registering**, so a
   configured log-on account (this machine: `.\pmaho`) survives every upgrade.
   The finish page + docs carry the services.msc Log On step for named-account
   configs (services.msc also auto-grants the logon-as-service right; `sc.exe
   config` does not).
3. **End state: permanent cutover.** After the gate, the packaged install is the
   live service on this machine; the dev venv remains for tests and CLI work
   only. Future fixes reach the service via installer upgrade — a path the gate
   itself proves.

## Build

### PyInstaller

One spec file, **onedir**, producing three exes into one shared COLLECT folder:

| Exe | Mode | Entry | Notes |
|---|---|---|---|
| `mmlcc-gui.exe` | windowed | `mml_cloud_courier.gui.__main__:main` | carries `mmlcc.ico` |
| `mmlcc.exe` | console | `mml_cloud_courier.cli.__main__:main` | |
| `mmlcc-service.exe` | console | service host (see below) | SCM ImagePath target |

- `mmlcc-service.exe` must support both the CLI verbs (`install`, `remove`,
  `start`, `stop`, `restart`, plus existing flags) **and** SCM dispatch when
  started by the Service Control Manager. Under PyInstaller this is the
  frozen-service recipe: detect the SCM-launch context and hand control to
  `servicemanager` (`PrepareToHostSingle`) instead of `HandleCommandLine`
  argument parsing. The existing `windows_service.py` install path must register
  ImagePath = the packaged exe when frozen (today it bakes the venv
  `python.exe`; the 676e9a0 hosting decision generalizes to "host in whatever
  interpreter is running").
- `gui/assets/` (PNGs + ico) must be collected into the bundle as datas so the
  `importlib.resources` loader finds them frozen.
- Hidden-import sweep for pywin32/servicemanager, google-crc32c native wheel,
  uvicorn/fastapi dynamic imports — verified by the gate's real service run and
  a packaged smoke checklist, not guessed at in the spec.

### Build script — `tools/build_release.ps1`

`.venv` → `pyinstaller` → `ISCC` → `dist/mml-cloud-courier-setup-<version>.exe`.
Version is read from `pyproject.toml` (single source) and stamped into the exe
version resources and the Inno `AppVersion`. The round bumps the project version
to **0.2.0** for the first packaged build and **0.2.1** for the gate's upgrade
test (both real commits); christening 1.0.0 stays the user's call later.
PyInstaller and Inno artifacts (`build/`, `dist/`) are gitignored.

## Installer behavior — `installer/mmlcc.iss`

- **Install dir:** `{autopf}\MML Cloud Courier`. **Data dir:**
  `%ProgramData%\MML Cloud Courier` (created if absent).
- **Tasks:** Start Menu shortcut for the GUI (always); optional desktop icon;
  optional add-install-dir-to-PATH for the CLI.
- **First install** (service not yet registered): register `MMLCloudCourier`
  (auto-start, LocalSystem) via `mmlcc-service.exe install`, create the data dir,
  `icacls` grant to the installing user's **SID** (resolved from the running
  installer process), start the service, finish-page note about the Log On step
  for named-account configs.
- **Upgrade** (service already registered, ImagePath already the packaged exe):
  stop service → replace files → start service. No re-registration, no ACL
  changes, no data-dir writes. Log-on account and DPAPI blobs untouched.
  (If a future version must change ImagePath, that release's notes carry the
  account-restore step — the installer detects ImagePath mismatch and
  re-registers, then shows the Log On reminder.)
- **Uninstall:** stop + delete the service, remove program files and shortcuts,
  **leave the data dir** (jobs.db, settings.json, DPAPI blobs) in place.
- Unsigned binaries: SmartScreen/Defender may warn on first run — internal
  deployment accepts this; noted in docs. Code signing is out of scope.

## Gate — defined here, recorded in `docs/superpowers/gates/`

Sequence on this machine (live install, real data — each step recorded):

1. Suite green in the worktree before any live-machine step (recorded counts,
   `-o addopts= -q`).
2. Build 0.2.0; packaged smoke: `mmlcc.exe --help`, GUI launches standalone,
   `mmlcc-service.exe` verbs respond.
3. Stop the venv-hosted live service.
4. Run setup.exe (first-install path: re-registers the service onto the
   packaged exe — the one intentional re-registration of the round).
5. Restore `.\pmaho` via services.msc (documented step under test).
6. Start service. **Pending-activation checks fold in here:** jobs DB migrates
   v2→v3; archive/unarchive works from the GUI (endpoints live); "Open report"
   renders the files table (server-side generation now ≥ 1b4746f).
7. Existing jobs + profiles intact; GUI launched from the Start Menu shows the
   new mark; small real transfer completes with CRC verification.
8. Logoff survival quick check (hosting changed → re-prove the Phase 3 result).
9. Build 0.2.1; upgrade install over 0.2.0: log-on account **preserved**, data
   intact, service serves the new version.
10. Uninstall → data dir survives, service gone; reinstall 0.2.1 → restore
    `.\pmaho` → everything intact. Final state: packaged 0.2.1 live (cutover).

## Tests

The pytest suite is unaffected by packaging (nothing imports the spec/iss/build
script); it must stay green through any incidental code changes (service host
refactor for frozen support is the one code-touching area — it needs unit
coverage for the frozen/non-frozen dispatch decision, mockable without SCM).
Everything artifact-shaped is verified by the gate checklist, not pytest.

## Out of scope

Code signing, MSI/GPO deployment, auto-update, bundling any OAuth client or SA
key, multi-machine rollout docs beyond the README pointer, GUI autostart at
sign-in, renaming the repo folder.

## Risks / open points

- **Frozen service hosting** is the highest-risk unknown (pywin32 + PyInstaller
  interplay). Mitigation: it is the gate's step 2/6 focus; the venv-hosted
  fallback remains available throughout (registration can be pointed back at
  the venv python at any time).
- **Packaged-client OAuth decision** stays open pending the admin ask; nothing
  in this round forecloses either outcome.
- The gate touches the real live install by design (that is its purpose); the
  fallback path above is the rollback story.
