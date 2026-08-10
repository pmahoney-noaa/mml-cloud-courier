"""Numbers and enum values, in words a non-technical user reads at 8am."""

from __future__ import annotations

import re
from datetime import datetime, timezone

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


_SERVICE_ERROR_RE = re.compile(r"^(\d{3}): (.*)$", re.DOTALL)


def split_service_error(message: str) -> tuple[int | None, str]:
    """call_async delivers ServiceError as str(exc) == '409: detail'.
    Return (status_code, detail), or (None, message) for anything else."""
    match = _SERVICE_ERROR_RE.match(message)
    if match is None:
        return None, message
    return int(match.group(1)), match.group(2)


def _parse_iso(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if then.tzinfo is None:            # sqlite CURRENT_TIMESTAMP is naive UTC
        then = then.replace(tzinfo=timezone.utc)
    return then


def iso_age_days(iso: str | None) -> float | None:
    then = _parse_iso(iso)
    if then is None:
        return None
    return (datetime.now(timezone.utc) - then).total_seconds() / 86400


def human_ago(iso: str | None) -> str:
    then = _parse_iso(iso)
    if then is None:
        return "never"
    seconds = (datetime.now(timezone.utc) - then).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    local = then.astimezone()
    label = f"{local:%b} {local.day}"
    if local.year != datetime.now().astimezone().year:
        label += f", {local.year}"
    return label
