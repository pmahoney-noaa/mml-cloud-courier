"""Numbers and enum values, in words a non-technical user reads at 8am."""

from __future__ import annotations

from datetime import datetime

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def human_bytes(n: int | float) -> str:
    value = float(n)
    for unit in _UNITS:
        if value < 1000 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(value)} B"
            # one decimal below 100 ("6.6 TB", "12.4 MB"), none above ("480 MB")
            text = f"{value:.1f}" if value < 100 else f"{value:.0f}"
            return f"{text} {unit}"
        value /= 1000


def human_rate(bytes_per_second: float) -> str:
    return f"{human_bytes(bytes_per_second)}/s"


def human_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}h {seconds % 3600 // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


def human_schedule(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone().strftime("%b %d %H:%M")


STATUS_LABELS = {
    "pending": "Queued",
    "scanning": "Scanning files",
    "running": "Running",
    "paused": "Paused",
    "stalled": "Stalled — retrying automatically",
    "complete": "Complete",
    "incomplete": "Incomplete — needs attention",
    "cancelled": "Cancelled",
}

STATE_LABELS = {
    "pending": "Waiting",
    "transferring": "Transferring",
    "transferred": "Checking",
    "verified": "Verified",
    "failed": "Failed",
    "skipped": "Skipped (already up to date)",
    "changed": "Changed — will retry",
    "quarantined": "Excluded after repeated failures",
}
