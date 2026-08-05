"""GCS resumable-upload session protocol, driven by us.

Why hand-rolled: the session URI is persisted to SQLite so an upload can
resume after the process dies, the committed offset is re-queried from the
server on resume, and tests inject a scripted session to exercise every
protocol branch without a network.

Protocol reference: https://cloud.google.com/storage/docs/performing-resumable-uploads
"""

from __future__ import annotations

from dataclasses import dataclass

from mml_cloud_transfer.core.hashing import crc32c_from_base64
from mml_cloud_transfer.gcs.client import GcsContext
from mml_cloud_transfer.gcs.objects import GcsHttpError, ObjectMeta

#: Non-final chunks must be a multiple of this.
CHUNK_ALIGN = 256 * 1024


class SessionExpired(Exception):
    """The session URI is dead (404/410) — restart the transfer from zero."""


@dataclass(frozen=True, slots=True)
class PutResult:
    committed: int
    finalized: ObjectMeta | None


def initiate_upload(
    ctx: GcsContext,
    object_name: str,
    total_size: int,
    *,
    precondition_generation: int | None = None,
) -> str:
    """Open a resumable session and return its URI (persist it immediately)."""
    blob = ctx.client.bucket(ctx.bucket).blob(object_name)
    return blob.create_resumable_upload_session(
        size=total_size,
        content_type="application/octet-stream",
        if_generation_match=precondition_generation,
    )


def _parse_committed(headers) -> int:
    header = headers.get("Range") or headers.get("range")
    if not header:
        return 0
    # Format: "bytes=0-N" — N is the last committed byte index.
    return int(header.split("-", 1)[1]) + 1


def _finalized_meta(body: dict) -> ObjectMeta:
    return ObjectMeta(
        name=body["name"],
        size=int(body["size"]),
        crc32c=crc32c_from_base64(body["crc32c"]),
        generation=int(body["generation"]),
    )


def _handle(response, total: int) -> PutResult:
    if response.status_code == 308:
        return PutResult(committed=_parse_committed(response.headers), finalized=None)
    if response.status_code in (200, 201):
        meta = _finalized_meta(response.json())
        return PutResult(committed=total, finalized=meta)
    if response.status_code in (404, 410):
        raise SessionExpired(f"upload session is gone (HTTP {response.status_code})")
    raise GcsHttpError(response.status_code, response.text[:500])


def put_chunk(session, uri: str, data: bytes, start: int, total: int) -> PutResult:
    """Send one chunk. The final chunk is the one where start+len == total."""
    end = start + len(data) - 1
    is_final = start + len(data) == total
    if not is_final and total >= CHUNK_ALIGN and len(data) % CHUNK_ALIGN != 0:
        raise ValueError(f"non-final chunks must be a multiple of 256 KiB, got {len(data)}")
    response = session.put(
        uri,
        data=data,
        headers={"Content-Range": f"bytes {start}-{end}/{total}"},
    )
    return _handle(response, total)


def query_offset(session, uri: str, total: int) -> PutResult:
    """Ask the server how much it has committed (used on resume)."""
    response = session.put(
        uri,
        data=b"",
        headers={"Content-Range": f"bytes */{total}"},
    )
    return _handle(response, total)
