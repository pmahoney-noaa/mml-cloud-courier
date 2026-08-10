# Phase 6 Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `setup.exe` that installs/upgrades/uninstalls MML Cloud Courier (service + GUI + CLI) from a 3-exe PyInstaller onedir bundle, proven by a manual gate ending in permanent cutover of this machine's live install.

**Architecture:** Two small code seams first (token reader-SID grants; frozen service hosting), then pure build machinery: `packaging/` holds the PyInstaller spec, entry scripts, version-info generator, Inno Setup script, and `build_release.ps1`. The pytest suite never touches artifacts; the gate (controller + user) proves them.

**Tech Stack:** PyInstaller (onedir), Inno Setup 6 (ISCC), pywin32 service hosting, hatchling (unchanged), PowerShell 7 build script.

**Spec:** `docs/superpowers/specs/2026-08-10-phase6-packaging-design.md` (including its two planning amendments).

## Global Constraints

- Baseline suite at packaging start: **726 passed / 13 skipped** expected (post-icon-merge; record the actual at worktree setup) via `.venv\Scripts\python -m pytest -o addopts= -q`. Bare `-q` drops the final summary line on this host — never estimate counts.
- **PyInstaller onedir only.** Never onefile (Phase 3 hosting-DLL failure class).
- **SCM ImagePath = packaged `mmlcc-service.exe`** when frozen; venv `python.exe <module>` hosting stays byte-identical for dev (existing `test_service_is_hosted_by_the_venv_python_not_pythonservice` must keep passing unmodified).
- **ACL grants use raw SIDs only** (`*S-1-…`), never account names (6e45d4a).
- **Tests and tasks NEVER touch the live install or SCM state**: no `install|remove|start|stop|restart|update` verbs against any `mmlcc-service` (venv or dist exe), no running `setup.exe`, nothing on port 47821 or `%ProgramData%\MML Cloud Courier`. The gate (controller + user) is the only place that happens. Allowed exception: running `dist\...\mmlcc-service.exe` with **no arguments** — the dispatcher fails fast with error 1063 and mutates nothing.
- **No OAuth client config is bundled** — `MMLCC_OAUTH_CLIENT` env var or browsed file stays the mechanism; the packaged-client decision stays open (pending admin ask).
- Repo folder stays `mml_cloud_transfer`.
- One commit per task; never amend; never bare `git stash`. Every dispatch cds into the worktree FIRST and re-verifies `git rev-parse --show-toplevel` + expected parent commit before each commit.

## Worktree setup (controller, before Task 1)

Prerequisite: the icon sub-project is merged to master and pushed (Task 3 bakes `gui/assets/mmlcc.ico` into the exes).

