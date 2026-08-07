"""Windows DPAPI encryption at machine scope.

Machine scope — not user scope — because the encrypting process is the
service, and the installer may change which account the service runs as;
a user-scoped blob would die with that account. The spec states the limit
plainly: at machine scope the file ACL is the real access control, and the
encryption protects a stolen backup or disk image, not a local admin.

UI_FORBIDDEN is always set: this code runs in session 0, where a DPAPI
prompt would hang forever.
"""

from __future__ import annotations

import sys

CRYPTPROTECT_UI_FORBIDDEN = 0x01
CRYPTPROTECT_LOCAL_MACHINE = 0x04
_FLAGS = CRYPTPROTECT_LOCAL_MACHINE | CRYPTPROTECT_UI_FORBIDDEN


def protect(data: bytes, *, description: str = "MML Cloud Courier credential") -> bytes:
    if sys.platform != "win32":
        raise NotImplementedError("DPAPI is Windows-only")
    import win32crypt

    return win32crypt.CryptProtectData(data, description, None, None, None, _FLAGS)


def unprotect(blob: bytes) -> bytes:
    if sys.platform != "win32":
        raise NotImplementedError("DPAPI is Windows-only")
    import pywintypes
    import win32crypt

    try:
        _description, data = win32crypt.CryptUnprotectData(blob, None, None, None, _FLAGS)
    except pywintypes.error as exc:
        raise ValueError(
            "credential blob is corrupt or was encrypted on another machine"
        ) from exc
    return data
