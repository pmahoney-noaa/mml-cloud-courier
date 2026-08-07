"""Upload paths. This task: single-shot plus the shared skip/verify rules.

Task 8 extends this module with the sliced path.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import google_crc32c

from mml_cloud_courier.core.crc32c_combine import combine_all
from mml_cloud_courier.core.errors import TransferStopped
from mml_cloud_courier.core.hashing import crc32c_to_base64, hash_file, hash_range
from mml_cloud_courier.core.slicing import SizePolicy, SliceSpec, plan_slices
from mml_cloud_courier.gcs.client import GcsContext
from mml_cloud_courier.gcs.objects import ObjectMeta, delete_object, get_meta
from mml_cloud_courier.gcs.resumable import (
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
        blob.metadata = {"mmlcc-sha256": local.sha256}  # audit hash travels with the object
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
    blob.metadata = {"mmlcc-sha256": sha256}
    blob.patch()


def _hash_prefix(fp: BinaryIO, hashes: _StreamHashes, length: int, chunk_size: int) -> None:
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
    should_stop: Callable[[], bool] | None = None,
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

    if session_uri is not None:
        try:
            status = query_offset(ctx.session, session_uri, size_bytes)
        except SessionExpired:
            session_uri = None
        else:
            if status.finalized is not None:
                # Finished before the crash was noticed — verify and return.
                # If the size doesn't match, this object is not our upload.
                if status.finalized.size != size_bytes:
                    raise ChecksumMismatch(
                        f"{object_name}: resuming query returned finalized object "
                        f"with size={status.finalized.size} but expected {size_bytes}"
                    )
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
                committed = status.committed

    if session_uri is None:
        session_uri = initiate_upload(
            ctx, object_name, size_bytes,
            precondition_generation=precondition_generation,
        )
        committed = 0
        report(session_uri, 0)

    bytes_sent = 0
    with Path(source_path).open("rb") as fp:
        if committed:
            _hash_prefix(fp, hashes, committed, chunk_size)

        offset = committed
        finalized = None
        while offset < size_bytes:
            if should_stop is not None and should_stop():
                raise TransferStopped(object_name)
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


SliceProgressFn = Callable[[int, str | None, int, int | None], None]


def slice_temp_name(object_name: str, index: int) -> str:
    return f"{object_name}.mmlcc.tmp/{index:04d}"


def upload_slice(
    ctx: GcsContext,
    source_path: str,
    object_name: str,
    spec: SliceSpec,
    *,
    session_uri: str | None = None,
    chunk_size: int = 8 * 1024 * 1024,
    on_progress: SliceProgressFn | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[int, ObjectMeta]:
    """Upload one slice to its temp object; returns (slice_crc32c, temp meta)."""
    temp_name = slice_temp_name(object_name, spec.index)

    def report(uri: str | None, committed: int, crc: int | None) -> None:
        if on_progress is not None:
            on_progress(spec.index, uri, committed, crc)

    hashes = _StreamHashes(with_sha256=False)
    committed = 0

    if session_uri is not None:
        try:
            status = query_offset(ctx.session, session_uri, spec.length)
        except SessionExpired:
            session_uri = None
        else:
            if status.finalized is not None:
                crc = hash_range(source_path, spec.offset, spec.length).crc32c
                verify_layer2(status.finalized, spec.length, crc)
                report(session_uri, spec.length, crc)
                return crc, status.finalized
            committed = status.committed

    if session_uri is None:
        session_uri = initiate_upload(ctx, temp_name, spec.length)
        committed = 0
        report(session_uri, 0, None)

    with Path(source_path).open("rb") as fp:
        fp.seek(spec.offset)
        if committed:
            _hash_prefix(fp, hashes, committed, chunk_size)

        offset = committed
        finalized = None
        while offset < spec.length:
            if should_stop is not None and should_stop():
                raise TransferStopped(object_name)
            data = fp.read(min(chunk_size, spec.length - offset))
            hashes.update(data)
            result = put_chunk(ctx.session, session_uri, data, offset, spec.length)
            offset += len(data)
            report(session_uri, result.committed, None)
            if result.finalized is not None:
                finalized = result.finalized
                break

    if finalized is None:
        raise ChecksumMismatch(f"{temp_name}: slice session ended without finalizing")
    verify_layer2(finalized, spec.length, hashes.crc32c)
    report(session_uri, spec.length, hashes.crc32c)
    return hashes.crc32c, finalized


def compose_slices(
    ctx: GcsContext,
    object_name: str,
    slice_metas: list[ObjectMeta],
    expected_crc32c: int,
    total_size: int,
    *,
    precondition_generation: int | None,
) -> UploadResult:
    """Compose temp objects (in list order) into the destination and verify."""
    bucket = ctx.client.bucket(ctx.bucket)
    destination = bucket.blob(object_name)
    # Pin each source to the exact generation we verified, rather than a
    # fresh, generation-less Blob (which would compose whatever happens to be
    # live). This makes compose fail fast on a replaced temp instead of
    # silently composing different bytes than the ones Layer 2 will verify.
    sources = [bucket.blob(meta.name, generation=meta.generation) for meta in slice_metas]
    destination.compose(sources, if_generation_match=precondition_generation)

    meta = get_meta(ctx, object_name)
    if meta is None:
        raise ChecksumMismatch(f"{object_name}: object missing after compose")
    verify_layer2(meta, total_size, expected_crc32c)

    # Sweep EVERY version under the temp prefix, not just the generations we
    # happen to hold. Compose has succeeded and Layer 2 has verified, so
    # anything still here is garbage. A generation-scoped loop would miss two
    # cases: temps overwritten by an earlier failed attempt (whose prior
    # generations are already noncurrent), and a generation mismatch that 404s
    # and is silently swallowed. On a versioning-enabled bucket either one
    # leaves billable bytes that no lifecycle rule can target, because
    # `.mmlcc.tmp/` is an infix and matchesPrefix has no wildcards.
    temp_prefix = f"{object_name}.mmlcc.tmp/"
    for blob in list(
        ctx.client.list_blobs(ctx.bucket, prefix=temp_prefix, versions=True)
    ):
        delete_object(ctx, blob.name, generation=blob.generation)

    return UploadResult(
        state="verified",
        local_crc32c=expected_crc32c,
        remote_crc32c=meta.crc32c,
        generation=meta.generation,
        sha256=None,
        bytes_sent=0,
    )


def upload_sliced(
    ctx: GcsContext,
    source_path: str,
    object_name: str,
    size_bytes: int,
    *,
    precondition_generation: int | None,
    policy: SizePolicy | None = None,
    slice_states: dict[int, tuple[str | None, int | None]] | None = None,
    max_workers: int = 4,
    chunk_size: int = 8 * 1024 * 1024,
    with_sha256: bool = False,
    on_progress: SliceProgressFn | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> UploadResult:
    slice_states = slice_states or {}
    specs = plan_slices(size_bytes, policy=policy)

    # Skip rule: one full local read only when the destination looks plausible.
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

    results: dict[int, tuple[int, ObjectMeta]] = {}
    to_upload: list[SliceSpec] = []
    for spec in specs:
        uri, known_crc = slice_states.get(spec.index, (None, None))
        if known_crc is not None:
            temp_meta = get_meta(ctx, slice_temp_name(object_name, spec.index))
            if temp_meta is not None and temp_meta.crc32c == known_crc:
                results[spec.index] = (known_crc, temp_meta)
                continue
        to_upload.append(spec)

    if should_stop is not None and should_stop():
        raise TransferStopped(object_name)

    bytes_sent = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                upload_slice,
                ctx,
                source_path,
                object_name,
                spec,
                session_uri=slice_states.get(spec.index, (None, None))[0],
                chunk_size=chunk_size,
                on_progress=on_progress,
                should_stop=should_stop,
            ): spec
            for spec in to_upload
        }
        for future, spec in futures.items():
            crc, temp_meta = future.result()  # re-raises worker failures
            results[spec.index] = (crc, temp_meta)
            bytes_sent += spec.length
    # Context exit calls shutdown(wait=True) without cancel_futures, so already-submitted
    # slices run to completion. Leftover temp objects and live sessions are picked up by
    # the next resume (matching temp CRCs are reused) or the bucket lifecycle rule.

    ordered = [results[spec.index] for spec in specs]
    whole_crc = combine_all([(crc, spec.length) for (crc, _), spec in zip(ordered, specs)])
    composed = compose_slices(
        ctx,
        object_name,
        [meta for _, meta in ordered],
        whole_crc,
        size_bytes,
        precondition_generation=precondition_generation,
    )

    sha256 = None
    if with_sha256:
        sha256 = hash_file(source_path, with_sha256=True).sha256
        stamp_sha256(ctx, object_name, sha256)

    return UploadResult(
        state="verified",
        local_crc32c=whole_crc,
        remote_crc32c=composed.remote_crc32c,
        generation=composed.generation,
        sha256=sha256,
        bytes_sent=bytes_sent,
    )
