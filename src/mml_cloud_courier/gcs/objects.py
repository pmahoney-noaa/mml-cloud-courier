"""Object metadata vocabulary shared by the uploader, downloader, and audit."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import requests

from mml_cloud_courier.core.hashing import crc32c_from_base64
from mml_cloud_courier.gcs.client import GcsContext


class GcsHttpError(Exception):
    """Raw-HTTP failure carrying the status code where classify() looks for it."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"HTTP {code}: {message}")
        self.code = code


def raise_for_status(response: requests.Response) -> None:
    """Raise GcsHttpError for anything that is not 2xx or 308."""
    if response.status_code == 308 or 200 <= response.status_code < 300:
        return
    raise GcsHttpError(response.status_code, response.text[:500])


@dataclass(frozen=True, slots=True)
class ObjectMeta:
    name: str
    size: int
    crc32c: int
    generation: int


def _to_meta(blob) -> ObjectMeta:
    return ObjectMeta(
        name=blob.name,
        size=int(blob.size),
        crc32c=crc32c_from_base64(blob.crc32c),
        generation=int(blob.generation),
    )


def get_meta(ctx: GcsContext, name: str) -> ObjectMeta | None:
    blob = ctx.client.bucket(ctx.bucket).get_blob(name)
    return None if blob is None else _to_meta(blob)


def list_prefix(ctx: GcsContext, prefix: str) -> Iterator[ObjectMeta]:
    for blob in ctx.client.list_blobs(ctx.bucket, prefix=prefix):
        yield _to_meta(blob)


def delete_object(
    ctx: GcsContext,
    name: str,
    *,
    generation: int | None = None,
    ignore_missing: bool = True,
) -> None:
    """Delete ``name``. With ``generation``, delete that exact version.

    A generation-less delete only clears the live pointer, which on a
    versioning-enabled bucket archives the object as a noncurrent version
    instead of removing it -- so temp objects would bill forever. Passing an
    explicit generation makes it a real delete. It is also safer: if the
    object was replaced since we read its metadata, the generation no longer
    matches and we decline to delete a newer object we never inspected.
    """
    from google.api_core.exceptions import NotFound

    try:
        ctx.client.bucket(ctx.bucket).delete_blob(name, generation=generation)
    except NotFound:
        if not ignore_missing:
            raise
