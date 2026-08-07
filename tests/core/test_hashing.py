import hashlib

import pytest

from mml_cloud_courier.core.hashing import (
    crc32c_from_base64,
    crc32c_to_base64,
    hash_file,
    hash_range,
)

# Standard CRC-32C check value for the ASCII string "123456789".
CHECK_VECTOR = b"123456789"
CHECK_CRC32C = 0xE3069283


def test_known_check_vector(tmp_path):
    p = tmp_path / "check.bin"
    p.write_bytes(CHECK_VECTOR)
    assert hash_file(p).crc32c == CHECK_CRC32C


def test_empty_file_hashes_to_zero(tmp_path):
    p = tmp_path / "empty.bin"
    p.write_bytes(b"")
    result = hash_file(p)
    assert result.crc32c == 0
    assert result.bytes_read == 0


def test_sha256_is_omitted_unless_requested(tmp_path):
    p = tmp_path / "data.bin"
    p.write_bytes(b"payload")
    assert hash_file(p).sha256 is None
    assert hash_file(p, with_sha256=True).sha256 == hashlib.sha256(b"payload").hexdigest()


def test_hash_range_reads_only_the_requested_window(tmp_path):
    p = tmp_path / "data.bin"
    p.write_bytes(b"AAAA" + CHECK_VECTOR + b"ZZZZ")
    result = hash_range(p, offset=4, length=len(CHECK_VECTOR))
    assert result.crc32c == CHECK_CRC32C
    assert result.bytes_read == len(CHECK_VECTOR)


def test_hash_range_rejects_a_short_file(tmp_path):
    p = tmp_path / "short.bin"
    p.write_bytes(b"abc")
    with pytest.raises(ValueError, match="expected 100 bytes"):
        hash_range(p, offset=0, length=100)


def test_chunk_size_does_not_change_the_result(tmp_path):
    p = tmp_path / "big.bin"
    p.write_bytes(bytes(range(256)) * 50)
    from mml_cloud_courier.core.hashing import hash_stream

    with p.open("rb") as fp:
        small = hash_stream(fp, chunk_size=7)
    with p.open("rb") as fp:
        large = hash_stream(fp, chunk_size=1 << 20)
    assert small.crc32c == large.crc32c
    assert small.bytes_read == large.bytes_read


def test_base64_round_trip():
    assert crc32c_from_base64(crc32c_to_base64(CHECK_CRC32C)) == CHECK_CRC32C
    # GCS reports base64 of the big-endian 4-byte value.
    assert crc32c_to_base64(0) == "AAAAAA=="
