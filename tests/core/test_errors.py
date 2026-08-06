import google.auth.exceptions
import requests.exceptions
import urllib3.exceptions

from mml_cloud_transfer.core.errors import Classification, ErrorCategory, classify


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
