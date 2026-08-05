"""Release gate: the four behaviours fake-gcs-server cannot vouch for.

Small and fast -- a few megabytes, under a minute. Run these before the
2.6 GiB scale test; when one of them is red, the scale test cannot succeed
and would only cost time and bytes proving it.
"""

import random

import pytest

from mml_cloud_transfer.core.crc32c_combine import combine_all
from mml_cloud_transfer.core.errors import ErrorCategory, classify
from mml_cloud_transfer.core.hashing import crc32c_from_base64, hash_file
from mml_cloud_transfer.core.slicing import SizePolicy, plan_slices
from mml_cloud_transfer.gcs.objects import delete_object, get_meta, list_prefix
from mml_cloud_transfer.gcs.resumable import (
    initiate_upload,
    put_chunk,
    query_offset,
)
from mml_cloud_transfer.gcs.uploader import (
    compose_slices,
    slice_temp_name,
    upload_resumable,
    upload_single_shot,
    upload_slice,
)

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


#: 3 MiB under this policy -> exactly three 1 MiB components.
COMPOSE_POLICY = SizePolicy(
    single_shot_max=64 * 1024,
    resumable_max=1024 * 1024,
    min_slice=1024 * 1024,
    max_components=32,
)
THREE_MIB = 3 * 1024 * 1024


@pytest.fixture
def composable(tmp_path):
    path = tmp_path / "composable.bin"
    path.write_bytes(blocks(12, seed=2))  # 3 MiB of distinct 256 KiB blocks
    return path


@pytest.mark.real_bucket
def test_compose_preserves_slice_order(real_bucket_ctx, composable):
    ctx, run_prefix = real_bucket_ctx
    name = f"{run_prefix}composed.bin"
    reversed_name = f"{run_prefix}composed-reversed.bin"

    specs = plan_slices(THREE_MIB, policy=COMPOSE_POLICY)
    assert len(specs) == 3, "the policy must produce three components"

    crcs = []
    metas = []
    for spec in specs:
        crc, meta = upload_slice(ctx, str(composable), name, spec, chunk_size=CHUNK)
        crcs.append(crc)
        metas.append(meta)

    combined = combine_all([(c, s.length) for c, s in zip(crcs, specs)])
    assert combined == hash_file(composable).crc32c, (
        "crc32c_combine over the slices must equal a straight whole-file hash"
    )

    bucket = ctx.client.bucket(ctx.bucket)
    try:
        # Compose the SAME components in the wrong order. If this produced the
        # same CRC, the correct-order assertion below would be vacuous and
        # Layer 2 could not detect a mis-stitched object at all.
        wrong = bucket.blob(reversed_name)
        wrong.compose([bucket.blob(m.name) for m in reversed(metas)])
        wrong.reload()
        assert crc32c_from_base64(wrong.crc32c) != combined, (
            "reversed compose produced the expected CRC — Layer 2 cannot detect order"
        )
    finally:
        delete_object(ctx, reversed_name)

    result = compose_slices(
        ctx, name, metas, combined, THREE_MIB, precondition_generation=0
    )
    assert result.state == "verified"
    assert result.remote_crc32c == combined

    leftovers = [m.name for m in list_prefix(ctx, f"{name}.mmlct.tmp/")]
    assert leftovers == [], f"compose left temp objects behind: {leftovers}"
    assert slice_temp_name(name, 0) == f"{name}.mmlct.tmp/0000"


@pytest.mark.real_bucket
def test_stale_precondition_is_a_conflict_on_real_gcs(real_bucket_ctx, tmp_path):
    ctx, run_prefix = real_bucket_ctx
    name = f"{run_prefix}precondition.bin"

    first = tmp_path / "first.bin"
    first.write_bytes(b"the original content")
    second = tmp_path / "second.bin"
    # Different content, or the skip rule fires before any precondition does.
    second.write_bytes(b"entirely different content")

    created = upload_single_shot(ctx, str(first), name, precondition_generation=0)
    assert created.state == "verified"

    # precondition_generation=0 means "this object must not exist". It does.
    with pytest.raises(Exception) as excinfo:
        upload_single_shot(ctx, str(second), name, precondition_generation=0)
    assert classify(excinfo.value).category is ErrorCategory.CONFLICT

    # The rejected write must not have replaced anything.
    meta = get_meta(ctx, name)
    assert meta is not None
    assert meta.generation == created.generation

    # Under the correct generation the same write succeeds.
    replaced = upload_single_shot(
        ctx, str(second), name, precondition_generation=created.generation
    )
    assert replaced.state == "verified"
    assert replaced.generation != created.generation
