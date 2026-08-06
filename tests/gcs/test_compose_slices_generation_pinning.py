"""Credential-free pin on compose_slices' temp-object sweep.

No bucket, no emulator -- a stub client that records every call it receives.
This exists because a reviewer mutation-tested the tree behind the
versioning fix (release-gate Finding 5, `docs/superpowers/gates/
2026-08-05-plan2-release-gate.md`) and found that EITHER reverting
compose_slices' delete call to a generation-less delete, OR making
delete_object() silently drop the `generation` kwarg, left the entire
bucket-free suite green. Nothing else in this codebase distinguishes "deletes
by exact generation" from "deletes the live pointer" -- the emulator can't
(fake-gcs-server ignores the `generation` query param on DELETE entirely),
and only the real-bucket suite could tell the difference, which won't run in
CI without credentials. This test makes that distinction visible without
either.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mml_cloud_transfer.core.hashing import crc32c_to_base64
from mml_cloud_transfer.gcs.client import GcsContext
from mml_cloud_transfer.gcs.objects import ObjectMeta
from mml_cloud_transfer.gcs.uploader import compose_slices

OBJECT_NAME = "dest/object.bin"
TEMP_PREFIX = f"{OBJECT_NAME}.mmlct.tmp/"
TOTAL_SIZE = 2048
EXPECTED_CRC = 0x1234ABCD


@dataclass
class _FakeBlobRef:
    """Stands in for google.cloud.storage.Blob -- only what compose_slices touches."""

    client: "_FakeClient"
    name: str
    generation: int | None = None

    def compose(self, sources, if_generation_match=None) -> None:
        self.client.compose_calls.append(
            {
                "destination": self.name,
                "sources": [(s.name, s.generation) for s in sources],
                "if_generation_match": if_generation_match,
            }
        )


@dataclass
class _FakeMetaBlob:
    """What bucket.get_blob() returns for the composed destination object."""

    name: str
    size: int
    crc32c: str
    generation: int


class _FakeBucket:
    def __init__(self, client: "_FakeClient") -> None:
        self._client = client

    def blob(self, name: str, generation: int | None = None) -> _FakeBlobRef:
        return _FakeBlobRef(self._client, name, generation)

    def get_blob(self, name: str) -> _FakeMetaBlob | None:
        return self._client.objects.get(name)

    def delete_blob(self, name: str, generation: int | None = None) -> None:
        self._client.delete_calls.append((name, generation))


@dataclass
class _FakeClient:
    """Records every call compose_slices (and, through it, delete_object) makes."""

    bucket_name: str
    objects: dict = field(default_factory=dict)
    temps: list = field(default_factory=list)
    delete_calls: list = field(default_factory=list)
    compose_calls: list = field(default_factory=list)
    list_calls: list = field(default_factory=list)

    def bucket(self, name: str) -> _FakeBucket:
        assert name == self.bucket_name, f"unexpected bucket {name!r}"
        return _FakeBucket(self)

    def list_blobs(self, bucket_name: str, *, prefix: str, versions: bool = False):
        assert bucket_name == self.bucket_name, f"unexpected bucket {bucket_name!r}"
        self.list_calls.append((prefix, versions))
        return [t for t in self.temps if t.name.startswith(prefix)]


def _build_fixture():
    """Two slice temps at distinct generations, plus a composed destination.

    The temps' generations (1001, 1002) deliberately differ from the
    ObjectMeta objects compose_slices is handed, mirroring what a real
    `list_blobs(..., versions=True)` after compose returns: the blobs
    currently sitting under the temp prefix, which is exactly what the
    totalizing sweep (release-gate Item 1) is supposed to delete by their own
    generation -- not whatever generation happened to be in slice_metas.
    """
    client = _FakeClient(bucket_name="fake-bucket")
    composed = _FakeMetaBlob(
        name=OBJECT_NAME,
        size=TOTAL_SIZE,
        crc32c=crc32c_to_base64(EXPECTED_CRC),
        generation=999,
    )
    client.objects[OBJECT_NAME] = composed

    temps = [
        _FakeBlobRef(client, f"{TEMP_PREFIX}0000", generation=1001),
        _FakeBlobRef(client, f"{TEMP_PREFIX}0001", generation=1002),
    ]
    client.temps = temps

    slice_metas = [
        ObjectMeta(name=t.name, size=1024, crc32c=0, generation=t.generation) for t in temps
    ]

    ctx = GcsContext(client=client, session=None, endpoint="fake://", bucket=client.bucket_name)
    return ctx, client, slice_metas, temps


def test_compose_slices_deletes_every_temp_with_an_explicit_generation():
    ctx, client, slice_metas, temps = _build_fixture()

    result = compose_slices(
        ctx, OBJECT_NAME, slice_metas, EXPECTED_CRC, TOTAL_SIZE,
        precondition_generation=None,
    )

    assert result.state == "verified"

    # The totalizing sweep must list with versions=True under the temp
    # prefix (Item 1) -- not just iterate the generations slice_metas held.
    assert client.list_calls == [(TEMP_PREFIX, True)]

    # ...and every temp it found must be deleted by its EXACT generation.
    # `None` here would silently downgrade to a live-pointer delete, which on
    # a versioning-enabled bucket archives the temp as a billable noncurrent
    # version instead of removing it -- the double-billing defect this pins.
    # This assertion goes red under EITHER of the two independent mutations
    # a reviewer found the prior test suite blind to: compose_slices itself
    # dropping the `generation=` kwarg on its delete call, or delete_object()
    # silently discarding the kwarg before it reaches delete_blob().
    assert sorted(client.delete_calls) == sorted((t.name, t.generation) for t in temps)
    assert all(generation is not None for _, generation in client.delete_calls)

    # compose() must also pin each source to the generation Layer 2 verified
    # (Item 2), not a fresh live-pointer Blob that would read whatever is
    # live at compose time.
    assert len(client.compose_calls) == 1
    assert sorted(client.compose_calls[0]["sources"]) == sorted(
        (t.name, t.generation) for t in temps
    )
