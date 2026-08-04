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

    # Probe the root to get the real error category if it fails
    try:
        it = os.scandir(walk_root)
    except OSError as exc:
        classification = classify(exc)
        yield ScanError(
            path=root,
            category=classification.category,
            message=f"{classification.message} ({root})",
        )
        return

    stack = [it]
    while stack:
        current_it = stack.pop()
        try:
            with current_it:
                while True:
                    try:
                        entry = next(current_it)
                    except StopIteration:
                        break
                    except OSError as exc:
                        classification = classify(exc)
                        yield ScanError(
                            path=str(current_it),
                            category=classification.category,
                            message=f"{classification.message}",
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
                            try:
                                stack.append(os.scandir(entry.path))
                            except OSError as exc:
                                classification = classify(exc)
                                yield ScanError(
                                    path=entry.path,
                                    category=classification.category,
                                    message=f"{classification.message} ({entry.path})",
                                )
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
        except OSError as exc:
            # Shouldn't reach here, but handle gracefully
            classification = classify(exc)
            yield ScanError(
                path=str(current_it),
                category=classification.category,
                message=f"{classification.message}",
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
