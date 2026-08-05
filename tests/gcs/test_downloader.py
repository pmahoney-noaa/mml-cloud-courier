import pytest

from mml_cloud_transfer.core.errors import ErrorCategory, classify
from mml_cloud_transfer.core.hashing import hash_file
from mml_cloud_transfer.gcs.client import make_context
from mml_cloud_transfer.gcs.downloader import (
    DownloadResult,
    download_file,
    plan_ranges,
)

RANGE = 256 * 1024


@pytest.fixture
def ctx(emulator, emulator_client):
    _, bucket_name = emulator_client
    return make_context(bucket_name, emulator_endpoint=emulator.endpoint)


@pytest.fixture
def remote(ctx):
    payload = bytes(range(256)) * 4096  # 1 MiB
    ctx.client.bucket(ctx.bucket).blob("d/big.bin").upload_from_string(payload)
    return payload


def test_plan_ranges_covers_everything_without_a_component_cap():
    ranges = plan_ranges(10 * RANGE + 5, range_bytes=RANGE)
    assert len(ranges) == 11
    assert sum(r.length for r in ranges) == 10 * RANGE + 5
    assert ranges[0].offset == 0
    for a, b in zip(ranges, ranges[1:]):
        assert b.offset == a.offset + a.length


@pytest.mark.emulator
def test_downloads_verifies_and_renames_atomically(ctx, remote, tmp_path):
    dest = tmp_path / "out" / "big.bin"
    result = download_file(
        ctx, "d/big.bin", str(dest), range_bytes=RANGE
    )
    assert isinstance(result, DownloadResult)
    assert result.state == "verified"
    assert result.bytes_received == len(remote)
    assert dest.read_bytes() == remote
    assert not (tmp_path / "out" / "big.bin.part").exists()
    assert hash_file(dest).crc32c == result.remote_crc32c


@pytest.mark.emulator
def test_progress_reports_completed_range_crcs(ctx, remote, tmp_path):
    events = []
    download_file(
        ctx, "d/big.bin", str(tmp_path / "p.bin"), range_bytes=RANGE,
        on_progress=lambda idx, done, crc: events.append((idx, done, crc)),
    )
    finished = {idx for idx, _, crc in events if crc is not None}
    assert finished == {0, 1, 2, 3}


class DyingGetSession:
    """Delegates to the real session but fails after N successful GETs."""

    def __init__(self, real, live_gets):
        self.real = real
        self.remaining = live_gets

    def get(self, *args, **kwargs):
        if self.remaining == 0:
            raise ConnectionResetError("injected mid-download failure")
        self.remaining -= 1
        return self.real.get(*args, **kwargs)

    def put(self, *args, **kwargs):
        return self.real.put(*args, **kwargs)


@pytest.mark.emulator
def test_completed_ranges_are_not_refetched_on_resume(ctx, remote, tmp_path):
    dest = tmp_path / "r.bin"
    first_events = []
    dying_ctx = type(ctx)(
        client=ctx.client, session=DyingGetSession(ctx.session, 2),
        endpoint=ctx.endpoint, bucket=ctx.bucket,
    )
    with pytest.raises(ConnectionResetError):
        download_file(
            dying_ctx, "d/big.bin", str(dest), range_bytes=RANGE, max_workers=1,
            on_progress=lambda idx, done, crc: first_events.append((idx, crc)),
        )
    states = {idx: crc for idx, crc in first_events if crc is not None}
    assert len(states) == 2, "two ranges must have completed before the crash"
    assert (tmp_path / "r.bin.part").exists(), "part file must survive a crash"

    second_events = []
    result = download_file(
        ctx, "d/big.bin", str(dest), range_bytes=RANGE,
        range_states=states, max_workers=1,
        on_progress=lambda idx, done, crc: second_events.append(idx),
    )
    assert result.state == "verified"
    assert set(second_events) == {2, 3}
    assert result.bytes_received == 2 * RANGE
    assert dest.read_bytes() == remote


@pytest.mark.emulator
def test_stale_range_states_without_a_part_file_are_ignored(ctx, remote, tmp_path):
    # Claimed CRCs without their bytes must NOT be honored: with no .part
    # file, everything is re-fetched and the result is still correct.
    dest = tmp_path / "s2.bin"
    events_a = []
    download_file(ctx, "d/big.bin", str(dest), range_bytes=RANGE,
                  on_progress=lambda i, d, c: events_a.append((i, c)))
    states = {i: c for i, c in events_a if c is not None}
    dest.unlink()  # both dest and part are now gone

    fetched = []
    result = download_file(ctx, "d/big.bin", str(dest), range_bytes=RANGE,
                           range_states=states,
                           on_progress=lambda i, d, c: fetched.append(i))
    assert result.state == "verified"
    assert set(fetched) == {0, 1, 2, 3}          # everything re-fetched
    assert result.bytes_received == len(remote)
    assert dest.read_bytes() == remote


@pytest.mark.emulator
def test_matching_local_file_is_skipped(ctx, remote, tmp_path):
    dest = tmp_path / "s.bin"
    download_file(ctx, "d/big.bin", str(dest), range_bytes=RANGE)
    again = download_file(ctx, "d/big.bin", str(dest), range_bytes=RANGE)
    assert again.state == "skipped"
    assert again.bytes_received == 0


@pytest.mark.emulator
def test_missing_object_classifies_not_found(ctx, tmp_path):
    with pytest.raises(Exception) as excinfo:
        download_file(ctx, "d/absent.bin", str(tmp_path / "x.bin"))
    assert classify(excinfo.value).category is ErrorCategory.NOT_FOUND


@pytest.mark.emulator
def test_zero_byte_object_downloads_cleanly(ctx, tmp_path):
    ctx.client.bucket(ctx.bucket).blob("d/empty.bin").upload_from_string(b"")
    dest = tmp_path / "empty.bin"
    result = download_file(ctx, "d/empty.bin", str(dest))
    assert result.state == "verified"
    assert dest.read_bytes() == b""
