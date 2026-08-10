<p align="center">
  <img src="src/mml_cloud_courier/gui/assets/mark-128.png" alt="MML Cloud Courier" width="96"/>
</p>

# MML Cloud Courier

Verified, resumable file transfers between Windows workstations and Google
Cloud Storage, built for the NOAA Fisheries Marine Mammal Laboratory.

Large field datasets need to reach cloud storage intact, unattended, and
without babysitting flaky links. MML Cloud Courier runs as a Windows service
that keeps transfers moving through network outages, sign-outs, and machine
restarts — every file integrity-verified end to end (CRC32C, plus SHA-256
manifests), every job resumable from exactly where it stopped.

## Shape

| Piece | What it is |
|---|---|
| **Service** (`mmlcc-service`) | Windows service hosting the transfer engine and a localhost API. Runs in session 0, so jobs survive user logoff; auto-starts and restarts on failure. |
| **GUI** (`mmlcc-gui`) | Desktop app for setting up connections, submitting transfers, and watching progress — live per-file state, checksums, error triage, tray notifications. Closes to the tray; transfers keep running. |
| **CLI** (`mmlcc`) | Scriptable interface: scan, transfer, resume, status, report, profile management. |

Key behaviors: sliced parallel uploads for large files with compose-and-verify;
automatic retry/stall handling tuned for multi-hour outages; job archiving;
HTML/CSV transfer reports with per-file checksums; OAuth (per-user, DPAPI-
protected) or service-account-key auth per connection profile.

## Install

Packaged installs use the Inno Setup installer produced by
`packaging/build_release.ps1` (`mml-cloud-courier-setup-<version>.exe`).
See **[docs/installation.md](docs/installation.md)** for install, upgrade,
uninstall, service-account, and multi-user guidance.

## Development

- Python 3.12, Windows. `py -3.12 -m venv .venv` then
  `pip install -e ".[dev]"` (add `,build` for packaging work).
- Tests: `python -m pytest -o addopts= -q`. Emulator-backed tests need
  `tools/fake-gcs-server.exe` — fetch with
  `pwsh tests/tools/get-fake-gcs-server.ps1`.
- Design/specs/plans live under `docs/superpowers/`; operator docs under
  `docs/`.

## Status

Private repository; not currently accepting external contributions. Any
change to repository visibility is gated on NOAA/DOC open-source policy
review.

## Disclaimer

This repository is a scientific product and is not official communication of
the National Oceanic and Atmospheric Administration, or the United States
Department of Commerce. All NOAA GitHub project code is provided on an ‘as
is’ basis and the user assumes responsibility for its use. NOAA and DOC have
relinquished control of the information and no longer has responsibility to
protect the integrity, confidentiality, or availability of the information.
Any claims against the Department of Commerce or Department of Commerce
bureaus stemming from the use of this GitHub project will be governed by all
applicable Federal law. Any reference to specific commercial products,
processes, or services by service mark, trademark, manufacturer, or
otherwise, does not constitute or imply their endorsement, recommendation or
favoring by the Department of Commerce. The Department of Commerce seal and
logo, or the seal and logo of a DOC bureau, shall not be used in any manner
to imply endorsement of any commercial product or activity by DOC or the
United States Government.
