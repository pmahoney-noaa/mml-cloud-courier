"""Ranged, resumable downloads with atomic finalization.

The object's generation is pinned on every range request, so a file that
is replaced mid-download produces a clean failure instead of a chimera of
two generations. The ``.part`` file becomes the destination only after the
combined CRC matches the object's metadata.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import google_crc32c

from mml_cloud_transfer.core.crc32c_combine import combine_all
from mml_cloud_transfer.core.hashing import hash_file
from mml_cloud_transfer.core.slicing import SliceSpec
from mml_cloud_transfer.gcs.client import GcsContext
from mml_cloud_transfer.gcs.objects import GcsHttpError, get_meta, raise_for_status
from mml_cloud_transfer.gcs.uploader import ChecksumMismatch, should_skip

DOWNLOAD_RANGE_BYTES = 128 * 1024 * 1024

RangeProgressFn = Callable[[int, int, int | None], None]


@dataclass(frozen=True, slots=True)
class DownloadResult:
    state: str  # "verified" | "skipped"
    local_crc32c: int
    remote_crc32c: int
    generation: int
    sha256: str | None
    bytes_received: int


def plan_ranges(
    size_bytes: int, *, range_bytes: int = DOWNLOAD_RANGE_BYTES
) -> list[SliceSpec]:
    """Fixed-size ranges — no component cap applies to downloads."""
    if size_bytes < 0:
        raise ValueError("size_bytes must not be negative")
    if size_bytes == 0:
        return []
    ranges = []
    offset = 0
    index = 0
    while offset < size_bytes:
        length = min(range_bytes, size_bytes - offset)
        ranges.append(SliceSpec(index=index, offset=offset, length=length))
        offset += length
        index += 1
    return ranges


def _media_url(ctx: GcsContext, object_name: str, generation: int) -> str:
    encoded = quote(object_name, safe="")
    return (
        f"{ctx.endpoint}/download/storage/v1/b/{ctx.bucket}/o/{encoded}"
        f"?alt=media&generation={generation}"
    )


def _fetch_range(
    ctx: GcsContext,
    url: str,
    part_path: Path,
    spec: SliceSpec,
    on_progress: RangeProgressFn | None,
) -> int:
    """Stream one range into the part file; returns the range CRC32C."""
    crc = google_crc32c.Checksum()
    done = 0
    response = ctx.session.get(
        url,
        headers={"Range": f"bytes={spec.offset}-{spec.offset + spec.length - 1}"},
        stream=True,
    )
    raise_for_status(response)
    # A separate handle per worker: seek + write is safe across threads
    # because ranges never overlap.
    with part_path.open("r+b") as fp:
        fp.seek(spec.offset)
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            fp.write(chunk)
            crc.update(chunk)
            done += len(chunk)
            if on_progress is not None:
                on_progress(spec.index, done, None)
    if done != spec.length:
        raise GcsHttpError(500, f"range {spec.index}: got {done} of {spec.length} bytes")
    range_crc = int.from_bytes(crc.digest(), "big")
    if on_progress is not None:
        on_progress(spec.index, done, range_crc)
    return range_crc


def download_file(
    ctx: GcsContext,
    object_name: str,
    dest_path: str,
    *,
    range_states: dict[int, int] | None = None,
    range_bytes: int = DOWNLOAD_RANGE_BYTES,
    max_workers: int = 4,
    with_sha256: bool = False,
    on_progress: RangeProgressFn | None = None,
) -> DownloadResult:
    range_states = range_states or {}
    meta = get_meta(ctx, object_name)
    if meta is None:
        raise GcsHttpError(404, f"object not found: {object_name}")

    dest = Path(dest_path)

    # Skip rule: the local file already matches the object.
    if dest.exists() and dest.stat().st_size == meta.size:
        local = hash_file(dest, with_sha256=with_sha256)
        if should_skip(meta, local.bytes_read, local.crc32c):
            return DownloadResult(
                state="skipped",
                local_crc32c=local.crc32c,
                remote_crc32c=meta.crc32c,
                generation=meta.generation,
                sha256=local.sha256,
                bytes_received=0,
            )

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = Path(str(dest) + ".part")

    ranges = plan_ranges(meta.size, range_bytes=range_bytes)

    # Create/size the part file. A part file of the wrong size belongs to a
    # different generation or range layout — start over in that case.
    if part.exists():
        if part.stat().st_size != meta.size:
            # Part file exists but wrong size -> different generation/layout, start over
            with part.open("wb") as fp:
                if meta.size:
                    fp.seek(meta.size - 1)
                    fp.write(b"\0")
            range_states = {}
    else:
        # Part file doesn't exist -> create it (preserving any passed-in range_states)
        with part.open("wb") as fp:
            if meta.size:
                fp.seek(meta.size - 1)
                fp.write(b"\0")

    # Restore data from destination or part file if available (for resume scenarios).
    # Destination: normal completion, part file persists (if reused).
    # Part file: crash-interrupted download, part file survives with partial data.
    if dest.exists() and dest.stat().st_size == meta.size:
        with dest.open("rb") as src, part.open("r+b") as dst:
            src_data = src.read()
            dst.seek(0)
            dst.write(src_data)
    elif part.exists() and part.stat().st_size == meta.size and range_states:
        # Part file from previous (interrupted) download exists with partial data.
        # Keep it as-is; fetched ranges will overwrite their portions.
        pass

    url = _media_url(ctx, object_name, meta.generation)
    crcs: dict[int, int] = dict(range_states)
    to_fetch = [spec for spec in ranges if spec.index not in crcs]

    bytes_received = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_fetch_range, ctx, url, part, spec, on_progress): spec
            for spec in to_fetch
        }
        for future, spec in futures.items():
            crcs[spec.index] = future.result()
            bytes_received += spec.length

    whole_crc = (
        combine_all([(crcs[spec.index], spec.length) for spec in ranges])
        if ranges
        else 0
    )
    if whole_crc != meta.crc32c or sum(s.length for s in ranges) != meta.size:
        raise ChecksumMismatch(
            f"{object_name}: assembled crc={whole_crc} vs remote crc={meta.crc32c}"
        )

    sha256 = hash_file(part, with_sha256=True).sha256 if with_sha256 else None
    # Atomically finalize by replacing destination with verified part file
    os.replace(str(part), str(dest))

    return DownloadResult(
        state="verified",
        local_crc32c=whole_crc,
        remote_crc32c=meta.crc32c,
        generation=meta.generation,
        sha256=sha256,
        bytes_received=bytes_received,
    )
