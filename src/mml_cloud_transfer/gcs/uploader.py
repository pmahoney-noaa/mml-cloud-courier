"""Upload paths. This task: single-shot plus the shared skip/verify rules.

Tasks 7 and 8 extend this module with the resumable and sliced paths.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import google_crc32c

from mml_cloud_transfer.core.hashing import crc32c_to_base64, hash_file
from mml_cloud_transfer.gcs.client import GcsContext
from mml_cloud_transfer.gcs.objects import ObjectMeta, get_meta
from mml_cloud_transfer.gcs.resumable import (
    CHUNK_ALIGN,
    SessionExpired,
    initiate_upload,
    put_chunk,
    query_offset,
)


class ChecksumMismatch(Exception):
    """Layer-2 failure: the finalized object does not match the local file."""


@dataclass(frozen=True, slots=True)
class UploadResult:
    state: str  # "verified" | "skipped"
    local_crc32c: int
    remote_crc32c: int
    generation: int
    sha256: str | None
    bytes_sent: int


def should_skip(meta: ObjectMeta | None, size: int, local_crc32c: int) -> bool:
    """The spec's skip rule: destination exists with matching size AND CRC32C."""
    return meta is not None and meta.size == size and meta.crc32c == local_crc32c


def verify_layer2(meta: ObjectMeta, size: int, local_crc32c: int) -> None:
    """Whole-object verification. Raises ChecksumMismatch on any disagreement."""
    if meta.size != size or meta.crc32c != local_crc32c:
        raise ChecksumMismatch(
            f"{meta.name}: remote size={meta.size} crc={meta.crc32c} vs "
            f"local size={size} crc={local_crc32c}"
        )


def upload_single_shot(
    ctx: GcsContext,
    source_path: str,
    object_name: str,
    *,
    precondition_generation: int | None,
    with_sha256: bool = False,
) -> UploadResult:
    local = hash_file(source_path, with_sha256=with_sha256)

    existing = get_meta(ctx, object_name)
    if should_skip(existing, local.bytes_read, local.crc32c):
        return UploadResult(
            state="skipped",
            local_crc32c=local.crc32c,
            remote_crc32c=existing.crc32c,
            generation=existing.generation,
            sha256=local.sha256,
            bytes_sent=0,
        )

    blob = ctx.client.bucket(ctx.bucket).blob(object_name)
    blob.crc32c = crc32c_to_base64(local.crc32c)  # Layer 1: server rejects a bad write
    if local.sha256 is not None:
        blob.metadata = {"mmlct-sha256": local.sha256}  # audit hash travels with the object
    blob.upload_from_filename(
        source_path,
        checksum=None,  # we set blob.crc32c ourselves — whole-file, not per-chunk
        if_generation_match=precondition_generation,
    )

    meta = get_meta(ctx, object_name)
    if meta is None:
        raise ChecksumMismatch(f"{object_name}: object missing immediately after upload")
    verify_layer2(meta, local.bytes_read, local.crc32c)

    return UploadResult(
        state="verified",
        local_crc32c=local.crc32c,
        remote_crc32c=meta.crc32c,
        generation=meta.generation,
        sha256=local.sha256,
        bytes_sent=local.bytes_read,
    )


ProgressFn = Callable[[str, int], None]


class _StreamHashes:
    """Incremental CRC32C (+ optional SHA-256) fed once per chunk."""

    def __init__(self, with_sha256: bool) -> None:
        self._crc = google_crc32c.Checksum()
        self._sha = hashlib.sha256() if with_sha256 else None

    def update(self, chunk: bytes) -> None:
        self._crc.update(chunk)
        if self._sha is not None:
            self._sha.update(chunk)

    @property
    def crc32c(self) -> int:
        return int.from_bytes(self._crc.digest(), "big")

    @property
    def sha256(self) -> str | None:
        return self._sha.hexdigest() if self._sha is not None else None


def stamp_sha256(ctx: GcsContext, object_name: str, sha256: str) -> None:
    """Record the audit hash in the object's custom metadata (spec requirement)."""
    blob = ctx.client.bucket(ctx.bucket).blob(object_name)
    blob.metadata = {"mmlct-sha256": sha256}
    blob.patch()


