"""Combine CRC32C checksums of adjacent byte ranges.

Adapted from zlib's ``crc32_combine`` with the CRC-32C (Castagnoli)
reflected polynomial. This is what lets a sliced upload produce a
whole-file checksum without a second read pass over the file.
"""

from __future__ import annotations

from collections.abc import Sequence

_GF2_DIM = 32
_CRC32C_REFLECTED_POLY = 0x82F63B78


def _matrix_times(matrix: list[int], vec: int) -> int:
    total = 0
    index = 0
    while vec:
        if vec & 1:
            total ^= matrix[index]
        vec >>= 1
        index += 1
    return total


def _matrix_square(square: list[int], matrix: list[int]) -> None:
    for n in range(_GF2_DIM):
        square[n] = _matrix_times(matrix, matrix[n])


def combine(crc1: int, crc2: int, len2: int) -> int:
    """Return the CRC32C of ``a + b`` given ``crc(a)``, ``crc(b)`` and ``len(b)``."""
    if len2 < 0:
        raise ValueError("len2 must not be negative")
    if len2 == 0:
        return crc1

    even = [0] * _GF2_DIM
    odd = [0] * _GF2_DIM

    # Operator for a single zero bit.
    odd[0] = _CRC32C_REFLECTED_POLY
    row = 1
    for n in range(1, _GF2_DIM):
        odd[n] = row
        row <<= 1

    _matrix_square(even, odd)
    _matrix_square(odd, even)

    # Apply len2 zero bytes to crc1 by repeated squaring.
    remaining = len2
    while True:
        _matrix_square(even, odd)
        if remaining & 1:
            crc1 = _matrix_times(even, crc1)
        remaining >>= 1
        if remaining == 0:
            break

        _matrix_square(odd, even)
        if remaining & 1:
            crc1 = _matrix_times(odd, crc1)
        remaining >>= 1
        if remaining == 0:
            break

    return crc1 ^ crc2


def combine_all(pairs: Sequence[tuple[int, int]]) -> int:
    """Fold a sequence of ``(crc32c, length)`` pairs, in byte order, into one CRC32C."""
    if not pairs:
        raise ValueError("combine_all requires at least one (crc32c, length) pair")
    result = pairs[0][0]
    for crc_value, length in pairs[1:]:
        result = combine(result, crc_value, length)
    return result
