"""Cooperative stop reaches into the chunk loops, so stop latency is one
chunk, not one file. The upload test runs against the emulator; the
download test drives _fetch_range with an in-memory fake session."""

import os
from types import SimpleNamespace

import pytest

from mml_cloud_courier.core.errors import TransferStopped
from mml_cloud_courier.core.slicing import SliceSpec
from mml_cloud_courier.gcs.client import make_context
from mml_cloud_courier.gcs.downloader import _fetch_range
from mml_cloud_courier.gcs.objects import get_meta
from mml_cloud_courier.gcs.uploader import upload_resumable

_MIB = 1024 * 1024


@pytest.mark.emulator
def test_should_stop_aborts_between_upload_chunks(emulator, emulator_client, tmp_path):
    _, bucket = emulator_client
    ctx = make_context(bucket, emulator_endpoint=emulator.endpoint)
    src = tmp_path / "big.bin"
    src.write_bytes(os.urandom(600 * 1024))

    seen = []  # (session_uri, committed) pairs; initiate + one chunk = 2 entries

    with pytest.raises(TransferStopped):
        upload_resumable(
            ctx, str(src), "stop/big.bin", src.stat().st_size,
            precondition_generation=0, chunk_size=256 * 1024,
            on_progress=lambda uri, committed: seen.append((uri, committed)),
            should_stop=lambda: len(seen) >= 2,
        )
    assert len(seen) == 2                       # stopped before the second chunk
    assert get_meta(ctx, "stop/big.bin") is None  # never finalized


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


def test_fetch_range_stops_between_chunks(tmp_path):
    total = 4 * _MIB
    part = tmp_path / "f.part"
    part.write_bytes(b"\0" * total)
    ctx = SimpleNamespace(session=SimpleNamespace(
        get=lambda url, headers=None, stream=False: _Response(total)
    ))
    chunks_written = {"n": 0}

    def should_stop():
        return chunks_written["n"] >= 1

    def counting_progress(idx, done, crc):
        chunks_written["n"] = done // _MIB

    with pytest.raises(TransferStopped):
        _fetch_range(
            ctx, "http://x", part,
            SliceSpec(index=0, offset=0, length=total),
            counting_progress,
            should_stop=should_stop,
            progress_interval_bytes=_MIB,
        )
