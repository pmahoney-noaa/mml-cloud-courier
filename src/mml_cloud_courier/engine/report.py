"""Job reports: summary.json, manifest.csv, and a self-contained report.html.

The verdict shown here is the job's stored status — reports present what
the runner decided, they never re-derive it.
"""

from __future__ import annotations

import csv
import html
import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mml_cloud_courier.core.hashing import crc32c_to_base64
from mml_cloud_courier.core.models import JobStatus
from mml_cloud_courier.core.paths import display_path
from mml_cloud_courier.store.db import connect
from mml_cloud_courier.store.repository import JobRepository

_CSV_COLUMNS = [
    "relative_path", "object_name", "size_bytes", "method", "state",
    "local_crc32c", "remote_crc32c", "sha256", "generation", "attempts",
    "error_category", "error_message", "started_at", "finished_at",
]

_MAX_FAILURES_SHOWN = 50


@dataclass(frozen=True, slots=True)
class ReportPaths:
    summary_json: Path
    manifest_csv: Path
    report_html: Path


def _duration_seconds(started: str | None, finished: str | None) -> float | None:
    if not started or not finished:
        return None
    delta = datetime.fromisoformat(finished) - datetime.fromisoformat(started)
    return max(delta.total_seconds(), 0.0)


def _b64_or_empty(value: int | None) -> str:
    return crc32c_to_base64(value) if value is not None else ""


def _tmp_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".tmp")


