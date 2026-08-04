"""Domain models shared across every layer.

Enum *values* are persisted to SQLite. Treat them as a storage format:
changing one requires a migration, not just a rename.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Direction(str, Enum):
    UPLOAD = "upload"
    DOWNLOAD = "download"


class JobStatus(str, Enum):
    PENDING = "pending"
    SCANNING = "scanning"
    RUNNING = "running"
    PAUSED = "paused"
    STALLED = "stalled"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    CANCELLED = "cancelled"


class FileState(str, Enum):
    PENDING = "pending"
    TRANSFERRING = "transferring"
    TRANSFERRED = "transferred"
    VERIFIED = "verified"
    FAILED = "failed"
    SKIPPED = "skipped"
    CHANGED = "changed"
    QUARANTINED = "quarantined"


class TransferMethod(str, Enum):
    SINGLE_SHOT = "single_shot"
    RESUMABLE = "resumable"
    SLICED = "sliced"


class SliceState(str, Enum):
    PENDING = "pending"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    FAILED = "failed"


#: The only two states that count toward a COMPLETE job verdict.
TERMINAL_SUCCESS_STATES = frozenset({FileState.VERIFIED, FileState.SKIPPED})


@dataclass(frozen=True, slots=True)
class PlannedFile:
    """One file discovered by the scanner, before any bytes move."""

    relative_path: str
    """Forward-slash separated, relative to the scan root. Becomes the object name."""

    source_path: str
    """Absolute path in extended-length (``\\\\?\\``) form on Windows."""

    size_bytes: int
    mtime_ns: int
