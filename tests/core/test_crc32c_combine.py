import os
import random

import google_crc32c
import pytest

from mml_cloud_courier.core.crc32c_combine import combine, combine_all


def crc(data: bytes) -> int:
    c = google_crc32c.Checksum()
    c.update(data)
    return int.from_bytes(c.digest(), "big")


def test_combine_matches_direct_hash_for_a_simple_split():
    a, b = b"hello ", b"world"
    assert combine(crc(a), crc(b), len(b)) == crc(a + b)


def test_combine_with_empty_tail_is_identity():
    a = b"anything at all"
    assert combine(crc(a), crc(b""), 0) == crc(a)


def test_combine_with_empty_head():
    b = b"tail bytes"
    assert combine(crc(b""), crc(b), len(b)) == crc(b)


@pytest.mark.parametrize("seed", range(25))
def test_combine_matches_direct_hash_for_random_splits(seed):
    rng = random.Random(seed)
    data = os.urandom(rng.randint(1, 5000))
    cut = rng.randint(0, len(data))
    a, b = data[:cut], data[cut:]
    assert combine(crc(a), crc(b), len(b)) == crc(a + b)


def test_combine_all_reassembles_many_slices():
    data = os.urandom(20_000)
    bounds = [0, 1, 999, 4096, 4097, 12_345, 20_000]
    pairs = [
        (crc(data[lo:hi]), hi - lo)
        for lo, hi in zip(bounds, bounds[1:])
    ]
    assert combine_all(pairs) == crc(data)


def test_combine_all_rejects_empty_input():
    with pytest.raises(ValueError):
        combine_all([])
