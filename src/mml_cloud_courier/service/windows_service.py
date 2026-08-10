"""pywin32 Windows Service wrapper: mmlcc-service install|start|stop|remove.

Runs in session 0, which is the point — jobs survive user logoff. The
class is built inside a factory so importing this module (e.g. on a
machine without pywin32) stays cheap and safe; console-mode development
uses `python -m mml_cloud_courier.service` instead.

The service is hosted by the venv's own python.exe running this module
(``_exe_name_``/``_exe_args_``), NOT by pywin32's pythonservice.exe. The
Phase 3 gate proved pythonservice.exe structurally cannot host it here:
with a per-user Python install its python DLL is invisible to
LocalSystem's DLL search, and relocated to the venv root it initializes
without the venv's site-packages, so ``import servicemanager`` fails.
The venv launcher has neither problem: it resolves the base interpreter
by absolute path from pyvenv.cfg and produces the same sys.path as every
test run. When the SCM starts ``python.exe windows_service.py`` with no
arguments, the ``__main__`` block enters the service control dispatcher.
Packaged builds host the service the same way in the PyInstaller exe
itself: ImagePath is the bare ``mmlcc-service.exe`` (no arguments), and
``run()`` dispatches on argument count.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

SERVICE_NAME = "MMLCloudCourier"
DISPLAY_NAME = "MML Cloud Courier Service"


def _ensure_service_stdio() -> None:
    """An SCM-launched PyInstaller exe has no console, and its bootloader
    leaves sys.stdout/stderr as None — uvicorn's default log formatter
    then dies on sys.stdout.isatty() at Config construction, killing
    SvcDoRun before the host runs (found live at the Phase 6 gate). The
    venv-hosted python.exe always provided stream objects even without a
    console; restore that parity with devnull streams."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


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


def _build_service_class():
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil

    from mml_cloud_courier.service.config import load_config
    from mml_cloud_courier.service.host import ServiceHost

    class MmlccService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = DISPLAY_NAME
        _svc_description_ = (
            "Verified, resumable file transfers to Google Cloud Storage."
        )
        _exe_name_ = sys.executable
        _exe_args_ = _service_exe_args()

        def __init__(self, args):
            super().__init__(args)
            self._stop = win32event.CreateEvent(None, 0, 0, None)
            self._host = None

        def SvcDoRun(self):
            _ensure_service_stdio()
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, ""),
            )
            self._host = ServiceHost(load_config())
            self._host.start()
            self._host.wait_ready(timeout=60)
            win32event.WaitForSingleObject(self._stop, win32event.INFINITE)

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            if self._host is not None:
                self._host.stop()
            win32event.SetEvent(self._stop)

    return MmlccService


def _configure_restart_on_failure() -> None:
    """Spec: auto-start with restart-on-failure. sc's argument style is
    'name= value' with the space required."""
    subprocess.run(
        ["sc", "failure", SERVICE_NAME, "reset=", "86400",
         "actions=", "restart/5000/restart/5000/restart/30000"],
        check=False, capture_output=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    import win32serviceutil

    args = list(sys.argv if argv is None else [sys.argv[0], *argv])
    if "install" in args and "--startup" not in args:
        args[args.index("install"):args.index("install")] = ["--startup", "auto"]
    err = win32serviceutil.HandleCommandLine(_build_service_class(), argv=args)
    err = int(err or 0)
    if "install" in args and err == 0:
        _configure_restart_on_failure()
    return err


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


if __name__ == "__main__":
    raise SystemExit(run())
