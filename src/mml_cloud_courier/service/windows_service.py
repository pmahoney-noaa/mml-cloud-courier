"""pywin32 Windows Service wrapper: mmlct-service install|start|stop|remove.

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
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

SERVICE_NAME = "MMLCloudTransfer"
DISPLAY_NAME = "MML Cloud Transfer Service"


def _build_service_class():
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil

    from mml_cloud_courier.service.config import load_config
    from mml_cloud_courier.service.host import ServiceHost

    class MmlctService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = DISPLAY_NAME
        _svc_description_ = (
            "Verified, resumable file transfers to Google Cloud Storage."
        )
        _exe_name_ = sys.executable
        _exe_args_ = f'"{Path(__file__).resolve()}"'

        def __init__(self, args):
            super().__init__(args)
            self._stop = win32event.CreateEvent(None, 0, 0, None)
            self._host = None

        def SvcDoRun(self):
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

    return MmlctService


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
    win32serviceutil.HandleCommandLine(_build_service_class(), argv=args)
    if "install" in args:
        _configure_restart_on_failure()
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 1:
        # Launched by the SCM (registered ImagePath, no arguments): hand
        # this process over to the service control dispatcher.
        import servicemanager

        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(_build_service_class())
        servicemanager.StartServiceCtrlDispatcher()
    else:
        raise SystemExit(main())