def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write to a sibling temp file then `os.replace` into place — atomic on
    Windows — so a concurrent reader (or a second writer: the worker's
    automatic report and the API's POST /report can both write these files)
    never observes a truncated/interleaved file."""
    tmp = _tmp_path(path)
    tmp.write_text(text, encoding=encoding)
    os.replace(tmp, path)


def write_report(
    db_path,
    job_id: int,
    out_dir: str | os.PathLike[str],
    *,
    bucket: str | None = None,
) -> ReportPaths:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    conn = connect(db_path)
    try:
        repo = JobRepository(conn)
        job = repo.get_job(job_id)
        rows = repo.get_files(job_id)
        events = repo.get_events(job_id)
    finally:
        conn.close()

    scan_error_events = [e for e in events if e["kind"] == "scan_error"]
    counts = Counter(r["state"] for r in rows)
    failures = [r for r in rows if r["error_category"] is not None]
    errors_by_category = Counter(r["error_category"] for r in failures)
    verdict = (
        "COMPLETE" if job["status"] == JobStatus.COMPLETE.value else "INCOMPLETE"
    )
    duration = _duration_seconds(job["started_at"], job["finished_at"])
    verified_bytes = sum(
        r["size_bytes"] for r in rows if r["state"] in ("verified", "skipped")
    )

    summary = {
        "job_id": job_id,
        "name": job["name"],
        "direction": job["direction"],
        "bucket": bucket,
        "source_root": display_path(job["source_root"]),
        "dest_prefix": job["dest_prefix"],
        "status": job["status"],
        "verdict": verdict,
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "duration_seconds": duration,
        "planned_files": job["planned_files"],
        "planned_bytes": job["planned_bytes"],
        "verified_or_skipped_bytes": verified_bytes,
        "throughput_bytes_per_second": (
            verified_bytes / duration if duration else None
        ),
        "counts": dict(counts),
        "errors_by_category": dict(errors_by_category),
        "scan_errors": len(scan_error_events),
    }
    summary_path = out / "summary.json"
    _atomic_write_text(
        summary_path, json.dumps(summary, indent=2, ensure_ascii=False)
    )

    csv_path = out / "manifest.csv"
    csv_tmp = _tmp_path(csv_path)
    with csv_tmp.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "relative_path": r["relative_path"],
                    "object_name": r["object_name"],
                    "size_bytes": r["size_bytes"],
                    "method": r["method"],
                    "state": r["state"],
                    "local_crc32c": _b64_or_empty(r["local_crc32c"]),
                    "remote_crc32c": _b64_or_empty(r["remote_crc32c"]),
                    "sha256": r["sha256"] or "",
                    "generation": r["generation"] or "",
                    "attempts": r["attempts"],
                    "error_category": r["error_category"] or "",
                    "error_message": r["error_message"] or "",
                    "started_at": r["started_at"] or "",
                    "finished_at": r["finished_at"] or "",
                }
            )
    os.replace(csv_tmp, csv_path)

    html_path = out / "report.html"
    _atomic_write_text(html_path, _render_html(summary, failures, scan_error_events))

    return ReportPaths(
        summary_json=summary_path, manifest_csv=csv_path, report_html=html_path
    )


def _render_html(summary: dict, failures, scan_error_events=()) -> str:
    ok = summary["verdict"] == "COMPLETE"
    banner_color = "#166534" if ok else "#991b1b"
    banner_bg = "#dcfce7" if ok else "#fee2e2"

    def esc(value) -> str:
        return html.escape(str(value if value is not None else ""))

    stats = "".join(
        f"<tr><th>{esc(label)}</th><td>{esc(value)}</td></tr>"
        for label, value in [
            ("Job", f'#{summary["job_id"]} — {summary["name"]}'),
            ("Direction", summary["direction"]),
            ("Bucket", summary["bucket"] or "—"),
            ("Source", summary["source_root"]),
            ("Destination prefix", summary["dest_prefix"] or "(bucket root)"),
            ("Started", summary["started_at"] or "—"),
            ("Finished", summary["finished_at"] or "—"),
            ("Planned", f'{summary["planned_files"]} files, {summary["planned_bytes"]} bytes'),
            ("File states", ", ".join(f"{k}: {v}" for k, v in sorted(summary["counts"].items()))),
        ]
    )

    failure_sections = []
    by_category: dict[str, list] = {}
    for row in failures:
        by_category.setdefault(row["error_category"], []).append(row)
    for category, rows in sorted(by_category.items()):
        shown = rows[:_MAX_FAILURES_SHOWN]
        items = "".join(
            f"<li><code>{esc(r['relative_path'])}</code> — {esc(r['error_message'])}</li>"
            for r in shown
        )
        more = (
            f"<p>… and {len(rows) - len(shown)} more.</p>"
            if len(rows) > len(shown)
            else ""
        )
        failure_sections.append(
            f"<h3>{esc(category)} ({len(rows)})</h3><ul>{items}</ul>{more}"
        )
    failures_html = (
        "".join(failure_sections) if failure_sections else "<p>No failures.</p>"
    )

    scan_errors_html = ""
    if scan_error_events:
        shown = scan_error_events[:_MAX_FAILURES_SHOWN]
        items = "".join(f"<li>{esc(e['detail'])}</li>" for e in shown)
        more = (
            f"<p>… and {len(scan_error_events) - len(shown)} more.</p>"
            if len(scan_error_events) > len(shown)
            else ""
        )
        scan_errors_html = (
            f"<h2>Scan errors ({len(scan_error_events)})</h2><ul>{items}</ul>{more}"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Transfer report — {esc(summary["name"])}</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 60rem; margin: 2rem auto; padding: 0 1rem; color: #111; }}
.banner {{ background: {banner_bg}; color: {banner_color}; padding: 1rem 1.5rem; border-radius: 8px; font-size: 1.4rem; font-weight: 700; }}
table {{ border-collapse: collapse; margin: 1.5rem 0; }}
th {{ text-align: left; padding: .3rem 1rem .3rem 0; vertical-align: top; }}
td {{ padding: .3rem 0; }}
code {{ background: #f1f5f9; padding: .1rem .3rem; border-radius: 3px; }}
</style>
</head>
<body>
<div class="banner">{esc(summary["verdict"])}</div>
<table>{stats}</table>
{scan_errors_html}
<h2>Failures</h2>
{failures_html}
</body>
</html>
"""