def _hash_prefix(fp, hashes: _StreamHashes, length: int, chunk_size: int) -> None:
    """Feed the first ``length`` bytes into ``hashes`` (used on resume)."""
    remaining = length
    while remaining > 0:
        chunk = fp.read(min(chunk_size, remaining))
        if not chunk:
            raise ValueError(f"file shorter than committed offset {length}")
        hashes.update(chunk)
        remaining -= len(chunk)


def upload_resumable(
    ctx: GcsContext,
    source_path: str,
    object_name: str,
    size_bytes: int,
    *,
    precondition_generation: int | None,
    session_uri: str | None = None,
    with_sha256: bool = False,
    chunk_size: int = 8 * 1024 * 1024,
    on_progress: ProgressFn | None = None,
) -> UploadResult:
    if chunk_size % CHUNK_ALIGN != 0:
        raise ValueError(f"chunk_size must be a multiple of 256 KiB, got {chunk_size}")
    if size_bytes <= 0:
        # Method selection routes empty files to single-shot; a zero total
        # would also produce a malformed Content-Range here.
        raise ValueError("upload_resumable requires size_bytes > 0")

    def report(uri: str, committed: int) -> None:
        if on_progress is not None:
            on_progress(uri, committed)

    # Skip rule — only worth a full local read if the destination exists.
    existing = get_meta(ctx, object_name)
    if existing is not None and existing.size == size_bytes:
        local = hash_file(source_path, with_sha256=with_sha256)
        if should_skip(existing, size_bytes, local.crc32c):
            return UploadResult(
                state="skipped",
                local_crc32c=local.crc32c,
                remote_crc32c=existing.crc32c,
                generation=existing.generation,
                sha256=local.sha256,
                bytes_sent=0,
            )

    hashes = _StreamHashes(with_sha256)
    committed = 0
    restart_without_precondition = False

    if session_uri is not None:
        try:
            status = query_offset(ctx.session, session_uri, size_bytes)
        except SessionExpired:
            session_uri = None
        else:
            if status.finalized is not None:
                # Finished before the crash was noticed — verify and return,
                # but only if it's actually complete. Otherwise treat as session expired.
                if status.finalized.size == size_bytes:
                    local = hash_file(source_path, with_sha256=with_sha256)
                    verify_layer2(status.finalized, size_bytes, local.crc32c)
                    return UploadResult(
                        state="verified",
                        local_crc32c=local.crc32c,
                        remote_crc32c=status.finalized.crc32c,
                        generation=status.finalized.generation,
                        sha256=local.sha256,
                        bytes_sent=0,
                    )
                else:
                    # Session returned finalized but incomplete; restart without precondition
                    # since the object already exists with a different generation.
                    # Preserve the committed bytes so we only send the remainder.
                    committed = status.finalized.size
                    session_uri = None
                    restart_without_precondition = True
            else:
                committed = status.committed

    if session_uri is None:
        restart_precondition = None if restart_without_precondition else precondition_generation
        session_uri = initiate_upload(
            ctx, object_name, size_bytes,
            precondition_generation=restart_precondition,
        )
        # Only reset committed if this is a fresh start, not a restart from incomplete.
        if not restart_without_precondition:
            committed = 0
            report(session_uri, 0)
        else:
            # Restart: report the committed bytes we already have on the server.
            report(session_uri, committed)

    bytes_sent = 0
    with Path(source_path).open("rb") as fp:
        if committed:
            _hash_prefix(fp, hashes, committed, chunk_size)

        offset = committed
        finalized = None
        while offset < size_bytes:
            data = fp.read(min(chunk_size, size_bytes - offset))
            hashes.update(data)
            result = put_chunk(ctx.session, session_uri, data, offset, size_bytes)
            offset += len(data)
            bytes_sent += len(data)
            report(session_uri, result.committed)
            if result.finalized is not None:
                finalized = result.finalized
                break

    if finalized is None:
        raise ChecksumMismatch(f"{object_name}: session ended without finalizing")
    verify_layer2(finalized, size_bytes, hashes.crc32c)
    if hashes.sha256 is not None:
        stamp_sha256(ctx, object_name, hashes.sha256)

    return UploadResult(
        state="verified",
        local_crc32c=hashes.crc32c,
        remote_crc32c=finalized.crc32c,
        generation=finalized.generation,
        sha256=hashes.sha256,
        bytes_sent=bytes_sent,
    )
