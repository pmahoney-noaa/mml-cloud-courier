"""Starting the Windows service needs elevation; the GUI runs without it.
ShellExecuteW with the runas verb raises one UAC prompt and nothing else."""

from __future__ import annotations

import sys

SERVICE_NAME = "MMLCloudCourier"


def start_service_elevated(shell_execute=None) -> bool:
    if shell_execute is None:
        if sys.platform != "win32":
            return False
        import ctypes
        shell_execute = ctypes.windll.shell32.ShellExecuteW
    ret = shell_execute(None, "runas", "sc.exe", f"start {SERVICE_NAME}", None, 1)
    return int(ret) > 32   # ShellExecute: values <= 32 are error codes
