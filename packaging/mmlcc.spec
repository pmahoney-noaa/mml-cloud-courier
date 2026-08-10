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
