import pytest

from mml_cloud_courier.core.errors import ErrorCategory, classify
from mml_cloud_courier.core.hashing import hash_file
from mml_cloud_courier.gcs.client import make_context
from mml_cloud_courier.gcs.objects import get_meta
from mml_cloud_courier.gcs.uploader import (
    ChecksumMismatch,
    UploadResult,
    should_skip,
    upload_single_shot,
    verify_layer2,
)


@pytest.fixture
def ctx(emulator, emulator_client):
    _, bucket_name = emulator_client
    return make_context(bucket_name, emulator_endpoint=emulator.endpoint)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "small.bin"
    path.write_bytes(b"payload " * 512)  # 4096 bytes
    return path


@pytest.mark.emulator
def test_upload_verifies_and_reports_generation(ctx, source):
    result = upload_single_shot(
        ctx, str(source), "archive/small.bin", precondition_generation=0
    )
    assert isinstance(result, UploadResult)
    assert result.state == "verified"
    assert result.bytes_sent == 4096
    assert result.local_crc32c == result.remote_crc32c == hash_file(source).crc32c
    meta = get_meta(ctx, "archive/small.bin")
    assert meta.generation == result.generation


@pytest.mark.emulator
def test_sha256_is_computed_only_when_asked(ctx, source):
    import hashlib

    without = upload_single_shot(
        ctx, str(source), "a.bin", precondition_generation=0
    )
    with_hash = upload_single_shot(
        ctx, str(source), "b.bin", precondition_generation=0, with_sha256=True
    )
    assert without.sha256 is None
    assert with_hash.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    # The audit hash travels with the object (spec: custom metadata).
    stamped = ctx.client.bucket(ctx.bucket).get_blob("b.bin")
    assert stamped.metadata == {"mmlcc-sha256": with_hash.sha256}
    plain = ctx.client.bucket(ctx.bucket).get_blob("a.bin")
    assert not plain.metadata


@pytest.mark.emulator
def test_matching_destination_is_skipped_without_sending_bytes(ctx, source):
    first = upload_single_shot(ctx, str(source), "c.bin", precondition_generation=0)
    again = upload_single_shot(
        ctx, str(source), "c.bin", precondition_generation=first.generation
    )
    assert again.state == "skipped"
    assert again.bytes_sent == 0
    assert again.generation == first.generation


@pytest.mark.emulator
def test_changed_destination_is_overwritten_under_its_generation(ctx, source, tmp_path):
    other = tmp_path / "other.bin"
    other.write_bytes(b"different content")
    first = upload_single_shot(ctx, str(other), "d.bin", precondition_generation=0)

    replaced = upload_single_shot(
        ctx, str(source), "d.bin", precondition_generation=first.generation
    )
    assert replaced.state == "verified"
    assert replaced.generation != first.generation


@pytest.mark.emulator
def test_stale_precondition_raises_conflict(ctx, source, tmp_path):
    other = tmp_path / "other.bin"
    other.write_bytes(b"different content")
    upload_single_shot(ctx, str(other), "e.bin", precondition_generation=0)

    with pytest.raises(Exception) as excinfo:
        upload_single_shot(ctx, str(source), "e.bin", precondition_generation=0)
    assert classify(excinfo.value).category is ErrorCategory.CONFLICT


def test_should_skip_needs_size_and_crc_to_match():
    from mml_cloud_courier.gcs.objects import ObjectMeta

    meta = ObjectMeta(name="x", size=10, crc32c=42, generation=1)
    assert should_skip(meta, size=10, local_crc32c=42)
    assert not should_skip(meta, size=11, local_crc32c=42)
    assert not should_skip(meta, size=10, local_crc32c=43)
    assert not should_skip(None, size=10, local_crc32c=42)


def test_verify_layer2_raises_on_any_mismatch():
    from mml_cloud_courier.gcs.objects import ObjectMeta

    good = ObjectMeta(name="x", size=10, crc32c=42, generation=1)
    verify_layer2(good, size=10, local_crc32c=42)  # no raise
    with pytest.raises(ChecksumMismatch):
        verify_layer2(good, size=10, local_crc32c=41)
    with pytest.raises(ChecksumMismatch):
        verify_layer2(good, size=9, local_crc32c=42)
