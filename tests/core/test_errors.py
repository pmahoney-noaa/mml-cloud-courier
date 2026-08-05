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
