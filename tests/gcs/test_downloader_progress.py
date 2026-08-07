"""Mid-range progress callbacks are throttled: each one opens a SQLite
connection in the runner, so the cadence must amortize that cost over
many MiB. The final callback (carrying the range CRC) always fires."""

from types import SimpleNamespace

from mml_cloud_courier.core.slicing import SliceSpec
from mml_cloud_courier.gcs.downloader import _fetch_range

_MIB = 1024 * 1024


class _Response:
    status_code = 200
    headers: dict = {}
    text = ""

    def __init__(self, total: int):
        self._total = total

    def iter_content(self, chunk_size):
        sent = 0
        while sent < self._total:
            n = min(chunk_size, self._total - sent)
            yield b"\0" * n
            sent += n

    def json(self):
        return {}


def _ctx(total):
    session = SimpleNamespace(
        get=lambda url, headers=None, stream=False: _Response(total)
    )
    return SimpleNamespace(session=session)


def test_mid_range_progress_is_throttled(tmp_path):
    total = 8 * _MIB
    part = tmp_path / "f.part"
    part.write_bytes(b"\0" * total)
    calls = []
    _fetch_range(
        _ctx(total), "http://x", part,
        SliceSpec(index=0, offset=0, length=total),
        lambda idx, done, crc: calls.append((done, crc)),
        progress_interval_bytes=4 * _MIB,
    )
    mid = [done for done, crc in calls if crc is None]
    finals = [(done, crc) for done, crc in calls if crc is not None]
    assert mid == [4 * _MIB, 8 * _MIB]     # not one call per MiB
    assert len(finals) == 1 and finals[0][0] == total


def test_small_ranges_only_fire_the_final_callback(tmp_path):
    total = 2 * _MIB
    part = tmp_path / "f.part"
    part.write_bytes(b"\0" * total)
    calls = []
    _fetch_range(
        _ctx(total), "http://x", part,
        SliceSpec(index=0, offset=0, length=total),
        lambda idx, done, crc: calls.append((done, crc)),
        progress_interval_bytes=32 * _MIB,
    )
    assert len(calls) == 1 and calls[0][1] is not None
