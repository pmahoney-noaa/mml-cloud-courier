import sys

import google.auth.exceptions
import pytest
import requests.exceptions
import urllib3.exceptions

from mml_cloud_courier.core.errors import Classification, ErrorCategory, classify, describe


class FakeApiError(Exception):
    """Stands in for google.api_core.exceptions, which exposes .code as an int."""

    def __init__(self, code: int, message: str = "api error"):
        super().__init__(message)
        self.code = code


class DataCorruption(Exception):
    """Same class name the storage client raises on a checksum mismatch."""


def test_classification_is_returned_for_every_exception():
    assert isinstance(classify(RuntimeError("boom")), Classification)


def test_unknown_errors_are_not_transient():
    result = classify(RuntimeError("boom"))
    assert result.category is ErrorCategory.UNKNOWN
    assert result.transient is False
    assert result.pauses_job is False


def test_permission_error_maps_to_permission_denied():
    assert classify(PermissionError(13, "denied")).category is ErrorCategory.PERMISSION_DENIED


def test_windows_sharing_violation_maps_to_file_locked():
    exc = OSError(13, "in use")
    exc.winerror = 32  # ERROR_SHARING_VIOLATION
    result = classify(exc)
    assert result.category is ErrorCategory.FILE_LOCKED
    assert result.transient is True


@pytest.mark.skipif(sys.platform != "win32",
                    reason="sharing violations are a Windows concept")
def test_a_real_lock_through_io_open_classifies_as_file_locked(tmp_path):
    """CPython's open() collapses ERROR_SHARING_VIOLATION into plain EACCES
    (winerror=None), so the winerror check alone never fires in production —
    Phase 5 gate finding. classify must re-probe via Win32."""
    import ctypes

    path = tmp_path / "locked.bin"
    path.write_bytes(b"x" * 10)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = ctypes.c_void_p
    handle = kernel32.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)
    assert handle and handle != ctypes.c_void_p(-1).value, "lock setup failed"
    try:
        with pytest.raises(PermissionError) as excinfo:
            with open(path, "rb") as fh:
                fh.read()
        assert excinfo.value.winerror is None   # the premise of the bug
        assert classify(excinfo.value).category is ErrorCategory.FILE_LOCKED
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def test_permission_error_on_an_accessible_file_stays_permission_denied(tmp_path):
    """The re-probe only reclassifies on an affirmative sharing violation:
    a fabricated or stale EACCES naming a perfectly readable file must not
    flip to file_locked."""
    path = tmp_path / "readable.bin"
    path.write_bytes(b"x")
    exc = PermissionError(13, "denied")
    exc.filename = str(path)
    assert classify(exc).category is ErrorCategory.PERMISSION_DENIED


def test_filename_too_long_maps_to_path_too_long():
    exc = OSError(36, "name too long")
    exc.winerror = 206  # ERROR_FILENAME_EXCED_RANGE
    assert classify(exc).category is ErrorCategory.PATH_TOO_LONG


def test_connection_errors_are_transient_network_failures():
    result = classify(ConnectionResetError("reset by peer"))
    assert result.category is ErrorCategory.NETWORK
    assert result.transient is True


def test_timeout_is_a_transient_network_failure():
    assert classify(TimeoutError()).category is ErrorCategory.NETWORK


def test_checksum_mismatch_is_terminal():
    result = classify(DataCorruption("crc mismatch"))
    assert result.category is ErrorCategory.CHECKSUM_MISMATCH
    assert result.transient is False


def test_auth_failures_pause_the_whole_job():
    for code in (401, 403):
        result = classify(FakeApiError(code))
        assert result.category is ErrorCategory.CREDENTIAL
        assert result.pauses_job is True
        assert result.transient is False


def test_rate_limiting_is_transient_quota():
    result = classify(FakeApiError(429))
    assert result.category is ErrorCategory.QUOTA
    assert result.transient is True


def test_server_errors_are_transient():
    for code in (500, 502, 503, 504):
        result = classify(FakeApiError(code))
        assert result.category is ErrorCategory.NETWORK
        assert result.transient is True


