"""Release gate: the four behaviours fake-gcs-server cannot vouch for.

Small and fast -- a few megabytes, under a minute. Run these before the
2.6 GiB scale test; when one of them is red, the scale test cannot succeed
and would only cost time and bytes proving it.
"""

import random

import pytest

from mml_cloud_transfer.core.hashing import hash_file
from mml_cloud_transfer.gcs.objects import get_meta
from mml_cloud_transfer.gcs.resumable import (
    initiate_upload,
    put_chunk,
    query_offset,
)
from mml_cloud_transfer.gcs.uploader import upload_resumable

CHUNK = 256 * 1024
TOTAL = 1024 * 1024


def blocks(count: int, seed: int) -> bytes:
    """`count` distinct 256 KiB blocks: block N starts with N, big-endian.

    Distinct blocks matter. If every block were identical, a compose that
    stitched slices in the wrong order would produce a byte-identical object
    and the order test below would pass while proving nothing.
    """
    template = random.Random(seed).randbytes(CHUNK)
    return b"".join(n.to_bytes(16, "big") + template[16:] for n in range(count))


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "session.bin"
    path.write_bytes(blocks(4, seed=1))  # 1 MiB
    return path


@pytest.mark.real_bucket
def test_status_query_returns_the_servers_committed_offset(real_bucket_ctx, source):
    """The StatusQueryShim killer.

    fake-gcs-server answers the 'bytes */total' probe with 200 and a
    truncated object. Real GCS must answer 308 with the committed Range.
    Every resume in this codebase depends on that distinction.
    """
    ctx, run_prefix = real_bucket_ctx
    name = f"{run_prefix}status-query.bin"

    uri = initiate_upload(ctx, name, TOTAL)
    assert uri.startswith("http"), uri

    first = put_chunk(ctx.session, uri, source.read_bytes()[:CHUNK], 0, TOTAL)
    assert first.committed == CHUNK
    assert first.finalized is None, "a 256 KiB chunk of a 1 MiB upload must not finalize"

    # The assertion the emulator cannot make: an out-of-band status query
    # reports the server's committed prefix without finalizing anything.
    status = query_offset(ctx.session, uri, TOTAL)
    assert status.finalized is None, (
        "the status probe finalized the upload — resume would silently truncate files"
    )
    assert status.committed == CHUNK

    # And the real resume path completes it, hashing the committed prefix
    # locally rather than re-sending it.
    result = upload_resumable(
        ctx, str(source), name, TOTAL,
        precondition_generation=None, session_uri=uri, chunk_size=CHUNK,
    )
    assert result.state == "verified"
    assert result.bytes_sent == TOTAL - CHUNK, "the committed prefix must not be re-sent"

    meta = get_meta(ctx, name)
    assert meta is not None
    assert meta.size == TOTAL
    assert meta.crc32c == hash_file(source).crc32c
