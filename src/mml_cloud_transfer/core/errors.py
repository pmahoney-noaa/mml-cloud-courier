"""Error taxonomy — one mapping from exception to user-facing meaning.

The grouped Errors view, tray notifications, and the job report all read
from here, so a category is defined once and rendered consistently.

This module deliberately imports no cloud libraries. Google's exceptions are
recognised by their integer ``.code`` attribute and by class name, which
keeps ``core`` pure and the tests dependency-free.
"""

from __future__ import annotations

import errno
from dataclasses import dataclass
from enum import Enum

# Windows error codes worth naming.
_ERROR_SHARING_VIOLATION = 32
_ERROR_LOCK_VIOLATION = 33
_ERROR_FILENAME_EXCED_RANGE = 206


class ErrorCategory(str, Enum):
    PERMISSION_DENIED = "permission_denied"
    FILE_LOCKED = "file_locked"
    PATH_TOO_LONG = "path_too_long"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    NETWORK = "network"
    QUOTA = "quota"
    CREDENTIAL = "credential"
    NOT_FOUND = "not_found"
    SOURCE_CHANGED = "source_changed"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Classification:
    category: ErrorCategory
    transient: bool
    """Worth retrying with backoff."""
    pauses_job: bool
    """Retrying other files is pointless until a human intervenes."""
    message: str
    action: str


@dataclass(frozen=True, slots=True)
class ScanError:
    path: str
    category: ErrorCategory
    message: str


_TABLE: dict[ErrorCategory, tuple[bool, bool, str, str]] = {
    ErrorCategory.PERMISSION_DENIED: (
        False, False,
        "Access to this file was denied.",
        "Grant the transfer service account read access to this path.",
    ),
    ErrorCategory.FILE_LOCKED: (
        True, False,
        "The file is open in another program.",
        "Close the program holding the file, then resume the job.",
    ),
    ErrorCategory.PATH_TOO_LONG: (
        False, False,
        "The file path is too long for Windows to open.",
        "Shorten the folder names, or move the data closer to the drive root.",
    ),
    ErrorCategory.CHECKSUM_MISMATCH: (
        False, False,
        "The transferred copy did not match the original checksum.",
        "Resume the job to transfer this file again.",
    ),
    ErrorCategory.NETWORK: (
        True, False,
        "The network connection failed.",
        "No action needed — this retries automatically.",
    ),
    ErrorCategory.QUOTA: (
        True, False,
        "Google Cloud Storage is rate limiting the transfer.",
        "No action needed — this retries automatically with backoff.",
    ),
    ErrorCategory.CREDENTIAL: (
        False, True,
        "The stored credential was rejected by Google Cloud Storage.",
        "Re-authenticate this connection, or ask an administrator to check its permissions.",
    ),
    ErrorCategory.NOT_FOUND: (
        False, False,
        "The object or file no longer exists.",
        "Re-scan the source, then start a new job.",
    ),
    ErrorCategory.SOURCE_CHANGED: (
        False, False,
        "The source file changed while it was being transferred.",
        "Make sure nothing is writing to the file, then resume the job.",
    ),
    ErrorCategory.CONFLICT: (
        False, False,
        "The destination object changed since this job was planned.",
        "Re-scan the destination, then start a new job.",
    ),
    ErrorCategory.UNKNOWN: (
        False, False,
        "An unexpected error occurred.",
        "Check the job log, then contact support with the diagnostics bundle.",
    ),
}


def _build(category: ErrorCategory) -> Classification:
    transient, pauses_job, message, action = _TABLE[category]
    return Classification(
        category=category,
        transient=transient,
        pauses_job=pauses_job,
        message=message,
        action=action,
    )


def _from_http_status(code: int) -> ErrorCategory | None:
    if code in (401, 403):
        return ErrorCategory.CREDENTIAL
    if code == 404:
        return ErrorCategory.NOT_FOUND
    if code == 412:
        return ErrorCategory.CONFLICT
    if code == 429:
        return ErrorCategory.QUOTA
    if code == 408:
        return ErrorCategory.NETWORK
    if 500 <= code <= 599:
        return ErrorCategory.NETWORK
    return None


def _from_os_error(exc: OSError) -> ErrorCategory:
    winerror = getattr(exc, "winerror", None)
    if winerror in (_ERROR_SHARING_VIOLATION, _ERROR_LOCK_VIOLATION):
        return ErrorCategory.FILE_LOCKED
    if winerror == _ERROR_FILENAME_EXCED_RANGE:
        return ErrorCategory.PATH_TOO_LONG
    if isinstance(exc, PermissionError):
        return ErrorCategory.PERMISSION_DENIED
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return ErrorCategory.NETWORK
    if isinstance(exc, FileNotFoundError):
        return ErrorCategory.NOT_FOUND
    if exc.errno == errno.ENAMETOOLONG:
        return ErrorCategory.PATH_TOO_LONG
    if exc.errno == errno.EACCES:
        return ErrorCategory.PERMISSION_DENIED
    return ErrorCategory.UNKNOWN


def classify(exc: BaseException) -> Classification:
    """Map any exception to its category and user-facing guidance."""
    if type(exc).__name__ == "DataCorruption":
        return _build(ErrorCategory.CHECKSUM_MISMATCH)

    code = getattr(exc, "code", None)
    if isinstance(code, int) and not isinstance(code, bool):
        category = _from_http_status(code)
        if category is not None:
            return _build(category)

    if isinstance(exc, OSError):
        return _build(_from_os_error(exc))

    if isinstance(exc, TimeoutError):
        return _build(ErrorCategory.NETWORK)

    return _build(ErrorCategory.UNKNOWN)