def test_not_found_is_terminal_for_one_file_only():
    result = classify(FakeApiError(404))
    assert result.category is ErrorCategory.NOT_FOUND
    assert result.pauses_job is False


def test_precondition_failure_is_a_conflict():
    assert classify(FakeApiError(412)).category is ErrorCategory.CONFLICT


def test_every_classification_carries_user_facing_text():
    for exc in (RuntimeError("x"), PermissionError(), FakeApiError(403), FakeApiError(429)):
        result = classify(exc)
        assert result.message
        assert result.action


# CRITICAL 2 regression: requests/urllib3 transport errors and
# google.auth.exceptions.RefreshError must not fall through to UNKNOWN, or
# every multi-GB raw-session transfer gets zero retry and credential
# failures don't pause the job.


def test_requests_connection_error_is_transient_network():
    result = classify(requests.exceptions.ConnectionError("connection broke"))
    assert result.category is ErrorCategory.NETWORK
    assert result.transient is True


def test_requests_read_timeout_is_transient_network():
    result = classify(requests.exceptions.ReadTimeout("timed out"))
    assert result.category is ErrorCategory.NETWORK
    assert result.transient is True


def test_requests_chunked_encoding_error_is_transient_network():
    result = classify(requests.exceptions.ChunkedEncodingError("truncated"))
    assert result.category is ErrorCategory.NETWORK
    assert result.transient is True


def test_urllib3_protocol_error_is_transient_network():
    result = classify(urllib3.exceptions.ProtocolError("connection aborted"))
    assert result.category is ErrorCategory.NETWORK
    assert result.transient is True


def test_google_auth_refresh_error_pauses_the_job_as_credential():
    result = classify(google.auth.exceptions.RefreshError("token refresh failed"))
    assert result.category is ErrorCategory.CREDENTIAL
    assert result.pauses_job is True


def test_a_400_naming_crc32c_is_a_checksum_mismatch():
    """GCS rejects a write whose declared CRC32C does not match the bytes.

    That is Layer 1 doing its job, and the user must be told the copy was
    corrupted -- not that something unexpected happened.
    """

    class BadRequest(Exception):
        code = 400

    exc = BadRequest(
        "Provided CRC32C hash 'AAAAAA==' doesn't match calculated CRC32C hash 'zzzzzz=='."
    )
    assert classify(exc).category is ErrorCategory.CHECKSUM_MISMATCH


def test_a_400_about_anything_else_stays_unknown():
    """Only checksum 400s are reclassified; a malformed request is not."""

    class BadRequest(Exception):
        code = 400

    assert classify(BadRequest("Invalid argument.")).category is ErrorCategory.UNKNOWN


def test_a_400_naming_md5_is_a_checksum_mismatch():
    """The MD5 wording of the same JSON API rejection must classify too."""

    class BadRequest(Exception):
        code = 400

    exc = BadRequest(
        "Provided MD5 hash 'AAAAAA==' doesn't match calculated MD5 hash 'zzzzzz=='."
    )
    assert classify(exc).category is ErrorCategory.CHECKSUM_MISMATCH


def test_a_400_with_xml_api_digest_wording_is_a_checksum_mismatch():
    """The XML API (InvalidDigest/BadDigest) phrases the rejection differently."""

    class BadRequest(Exception):
        code = 400

    exc = BadRequest("The Content-MD5 you specified did not match what we received.")
    assert classify(exc).category is ErrorCategory.CHECKSUM_MISMATCH


def test_a_400_with_hash_token_in_object_name_but_no_mismatch_phrase_stays_unknown():
    """Regression test: a hash token in the URL/object path is not a mismatch.

    google-api-core error strings lead with the request URL, so an unrelated
    400 against a path like checksums/manifest.txt would also contain
    "checksum" -- that must not be reclassified as a corrupted transfer.
    """

    class BadRequest(Exception):
        code = 400

    exc = BadRequest(
        "400 PATCH https://storage.googleapis.com/storage/v1/b/b/o/2024%2Fchecksums%2Fmanifest.txt: "
        "Invalid argument."
    )
    assert classify(exc).category is ErrorCategory.UNKNOWN


