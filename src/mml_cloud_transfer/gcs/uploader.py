"""Upload paths. This task: single-shot plus the shared skip/verify rules.

Tasks 7 and 8 extend this module with the resumable and sliced paths.
"""

from __future__ import annotations

from dataclasses import dataclass

from mml_cloud_transfer.core.hashing import crc32c_to_base64, hash_file
from mml_cloud_transfer.gcs.client import GcsContext
from mml_cloud_transfer.gcs.objects import ObjectMeta, get_meta


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
