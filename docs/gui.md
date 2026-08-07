# MML Cloud Courier — GUI notes

Launch: `mmlcc-gui` (or `python -m mml_cloud_courier.gui`). Requires the
`gui` extra (`pip install -e ".[gui]"`; dev installs already include it).

The GUI is a thin client of the Windows service. It keeps no transfer
state: closing it (which minimizes to the tray) or logging off does not
affect running jobs; reopening re-renders everything from the service.

Discovery: the service URL and bearer token come from the service data
directory (`%ProgramData%\MML Cloud Courier`, or `MMLCC_DATA_DIR`), with
`MMLCC_SERVICE_URL` / `MMLCC_TOKEN_FILE` as explicit overrides (used by
tests). If the token is unreadable the window says so; the account needs
read access to the data directory (the installer will own this grant in
Phase 6).

Banner "service is not running": the Start button asks for elevation via
UAC (`sc start MMLCloudCourier`). Everything else in the GUI works
unelevated.

OAuth client configuration for the Google sign-in path comes from
`MMLCC_OAUTH_CLIENT` or a browsed `client_secret_*.json` until Phase 6
packages a client ID.
