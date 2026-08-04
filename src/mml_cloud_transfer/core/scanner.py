"""Walk a source tree into PlannedFile entries.

Yields lazily: a job may contain millions of files, so nothing accumulates
a full list unless the caller asks for one. Per-entry failures become
ScanError values rather than exceptions, so one unreadable folder never
aborts a scan.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from mml_cloud_transfer.core.errors import ErrorCategory, ScanError, classify
from mml_cloud_transfer.core.models import PlannedFile
from mml_cloud_transfer.core.paths import extended_path, to_relative_path

ScanEntry = PlannedFile | ScanError


@dataclass(frozen=True, slots=True)
class ScanTotals:
    file_count: int
    byte_count: int
    error_count: int


def iter_source(root: str, *, follow_extended: bool = True) -> Iterator[ScanEntry]:
    """Yield one entry per file beneath ``root``.

    ``follow_extended`` controls whether paths are rewritten to ``\\\\?\\``
    form; tests disable it so they can run on any platform.
    """
    walk_root = extended_path(root) if follow_extended else root

    stack = [walk_root]
    while stack:
        current = stack.pop()
        try:
            it = os.scandir(current)
        except OSError as exc:
            classification = classify(exc)
            yield ScanError(
                path=current if current == walk_root else current,
                category=classification.category,
                message=f"{classification.message} ({current})",
            )
            continue

        with it:
            while True:
                try:
                    entry = next(it)
                except StopIteration:
                    break
                except OSError as exc:
                    classification = classify(exc)
                    yield ScanError(
                        path=current,
                        category=classification.category,
                        message=f"{classification.message} ({current})",
                    )
                    break

                try:
                    # Skip symlinks and junctions (reparse points)
                    if entry.is_symlink() or entry.is_junction():
                        yield ScanError(
                            path=entry.path,
                            category=ErrorCategory.UNKNOWN,
                            message=f"Skipped link or junction: {entry.path}",
                        )
                        continue
                    if entry.is_dir():
                        stack.append(entry.path)
                        continue

                    stat = entry.stat()
                    yield PlannedFile(
                        relative_path=to_relative_path(walk_root, entry.path),
                        source_path=entry.path,
                        size_bytes=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                    )
                except OSError as exc:
                    classification = classify(exc)
                    yield ScanError(
                        path=entry.path,
                        category=classification.category,
                        message=f"{classification.message} ({entry.path})",
                    )


def summarise(
    entries: Iterable[ScanEntry],
) -> tuple[list[PlannedFile], list[ScanError], ScanTotals]:
    """Materialise a scan. Only for small trees and tests — prefer streaming."""
    files: list[PlannedFile] = []
    errors: list[ScanError] = []
    for entry in entries:
        if isinstance(entry, PlannedFile):
            files.append(entry)
        else:
            errors.append(entry)
    totals = ScanTotals(
        file_count=len(files),
        byte_count=sum(f.size_bytes for f in files),
        error_count=len(errors),
    )
    return files, errors, totals
