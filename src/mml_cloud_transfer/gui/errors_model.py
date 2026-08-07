"""Errors grouped by cause, not by file — the spec's one-problem rule.
Message and action text arrive from the API (core/errors taxonomy);
this module only shapes them for the tree view and clipboard."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorGroup:
    category: str
    count: int
    quarantined: int
    message: str
    action: str

    @property
    def label(self) -> str:
        noun = "file" if self.count == 1 else "files"
        return f"{self.message} — {self.count:,} {noun}"


def build_error_groups(raw: list[dict]) -> list[ErrorGroup]:
    return [
        ErrorGroup(
            category=row["category"], count=row["count"],
            quarantined=row["quarantined"], message=row["message"],
            action=row["action"],
        )
        for row in raw
    ]


def fetch_group_paths(client, job_id: int, category: str, *, cap: int = 20_000) -> list[str]:
    paths: list[str] = []
    page_size = 500
    while len(paths) < cap:
        page = client.files(job_id, category=category,
                            limit=page_size, offset=len(paths))
        paths += [row["relative_path"] for row in page]
        if len(page) < page_size:
            break
    return paths[:cap]