1. Push master to origin, then `EnterWorktree` (name: `phase6-packaging`).
2. `py -3.12 -m venv .venv` then `.venv\Scripts\python -m pip install -e ".[dev]"`.
3. `mkdir tools` + copy `tools\fake-gcs-server.exe` from the main repo.
4. Baseline run recorded: `.venv\Scripts\python -m pytest -o addopts= -q`.
5. Before Task 4 only: verify Inno Setup 6 is installed (`Test-Path "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"`). If absent, the CONTROLLER asks the user to run `winget install -e --id JRSoftware.InnoSetup` (elevation is the user's) — subagents never fight UAC.

---

### Task 1: Token reader grants (`gui-users.sids`)

**Files:**
- Modify: `src/mml_cloud_courier/service/security.py`
- Test: `tests/service/test_security.py` (append)

**Interfaces:**
- Consumes: existing `restrict_acl(path)`, `ensure_token(path) -> str`.
- Produces: `_reader_sids(directory: Path) -> list[str]` and `_grant_readers(path: Path) -> None` in `security.py`; `ensure_token` now applies `(R)` grants for every SID listed in `<token dir>\gui-users.sids` each time it creates a token. Task 4's installer writes that file.

- [ ] **Step 1: Write the failing tests**

Append to `tests/service/test_security.py`:

```python
def test_reader_sids_parses_sids_skipping_comments_blanks_and_junk(tmp_path):
    (tmp_path / "gui-users.sids").write_text(
        "# gui users\n\nS-1-5-21-111-222-333-1001\nnot-a-sid\n"
        "s-1-5-21-9-9-9-500\n",
        encoding="utf-8",
    )
    assert security._reader_sids(tmp_path) == [
        "S-1-5-21-111-222-333-1001",
        "s-1-5-21-9-9-9-500",
    ]


def test_reader_sids_missing_file_means_no_readers(tmp_path):
    assert security._reader_sids(tmp_path) == []


@pytest.mark.skipif(sys.platform != "win32", reason="ACLs are Windows-only")
def test_ensure_token_grants_listed_readers(tmp_path, monkeypatch):
    """The installer writes gui-users.sids (Phase 6); every token
    (re)creation must re-apply the read grants, or a regeneration would
    silently lock the GUI user out again."""
    calls = []
    real_run = subprocess.run

    def spy(cmd, **kwargs):
        calls.append(list(cmd))
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(security.subprocess, "run", spy)
    # S-1-5-32-545 = BUILTIN\Users: a well-known SID that always resolves.
    (tmp_path / "gui-users.sids").write_text("S-1-5-32-545\n", encoding="utf-8")
    ensure_token(tmp_path / "api_token")
    grant_calls = [c for c in calls if "/grant" in c]
    assert any("*S-1-5-32-545:(R)" in c for c in grant_calls)


@pytest.mark.skipif(sys.platform != "win32", reason="ACLs are Windows-only")
def test_token_acl_actually_includes_reader_sids(tmp_path):
    (tmp_path / "gui-users.sids").write_text("S-1-5-32-545\n", encoding="utf-8")
    ensure_token(tmp_path / "api_token")
    out = subprocess.run(
        ["icacls", str(tmp_path / "api_token")],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "S-1-5-32-545" in out or "Users" in out
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/service/test_security.py -o addopts= -v`
Expected: the 4 new tests FAIL (`_reader_sids` not defined); the 10 existing ones still pass.

- [ ] **Step 3: Implement**

In `src/mml_cloud_courier/service/security.py`, add after `restrict_acl`:

```python
def _reader_sids(directory: Path) -> list[str]:
    """SIDs granted read on the API token: <data_dir>\\gui-users.sids,
    one raw SID per line, `#` comments and blanks ignored. Written by the
    installer (Phase 6) — this is the 'which additional principal the
    GUI's user gets' decision the module docstring reserves for it.
    Missing file means no extra readers."""
    try:
        lines = (directory / "gui-users.sids").read_text(
            encoding="utf-8"
        ).splitlines()
    except OSError:
        return []
    sids = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and line.upper().startswith("S-1-"):
            sids.append(line)
    return sids


def _grant_readers(path: Path) -> None:
    """Additive (R) grants for the installer-listed GUI users. Runs on
    every token creation because restrict_acl just wiped the ACL."""
    if sys.platform != "win32":
        return
    for sid in _reader_sids(path.parent):
        subprocess.run(
            ["icacls", str(path), "/grant", f"*{sid}:(R)"],
            check=True, capture_output=True, text=True,
        )
```

In `ensure_token`, insert the grant between the ACL restriction and the token write:

```python
    path.touch()
    restrict_acl(path)
    _grant_readers(path)
    token = secrets.token_urlsafe(32)
```

Also update the module docstring's last sentence from "…is an installer decision (Phase 6), not made here." to "…is an installer decision (Phase 6): the installer lists reader SIDs in gui-users.sids next to the token, honored on every token creation."

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/service/test_security.py -o addopts= -v`
Expected: all PASS (14).

- [ ] **Step 5: Commit**

```powershell
git add src/mml_cloud_courier/service/security.py tests/service/test_security.py
git commit -m "feat: token read grants for installer-listed GUI user SIDs (gui-users.sids)"
```

---

### Task 2: Frozen service hosting seams

**Files:**
- Modify: `src/mml_cloud_courier/service/windows_service.py`
- Test: `tests/service/test_windows_service.py` (append; existing 2 tests unmodified)

**Interfaces:**
- Consumes: existing `_build_service_class()`, `main(argv=None) -> int`.
- Produces: `_service_exe_args() -> str | None` (None when frozen), `_scm_launch() -> bool`, and `run() -> int` — the single entry Task 3's `entry_service.py` calls. Dev venv behavior is byte-identical.

- [ ] **Step 1: Write the failing tests**

Append to `tests/service/test_windows_service.py`:

```python
def test_exe_args_are_none_when_frozen(monkeypatch):
    """Packaged (PyInstaller), the exe IS the service host: ImagePath must
    be the bare exe, no arguments — pywin32 omits them when None."""
    from mml_cloud_courier.service import windows_service

    monkeypatch.setattr(windows_service.sys, "frozen", True, raising=False)
    assert windows_service._service_exe_args() is None


def test_exe_args_point_at_the_module_when_not_frozen(monkeypatch):
    from mml_cloud_courier.service import windows_service

    monkeypatch.delattr(windows_service.sys, "frozen", raising=False)
    args = windows_service._service_exe_args()
    assert args.startswith('"') and args.endswith('windows_service.py"')


def test_scm_launch_means_no_arguments(monkeypatch):
    from mml_cloud_courier.service import windows_service

    monkeypatch.setattr(windows_service.sys, "argv", ["mmlcc-service.exe"])
    assert windows_service._scm_launch()
    monkeypatch.setattr(
        windows_service.sys, "argv", ["mmlcc-service.exe", "install"]
    )
    assert not windows_service._scm_launch()


def test_run_routes_command_lines_to_main(monkeypatch):
    from mml_cloud_courier.service import windows_service

    monkeypatch.setattr(windows_service.sys, "argv", ["mmlcc-service", "--x"])
    calls = []
    monkeypatch.setattr(windows_service, "main", lambda: calls.append(1) or 0)
    assert windows_service.run() == 0
    assert calls
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/service/test_windows_service.py -o addopts= -v`
Expected: 4 new FAIL (attributes missing); 2 existing PASS.

- [ ] **Step 3: Implement**

In `src/mml_cloud_courier/service/windows_service.py`:

Add module-level helpers after the `DISPLAY_NAME` constant:

```python
def _service_exe_args() -> str | None:
    """ImagePath arguments. Venv-hosted, the SCM launches
    `python.exe <this file>`; packaged (PyInstaller onedir), the exe IS
    the host and takes no arguments — pywin32 omits them when None."""
    if getattr(sys, "frozen", False):
        return None
    return f'"{Path(__file__).resolve()}"'


def _scm_launch() -> bool:
    """True when launched by the SCM: the registered ImagePath carries no
    arguments beyond the program itself (both hosting modes)."""
    return len(sys.argv) == 1
```

In `_build_service_class`, replace the `_exe_args_` line:

```python
        _exe_name_ = sys.executable
        _exe_args_ = _service_exe_args()
```

Add `run()` after `main()`:

```python
def run() -> int:
    """Entry for both hosts: an SCM launch enters the service control
    dispatcher; anything else is the install|start|stop|remove|update
    command line. packaging/entry_service.py (the packaged exe) calls
    this; the venv __main__ block below is the same flow."""
    if _scm_launch():
        import servicemanager

        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(_build_service_class())
        servicemanager.StartServiceCtrlDispatcher()
        return 0
    return main()
```

Replace the whole `if __name__ == "__main__":` block with:

```python
if __name__ == "__main__":
    raise SystemExit(run())
```

Update the module docstring's hosting paragraph: after the sentence about the venv launcher, append: "Packaged builds host the service the same way in the PyInstaller exe itself: ImagePath is the bare `mmlcc-service.exe` (no arguments), and `run()` dispatches on argument count."

- [ ] **Step 4: Run the service test file, then the service suite**

Run: `.venv\Scripts\python -m pytest tests/service/test_windows_service.py -o addopts= -v` → 6 PASS.
Run: `.venv\Scripts\python -m pytest tests/service -o addopts= -q` → all pass.

- [ ] **Step 5: Commit**

```powershell
git add src/mml_cloud_courier/service/windows_service.py tests/service/test_windows_service.py
git commit -m "feat: frozen-aware service hosting (bare-exe ImagePath, run() dispatch)"
```

---

### Task 3: PyInstaller bundle (`packaging/` spec + entries + version info)

**Files:**
- Create: `packaging/entry_gui.py`, `packaging/entry_cli.py`, `packaging/entry_service.py`
- Create: `packaging/version_info.py`
- Create: `packaging/mmlcc.spec`
- Modify: `pyproject.toml` (add `build` extra)
- Modify: `.gitignore` (ignore the generated `packaging/_version_info.txt`)

**Interfaces:**
- Consumes: `mml_cloud_courier.gui.__main__:main`, `mml_cloud_courier.cli.__main__:main`, `windows_service.run` (Task 2), committed `gui/assets/` incl. `mmlcc.ico` (icon sub-project).
- Produces: `dist\mml-cloud-courier\` onedir with `mmlcc-gui.exe`, `mmlcc.exe`, `mmlcc-service.exe` (+ shared `_internal`); `packaging/version_info.py` CLI writing `packaging/_version_info.txt` from pyproject's version. Task 4 compiles this dist into setup.exe; Task 5 scripts the whole chain.

- [ ] **Step 1: Add the build extra and gitignore entry**

In `pyproject.toml` under `[project.optional-dependencies]`, after the `dev` list:

```toml
build = ["pyinstaller>=6.10,<7"]
```

In `.gitignore`, under the "Python" block, add:

```
packaging/_version_info.txt
```

Install: `.venv\Scripts\python -m pip install -e ".[dev,build]"`

- [ ] **Step 2: Entry scripts**

`packaging/entry_gui.py`:

```python
"""PyInstaller entry: mmlcc-gui.exe (windowed)."""

import sys

from mml_cloud_courier.gui.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
```

`packaging/entry_cli.py`:

```python
"""PyInstaller entry: mmlcc.exe (console CLI)."""

import sys

from mml_cloud_courier.cli.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
```

`packaging/entry_service.py`:

```python
"""PyInstaller entry: mmlcc-service.exe — SCM host AND command line.

Launched by the SCM (bare ImagePath, no arguments) it enters the service
control dispatcher; with arguments it is the install|start|stop|remove|
update command line. Both routes live in windows_service.run()."""

from mml_cloud_courier.service.windows_service import run

if __name__ == "__main__":
    raise SystemExit(run())
```

- [ ] **Step 3: Version-info generator**

`packaging/version_info.py`:

```python
"""Write packaging/_version_info.txt (PyInstaller VSVersionInfo) from
pyproject.toml's [project] version — the single version source.

Run by build_release.ps1 before PyInstaller:

    .venv\\Scripts\\python packaging\\version_info.py
"""

from __future__ import annotations

import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

TEMPLATE = """\
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({numbers}),
    prodvers=({numbers}),
    mask=0x3F,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'NOAA Fisheries Marine Mammal Laboratory'),
        StringStruct('FileDescription', 'MML Cloud Courier'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('ProductName', 'MML Cloud Courier'),
        StringStruct('ProductVersion', '{version}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
"""


def main() -> int:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    version = pyproject["project"]["version"]
    parts = [int(p) for p in version.split(".")[:3]]
    while len(parts) < 3:
        parts.append(0)
    numbers = ", ".join(str(p) for p in [*parts, 0])
    out = HERE / "_version_info.txt"
    out.write_text(
        TEMPLATE.format(numbers=numbers, version=version), encoding="utf-8"
    )
    print(f"wrote {out} for version {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: The spec file**

`packaging/mmlcc.spec`:

```python
# PyInstaller spec: ONE onedir bundle, THREE exes sharing _internal.
# onedir is a hard constraint — onefile resurrects the Phase 3
# hosting-DLL failure class. Build via packaging/build_release.ps1.

from pathlib import Path

HERE = Path(SPECPATH).resolve()
ROOT = HERE.parent
SRC = ROOT / "src"
ASSETS = SRC / "mml_cloud_courier" / "gui" / "assets"
ICON = str(ASSETS / "mmlcc.ico")
VERSION_FILE = str(HERE / "_version_info.txt")

# uvicorn assembles its loop/protocol/lifespan classes from strings;
# static analysis cannot see them. win32timezone is the classic pywin32
# service hidden import.
UVICORN_HIDDEN = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
]
SERVICE_HIDDEN = UVICORN_HIDDEN + ["win32timezone"]

GUI_DATAS = [(str(ASSETS), "mml_cloud_courier/gui/assets")]


def build(entry, *, hiddenimports=(), datas=()):
    return Analysis(
        [str(HERE / entry)],
        pathex=[str(SRC)],
        datas=list(datas),
        hiddenimports=list(hiddenimports),
        noarchive=False,
    )


a_gui = build("entry_gui.py", datas=GUI_DATAS)
a_cli = build("entry_cli.py", hiddenimports=UVICORN_HIDDEN)
a_svc = build("entry_service.py", hiddenimports=SERVICE_HIDDEN)

exe_gui = EXE(
    PYZ(a_gui.pure),
    a_gui.scripts,
    [],
    exclude_binaries=True,
    name="mmlcc-gui",
    console=False,
    icon=ICON,
    version=VERSION_FILE,
)
exe_cli = EXE(
    PYZ(a_cli.pure),
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name="mmlcc",
    console=True,
    icon=ICON,
    version=VERSION_FILE,
)
exe_svc = EXE(
    PYZ(a_svc.pure),
    a_svc.scripts,
    [],
    exclude_binaries=True,
    name="mmlcc-service",
    console=True,
    icon=ICON,
    version=VERSION_FILE,
)

coll = COLLECT(
    exe_gui,
    a_gui.binaries,
    a_gui.datas,
    exe_cli,
    a_cli.binaries,
    a_cli.datas,
    exe_svc,
    a_svc.binaries,
    a_svc.datas,
    name="mml-cloud-courier",
)
```

- [ ] **Step 5: Build and smoke the bundle**

```powershell
.venv\Scripts\python packaging\version_info.py
.venv\Scripts\python -m PyInstaller packaging\mmlcc.spec --noconfirm --distpath dist --workpath build\pyinstaller
```

Expected: `dist\mml-cloud-courier\` contains `mmlcc-gui.exe`, `mmlcc.exe`, `mmlcc-service.exe`, and `_internal\mml_cloud_courier\gui\assets\mark-16.png` (datas landed).

Smoke (each from `dist\mml-cloud-courier\`):
1. `.\mmlcc.exe --help` → exit 0, usage text mentioning transfer/status subcommands.
2. `.\mmlcc-service.exe` (NO arguments — the only allowed service invocation outside the gate) → non-zero exit with pywin32 error **1063** ("the service process could not connect to the service controller") in the output. That failure is the PASS signal: servicemanager imported and the dispatcher was reached inside the frozen exe.
3. `Start-Process .\mmlcc-gui.exe; Start-Sleep 4; Get-Process mmlcc-gui` → process alive (Qt plugins + assets loaded; a service-down banner in the window is fine), then `Stop-Process -Name mmlcc-gui`.

If ANY hidden-import ModuleNotFoundError appears, add the module to the spec's hidden lists — that is expected build iteration, not scope creep.

- [ ] **Step 6: Full suite still green (packaging adds no imports to the app)**

Run: `.venv\Scripts\python -m pytest -o addopts= -q`
Expected: unchanged from worktree baseline.

- [ ] **Step 7: Commit (no artifacts — build/ and dist/ are gitignored)**

```powershell
git add packaging/entry_gui.py packaging/entry_cli.py packaging/entry_service.py packaging/version_info.py packaging/mmlcc.spec pyproject.toml .gitignore
git commit -m "feat: PyInstaller onedir spec — three exes, shared _internal, versioned resources"
```

---

### Task 4: Inno Setup script

**Files:**
- Create: `packaging/mmlcc.iss`

**Interfaces:**
- Consumes: `dist\mml-cloud-courier\` from Task 3 (compile-time `/DDistDir`), `windows_service` verbs `install`/`update` via the packaged exe, Task 1's `gui-users.sids` contract.
- Produces: `dist\mml-cloud-courier-setup-<version>.exe` when compiled with `/DAppVersion=<v> /DDistDir=<dist> /O<out>`. Task 5's build script drives exactly those defines.

- [ ] **Step 1: Verify the pywin32 `update` behavior this installer relies on**

Read `.venv\Lib\site-packages\win32\lib\win32serviceutil.py`: confirm that (a) `HandleCommandLine`'s `update` verb calls `ChangeServiceConfig` with the class's `_exe_name_`/`_exe_args_` (so running it from the packaged exe repoints ImagePath), and (b) `userName=None` there means the existing log-on account is preserved (pywin32 passes `None` through to the Win32 API, where it means no change). Record both findings in the commit message. If (b) is falsified, replace the `update` branch below with `remove` + `install` and make the Log On reminder unconditional — and say so in the commit message.

- [ ] **Step 2: Write `packaging/mmlcc.iss`**

```ini
; MML Cloud Courier installer. Compile via packaging/build_release.ps1:
;   ISCC /DAppVersion=<v> /DDistDir=<repo>\dist\mml-cloud-courier /O<repo>\dist packaging\mmlcc.iss
; Service policy (spec): FIRST install registers (LocalSystem, auto-start);
; upgrades stop/replace/start WITHOUT re-registering so a configured
; log-on account survives; ImagePath mismatch triggers the one
; re-registration path ('update' — account preserved per Task 4 research).
; Uninstall removes the service + files but LEAVES the data dir.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef DistDir
  #define DistDir "..\dist\mml-cloud-courier"
#endif

[Setup]
AppId={{9E7C1A76-52D4-4B7E-A870-1C3F2A6D9B58}}
AppName=MML Cloud Courier
AppVersion={#AppVersion}
AppPublisher=NOAA Fisheries Marine Mammal Laboratory
DefaultDirName={autopf}\MML Cloud Courier
DisableProgramGroupPage=yes
OutputBaseFilename=mml-cloud-courier-setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\mmlcc-gui.exe
ChangesEnvironment=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; Flags: unchecked
Name: "addtopath"; Description: "Add the install folder to PATH (for the mmlcc command line)"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\MML Cloud Courier"; Filename: "{app}\mmlcc-gui.exe"
Name: "{autodesktop}\MML Cloud Courier"; Filename: "{app}\mmlcc-gui.exe"; Tasks: desktopicon

[Registry]
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; \
  ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; \
  Tasks: addtopath; Check: NeedsAddPath('{app}')

[Code]
const
  ServiceName = 'MMLCloudCourier';
  EnvKey = 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment';

var
  NeededRegistration: Boolean;

function NeedsAddPath(Param: String): Boolean;
var
  OrigPath, Dir: String;
begin
  { Check: clauses cannot call ExpandConstant; expand the parameter here. }
  Dir := ExpandConstant(Param);
  if not RegQueryStringValue(HKLM, EnvKey, 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  Result :=
    Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(OrigPath) + ';') = 0;
end;

procedure RemoveFromPath(Dir: String);
var
  OrigPath, NewPath: String;
begin
  if not RegQueryStringValue(HKLM, EnvKey, 'Path', OrigPath) then
    exit;
  NewPath := OrigPath;
  StringChangeEx(NewPath, ';' + Dir, '', True);
  StringChangeEx(NewPath, Dir + ';', '', True);
  StringChangeEx(NewPath, Dir, '', True);
  if NewPath <> OrigPath then
    RegWriteExpandStringValue(HKLM, EnvKey, 'Path', NewPath);
end;

function ServiceExists(): Boolean;
var
  R: Integer;
begin
  Exec(ExpandConstant('{sys}\sc.exe'), 'query ' + ServiceName, '',
    SW_HIDE, ewWaitUntilTerminated, R);
  Result := (R = 0);
end;

function PackagedImagePath(): String;
begin
  Result := ExpandConstant('{app}\mmlcc-service.exe');
end;

function ImagePathIsCurrent(): Boolean;
var
  S: String;
begin
  Result := False;
  if RegQueryStringValue(HKLM,
      'SYSTEM\CurrentControlSet\Services\' + ServiceName, 'ImagePath', S)
  then
    Result := CompareText(RemoveQuotes(Trim(S)), PackagedImagePath()) = 0;
end;

procedure StopService();
var
  R, I: Integer;
begin
  Exec(ExpandConstant('{sys}\sc.exe'), 'stop ' + ServiceName, '',
    SW_HIDE, ewWaitUntilTerminated, R);
  for I := 1 to 30 do
  begin
    Exec(ExpandConstant('{cmd}'),
      '/c sc query ' + ServiceName + ' | find "STOPPED"', '',
      SW_HIDE, ewWaitUntilTerminated, R);
    if R = 0 then
      exit;
    Sleep(1000);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if ServiceExists() then
    StopService();
end;

function LastCsvField(Line: String): String;
var
  I: Integer;
begin
  Result := Line;
  for I := Length(Line) downto 1 do
    if Line[I] = ',' then
    begin
      Result := Copy(Line, I + 1, MaxInt);
      break;
    end;
  Result := RemoveQuotes(Trim(Result));
end;

function InstallingUserSid(): String;
var
  Tmp: String;
  R: Integer;
  Lines: TArrayOfString;
begin
  { By process token, never account name (6e45d4a): whoami emits the SID
    directly, no name mapping anywhere. Elevated, this is the elevating
    user's SID — docs say to run setup as the user who runs the GUI. }
  Result := '';
  Tmp := ExpandConstant('{tmp}\whoami-sid.txt');
  if not Exec(ExpandConstant('{cmd}'),
      '/c whoami /user /fo csv > "' + Tmp + '"', '',
      SW_HIDE, ewWaitUntilTerminated, R) then
    exit;
  if not LoadStringsFromFile(Tmp, Lines) then
    exit;
  if GetArrayLength(Lines) < 2 then
    exit;
  Result := LastCsvField(Lines[GetArrayLength(Lines) - 1]);
  if Pos('S-1-', Uppercase(Result)) <> 1 then
    Result := '';
end;

function DataDir(): String;
begin
  Result := ExpandConstant('{commonappdata}\MML Cloud Courier');
end;

procedure EnsureGuiUserSid(Sid: String);
var
  Path, Content: String;
  R: Integer;
begin
  if Sid = '' then
    exit;
  ForceDirectories(DataDir());
  Path := DataDir() + '\gui-users.sids';
  if not FileExists(Path) then
    SaveStringToFile(Path,
      '# SIDs granted read on api_token, one per line' + #13#10 +
      Sid + #13#10, False)
  else
  begin
    LoadStringFromFile(Path, Content);
    if Pos(Sid, Content) = 0 then
      SaveStringToFile(Path, Sid + #13#10, True);
  end;
  { Immediate grant on an existing token; regenerated tokens get it from
    the service via gui-users.sids. }
  if FileExists(DataDir() + '\api_token') then
    Exec(ExpandConstant('{sys}\icacls.exe'),
      '"' + DataDir() + '\api_token" /grant *' + Sid + ':(R)', '',
      SW_HIDE, ewWaitUntilTerminated, R);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  R: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    NeededRegistration := False;
    if not ServiceExists() then
    begin
      { First install: registers LocalSystem + auto-start + failure
        actions (the exe's install verb does all three). }
      Exec(PackagedImagePath(), 'install', '', SW_HIDE,
        ewWaitUntilTerminated, R);
      NeededRegistration := True;
    end
    else if not ImagePathIsCurrent() then
    begin
      { The one re-registration path: repoint ImagePath, account
        preserved (Task 4 research finding). }
      Exec(PackagedImagePath(), 'update', '', SW_HIDE,
        ewWaitUntilTerminated, R);
      NeededRegistration := True;
    end;
    EnsureGuiUserSid(InstallingUserSid());
    Exec(ExpandConstant('{sys}\sc.exe'), 'start ' + ServiceName, '',
      SW_HIDE, ewWaitUntilTerminated, R);
  end;
  if (CurStep = ssDone) and NeededRegistration then
    MsgBox('The MML Cloud Courier service was registered.' + #13#10 + #13#10
      + 'Fresh registrations run as LocalSystem. If this machine uses a '
      + 'named service account (for example for user ADC credentials), '
      + 'open services.msc -> "MML Cloud Courier Service" -> Log On and '
      + 'set it now. Setting it there also grants the "log on as a '
      + 'service" right automatically.', mbInformation, MB_OK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  R: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    StopService();
    Exec(ExpandConstant('{sys}\sc.exe'), 'delete ' + ServiceName, '',
      SW_HIDE, ewWaitUntilTerminated, R);
  end;
  if CurUninstallStep = usPostUninstall then
    RemoveFromPath(ExpandConstant('{app}'));
end;
```

- [ ] **Step 3: Compile check (compile only — NEVER run the output)**

```powershell
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" `
  "/DAppVersion=0.0.0" `
  "/DDistDir=$(Resolve-Path dist\mml-cloud-courier)" `
  "/O$(Resolve-Path dist)" `
  packaging\mmlcc.iss
```

Expected: exit 0; `dist\mml-cloud-courier-setup-0.0.0.exe` exists. Fix Pascal compile errors here (ISCC's messages carry line numbers). Delete the 0.0.0 artifact afterward (`Remove-Item dist\mml-cloud-courier-setup-0.0.0.exe`).

- [ ] **Step 4: Commit**

```powershell
git add packaging/mmlcc.iss
git commit -m "feat: Inno Setup installer — first-install-only registration, data-preserving uninstall"
```

---

### Task 5: `packaging/build_release.ps1`

**Files:**
- Create: `packaging/build_release.ps1`

**Interfaces:**
- Consumes: `packaging/version_info.py`, `packaging/mmlcc.spec`, `packaging/mmlcc.iss` (Tasks 3-4).
- Produces: one command that emits `dist\mml-cloud-courier-setup-<version>.exe`; the gate and every future release run exactly this.

- [ ] **Step 1: Write the script**

```powershell
#Requires -Version 7
<#
Build the MML Cloud Courier release: PyInstaller onedir -> Inno setup.exe.
Run from anywhere; paths resolve relative to this script's repo.

    pwsh packaging/build_release.ps1                 # full build
    pwsh packaging/build_release.ps1 -SkipInstaller  # bundle only
#>
param(
    [switch]$SkipInstaller
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "venv python not found at $python" }

$version = & $python -c "import tomllib, pathlib; print(tomllib.loads(pathlib.Path(r'$root\pyproject.toml').read_text('utf-8'))['project']['version'])"
Write-Host "Building MML Cloud Courier $version"

& $python (Join-Path $root "packaging\version_info.py")
if ($LASTEXITCODE -ne 0) { throw "version_info.py failed" }

& $python -m PyInstaller (Join-Path $root "packaging\mmlcc.spec") `
    --noconfirm --distpath (Join-Path $root "dist") `
    --workpath (Join-Path $root "build\pyinstaller")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

if (-not $SkipInstaller) {
    $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (-not (Test-Path $iscc)) {
        throw "ISCC.exe not found - install Inno Setup 6 (winget install -e --id JRSoftware.InnoSetup)"
    }
    & $iscc "/DAppVersion=$version" `
        "/DDistDir=$root\dist\mml-cloud-courier" `
        "/O$root\dist" `
        (Join-Path $root "packaging\mmlcc.iss")
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }
    Write-Host "Installer: dist\mml-cloud-courier-setup-$version.exe"
}
```

- [ ] **Step 2: Run it end-to-end**

Run: `pwsh packaging\build_release.ps1`
Expected: exits 0; `dist\mml-cloud-courier-setup-0.1.0.exe` exists (version still 0.1.0 until Task 6). Do NOT run the setup exe.

- [ ] **Step 3: Commit**

```powershell
git add packaging/build_release.ps1
git commit -m "feat: one-command release build (version stamp -> PyInstaller -> ISCC)"
```

---

### Task 6: Installation docs + version 0.2.0

**Files:**
- Create: `docs/installation.md`
- Modify: `pyproject.toml` (version `0.1.0` → `0.2.0`)

**Interfaces:**
- Consumes: everything above; the finish-page/reminder wording from Task 4.
- Produces: the operator document the gate follows; version 0.2.0 for the gate's first build (0.2.1 lands live during the gate's upgrade test).

- [ ] **Step 1: Write `docs/installation.md`**

Content requirements (write full prose for each; no real bucket/machine/account names anywhere in this doc — for the service account use a placeholder like `.\svcaccount`; the real name lives only in the gate records):

1. **Install**: run `mml-cloud-courier-setup-<version>.exe` elevated, **as the user who will run the GUI** (the installer grants that user's SID read access to the service API token via `%ProgramData%\MML Cloud Courier\gui-users.sids`). SmartScreen may warn — the binaries are unsigned; internal deployment accepts this.
2. **Service account**: fresh registrations run as LocalSystem (the packaged default, paired with a service-account key). For named-account configs (user ADC), set the account in services.msc → Log On after first install — that tab also grants the logon-as-service right, which `sc.exe config` does not. **Upgrades never reset the account** (the installer re-registers only when the service is missing or ImagePath moved).
3. **Upgrade**: close the GUI, run the new setup.exe; the service is stopped, files replaced, service restarted. Data (`jobs.db`, settings, credentials) untouched.
4. **Uninstall**: Apps list → uninstall. The service is removed; `%ProgramData%\MML Cloud Courier` (jobs, settings, DPAPI credential blobs) is deliberately left behind; delete it manually for a scorched-earth removal.
5. **Multiple GUI users**: append additional SIDs (one per line, `whoami /user`) to `gui-users.sids`; they take effect on the next token creation, or immediately via `icacls "%ProgramData%\MML Cloud Courier\api_token" /grant *<SID>:(R)`.
6. **OAuth client config**: never bundled. `MMLCC_OAUTH_CLIENT` env var or a browsed file, exactly as in dev; the org-internal packaged client remains an open decision.
7. **CLI**: optional PATH task at install; otherwise `"%ProgramFiles%\MML Cloud Courier\mmlcc.exe"`.
8. **Building a release** (dev section): `pip install -e ".[dev,build]"`, Inno Setup 6 via winget, `pwsh packaging/build_release.ps1`.

- [ ] **Step 2: Bump the version**

In `pyproject.toml`: `version = "0.2.0"`.

- [ ] **Step 3: Full suite with recorded counts**

Run: `.venv\Scripts\python -m pytest -o addopts= -q`
Expected: worktree baseline + 8 new tests (Task 1: +4, Task 2: +4) — with a 726/13 baseline that is **734 passed / 13 skipped**; record actuals.

- [ ] **Step 4: Commit**

```powershell
git add docs/installation.md pyproject.toml
git commit -m "docs: installation guide; version 0.2.0 for the Phase 6 gate build"
```

---

## Gate (controller + user at the keyboard — NOT subagent work)

After the branch merges to master `--no-ff` and pushes, execute the spec's 10-step gate ON MASTER in the main repo (not the worktree), recording every step in `docs/superpowers/gates/2026-08-10-phase6-gate.md`:

1. Suite green on merged master (recorded counts).
2. `pwsh packaging/build_release.ps1` (0.2.0) + packaged smoke (CLI --help / bare service exe 1063 / GUI launch).
3. Stop the venv-hosted live service (user consent — live machine).
4. Run setup.exe: first-install path re-registers onto the packaged exe (ImagePath mismatch → `update`), writes `gui-users.sids`, starts the service.
5. Check services.msc Log On: record whether the account survived the `update` (Task 4 research says yes); restore `.\pmaho` if not.
6. Pending-activation checks: jobs DB v2→v3 in the service log/DB, archive/unarchive live from the GUI, "Open report" renders the files table.
7. Jobs + profiles intact; Start Menu GUI shows the mark; small real transfer completes verified.
8. Logoff survival quick check.
9. Bump to 0.2.1 (commit on master), rebuild, upgrade-install: account preserved, data intact, no re-registration (ImagePath already current).
10. Uninstall → data dir survives, service gone → reinstall 0.2.1 → services.msc Log On restore (this fresh registration DOES reset to LocalSystem) → all intact. **End state: packaged 0.2.1 is the live service (permanent cutover).**

Rollback at any step: re-register the venv hosting (`.venv\Scripts\python -m mml_cloud_courier.service.windows_service install` equivalent via `mmlcc-service install` from the main venv) and restore the Log On account.