def test_a_400_compose_error_with_ifgenerationmatch_query_string_stays_unknown():
    """Regression test: ifGenerationMatch=0 in the query string is not a mismatch.

    Every precondition-guarded request this product makes carries
    "ifGenerationMatch=" in the query string, which contains the literal
    substring "match". A compose rejection against an object path containing
    "checksums/" must not be reclassified just because the URL happens to
    supply both a hash-shaped token and "match".
    """

    class BadRequest(Exception):
        code = 400

    exc = BadRequest(
        "400 POST https://storage.googleapis.com/storage/v1/b/bucket/o/"
        "surveys%2F2024%2Fchecksums%2Fframes.bin/compose?ifGenerationMatch=0: "
        "The number of source components provided is not valid."
    )
    assert classify(exc).category is ErrorCategory.UNKNOWN


def test_a_400_resumable_init_error_with_ifgenerationmatch_query_string_stays_unknown():
    """Regression test: same failure mode, on the resumable-init endpoint.

    The object name itself ("md5sums.txt") supplies a hash token, and the
    query string supplies "match" via ifGenerationMatch -- neither should
    combine to look like a checksum mismatch on a generic 400.
    """

    class BadRequest(Exception):
        code = 400

    exc = BadRequest(
        "400 POST https://storage.googleapis.com/upload/storage/v1/b/bucket/"
        "o?uploadType=resumable&ifGenerationMatch=0&name=data%2Fmd5sums.txt: "
        "Invalid argument."
    )
    assert classify(exc).category is ErrorCategory.UNKNOWN


def test_a_400_crc32c_mismatch_with_full_url_and_query_string_is_still_caught():
    """The real positive case must still classify once URLs are stripped.

    A genuine checksum rejection also arrives with a full URL and query
    string; stripping the URL to avoid the ifGenerationMatch false positive
    must not also blind the classifier to the mismatch phrase in the body.
    """

    class BadRequest(Exception):
        code = 400

    exc = BadRequest(
        "400 POST https://storage.googleapis.com/upload/storage/v1/b/bucket/"
        "o?uploadType=resumable&upload_id=abc123&name=data%2Fframes.bin: "
        "Provided CRC32C hash '3q2+7w==' doesn't match calculated CRC32C hash 'CNb0NA=='."
    )
    assert classify(exc).category is ErrorCategory.CHECKSUM_MISMATCH


def test_a_400_xml_api_content_md5_wording_with_query_string_is_a_checksum_mismatch():
    """The XML API phrases the same rejection as a Content-MD5 mismatch.

    Distinct from test_a_400_with_xml_api_digest_wording_is_a_checksum_mismatch
    above (which uses a bare message): this one carries a full URL with a
    precondition query string, so it also exercises the URL-stripping fix
    rather than only the body-wording match.
    """

    class BadRequest(Exception):
        code = 400

    exc = BadRequest(
        "400 PUT https://storage.googleapis.com/bucket/data%2Fmd5sums.txt"
        "?ifGenerationMatch=0: "
        "The Content-MD5 you specified did not match what we received."
    )
    assert classify(exc).category is ErrorCategory.CHECKSUM_MISMATCH


def test_google_api_core_retry_error_is_transient_network():
    """Phase 3 gate finding (2026-08-06, job 5): during a network outage the
    client library's single-shot upload path exhausts its internal retries
    and raises google.api_core's RetryError — no .code, not a
    requests/urllib3 type — which fell through to UNKNOWN (non-transient),
    permanently failing the file after one attempt while every raw-session
    path retried correctly. The wrapped cause is transient by definition."""
    from google.api_core.exceptions import RetryError

    cls = classify(RetryError("Timeout of 120.0s exceeded", ConnectionError("dns")))
    assert cls.category is ErrorCategory.NETWORK
    assert cls.transient


# ---- refresh-failure classification (Plan 4 Task 1) -----------------------
#
# Gate lesson (fixes 669bb4b, b1a5d4b): classify() gaps only surface with
# REAL exception types, so these tests drive google-auth's actual refresh
# machinery instead of hand-building exceptions wherever possible.

