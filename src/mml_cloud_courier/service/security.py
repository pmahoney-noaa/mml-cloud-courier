"""The API bearer token: a file under the data directory, ACL-restricted.

Localhost is not access control on a multi-user machine (spec). The ACL is
cut to SYSTEM, Administrators, and the account that created the file — the
account the service runs as. Which additional principal the GUI's user gets
is an installer decision (Phase 6): the installer lists reader SIDs in
gui-users.sids next to the token, honored on every token creation.
"""

from __future__ import annotations

import secrets
import subprocess
import sys
from pathlib import Path

_SYSTEM_SID = "*S-1-5-18"
_ADMINISTRATORS_SID = "*S-1-5-32-544"


def _current_sid() -> str | None:
    """The process token's SID, via whoami (stdlib-only). None if unknown.

    Env-var account names (USERDOMAIN\\USERNAME) are NOT a substitute: on a
    workgroup machine LocalSystem presents WORKGROUP\\MACHINE$, which icacls
    cannot map to a SID (error 1332) — a failure observed live during the
    Phase 3 gate. The token SID needs no mapping at all."""
    try:
        out = subprocess.run(
            ["whoami", "/user", "/fo", "csv"],
            check=True, capture_output=True, text=True,
        ).stdout
        sid = out.strip().splitlines()[-1].rsplit(",", 1)[-1].strip().strip('"')
    except (OSError, subprocess.CalledProcessError, IndexError):
        return None
    return sid if sid.upper().startswith("S-1-") else None


def _acl_grants(*, inheritable: bool = False) -> list[str]:
    """icacls /grant:r arguments: always SYSTEM and Administrators (by SID),
    plus the current process's SID unless SYSTEM already covers it. Raw SIDs
    (`*S-...`) resolve without any account-name mapping, so this behaves
    identically for domain users, workgroup machines, and virtual service
    accounts. If the SID cannot be resolved, the base grants stand alone —
    the service account is then SYSTEM or an Administrator in every
    supported deployment, so startup still succeeds."""
    suffix = "(OI)(CI)(F)" if inheritable else "(F)"
    grants = [
        "/grant:r", f"{_SYSTEM_SID}:{suffix}",
        "/grant:r", f"{_ADMINISTRATORS_SID}:{suffix}",
    ]
    sid = _current_sid()
    if sid and f"*{sid}" not in (_SYSTEM_SID, _ADMINISTRATORS_SID):
        grants += ["/grant:r", f"*{sid}:{suffix}"]
    return grants


def restrict_acl(path: Path, *, inheritable: bool = False) -> None:
    """Drop inherited ACEs; grant only SYSTEM, Administrators, this account.

    inheritable=True is for directories whose future children must inherit
    the restriction (the credential store)."""
    if sys.platform != "win32":
        return  # ACLs are Windows-only; POSIX dev runs skip this
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", *_acl_grants(inheritable=inheritable)],
        check=True, capture_output=True, text=True,
    )


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


def ensure_token(path: Path) -> str:
    """Create (if missing) and return the API bearer token.

    The empty file is ACL-restricted *before* the secret is written, so the
    token bytes never exist under a permissive ACL.
    """
    if path.exists():
        try:
            return read_token(path)
        except ValueError:
            # Half-created by a start that failed between touch() and the
            # token write — regenerate rather than crash-loop on it.
            path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    restrict_acl(path)
    _grant_readers(path)
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    return token


def read_token(path: Path) -> str:
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError(f"token file is empty: {path}")
    return token
