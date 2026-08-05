import pytest

from mml_cloud_transfer.core.hashing import hash_file
from mml_cloud_transfer.gcs.client import make_context
from mml_cloud_transfer.gcs.objects import get_meta
from mml_cloud_transfer.gcs.uploader import upload_resumable

CHUNK = 256 * 1024


@pytest.fixture
def ctx(emulator, emulator_client):
    _, bucket_name = emulator_client
    return make_context(bucket_name, emulator_endpoint=emulator.endpoint)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "medium.bin"
    path.write_bytes(bytes(range(256)) * 4096)  # 1 MiB, non-uniform content
    return path


class DyingSession:
    """Delegates to a real session but raises after N successful puts."""

    def __init__(self, real, live_puts):
        self.real = real
        self.remaining = live_puts

    def put(self, *args, **kwargs):
        if self.remaining == 0:
            raise ConnectionResetError("injected mid-upload failure")
        self.remaining -= 1
        return self.real.put(*args, **kwargs)


@pytest.mark.emulator
def test_uploads_and_verifies_in_one_pass(ctx, source):
    events = []
    result = upload_resumable(
        ctx, str(source), "r/medium.bin", 1024 * 1024,
        precondition_generation=0, chunk_size=CHUNK,
        on_progress=lambda uri, committed: events.append((uri, committed)),
    )
    assert result.state == "verified"
    assert result.bytes_sent == 1024 * 1024
    assert result.local_crc32c == hash_file(source).crc32c
    assert get_meta(ctx, "r/medium.bin").generation == result.generation
    # Progress was reported with a stable session URI and growing offsets.
    uris = {uri for uri, _ in events}
    assert len(uris) == 1
    offsets = [c for _, c in events]
    assert offsets == sorted(offsets)
    assert offsets[-1] == 1024 * 1024


@pytest.mark.emulator
def test_resumes_from_the_committed_offset_after_a_crash(ctx, source):
    recorded = []
    dying = DyingSession(ctx.session, live_puts=2)
    broken_ctx = type(ctx)(
        client=ctx.client, session=dying, endpoint=ctx.endpoint, bucket=ctx.bucket
    )
    with pytest.raises(ConnectionResetError):
        upload_resumable(
            broken_ctx, str(source), "r/resumed.bin", 1024 * 1024,
            precondition_generation=0, chunk_size=CHUNK,
            on_progress=lambda uri, committed: recorded.append((uri, committed)),
        )
    assert recorded, "progress must have been reported before the crash"
    uri, committed = recorded[-1]
    assert 0 < committed < 1024 * 1024

    result = upload_resumable(
        ctx, str(source), "r/resumed.bin", 1024 * 1024,
        precondition_generation=0, session_uri=uri, chunk_size=CHUNK,
    )
    assert result.state == "verified"
    assert result.local_crc32c == hash_file(source).crc32c
    # Only the remainder was re-sent.
    assert result.bytes_sent == 1024 * 1024 - committed


@pytest.mark.emulator
def test_matching_destination_is_skipped(ctx, source):
    first = upload_resumable(
        ctx, str(source), "r/skip.bin", 1024 * 1024,
        precondition_generation=0, chunk_size=CHUNK,
    )
    again = upload_resumable(
        ctx, str(source), "r/skip.bin", 1024 * 1024,
        precondition_generation=first.generation, chunk_size=CHUNK,
    )
    assert again.state == "skipped"
    assert again.bytes_sent == 0


@pytest.mark.emulator
def test_sha256_survives_a_resume(ctx, source):
    import hashlib

    recorded = []
    dying = DyingSession(ctx.session, live_puts=2)
    broken_ctx = type(ctx)(
        client=ctx.client, session=dying, endpoint=ctx.endpoint, bucket=ctx.bucket
    )
    with pytest.raises(ConnectionResetError):
        upload_resumable(
            broken_ctx, str(source), "r/sha.bin", 1024 * 1024,
            precondition_generation=0, chunk_size=CHUNK, with_sha256=True,
            on_progress=lambda uri, committed: recorded.append((uri, committed)),
        )
    uri, _ = recorded[-1]
    result = upload_resumable(
        ctx, str(source), "r/sha.bin", 1024 * 1024,
        precondition_generation=0, session_uri=uri, chunk_size=CHUNK, with_sha256=True,
    )
    assert result.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    stamped = ctx.client.bucket(ctx.bucket).get_blob("r/sha.bin")
    assert stamped.metadata == {"mmlct-sha256": result.sha256}


def test_chunk_size_must_be_aligned(tmp_path):
    path = tmp_path / "x.bin"
    path.write_bytes(b"x")
    with pytest.raises(ValueError, match="256 KiB"):
        upload_resumable(
            None, str(path), "x", 1,
            precondition_generation=0, chunk_size=100_000,
        )
