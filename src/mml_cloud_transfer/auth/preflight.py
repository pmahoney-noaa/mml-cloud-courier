"""Permission probes against a real bucket, in plain language.

Adapted from the release gate's preflight (tests/tools/preflight-gcs.ps1),
which proved the shape: bucket-metadata reads are a separate permission the
product must never require (storage.buckets.get is denied on the reference
bucket), so capabilities are established by DOING the operations, inside a
throwaway probe prefix, and cleaning up by explicit generation so a
versioning-enabled bucket is left with nothing — not even noncurrent
versions (gate Finding 5).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from mml_cloud_transfer.core.errors import classify
from mml_cloud_transfer.core.models import Direction
from mml_cloud_transfer.gcs.client import GcsContext
from mml_cloud_transfer.gcs.objects import delete_object, get_meta

#: Reserved path segment for transient permission-probe objects. Written
#: and deleted seconds apart by run_preflight below; engine/runner.py's
#: scan_remote must never let one into a manifest (final-review finding 2).
PROBE_SEGMENT = ".mmlct-preflight"


def _join(words: list[str]) -> str:
    if len(words) == 1:
        return words[0]
    return ", ".join(words[:-1]) + f" and {words[-1]}"


@dataclass(frozen=True, slots=True)
class PreflightResult:
    bucket: str
    prefix: str
    can_list: bool
    can_read: bool
    can_write: bool
    can_compose: bool
    can_delete: bool
    messages: tuple[str, ...]

    def ok_for(self, direction: Direction) -> bool:
        """Uploads need the full set: write obviously; read for Layer 2
        verification and the Layer 3 audit; compose and delete for sliced
        files' temp objects. Requiring everything at creation beats
        discovering a gap overnight. Downloads read and list only."""
        if direction is Direction.UPLOAD:
            return all((self.can_list, self.can_read, self.can_write,
                        self.can_compose, self.can_delete))
        return self.can_list and self.can_read

    def summary(self) -> str:
        caps = {
            "list": self.can_list, "read": self.can_read, "write": self.can_write,
            "compose": self.can_compose, "delete": self.can_delete,
        }
        target = f"gs://{self.bucket}/{self.prefix}".rstrip("/")
        can = [name for name, ok in caps.items() if ok]
        cannot = [name for name, ok in caps.items() if not ok]
        if not cannot:
            return f"This credential can {_join(can)} to {target}."
        if not can:
            return f"This credential cannot access {target} at all."
        return (
            f"This credential can {_join(can)} but cannot {_join(cannot)}"
            f" to {target}."
        )


def run_preflight(ctx: GcsContext, prefix: str) -> PreflightResult:
    base = prefix.strip("/")
    probe = (f"{base}/" if base else "") + f"{PROBE_SEGMENT}/{uuid.uuid4().hex[:8]}"
    target = f"gs://{ctx.bucket}/{base}".rstrip("/")
    messages: list[str] = []
    written: list[tuple[str, int]] = []  # (name, generation): version-aware cleanup
    bucket_handle = ctx.client.bucket(ctx.bucket)

    def fail(operation: str, exc: Exception) -> None:
        messages.append(f"cannot {operation} to {target}: {classify(exc).message}")

    try:
        list(ctx.client.list_blobs(ctx.bucket, prefix=probe, max_results=1))
        can_list = True
    except Exception as exc:
        can_list = False
        fail("list", exc)

    can_write = True
    for name in (f"{probe}/a.bin", f"{probe}/b.bin"):
        try:
            blob = bucket_handle.blob(name)
            blob.upload_from_string(b"mmlct preflight probe", checksum="crc32c")
            written.append((name, int(blob.generation)))
        except Exception as exc:
            can_write = False
            fail("write", exc)
            break

    try:
        # With nothing written (read-only credential), a metadata GET on an
        # absent name still proves objects.get: a 404 means the server
        # consulted the ACL and answered; a 403 means it refused to — the
        # same distinction worker._probe relies on.
        read_name = written[0][0] if written else f"{probe}/absent.bin"
        get_meta(ctx, read_name)
        can_read = True
    except Exception as exc:
        can_read = False
        fail("read", exc)

    can_compose = False
    if len(written) == 2:
        try:
            composed = bucket_handle.blob(f"{probe}/composed.bin")
            composed.compose([bucket_handle.blob(name) for name, _ in written])
            written.append((f"{probe}/composed.bin", int(composed.generation)))
            can_compose = True
        except Exception as exc:
            fail("compose", exc)

    can_delete = bool(written)
    for name, generation in written:
        try:
            delete_object(ctx, name, generation=generation, ignore_missing=False)
        except Exception as exc:
            can_delete = False
            fail("delete", exc)

    return PreflightResult(
        bucket=ctx.bucket, prefix=base,
        can_list=can_list, can_read=can_read, can_write=can_write,
        can_compose=can_compose, can_delete=can_delete,
        messages=tuple(messages),
    )
