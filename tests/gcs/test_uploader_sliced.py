import pytest

from mml_cloud_courier.core.hashing import hash_file
from mml_cloud_courier.core.slicing import SizePolicy, plan_slices
from mml_cloud_courier.gcs.client import make_context
from mml_cloud_courier.gcs.objects import get_meta, list_prefix
from mml_cloud_courier.gcs.uploader import (
    slice_temp_name,
    upload_slice,
    upload_sliced,
)

CHUNK = 256 * 1024
TINY = SizePolicy(
    single_shot_max=64 * 1024,
    resumable_max=256 * 1024,
    min_slice=256 * 1024,
    max_components=32,
)


@pytest.fixture
def ctx(emulator, emulator_client):
    _, bucket_name = emulator_client
    return make_context(bucket_name, emulator_endpoint=emulator.endpoint)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "large.bin"
    path.write_bytes(bytes(range(256)) * 4096)  # 1 MiB -> 4 slices under TINY
    return path


def test_slice_temp_name_is_stable_and_ordered():
    assert slice_temp_name("archive/big.bin", 0) == "archive/big.bin.mmlct.tmp/0000"
    assert slice_temp_name("archive/big.bin", 31) == "archive/big.bin.mmlct.tmp/0031"


@pytest.mark.emulator
def test_sliced_upload_composes_and_verifies(ctx, source):
    result = upload_sliced(
        ctx, str(source), "s/large.bin", 1024 * 1024,
        precondition_generation=0, policy=TINY, chunk_size=CHUNK,
    )
    assert result.state == "verified"
    assert result.local_crc32c == hash_file(source).crc32c
    meta = get_meta(ctx, "s/large.bin")
    assert meta.size == 1024 * 1024
    assert meta.crc32c == result.remote_crc32c


@pytest.mark.emulator
def test_temp_objects_are_deleted_after_compose(ctx, source):
    upload_sliced(
        ctx, str(source), "s/clean.bin", 1024 * 1024,
        precondition_generation=0, policy=TINY, chunk_size=CHUNK,
    )
    leftovers = list(list_prefix(ctx, "s/clean.bin.mmlct.tmp/"))
    assert leftovers == []


@pytest.mark.emulator
def test_progress_reports_slice_uris_and_crcs(ctx, source):
    events = []
    upload_sliced(
        ctx, str(source), "s/progress.bin", 1024 * 1024,
        precondition_generation=0, policy=TINY, chunk_size=CHUNK,
        on_progress=lambda idx, uri, committed, crc: events.append((idx, uri, committed, crc)),
    )
    finished = {idx for idx, _, _, crc in events if crc is not None}
    assert finished == {0, 1, 2, 3}


@pytest.mark.emulator
def test_completed_slices_are_not_reuploaded_on_resume(ctx, source):
    # First: upload two of the four slices by driving upload_slice directly,
    # recording their CRCs — simulating a run that died halfway.
    slices = plan_slices(1024 * 1024, policy=TINY)
    states: dict[int, tuple[str | None, int | None]] = {}
    for spec in slices[:2]:
        crc, meta = upload_slice(
            ctx, str(source), "s/resume.bin", spec, chunk_size=CHUNK
        )
        states[spec.index] = (None, crc)

    events = []
    result = upload_sliced(
        ctx, str(source), "s/resume.bin", 1024 * 1024,
        precondition_generation=0, policy=TINY, chunk_size=CHUNK,
        slice_states=states,
        on_progress=lambda idx, uri, committed, crc: events.append(idx),
    )
    assert result.state == "verified"
    # Slices 0 and 1 were reused: no upload progress events for them.
    assert set(events) <= {2, 3}


@pytest.mark.emulator
def test_skip_rule_applies_before_any_slicing(ctx, source):
    first = upload_sliced(
        ctx, str(source), "s/skip.bin", 1024 * 1024,
        precondition_generation=0, policy=TINY, chunk_size=CHUNK,
    )
    again = upload_sliced(
        ctx, str(source), "s/skip.bin", 1024 * 1024,
        precondition_generation=first.generation, policy=TINY, chunk_size=CHUNK,
    )
    assert again.state == "skipped"
    assert again.bytes_sent == 0


@pytest.mark.emulator
def test_sliced_sha256_is_computed_and_stamped(ctx, source):
    import hashlib

    result = upload_sliced(
        ctx, str(source), "s/sha.bin", 1024 * 1024,
        precondition_generation=0, policy=TINY, chunk_size=CHUNK, with_sha256=True,
    )
    assert result.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    stamped = ctx.client.bucket(ctx.bucket).get_blob("s/sha.bin")
    assert stamped.metadata == {"mmlct-sha256": result.sha256}


@pytest.mark.emulator
def test_partial_slice_resumes_from_its_session_uri(ctx, source):
    from tests.gcs.test_uploader_resumable import DyingSession, StatusQueryShim

    # 1 MiB -> 2 slices of 512 KiB; chunk 256 KiB -> 2 chunks per slice.
    policy = SizePolicy(
        single_shot_max=64 * 1024, resumable_max=128 * 1024,
        min_slice=512 * 1024, max_components=32,
    )
    events = []
    dying = DyingSession(ctx.session, live_puts=1)  # chunk 1 commits, chunk 2 dies
    broken = type(ctx)(
        client=ctx.client, session=dying, endpoint=ctx.endpoint, bucket=ctx.bucket
    )
    with pytest.raises(ConnectionResetError):
        upload_sliced(
            broken, str(source), "s/partial.bin", 1024 * 1024,
            precondition_generation=0, policy=policy, chunk_size=CHUNK,
            max_workers=1,
            on_progress=lambda i, u, c, r: events.append((i, u, c, r)),
        )
    partial = [(i, u, c) for i, u, c, r in events if u and r is None and 0 < c < 512 * 1024]
    assert partial, "expected a slice with committed bytes and no crc"
    idx, uri, committed = partial[-1]

    resumed = type(ctx)(
        client=ctx.client, session=StatusQueryShim(ctx.session, committed),
        endpoint=ctx.endpoint, bucket=ctx.bucket,
    )
    result = upload_sliced(
        resumed, str(source), "s/partial.bin", 1024 * 1024,
        precondition_generation=0, policy=policy, chunk_size=CHUNK, max_workers=1,
        slice_states={idx: (uri, None)},
    )
    assert result.state == "verified"
    assert result.local_crc32c == hash_file(source).crc32c
