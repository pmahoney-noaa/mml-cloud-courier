"""Windows path normalisation.

The transfer service runs under its own identity, so it never inherits the
user's mapped drive letters. Everything is converted to UNC before it is
stored, and to extended-length form before it touches the filesystem.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

_EXTENDED_PREFIX = "\\\\?\\"
_EXTENDED_UNC_PREFIX = "\\\\?\\UNC\\"


def is_unc(path: str) -> bool:
    normalised = path.replace("/", "\\")
    return normalised.startswith("\\\\") and not normalised.startswith(_EXTENDED_PREFIX)


def extended_path(path: str) -> str:
    """Return ``path`` in ``\\\\?\\`` form so the 260-character limit does not apply.

    Relative input is resolved against the current directory first — the
    ``\\\\?\\`` prefix is only valid on an absolute path, and Windows rejects
    forms like ``\\\\?\\.\\src`` outright.
    """
    normalised = path.replace("/", "\\")
    if normalised.startswith(_EXTENDED_PREFIX):
        return normalised
    if is_unc(normalised):
        return _EXTENDED_UNC_PREFIX + normalised[2:]
    normalised = os.path.abspath(normalised).replace("/", "\\")
    return _EXTENDED_PREFIX + normalised


def default_drive_resolver(drive: str) -> str | None:
    """Map ``Z:`` to its UNC target, or None if it is not a network drive."""
    if sys.platform != "win32":
        return None

    import ctypes

    buffer_size = ctypes.c_ulong(1024)
    buffer = ctypes.create_unicode_buffer(buffer_size.value)
    result = ctypes.windll.mpr.WNetGetConnectionW(  # type: ignore[attr-defined]
        ctypes.c_wchar_p(drive), buffer, ctypes.byref(buffer_size)
    )
    return buffer.value if result == 0 else None


def resolve_mapped_drive(
    path: str,
    resolver: Callable[[str], str | None] = default_drive_resolver,
) -> str:
    """Rewrite a mapped-drive path to its UNC equivalent, if it is one."""
    normalised = path.replace("/", "\\")
    if is_unc(normalised) or normalised.startswith(_EXTENDED_PREFIX):
        return normalised
    if len(normalised) < 2 or normalised[1] != ":":
        return normalised

    target = resolver(normalised[:2])
    if target is None:
        return normalised
    return target.rstrip("\\") + normalised[2:]


def to_relative_path(root: str, path: str) -> str:
    """Return ``path`` relative to ``root``, forward-slash separated."""
    root_n = root.replace("/", "\\").rstrip("\\")
    path_n = path.replace("/", "\\")
    if path_n.lower() != root_n.lower() and not path_n.lower().startswith(root_n.lower() + "\\"):
        raise ValueError(f"{path!r} is not inside {root!r}")
    return path_n[len(root_n) :].lstrip("\\").replace("\\", "/")


def to_object_name(prefix: str, relative_path: str) -> str:
    """Join a bucket prefix and a relative path into a GCS object name."""
    left = prefix.strip("/")
    right = relative_path.strip("/")
    return f"{left}/{right}" if left else right


def display_path(path: str) -> str:
    """Return ``path`` without the ``\\\\?\\`` machinery, for human eyes.

    Storage and filesystem access keep the extended form; anything shown to
    a user (errors, reports, logs) goes through here.
    """
    if path.startswith(_EXTENDED_UNC_PREFIX):
        return "\\\\" + path[len(_EXTENDED_UNC_PREFIX) :]
    if path.startswith(_EXTENDED_PREFIX):
        return path[len(_EXTENDED_PREFIX) :]
    return path
