import json

import pytest

from mml_cloud_transfer.gcs.objects import GcsHttpError
from mml_cloud_transfer.gcs.resumable import (
    PutResult,
    SessionExpired,
    put_chunk,
    query_offset,
)


class StubResponse:
    def __init__(self, status_code, headers=None, body=""):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = body if isinstance(body, str) else ""
        self._body = body

    def json(self):
        return json.loads(self._body)


class StubSession:
    """Scripted session: pops one canned response per put() call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def put(self, url, data=b"", headers=None):
        self.calls.append({"url": url, "data": data, "headers": headers or {}})
        return self.responses.pop(0)


FINALIZE_BODY = json.dumps(
    {"name": "a.bin", "size": "1000", "crc32c": "AAAAAA==", "generation": "77"}
)


def test_308_with_range_reports_the_committed_prefix():
    session = StubSession([StubResponse(308, {"Range": "bytes=0-524287"})])
    result = put_chunk(session, "http://s/u", b"x" * 262144, start=262144, total=1000000)
    assert result == PutResult(committed=524288, finalized=None)
    sent = session.calls[0]
    assert sent["headers"]["Content-Range"] == "bytes 262144-524287/1000000"


def test_308_without_range_means_nothing_committed():
    session = StubSession([StubResponse(308)])
    result = put_chunk(session, "http://s/u", b"x" * 262144, start=0, total=1000000)
    assert result.committed == 0


def test_finalize_parses_the_object_json():
    session = StubSession([StubResponse(200, body=FINALIZE_BODY)])
    result = put_chunk(session, "http://s/u", b"x" * 1000, start=0, total=1000)
    assert result.finalized is not None
    assert result.finalized.name == "a.bin"
    assert result.finalized.size == 1000
    assert result.finalized.crc32c == 0
    assert result.finalized.generation == 77
    assert result.committed == 1000


def test_query_offset_sends_the_star_content_range():
    session = StubSession([StubResponse(308, {"Range": "bytes=0-999"})])
    result = query_offset(session, "http://s/u", total=5000)
    assert result.committed == 1000
    sent = session.calls[0]
    assert sent["headers"]["Content-Range"] == "bytes */5000"
    assert sent["data"] == b""


def test_query_offset_detects_an_already_finalized_upload():
    session = StubSession([StubResponse(200, body=FINALIZE_BODY)])
    result = query_offset(session, "http://s/u", total=1000)
    assert result.finalized is not None


def test_dead_session_raises_session_expired():
    for code in (404, 410):
        session = StubSession([StubResponse(code)])
        with pytest.raises(SessionExpired):
            put_chunk(session, "http://s/u", b"x" * 10, start=0, total=10)


def test_server_errors_surface_as_gcs_http_error():
    session = StubSession([StubResponse(503, body="try later")])
    with pytest.raises(GcsHttpError) as excinfo:
        put_chunk(session, "http://s/u", b"x" * 10, start=0, total=10)
    assert excinfo.value.code == 503


def test_non_final_chunks_must_be_256kib_aligned():
    session = StubSession([])
    with pytest.raises(ValueError, match="256 KiB"):
        put_chunk(session, "http://s/u", b"x" * 1000, start=0, total=10_000_000)