import json as _json
import socket
import threading
import time as _time
from http.server import BaseHTTPRequestHandler, HTTPServer


def _harvest_refresh_exception(token_uri: str) -> BaseException:
    """Run a real google.oauth2 refresh against token_uri; return what it raises."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials(
        None,
        refresh_token="fake-refresh-token",
        token_uri=token_uri,
        client_id="fake-client",
        client_secret="fake-secret",
    )
    with pytest.raises(Exception) as info:
        creds.refresh(Request())
    return info.value


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]  # freed on close; nothing listens


@pytest.fixture
def invalid_grant_endpoint():
    """A local token endpoint answering exactly like Google does for a
    revoked/expired refresh token: HTTP 400, error=invalid_grant."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = _json.dumps({
                "error": "invalid_grant",
                "error_description": "Token has been expired or revoked.",
            }).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep pytest output clean
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/token"
    server.shutdown()
    thread.join(timeout=5)


def test_a_real_invalid_grant_refresh_is_credential_and_pauses(invalid_grant_endpoint):
    from google.auth.exceptions import TransportError

    # The endpoint is loopback, but one full-suite run still produced a
    # TransportError blip (2026-08-07). This test's subject is RefreshError
    # classification, so retry the harvest and skip if loopback stays down.
    for _attempt in range(3):
        exc = _harvest_refresh_exception(invalid_grant_endpoint)
        if not isinstance(exc, TransportError):
            break
        _time.sleep(0.5)
    else:
        pytest.skip(f"local invalid_grant endpoint unreachable: {exc!r}")
    assert type(exc).__name__ == "RefreshError"  # the real library type
    result = classify(exc)
    assert result.category is ErrorCategory.CREDENTIAL
    assert result.pauses_job is True


def test_a_real_transport_failure_during_refresh_is_network():
    """Network down at refresh time: google-auth raises TransportError
    chained from the requests exception. That is an outage, not a bad
    credential — it must NOT pause the job."""
    exc = _harvest_refresh_exception(f"http://127.0.0.1:{_closed_port()}/token")
    result = classify(exc)
    assert result.category is ErrorCategory.NETWORK
    assert result.transient is True
    assert result.pauses_job is False


def test_a_refresh_error_chained_from_a_transport_error_is_network():
    """Defends against a future google-auth that wraps transport failures
    in RefreshError: the cause chain, not the outer type, decides."""
    from google.auth.exceptions import RefreshError, TransportError
    import requests

    transport = TransportError(requests.exceptions.ConnectionError("boom"))
    transport.__cause__ = requests.exceptions.ConnectionError("boom")
    exc = RefreshError("refresh failed")
    exc.__cause__ = transport
    assert classify(exc).category is ErrorCategory.NETWORK


def test_a_retryable_refresh_error_is_network():
    """google-auth marks token-endpoint 5xx / server_error responses
    retryable=True (google/oauth2/_client.py::_handle_error_response) —
    the library itself says 'try again', so we must not pause the job.
    Constructed directly with the real class and the real constructor
    shape because driving the real path would spin the library's
    multi-second exponential backoff."""
    from google.auth.exceptions import RefreshError

    exc = RefreshError(
        "server_error: temporarily unavailable",
        {"error": "server_error"},
        retryable=True,
    )
    assert classify(exc).category is ErrorCategory.NETWORK


def test_a_plain_refresh_error_stays_credential():
    from google.auth.exceptions import RefreshError

    result = classify(RefreshError("invalid_grant: Bad Request"))
    assert result.category is ErrorCategory.CREDENTIAL
    assert result.pauses_job is True


def test_default_credentials_error_stays_credential():
    from google.auth.exceptions import DefaultCredentialsError

    assert classify(DefaultCredentialsError("no ADC")).category is ErrorCategory.CREDENTIAL


def test_describe_returns_the_taxonomy_entry():
    info = describe(ErrorCategory.FILE_LOCKED)
    assert info.category is ErrorCategory.FILE_LOCKED
    assert "open in another program" in info.message
    assert info.action
