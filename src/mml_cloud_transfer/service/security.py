"""The API bearer token: a file under the data directory, ACL-restricted.

Localhost is not access control on a multi-user machine (spec). The ACL is
cut to SYSTEM, Administrators, and the account that created the file — the
account the service runs as. Which additional principal the GUI's user gets
is an installer decision (Phase 6), not made here.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path

_SYSTEM_SID = "*S-1-5-18"
_ADMINISTRATORS_SID = "*S-1-5-32-544"


def _current_account() -> str:
    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME", "")
    return f"{domain}\\{user}" if domain else user


def restrict_acl(path: Path) -> None:
    """Drop inherited ACEs; grant only SYSTEM, Administrators, this account."""
    if sys.platform != "win32":
        return  # ACLs are Windows-only; POSIX dev runs skip this
    subprocess.run(
        [
            "icacls", str(path), "/inheritance:r",
            "/grant:r", f"{_SYSTEM_SID}:(F)",
            "/grant:r", f"{_ADMINISTRATORS_SID}:(F)",
            "/grant:r", f"{_current_account()}:(F)",
        ],
        check=True, capture_output=True, text=True,
    )


def ensure_token(path: Path) -> str:
    """Create (if missing) and return the API bearer token.

    The empty file is ACL-restricted *before* the secret is written, so the
    token bytes never exist under a permissive ACL.
    """
    if path.exists():
        return read_token(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    restrict_acl(path)
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    return token


def read_token(path: Path) -> str:
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError(f"token file is empty: {path}")
    return token
