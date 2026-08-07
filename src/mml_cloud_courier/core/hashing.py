"""Single-pass streaming hashes.

Every byte is read once. Enabling SHA-256 costs CPU but never a second
read, which matters when a single file is measured in hundreds of GB.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import google_crc32c

DEFAULT_CHUNK_SIZE = 1 << 20


@dataclass(frozen=True, slots=True)
class HashResult:
    crc32c: int
    sha256: str | None
    bytes_read: int


def hash_stream(
    fp: BinaryIO,
    *,
    length: int | None = None,
    with_sha256: bool = False,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> HashResult:
    """Hash ``length`` bytes from ``fp``, or to EOF when ``length`` is None."""
    crc = google_crc32c.Checksum()
    sha = hashlib.sha256() if with_sha256 else None
    remaining = length
    total = 0

    while remaining is None or remaining > 0:
        want = chunk_size if remaining is None else min(chunk_size, remaining)
        chunk = fp.read(want)
        if not chunk:
            break
        crc.update(chunk)
        if sha is not None:
            sha.update(chunk)
        total += len(chunk)
        if remaining is not None:
            remaining -= len(chunk)

    if length is not None and total != length:
        raise ValueError(f"expected {length} bytes, read {total}")

    return HashResult(
        crc32c=int.from_bytes(crc.digest(), "big"),
        sha256=sha.hexdigest() if sha is not None else None,
        bytes_read=total,
    )


def hash_file(path: str | os.PathLike[str], *, with_sha256: bool = False) -> HashResult:
    with Path(path).open("rb") as fp:
        return hash_stream(fp, with_sha256=with_sha256)


def hash_range(
    path: str | os.PathLike[str],
    offset: int,
    length: int,
    *,
    with_sha256: bool = False,
) -> HashResult:
    with Path(path).open("rb") as fp:
        fp.seek(offset)
        return hash_stream(fp, length=length, with_sha256=with_sha256)


def crc32c_to_base64(value: int) -> str:
    """Encode as GCS reports it: base64 of the big-endian 4-byte value."""
    return base64.b64encode(value.to_bytes(4, "big")).decode("ascii")


def crc32c_from_base64(encoded: str) -> int:
    return int.from_bytes(base64.b64decode(encoded), "big")
