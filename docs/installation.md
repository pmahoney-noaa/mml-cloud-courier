# MML Cloud Courier — installation guide

This is the operator guide for installing, upgrading, and removing MML
Cloud Courier from a Windows workstation using the packaged setup.exe.
For building that installer yourself, see "Building a release" at the
end.

## Install

Run `mml-cloud-courier-setup-<version>.exe` elevated (right-click, "Run
as administrator"), **logged in as the user who will run the GUI**. The
installer reads the elevating user's SID and grants it read access to
the service's API token, via a file the service consults on every token
creation (`%ProgramData%\MML Cloud Courier\gui-users.sids`). If you run
the installer as a different account than the one who will use the GUI,
that GUI user won't be able to reach the service until you add their
SID by hand — see "Multiple GUI users" below.

Windows SmartScreen may warn that the installer or the app is from an
unrecognized publisher. The binaries are not code-signed. For this
internal deployment, that warning is expected — choose "More info" then
"Run anyway" to proceed.

The installer creates Start Menu and (optionally) desktop shortcuts for
the GUI, and can optionally add the install folder to `PATH` so the
`mmlcc` command-line tool is available from any shell (see "CLI"
below).

## Service account

A first-time install registers the Windows service to run as
**LocalSystem**, auto-starting. That's the packaged default, and it's
paired with a service-account key for Google Cloud Storage access.

If this machine instead needs a named service account — for example to
use a user's Application Default Credentials (ADC) rather than a
service-account key — set that up after the first install:

1. Open `services.msc`.
2. Find "MML Cloud Courier Service", open its Properties, and go to
   the **Log On** tab.
3. Switch "Log on as" to "This account" and enter the account (for
   example `.\svcaccount`) and its password.

Setting the account from that tab also grants it the "Log on as a
service" right automatically — `sc.exe config` alone does not do this,
so don't try to reassign the account by editing the service with `sc`.

**Upgrades never reset the service account.** The installer only
re-registers the service (and only then touches the log-on account) when
the service doesn't exist yet, or when its `ImagePath` no longer points
at the freshly installed executable. A normal upgrade — same install
location, service already registered — stops the service, replaces the
files, and restarts it without re-registering anything. A configured
named account survives upgrades.

If registration does fail (rare — usually a permissions problem), the
installer shows an error dialog naming the exact command to run
yourself from an elevated command prompt (either the service exe's
`install` or `update` verb, depending on which case triggered it).

## Upgrade

Close the GUI, then run the new version's setup.exe elevated, the same
way as a first install. The installer stops the running service,
replaces the installed files, and starts the service again. As
described above, no re-registration happens on a normal upgrade, so the
service account and its permissions are left alone.

Data is untouched by an upgrade: `jobs.db`, saved settings, and stored
credentials (the DPAPI-protected credential blobs) all live under
`%ProgramData%\MML Cloud Courier` and are never touched by the
installer's file replacement step.

## Uninstall

Uninstall from Windows' "Apps & features" (or "Add or Remove Programs")
list, same as any other application. Uninstalling stops and deletes the
Windows service, and removes the install folder's entry from `PATH` if
one was added.

Uninstalling **deliberately leaves `%ProgramData%\MML Cloud Courier`
in place** — that's where jobs, settings, and the encrypted credential
blobs live. If you want a full removal (for example, decommissioning a
workstation), delete that folder by hand after the uninstall finishes.

## Multiple GUI users

Only the user who ran the installer gets automatic read access to the
service's API token. To let additional Windows accounts run the GUI on
the same machine, add their SIDs to the reader list:

1. Have each additional user run `whoami /user` to find their SID (the
   `S-1-...` string).
2. Append each SID on its own line to
   `%ProgramData%\MML Cloud Courier\gui-users.sids` (lines starting with
   `#` are comments and are ignored).

Newly added SIDs take effect automatically the next time the service
creates a fresh token (for example after a token file is deleted or
regenerated). To grant access immediately against the *existing* token
without waiting for that, run, elevated:

```
icacls "%ProgramData%\MML Cloud Courier\api_token" /grant *<SID>:(R)
```

replacing `<SID>` with the SID you added.

## OAuth client configuration

The installer does not bundle an OAuth client for the Google sign-in
path. Configure it exactly as in a development install: point
`MMLCC_OAUTH_CLIENT` at a client secret, or browse to a
`client_secret_*.json` file from the GUI's sign-in screen. Packaging an
org-internal OAuth client so this step isn't needed per machine remains
an open decision for a future release.

## CLI

If you checked "Add the install folder to PATH" during install, the
`mmlcc` command-line tool is available from any new shell. If you
didn't check that task (or need it from a shell opened before install),
run it by full path instead:

```
"%ProgramFiles%\MML Cloud Courier\mmlcc.exe"
```

## Building a release

This section is for developers producing a new setup.exe, not for
operators installing one.

Prerequisites, in a Python 3.12 virtual environment at the repo root:

```
pip install -e ".[dev,build]"
```

You also need Inno Setup 6, which compiles `packaging\mmlcc.iss` into
the setup.exe. Install it with winget:

```
winget install -e --id JRSoftware.InnoSetup
```

Either a machine-wide or per-user install works — the build script
checks both of Inno Setup's usual install locations for `ISCC.exe`.

Build with:

```
pwsh packaging/build_release.ps1
```

This runs PyInstaller to produce the onedir bundle under
`dist\mml-cloud-courier`, then invokes Inno Setup to compile the
installer from `packaging\mmlcc.iss`. The version number is read from
`pyproject.toml`; the resulting installer is written to
`dist\mml-cloud-courier-setup-<version>.exe`, matching the name
operators run in "Install" above. Pass `-SkipInstaller` to stop after
the PyInstaller bundle, skipping the Inno Setup step.
