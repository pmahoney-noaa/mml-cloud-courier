"""Real DPAPI round-trips — no mocks; this machine's DPAPI is the unit."""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")

from mml_cloud_transfer.auth.dpapi import protect, unprotect


def test_round_trip():
    secret = b'{"refresh_token": "1//abc-secret"}'
    assert unprotect(protect(secret)) == secret


def test_ciphertext_does_not_contain_the_plaintext():
    secret = b"THE-SECRET-REFRESH-TOKEN"
    blob = protect(secret)
    assert blob != secret
    assert secret not in blob


def test_tampered_blob_raises_value_error():
    blob = bytearray(protect(b"payload"))
    blob[len(blob) // 2] ^= 0xFF
    with pytest.raises(ValueError):
        unprotect(bytes(blob))


def test_garbage_blob_raises_value_error():
    with pytest.raises(ValueError):
        unprotect(b"not a dpapi blob at all")
