# MML Cloud Transfer — Plan 1: Core and Store

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pure-logic and persistence foundation of MML Cloud Transfer, ending with a `mmlct scan` command that walks a source tree and writes a complete, queryable file manifest to SQLite.

**Architecture:** Two layers. `core` holds pure logic — models, CRC32C hashing and combination, path normalization, slice planning, error classification, and source scanning — with no network, no database, and no GUI dependencies, so it is testable in milliseconds. `store` wraps SQLite in WAL mode with a repository that owns every state transition, so a killed process never loses more than the in-flight chunk. A thin CLI ties them together.

**Tech Stack:** Python 3.12, `google-crc32c`, stdlib `sqlite3`, stdlib `argparse`, pytest.

**Spec:** [2026-08-04-gcs-transfer-manager-design.md](../specs/2026-08-04-gcs-transfer-manager-design.md)

## Global Constraints

These apply to every task. Do not restate them; do not violate them.

- **Python 3.12.** Not 3.13 or 3.14 — PySide6 and `google-crc32c` wheel availability drives this.
- **`core` imports nothing from `google.cloud.storage`, PySide6, or `sqlite3`.** It is pure logic. Where GCS exception handling is needed, classify by duck-typing on attributes, never by importing cloud libraries.
- **Enum values are persisted to SQLite as their exact string values.** Changing a value is a data migration, not a rename. Tests assert the literal strings.
- **All relative paths use forward slashes**, regardless of platform, because they become GCS object names.
- **Filesystem access uses `\\?\`-prefixed extended-length paths** so files beyond 260 characters do not fail.
- **A file is only ever `verified` or `skipped` on success.** No other state counts toward a COMPLETE verdict.
- **Sliced transfers never exceed 32 components**, so a single `compose` call always suffices.
- **TDD throughout:** write the failing test, watch it fail, write minimal code, watch it pass, commit.

---

### Task 1: Project scaffolding and domain models

Sets up the package, tooling, and the enums and dataclasses every later task depends on.

**Files:**
- Create: `pyproject.toml`
- Create: `src/mml_cloud_transfer/__init__.py`
- Create: `src/mml_cloud_transfer/core/__init__.py`
- Create: `src/mml_cloud_transfer/core/models.py`
- Create: `tests/__init__.py`
- Create: `tests/core/__init__.py`
- Test: `tests/core/test_models.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Direction`, `JobStatus`, `FileState`, `TransferMethod`, `SliceState` (all `str` enums); `TERMINAL_SUCCESS_STATES: frozenset[FileState]`; `PlannedFile(relative_path: str, source_path: str, size_bytes: int, mtime_ns: int)`

- [ ] **Step 1: Create the project layout and packaging config**

Create `pyproject.toml`:

```toml
[project]
name = "mml-cloud-transfer"
version = "0.1.0"
description = "Verified, resumable file transfers between Windows workstations and Google Cloud Storage"
requires-python = ">=3.12,<3.13"
dependencies = [
    "google-crc32c>=1.5.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
]

[project.scripts]
mmlct = "mml_cloud_transfer.cli.__main__:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/mml_cloud_transfer"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

Create these files, each empty for now:
`src/mml_cloud_transfer/__init__.py`, `src/mml_cloud_transfer/core/__init__.py`, `tests/__init__.py`, `tests/core/__init__.py`.

- [ ] **Step 2: Install the package in editable mode**

Run: `py -3.12 -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"`
Expected: installs cleanly, including a `google-crc32c` wheel (no compiler invoked).

If pip tries to build `google-crc32c` from source, stop — you are not on Python 3.12. Check with `.venv/Scripts/python --version`.

- [ ] **Step 3: Write the failing test**

Create `tests/core/test_models.py`:

```python
import pytest

from mml_cloud_transfer.core.models import (
    TERMINAL_SUCCESS_STATES,
    Direction,
    FileState,
    JobStatus,
    PlannedFile,
    SliceState,
    TransferMethod,
)


def test_file_state_values_are_stable():
    # These strings are persisted in SQLite. Changing one is a data migration.
    assert [s.value for s in FileState] == [
        "pending",
        "transferring",
        "transferred",
        "verified",
        "failed",
        "skipped",
        "changed",
        "quarantined",
    ]


def test_job_status_values_are_stable():
    assert [s.value for s in JobStatus] == [
        "pending",
        "scanning",
        "running",
        "paused",
        "stalled",
        "complete",
        "incomplete",
        "cancelled",
    ]


def test_direction_and_method_values_are_stable():
    assert [d.value for d in Direction] == ["upload", "download"]
    assert [m.value for m in TransferMethod] == ["single_shot", "resumable", "sliced"]
    assert [s.value for s in SliceState] == ["pending", "uploading", "uploaded", "failed"]


def test_only_verified_and_skipped_count_as_success():
    assert TERMINAL_SUCCESS_STATES == frozenset({FileState.VERIFIED, FileState.SKIPPED})


def test_planned_file_is_immutable():
    pf = PlannedFile(
        relative_path="run47/stack_0001.tiff",
        source_path=r"\\?\UNC\nas01\imaging\run47\stack_0001.tiff",
        size_bytes=1024,
        mtime_ns=1_700_000_000_000_000_000,
    )
    assert pf.relative_path == "run47/stack_0001.tiff"
    with pytest.raises(AttributeError):
        pf.size_bytes = 2048  # type: ignore[misc]
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/core/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mml_cloud_transfer.core.models'`

- [ ] **Step 5: Write the minimal implementation**

Create `src/mml_cloud_transfer/core/models.py`:

```python
"""Domain models shared across every layer.

Enum *values* are persisted to SQLite. Treat them as a storage format:
changing one requires a migration, not just a rename.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Direction(str, Enum):
    UPLOAD = "upload"
    DOWNLOAD = "download"


class JobStatus(str, Enum):
    PENDING = "pending"
    SCANNING = "scanning"
    RUNNING = "running"
    PAUSED = "paused"
    STALLED = "stalled"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    CANCELLED = "cancelled"


class FileState(str, Enum):
    PENDING = "pending"
    TRANSFERRING = "transferring"
    TRANSFERRED = "transferred"
    VERIFIED = "verified"
    FAILED = "failed"
    SKIPPED = "skipped"
    CHANGED = "changed"
    QUARANTINED = "quarantined"


class TransferMethod(str, Enum):
    SINGLE_SHOT = "single_shot"
    RESUMABLE = "resumable"
    SLICED = "sliced"


class SliceState(str, Enum):
    PENDING = "pending"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    FAILED = "failed"


#: The only two states that count toward a COMPLETE job verdict.
TERMINAL_SUCCESS_STATES = frozenset({FileState.VERIFIED, FileState.SKIPPED})


@dataclass(frozen=True, slots=True)
class PlannedFile:
    """One file discovered by the scanner, before any bytes move."""

    relative_path: str
    """Forward-slash separated, relative to the scan root. Becomes the object name."""

    source_path: str
    """Absolute path in extended-length (``\\\\?\\``) form on Windows."""

    size_bytes: int
    mtime_ns: int
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/core/test_models.py -v`
Expected: PASS, 5 tests

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/ tests/
git commit -m "feat: add project scaffolding and domain models"
```

---

### Task 2: CRC32C combination

The correctness-critical module. It lets us compute a whole-file CRC32C from per-slice CRC32Cs without re-reading a 500 GB file, which is what makes Layer 2 verification affordable.

**Files:**
- Create: `src/mml_cloud_transfer/core/crc32c_combine.py`
- Test: `tests/core/test_crc32c_combine.py`

**Interfaces:**
- Consumes: nothing
- Produces: `combine(crc1: int, crc2: int, len2: int) -> int`; `combine_all(pairs: Sequence[tuple[int, int]]) -> int`

**Background for the implementer:** CRC32C over a concatenation `a + b` can be derived from `crc(a)`, `crc(b)`, and `len(b)` alone. The trick is that appending `len(b)` zero bytes to `a` is a linear operation over GF(2), representable as a 32×32 bit-matrix, and applying it `len(b)` times is done by repeated squaring. This is exactly zlib's `crc32_combine`, with the CRC-32 polynomial swapped for CRC-32C's reflected form `0x82F63B78`. You do not need to understand the algebra to implement it — but the property test is what proves it, so do not skip it.

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_crc32c_combine.py`:

```python
import os
import random

import google_crc32c
import pytest

from mml_cloud_transfer.core.crc32c_combine import combine, combine_all


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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/core/test_crc32c_combine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mml_cloud_transfer.core.crc32c_combine'`

- [ ] **Step 3: Write the minimal implementation**

Create `src/mml_cloud_transfer/core/crc32c_combine.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/core/test_crc32c_combine.py -v`
Expected: PASS, 30 tests (25 parametrized plus 5 others)

If the random-split tests fail while the simple ones pass, the polynomial constant or the squaring loop is wrong — re-check `_CRC32C_REFLECTED_POLY` is `0x82F63B78` and not the CRC-32 value `0xEDB88320`.

- [ ] **Step 5: Commit**

```bash
git add src/mml_cloud_transfer/core/crc32c_combine.py tests/core/test_crc32c_combine.py
git commit -m "feat: add CRC32C combination for sliced whole-file checksums"
```

---

### Task 3: Streaming file hashing

Reads each file exactly once, producing CRC32C and optionally SHA-256 in the same pass.

**Files:**
- Create: `src/mml_cloud_transfer/core/hashing.py`
- Test: `tests/core/test_hashing.py`

**Interfaces:**
- Consumes: nothing
- Produces: `HashResult(crc32c: int, sha256: str | None, bytes_read: int)`; `hash_stream(fp, *, length=None, with_sha256=False, chunk_size=1048576) -> HashResult`; `hash_file(path, *, with_sha256=False) -> HashResult`; `hash_range(path, offset, length, *, with_sha256=False) -> HashResult`; `crc32c_to_base64(value: int) -> str`; `crc32c_from_base64(encoded: str) -> int`

The base64 helpers matter because GCS reports object CRC32C as base64 of the big-endian 4-byte value, while we store integers.

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_hashing.py`:

```python
import hashlib

import pytest

from mml_cloud_transfer.core.hashing import (
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
    from mml_cloud_transfer.core.hashing import hash_stream

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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/core/test_hashing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mml_cloud_transfer.core.hashing'`

- [ ] **Step 3: Write the minimal implementation**

Create `src/mml_cloud_transfer/core/hashing.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/core/test_hashing.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
git add src/mml_cloud_transfer/core/hashing.py tests/core/test_hashing.py
git commit -m "feat: add single-pass CRC32C and SHA-256 file hashing"
```

---

### Task 4: Path normalization

Handles the three Windows path problems that would otherwise surface at 2am: mapped drives the service cannot see, the 260-character limit, and backslash-vs-slash in object names.

**Files:**
- Create: `src/mml_cloud_transfer/core/paths.py`
- Test: `tests/core/test_paths.py`

**Interfaces:**
- Consumes: nothing
- Produces: `extended_path(path: str) -> str`; `is_unc(path: str) -> bool`; `resolve_mapped_drive(path: str, resolver: Callable[[str], str | None]) -> str`; `to_relative_path(root: str, path: str) -> str`; `to_object_name(prefix: str, relative_path: str) -> str`; `default_drive_resolver(drive: str) -> str | None`

`resolver` is injected so the tests run on any platform. The production default calls `WNetGetConnectionW` on Windows and returns `None` elsewhere.

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_paths.py`:

```python
import pytest

from mml_cloud_transfer.core.paths import (
    extended_path,
    is_unc,
    resolve_mapped_drive,
    to_object_name,
    to_relative_path,
)


def fake_resolver(drive: str) -> str | None:
    return {"Z:": r"\\nas01\imaging"}.get(drive.upper())


def test_extended_path_prefixes_a_local_path():
    assert extended_path(r"C:\data\run47") == "\\\\?\\C:\\data\\run47"


def test_extended_path_uses_the_unc_form_for_shares():
    assert extended_path(r"\\nas01\imaging\run47") == "\\\\?\\UNC\\nas01\\imaging\\run47"


def test_extended_path_is_idempotent():
    once = extended_path(r"C:\data")
    assert extended_path(once) == once


def test_extended_path_normalises_forward_slashes():
    assert extended_path("C:/data/run47") == "\\\\?\\C:\\data\\run47"


def test_is_unc():
    assert is_unc(r"\\nas01\imaging")
    assert not is_unc(r"C:\data")
    assert not is_unc(r"Z:\data")


def test_resolve_mapped_drive_rewrites_to_unc():
    assert resolve_mapped_drive(r"Z:\run47\a.tif", fake_resolver) == r"\\nas01\imaging\run47\a.tif"


def test_resolve_mapped_drive_leaves_local_drives_alone():
    assert resolve_mapped_drive(r"C:\run47", fake_resolver) == r"C:\run47"


def test_resolve_mapped_drive_leaves_unc_alone():
    assert resolve_mapped_drive(r"\\nas01\imaging\x", fake_resolver) == r"\\nas01\imaging\x"


def test_to_relative_path_uses_forward_slashes():
    assert to_relative_path(r"C:\data", r"C:\data\run47\a.tif") == "run47/a.tif"


def test_to_relative_path_is_case_insensitive_on_the_root():
    assert to_relative_path(r"C:\Data", r"C:\data\a.tif") == "a.tif"


def test_to_relative_path_rejects_a_path_outside_the_root():
    with pytest.raises(ValueError, match="not inside"):
        to_relative_path(r"C:\data", r"C:\other\a.tif")


def test_to_object_name_joins_and_trims_separators():
    assert to_object_name("archive/run47/", "a/b.tif") == "archive/run47/a/b.tif"
    assert to_object_name("", "a/b.tif") == "a/b.tif"
    assert to_object_name("/archive/", "/a.tif") == "archive/a.tif"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/core/test_paths.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mml_cloud_transfer.core.paths'`

- [ ] **Step 3: Write the minimal implementation**

Create `src/mml_cloud_transfer/core/paths.py`:

```python
"""Windows path normalisation.

The transfer service runs under its own identity, so it never inherits the
user's mapped drive letters. Everything is converted to UNC before it is
stored, and to extended-length form before it touches the filesystem.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

_EXTENDED_PREFIX = "\\\\?\\"
_EXTENDED_UNC_PREFIX = "\\\\?\\UNC\\"


def is_unc(path: str) -> bool:
    normalised = path.replace("/", "\\")
    return normalised.startswith("\\\\") and not normalised.startswith(_EXTENDED_PREFIX)


def extended_path(path: str) -> str:
    """Return ``path`` in ``\\\\?\\`` form so the 260-character limit does not apply."""
    normalised = path.replace("/", "\\")
    if normalised.startswith(_EXTENDED_PREFIX):
        return normalised
    if is_unc(normalised):
        return _EXTENDED_UNC_PREFIX + normalised[2:]
    return _EXTENDED_PREFIX + normalised


def default_drive_resolver(drive: str) -> str | None:
    """Map ``Z:`` to its UNC target, or None if it is not a network drive."""
    if sys.platform != "win32":
        return None

    import ctypes

    buffer_size = ctypes.c_ulong(1024)
    buffer = ctypes.create_unicode_buffer(buffer_size.value)
    result = ctypes.windll.mpr.WNetGetConnectionW(  # type: ignore[attr-defined]
        ctypes.c_wchar_p(drive), buffer, ctypes.byref(buffer_size)
    )
    return buffer.value if result == 0 else None


def resolve_mapped_drive(
    path: str,
    resolver: Callable[[str], str | None] = default_drive_resolver,
) -> str:
    """Rewrite a mapped-drive path to its UNC equivalent, if it is one."""
    normalised = path.replace("/", "\\")
    if is_unc(normalised) or normalised.startswith(_EXTENDED_PREFIX):
        return normalised
    if len(normalised) < 2 or normalised[1] != ":":
        return normalised

    target = resolver(normalised[:2])
    if target is None:
        return normalised
    return target.rstrip("\\") + normalised[2:]


def to_relative_path(root: str, path: str) -> str:
    """Return ``path`` relative to ``root``, forward-slash separated."""
    root_n = root.replace("/", "\\").rstrip("\\")
    path_n = path.replace("/", "\\")
    if path_n.lower() != root_n.lower() and not path_n.lower().startswith(root_n.lower() + "\\"):
        raise ValueError(f"{path!r} is not inside {root!r}")
    return path_n[len(root_n) :].lstrip("\\").replace("\\", "/")


def to_object_name(prefix: str, relative_path: str) -> str:
    """Join a bucket prefix and a relative path into a GCS object name."""
    left = prefix.strip("/")
    right = relative_path.strip("/")
    return f"{left}/{right}" if left else right
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/core/test_paths.py -v`
Expected: PASS, 12 tests

- [ ] **Step 5: Commit**

```bash
git add src/mml_cloud_transfer/core/paths.py tests/core/test_paths.py
git commit -m "feat: add Windows path normalisation for UNC and long paths"
```

---

### Task 5: Transfer method selection and slice planning

Decides how each file moves, and cuts large files into at most 32 slices so one `compose` call always suffices.

**Files:**
- Create: `src/mml_cloud_transfer/core/slicing.py`
- Test: `tests/core/test_slicing.py`

**Interfaces:**
- Consumes: `TransferMethod` from `core.models`
- Produces: `SliceSpec(index: int, offset: int, length: int)`; `choose_method(size_bytes: int) -> TransferMethod`; `plan_slices(size_bytes: int) -> list[SliceSpec]`; constants `SINGLE_SHOT_MAX_BYTES`, `RESUMABLE_MAX_BYTES`, `MIN_SLICE_BYTES`, `MAX_COMPONENTS`

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_slicing.py`:

```python
import pytest

from mml_cloud_transfer.core.models import TransferMethod
from mml_cloud_transfer.core.slicing import (
    MAX_COMPONENTS,
    SliceSpec,
    choose_method,
    plan_slices,
)

MIB = 1024**2
GIB = 1024**3


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, TransferMethod.SINGLE_SHOT),
        (1, TransferMethod.SINGLE_SHOT),
        (8 * MIB, TransferMethod.SINGLE_SHOT),
        (8 * MIB + 1, TransferMethod.RESUMABLE),
        (GIB, TransferMethod.RESUMABLE),
        (GIB + 1, TransferMethod.SLICED),
        (500 * GIB, TransferMethod.SLICED),
    ],
)
def test_choose_method_boundaries(size, expected):
    assert choose_method(size) is expected


def test_small_files_are_a_single_slice():
    assert plan_slices(1234) == [SliceSpec(index=0, offset=0, length=1234)]


def test_two_gib_splits_into_two_one_gib_slices():
    slices = plan_slices(2 * GIB)
    assert slices == [
        SliceSpec(index=0, offset=0, length=GIB),
        SliceSpec(index=1, offset=GIB, length=GIB),
    ]


def test_a_very_large_file_never_exceeds_the_component_cap():
    slices = plan_slices(500 * GIB)
    assert len(slices) == MAX_COMPONENTS
    assert sum(s.length for s in slices) == 500 * GIB


@pytest.mark.parametrize("size", [1, 8 * MIB, GIB + 1, 40 * GIB, 500 * GIB, 3000 * GIB])
def test_slices_are_contiguous_and_complete(size):
    slices = plan_slices(size)
    assert len(slices) <= MAX_COMPONENTS
    assert slices[0].offset == 0
    assert sum(s.length for s in slices) == size
    for previous, current in zip(slices, slices[1:]):
        assert current.offset == previous.offset + previous.length
        assert current.index == previous.index + 1
    assert all(s.length > 0 for s in slices)


def test_zero_byte_file_yields_one_empty_slice():
    assert plan_slices(0) == [SliceSpec(index=0, offset=0, length=0)]


def test_negative_size_is_rejected():
    with pytest.raises(ValueError):
        plan_slices(-1)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/core/test_slicing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mml_cloud_transfer.core.slicing'`

- [ ] **Step 3: Write the minimal implementation**

Create `src/mml_cloud_transfer/core/slicing.py`:

```python
"""Choose a transfer method per file, and cut large files into slices.

Slice size is deliberately ``max(1 GiB, ceil(size / 32))`` so the component
count never exceeds 32 and a single ``compose`` call always finishes the job.
"""

from __future__ import annotations

from dataclasses import dataclass

from mml_cloud_transfer.core.models import TransferMethod

SINGLE_SHOT_MAX_BYTES = 8 * 1024**2
RESUMABLE_MAX_BYTES = 1024**3
MIN_SLICE_BYTES = 1024**3
MAX_COMPONENTS = 32


@dataclass(frozen=True, slots=True)
class SliceSpec:
    index: int
    offset: int
    length: int


def choose_method(size_bytes: int) -> TransferMethod:
    if size_bytes < 0:
        raise ValueError("size_bytes must not be negative")
    if size_bytes <= SINGLE_SHOT_MAX_BYTES:
        return TransferMethod.SINGLE_SHOT
    if size_bytes <= RESUMABLE_MAX_BYTES:
        return TransferMethod.RESUMABLE
    return TransferMethod.SLICED


def plan_slices(size_bytes: int) -> list[SliceSpec]:
    """Cut ``size_bytes`` into at most ``MAX_COMPONENTS`` contiguous slices."""
    if size_bytes < 0:
        raise ValueError("size_bytes must not be negative")
    if size_bytes == 0:
        return [SliceSpec(index=0, offset=0, length=0)]

    slice_size = max(MIN_SLICE_BYTES, -(-size_bytes // MAX_COMPONENTS))

    slices: list[SliceSpec] = []
    offset = 0
    index = 0
    while offset < size_bytes:
        length = min(slice_size, size_bytes - offset)
        slices.append(SliceSpec(index=index, offset=offset, length=length))
        offset += length
        index += 1
    return slices
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/core/test_slicing.py -v`
Expected: PASS, 22 tests

- [ ] **Step 5: Commit**

```bash
git add src/mml_cloud_transfer/core/slicing.py tests/core/test_slicing.py
git commit -m "feat: add transfer method selection and slice planning"
```

---

### Task 6: Error taxonomy

The single source of truth mapping any exception to a category, a plain-language message, and a suggested action. Later tasks feed the grouped Errors view and the job report from this one function.

**Files:**
- Create: `src/mml_cloud_transfer/core/errors.py`
- Test: `tests/core/test_errors.py`

**Interfaces:**
- Consumes: nothing
- Produces: `ErrorCategory` (str enum); `Classification(category, transient: bool, pauses_job: bool, message: str, action: str)`; `classify(exc: BaseException) -> Classification`; `ScanError(path: str, category: ErrorCategory, message: str)`

**Why duck-typing:** `core` must not import cloud libraries. Google's `api_core` exceptions expose an integer HTTP status on `.code`, and the storage client raises a class literally named `DataCorruption`. Classifying on those two observable attributes keeps the boundary intact and stays fully testable with stub exceptions.

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_errors.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/core/test_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mml_cloud_transfer.core.errors'`

- [ ] **Step 3: Write the minimal implementation**

Create `src/mml_cloud_transfer/core/errors.py`:

```python
"""Error taxonomy — one mapping from exception to user-facing meaning.

The grouped Errors view, tray notifications, and the job report all read
from here, so a category is defined once and rendered consistently.

This module deliberately imports no cloud libraries. Google's exceptions are
recognised by their integer ``.code`` attribute and by class name, which
keeps ``core`` pure and the tests dependency-free.
"""

from __future__ import annotations

import errno
from dataclasses import dataclass
from enum import Enum

# Windows error codes worth naming.
_ERROR_SHARING_VIOLATION = 32
_ERROR_LOCK_VIOLATION = 33
_ERROR_FILENAME_EXCED_RANGE = 206


class ErrorCategory(str, Enum):
    PERMISSION_DENIED = "permission_denied"
    FILE_LOCKED = "file_locked"
    PATH_TOO_LONG = "path_too_long"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    NETWORK = "network"
    QUOTA = "quota"
    CREDENTIAL = "credential"
    NOT_FOUND = "not_found"
    SOURCE_CHANGED = "source_changed"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Classification:
    category: ErrorCategory
    transient: bool
    """Worth retrying with backoff."""
    pauses_job: bool
    """Retrying other files is pointless until a human intervenes."""
    message: str
    action: str


@dataclass(frozen=True, slots=True)
class ScanError:
    path: str
    category: ErrorCategory
    message: str


_TABLE: dict[ErrorCategory, tuple[bool, bool, str, str]] = {
    ErrorCategory.PERMISSION_DENIED: (
        False, False,
        "Access to this file was denied.",
        "Grant the transfer service account read access to this path.",
    ),
    ErrorCategory.FILE_LOCKED: (
        True, False,
        "The file is open in another program.",
        "Close the program holding the file, then resume the job.",
    ),
    ErrorCategory.PATH_TOO_LONG: (
        False, False,
        "The file path is too long for Windows to open.",
        "Shorten the folder names, or move the data closer to the drive root.",
    ),
    ErrorCategory.CHECKSUM_MISMATCH: (
        False, False,
        "The transferred copy did not match the original checksum.",
        "Resume the job to transfer this file again.",
    ),
    ErrorCategory.NETWORK: (
        True, False,
        "The network connection failed.",
        "No action needed — this retries automatically.",
    ),
    ErrorCategory.QUOTA: (
        True, False,
        "Google Cloud Storage is rate limiting the transfer.",
        "No action needed — this retries automatically with backoff.",
    ),
    ErrorCategory.CREDENTIAL: (
        False, True,
        "The stored credential was rejected by Google Cloud Storage.",
        "Re-authenticate this connection, or ask an administrator to check its permissions.",
    ),
    ErrorCategory.NOT_FOUND: (
        False, False,
        "The object or file no longer exists.",
        "Re-scan the source, then start a new job.",
    ),
    ErrorCategory.SOURCE_CHANGED: (
        False, False,
        "The source file changed while it was being transferred.",
        "Make sure nothing is writing to the file, then resume the job.",
    ),
    ErrorCategory.CONFLICT: (
        False, False,
        "The destination object changed since this job was planned.",
        "Re-scan the destination, then start a new job.",
    ),
    ErrorCategory.UNKNOWN: (
        False, False,
        "An unexpected error occurred.",
        "Check the job log, then contact support with the diagnostics bundle.",
    ),
}


def _build(category: ErrorCategory) -> Classification:
    transient, pauses_job, message, action = _TABLE[category]
    return Classification(
        category=category,
        transient=transient,
        pauses_job=pauses_job,
        message=message,
        action=action,
    )


def _from_http_status(code: int) -> ErrorCategory | None:
    if code in (401, 403):
        return ErrorCategory.CREDENTIAL
    if code == 404:
        return ErrorCategory.NOT_FOUND
    if code == 412:
        return ErrorCategory.CONFLICT
    if code in (408, 429):
        return ErrorCategory.QUOTA
    if 500 <= code <= 599:
        return ErrorCategory.NETWORK
    return None


def _from_os_error(exc: OSError) -> ErrorCategory:
    winerror = getattr(exc, "winerror", None)
    if winerror in (_ERROR_SHARING_VIOLATION, _ERROR_LOCK_VIOLATION):
        return ErrorCategory.FILE_LOCKED
    if winerror == _ERROR_FILENAME_EXCED_RANGE:
        return ErrorCategory.PATH_TOO_LONG
    if isinstance(exc, PermissionError):
        return ErrorCategory.PERMISSION_DENIED
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return ErrorCategory.NETWORK
    if isinstance(exc, FileNotFoundError):
        return ErrorCategory.NOT_FOUND
    if exc.errno == errno.ENAMETOOLONG:
        return ErrorCategory.PATH_TOO_LONG
    if exc.errno == errno.EACCES:
        return ErrorCategory.PERMISSION_DENIED
    return ErrorCategory.UNKNOWN


def classify(exc: BaseException) -> Classification:
    """Map any exception to its category and user-facing guidance."""
    if type(exc).__name__ == "DataCorruption":
        return _build(ErrorCategory.CHECKSUM_MISMATCH)

    code = getattr(exc, "code", None)
    if isinstance(code, int) and not isinstance(code, bool):
        category = _from_http_status(code)
        if category is not None:
            return _build(category)

    if isinstance(exc, OSError):
        return _build(_from_os_error(exc))

    if isinstance(exc, TimeoutError):
        return _build(ErrorCategory.NETWORK)

    return _build(ErrorCategory.UNKNOWN)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/core/test_errors.py -v`
Expected: PASS, 14 tests

- [ ] **Step 5: Commit**

```bash
git add src/mml_cloud_transfer/core/errors.py tests/core/test_errors.py
git commit -m "feat: add error taxonomy mapping exceptions to user guidance"
```

---

### Task 7: Source scanner

Walks a source tree and yields one entry per file, collecting per-entry errors instead of aborting. Streams rather than building a list, because a job may contain millions of files.

**Files:**
- Create: `src/mml_cloud_transfer/core/scanner.py`
- Test: `tests/core/test_scanner.py`

**Interfaces:**
- Consumes: `PlannedFile` from `core.models`; `ErrorCategory`, `ScanError`, `classify` from `core.errors`; `to_relative_path`, `extended_path` from `core.paths`
- Produces: `iter_source(root: str, *, follow_extended: bool = True) -> Iterator[PlannedFile | ScanError]`; `ScanTotals(file_count: int, byte_count: int, error_count: int)`; `summarise(entries: Iterable[PlannedFile | ScanError]) -> tuple[list[PlannedFile], list[ScanError], ScanTotals]`

Symlinks, junctions, and other reparse points are skipped and reported, never followed — this prevents both cycles and duplicated data.

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_scanner.py`:

```python
import os

import pytest

from mml_cloud_transfer.core.errors import ErrorCategory, ScanError
from mml_cloud_transfer.core.models import PlannedFile
from mml_cloud_transfer.core.scanner import iter_source, summarise


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "run47").mkdir()
    (tmp_path / "run47" / "nested").mkdir()
    (tmp_path / "empty_dir").mkdir()
    (tmp_path / "top.txt").write_bytes(b"a" * 10)
    (tmp_path / "run47" / "a.tif").write_bytes(b"b" * 20)
    (tmp_path / "run47" / "nested" / "b.tif").write_bytes(b"c" * 30)
    return tmp_path


def collect(root):
    files, errors, totals = summarise(iter_source(str(root), follow_extended=False))
    return files, errors, totals


def test_finds_every_file_with_forward_slash_relative_paths(tree):
    files, _, _ = collect(tree)
    assert sorted(f.relative_path for f in files) == [
        "run47/a.tif",
        "run47/nested/b.tif",
        "top.txt",
    ]


def test_records_size_and_mtime(tree):
    files, _, _ = collect(tree)
    by_path = {f.relative_path: f for f in files}
    assert by_path["run47/a.tif"].size_bytes == 20
    assert by_path["run47/a.tif"].mtime_ns > 0


def test_totals_are_accurate(tree):
    _, _, totals = collect(tree)
    assert totals.file_count == 3
    assert totals.byte_count == 60
    assert totals.error_count == 0


def test_empty_directories_produce_no_entries(tree):
    files, errors, _ = collect(tree)
    assert not any("empty_dir" in f.relative_path for f in files)
    assert errors == []


def test_yields_planned_files_not_lists(tree):
    first = next(iter_source(str(tree), follow_extended=False))
    assert isinstance(first, (PlannedFile, ScanError))


def test_missing_root_is_reported_as_an_error_not_raised(tmp_path):
    files, errors, totals = collect(tmp_path / "does-not-exist")
    assert files == []
    assert len(errors) == 1
    assert errors[0].category is ErrorCategory.NOT_FOUND
    assert totals.error_count == 1


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unsupported")
def test_symlinks_are_skipped_and_reported(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "file.txt").write_bytes(b"data")
    try:
        os.symlink(real, tmp_path / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    files, errors, _ = collect(tmp_path)
    assert sorted(f.relative_path for f in files) == ["real/file.txt"]
    assert [e.category for e in errors] == [ErrorCategory.UNKNOWN]
    assert "link" in errors[0].path
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/core/test_scanner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mml_cloud_transfer.core.scanner'`

- [ ] **Step 3: Write the minimal implementation**

Create `src/mml_cloud_transfer/core/scanner.py`:

```python
"""Walk a source tree into PlannedFile entries.

Yields lazily: a job may contain millions of files, so nothing accumulates
a full list unless the caller asks for one. Per-entry failures become
ScanError values rather than exceptions, so one unreadable folder never
aborts a scan.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from mml_cloud_transfer.core.errors import ErrorCategory, ScanError, classify
from mml_cloud_transfer.core.models import PlannedFile
from mml_cloud_transfer.core.paths import extended_path, to_relative_path

ScanEntry = PlannedFile | ScanError


@dataclass(frozen=True, slots=True)
class ScanTotals:
    file_count: int
    byte_count: int
    error_count: int


def iter_source(root: str, *, follow_extended: bool = True) -> Iterator[ScanEntry]:
    """Yield one entry per file beneath ``root``.

    ``follow_extended`` controls whether paths are rewritten to ``\\\\?\\``
    form; tests disable it so they can run on any platform.
    """
    walk_root = extended_path(root) if follow_extended else root

    if not os.path.isdir(walk_root):
        yield ScanError(
            path=root,
            category=ErrorCategory.NOT_FOUND,
            message=f"Source folder not found: {root}",
        )
        return

    stack = [walk_root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            classification = classify(exc)
            yield ScanError(
                path=current,
                category=classification.category,
                message=f"{classification.message} ({current})",
            )
            continue

        for entry in entries:
            try:
                if entry.is_symlink():
                    yield ScanError(
                        path=entry.path,
                        category=ErrorCategory.UNKNOWN,
                        message=f"Skipped link or junction: {entry.path}",
                    )
                    continue
                if entry.is_dir():
                    stack.append(entry.path)
                    continue

                stat = entry.stat()
                yield PlannedFile(
                    relative_path=to_relative_path(walk_root, entry.path),
                    source_path=entry.path,
                    size_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                )
            except OSError as exc:
                classification = classify(exc)
                yield ScanError(
                    path=entry.path,
                    category=classification.category,
                    message=f"{classification.message} ({entry.path})",
                )


def summarise(
    entries: Iterable[ScanEntry],
) -> tuple[list[PlannedFile], list[ScanError], ScanTotals]:
    """Materialise a scan. Only for small trees and tests — prefer streaming."""
    files: list[PlannedFile] = []
    errors: list[ScanError] = []
    for entry in entries:
        if isinstance(entry, PlannedFile):
            files.append(entry)
        else:
            errors.append(entry)
    totals = ScanTotals(
        file_count=len(files),
        byte_count=sum(f.size_bytes for f in files),
        error_count=len(errors),
    )
    return files, errors, totals
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/core/test_scanner.py -v`
Expected: PASS, 7 tests (the symlink test may report SKIPPED without Developer Mode or admin rights — that is acceptable)

- [ ] **Step 5: Commit**

```bash
git add src/mml_cloud_transfer/core/scanner.py tests/core/test_scanner.py
git commit -m "feat: add streaming source scanner with per-entry error capture"
```

---

### Task 8: SQLite schema and connection factory

**Files:**
- Create: `src/mml_cloud_transfer/store/__init__.py`
- Create: `src/mml_cloud_transfer/store/schema.py`
- Create: `src/mml_cloud_transfer/store/db.py`
- Create: `tests/store/__init__.py`
- Test: `tests/store/test_schema.py`

**Interfaces:**
- Consumes: nothing
- Produces: `SCHEMA_VERSION: int`; `apply_migrations(conn: sqlite3.Connection) -> None`; `connect(path: str | os.PathLike[str]) -> sqlite3.Connection`

- [ ] **Step 1: Write the failing test**

Create `tests/store/__init__.py` (empty) and `tests/store/test_schema.py`:

```python
import sqlite3

import pytest

from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.schema import SCHEMA_VERSION, apply_migrations

EXPECTED_TABLES = {
    "schema_version",
    "profiles",
    "jobs",
    "job_files",
    "file_slices",
    "events",
}


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "jobs.db")
    yield connection
    connection.close()


def test_all_tables_exist(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    assert EXPECTED_TABLES <= {r["name"] for r in rows}


def test_schema_version_is_recorded(conn):
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert row["version"] == SCHEMA_VERSION


def test_wal_mode_is_enabled(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_foreign_keys_are_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO job_files (job_id, relative_path, source_path, object_name,"
            " size_bytes, mtime_ns, method, state)"
            " VALUES (999, 'a', 'a', 'a', 0, 0, 'single_shot', 'pending')"
        )
        conn.commit()


def test_rows_are_dict_like(conn):
    conn.execute(
        "INSERT INTO jobs (name, direction, source_root, dest_prefix, status, created_at)"
        " VALUES ('j', 'upload', 'C:/x', 'p', 'pending', '2026-08-04T00:00:00Z')"
    )
    row = conn.execute("SELECT name, direction FROM jobs").fetchone()
    assert row["name"] == "j"
    assert row["direction"] == "upload"


def test_migrations_are_idempotent(tmp_path):
    path = tmp_path / "jobs.db"
    first = connect(path)
    first.execute(
        "INSERT INTO jobs (name, direction, source_root, dest_prefix, status, created_at)"
        " VALUES ('j', 'upload', 'C:/x', 'p', 'pending', '2026-08-04T00:00:00Z')"
    )
    first.commit()
    first.close()

    second = connect(path)
    apply_migrations(second)
    assert second.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 1
    assert second.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()["n"] == 1
    second.close()


def test_duplicate_relative_path_in_one_job_is_rejected(conn):
    conn.execute(
        "INSERT INTO jobs (id, name, direction, source_root, dest_prefix, status, created_at)"
        " VALUES (1, 'j', 'upload', 'C:/x', 'p', 'pending', '2026-08-04T00:00:00Z')"
    )
    insert = (
        "INSERT INTO job_files (job_id, relative_path, source_path, object_name,"
        " size_bytes, mtime_ns, method, state)"
        " VALUES (1, 'a.tif', 'C:/x/a.tif', 'p/a.tif', 1, 1, 'single_shot', 'pending')"
    )
    conn.execute(insert)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/store/test_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mml_cloud_transfer.store'`

- [ ] **Step 3: Write the schema**

Create `src/mml_cloud_transfer/store/__init__.py` (empty) and `src/mml_cloud_transfer/store/schema.py`:

```python
"""SQLite schema and migrations.

Enum values from ``core.models`` are stored as plain text. Every state
transition is committed, so a killed process loses at most the in-flight
chunk.
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE,
    project_id     TEXT NOT NULL,
    bucket         TEXT NOT NULL,
    auth_type      TEXT NOT NULL,
    credential_ref TEXT,
    default_prefix TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id                 INTEGER PRIMARY KEY,
    name               TEXT NOT NULL,
    direction          TEXT NOT NULL,
    profile_id         INTEGER REFERENCES profiles(id),
    source_root        TEXT NOT NULL,
    dest_prefix        TEXT NOT NULL,
    status             TEXT NOT NULL,
    audit_hash         INTEGER NOT NULL DEFAULT 0,
    scheduled_start_at TEXT,
    created_at         TEXT NOT NULL,
    started_at         TEXT,
    finished_at        TEXT,
    planned_files      INTEGER NOT NULL DEFAULT 0,
    planned_bytes      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS job_files (
    id                INTEGER PRIMARY KEY,
    job_id            INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    relative_path     TEXT NOT NULL,
    source_path       TEXT NOT NULL,
    object_name       TEXT NOT NULL,
    size_bytes        INTEGER NOT NULL,
    mtime_ns          INTEGER NOT NULL,
    method            TEXT NOT NULL,
    state             TEXT NOT NULL,
    local_crc32c      INTEGER,
    remote_crc32c     INTEGER,
    sha256            TEXT,
    generation        INTEGER,
    bytes_transferred INTEGER NOT NULL DEFAULT 0,
    attempts          INTEGER NOT NULL DEFAULT 0,
    error_category    TEXT,
    error_message     TEXT,
    heartbeat_at      TEXT,
    started_at        TEXT,
    finished_at       TEXT,
    UNIQUE (job_id, relative_path)
);

CREATE INDEX IF NOT EXISTS idx_job_files_state ON job_files (job_id, state);

CREATE TABLE IF NOT EXISTS file_slices (
    id                INTEGER PRIMARY KEY,
    file_id           INTEGER NOT NULL REFERENCES job_files(id) ON DELETE CASCADE,
    slice_index       INTEGER NOT NULL,
    offset_bytes      INTEGER NOT NULL,
    length_bytes      INTEGER NOT NULL,
    state             TEXT NOT NULL,
    session_uri       TEXT,
    temp_object       TEXT,
    crc32c            INTEGER,
    bytes_transferred INTEGER NOT NULL DEFAULT 0,
    UNIQUE (file_id, slice_index)
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY,
    job_id  INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    file_id INTEGER,
    at      TEXT NOT NULL,
    kind    TEXT NOT NULL,
    detail  TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_job ON events (job_id, id);
"""


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Create or upgrade the schema. Safe to call on every connect."""
    conn.executescript(_DDL)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    conn.commit()
```

- [ ] **Step 4: Write the connection factory**

Create `src/mml_cloud_transfer/store/db.py`:

```python
"""SQLite connection factory with the pragmas this application depends on."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from mml_cloud_transfer.store.schema import apply_migrations


def connect(path: str | os.PathLike[str]) -> sqlite3.Connection:
    """Open (creating if needed) the job database in WAL mode."""
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, isolation_level=None, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    apply_migrations(conn)
    return conn
```

Note `isolation_level=None` puts the connection in autocommit mode; the repository in Task 9 opens explicit transactions where atomicity matters. This is deliberate — it keeps state transitions durable by default.

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/store/test_schema.py -v`
Expected: PASS, 7 tests

- [ ] **Step 6: Commit**

```bash
git add src/mml_cloud_transfer/store/ tests/store/
git commit -m "feat: add SQLite schema and WAL connection factory"
```

---

### Task 9: Job repository

Owns every state transition. This is the module that makes resume work, so its crash-recovery behaviour is tested explicitly.

**Files:**
- Create: `src/mml_cloud_transfer/store/repository.py`
- Test: `tests/store/test_repository.py`

**Interfaces:**
- Consumes: `connect` from `store.db`; `Direction`, `FileState`, `JobStatus`, `PlannedFile`, `TransferMethod`, `TERMINAL_SUCCESS_STATES` from `core.models`; `choose_method` from `core.slicing`; `to_object_name` from `core.paths`; `ErrorCategory` from `core.errors`
- Produces: `JobRepository(conn)` with exactly these methods — `create_job`, `get_job`, `set_job_status`, `add_planned_files`, `get_files`, `iter_pending_files`, `mark_transferring`, `heartbeat`, `mark_verified`, `mark_skipped`, `mark_failed`, `mark_changed`, `quarantine`, `reset_stale_transfers`, `count_by_state`, `job_verdict`, `record_event`, `get_events`

- [ ] **Step 1: Write the failing test**

Create `tests/store/test_repository.py`:

```python
import pytest

from mml_cloud_transfer.core.errors import ErrorCategory
from mml_cloud_transfer.core.models import (
    Direction,
    FileState,
    JobStatus,
    PlannedFile,
    TransferMethod,
)
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

GIB = 1024**3


def make_files(n: int, size: int = 100) -> list[PlannedFile]:
    return [
        PlannedFile(
            relative_path=f"run47/file{i}.tif",
            source_path=rf"C:\data\run47\file{i}.tif",
            size_bytes=size,
            mtime_ns=1_700_000_000_000_000_000 + i,
        )
        for i in range(n)
    ]


@pytest.fixture
def repo(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    yield JobRepository(conn)
    conn.close()


def test_create_job_returns_an_id_and_pending_status(repo):
    job_id = repo.create_job(
        name="Run 47",
        direction=Direction.UPLOAD,
        source_root=r"\\nas01\imaging\run47",
        dest_prefix="archive/run47",
    )
    job = repo.get_job(job_id)
    assert job["status"] == JobStatus.PENDING.value
    assert job["dest_prefix"] == "archive/run47"


def test_add_planned_files_sets_object_name_and_method(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix="archive"
    )
    repo.add_planned_files(job_id, make_files(1))
    row = repo.get_files(job_id)[0]
    assert row["object_name"] == "archive/run47/file0.tif"
    assert row["method"] == TransferMethod.SINGLE_SHOT.value
    assert row["state"] == FileState.PENDING.value


def test_large_files_are_planned_as_sliced(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1, size=40 * GIB))
    assert repo.get_files(job_id)[0]["method"] == TransferMethod.SLICED.value


def test_add_planned_files_updates_job_totals(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(5, size=200))
    job = repo.get_job(job_id)
    assert job["planned_files"] == 5
    assert job["planned_bytes"] == 1000


def test_add_planned_files_is_idempotent_for_the_same_path(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(3))
    repo.add_planned_files(job_id, make_files(3))
    assert len(repo.get_files(job_id)) == 3
    assert repo.get_job(job_id)["planned_files"] == 3


def test_iter_pending_files_skips_finished_work(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(4))
    files = repo.get_files(job_id)
    repo.mark_verified(files[0]["id"], local_crc32c=1, remote_crc32c=1, generation=7)
    repo.mark_skipped(files[1]["id"])

    remaining = [f["relative_path"] for f in repo.iter_pending_files(job_id)]
    assert remaining == ["run47/file2.tif", "run47/file3.tif"]


def test_mark_verified_records_checksums_and_generation(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1))
    file_id = repo.get_files(job_id)[0]["id"]
    repo.mark_verified(file_id, local_crc32c=42, remote_crc32c=42, generation=99, sha256="ab")

    row = repo.get_files(job_id)[0]
    assert row["state"] == FileState.VERIFIED.value
    assert row["local_crc32c"] == 42
    assert row["remote_crc32c"] == 42
    assert row["generation"] == 99
    assert row["sha256"] == "ab"
    assert row["finished_at"] is not None


def test_mark_failed_increments_attempts_and_stores_the_category(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1))
    file_id = repo.get_files(job_id)[0]["id"]

    repo.mark_failed(file_id, ErrorCategory.FILE_LOCKED, "in use")
    repo.mark_failed(file_id, ErrorCategory.FILE_LOCKED, "in use")

    row = repo.get_files(job_id)[0]
    assert row["attempts"] == 2
    assert row["error_category"] == ErrorCategory.FILE_LOCKED.value
    assert row["state"] == FileState.FAILED.value


def test_failed_files_are_retried_on_the_next_run(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1))
    file_id = repo.get_files(job_id)[0]["id"]
    repo.mark_failed(file_id, ErrorCategory.NETWORK, "dropped")

    assert [f["id"] for f in repo.iter_pending_files(job_id)] == [file_id]


def test_quarantined_files_are_not_retried(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1))
    file_id = repo.get_files(job_id)[0]["id"]
    repo.quarantine(file_id)

    assert list(repo.iter_pending_files(job_id)) == []


def test_reset_stale_transfers_recovers_from_a_crash(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(2))
    files = repo.get_files(job_id)
    repo.mark_transferring(files[0]["id"])
    repo.mark_transferring(files[1]["id"])
    repo.mark_verified(files[1]["id"], local_crc32c=1, remote_crc32c=1, generation=1)

    # Simulate the service dying: file 0 is stuck in 'transferring'.
    recovered = repo.reset_stale_transfers(job_id, stale_after_seconds=0)

    assert recovered == 1
    assert repo.get_files(job_id)[0]["state"] == FileState.PENDING.value
    assert repo.get_files(job_id)[1]["state"] == FileState.VERIFIED.value


def test_count_by_state(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(3))
    files = repo.get_files(job_id)
    repo.mark_verified(files[0]["id"], local_crc32c=1, remote_crc32c=1, generation=1)
    repo.mark_failed(files[1]["id"], ErrorCategory.NETWORK, "x")

    counts = repo.count_by_state(job_id)
    assert counts[FileState.VERIFIED] == 1
    assert counts[FileState.FAILED] == 1
    assert counts[FileState.PENDING] == 1


def test_verdict_is_incomplete_until_every_file_succeeds(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(2))
    files = repo.get_files(job_id)

    assert repo.job_verdict(job_id) is JobStatus.INCOMPLETE
    repo.mark_verified(files[0]["id"], local_crc32c=1, remote_crc32c=1, generation=1)
    assert repo.job_verdict(job_id) is JobStatus.INCOMPLETE
    repo.mark_skipped(files[1]["id"])
    assert repo.job_verdict(job_id) is JobStatus.COMPLETE


def test_an_empty_job_is_complete(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    assert repo.job_verdict(job_id) is JobStatus.COMPLETE


def test_events_are_appended_in_order(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.record_event(job_id, "scan_started", "root=C:/data")
    repo.record_event(job_id, "scan_finished", "files=3")

    kinds = [e["kind"] for e in repo.get_events(job_id)]
    assert kinds == ["scan_started", "scan_finished"]


def test_state_survives_reopening_the_database(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    repo = JobRepository(conn)
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(2))
    repo.mark_verified(
        repo.get_files(job_id)[0]["id"], local_crc32c=5, remote_crc32c=5, generation=1
    )
    conn.close()

    reopened = connect(tmp_path / "jobs.db")
    repo2 = JobRepository(reopened)
    assert repo2.count_by_state(job_id)[FileState.VERIFIED] == 1
    assert len(list(repo2.iter_pending_files(job_id))) == 1
    reopened.close()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/store/test_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mml_cloud_transfer.store.repository'`

- [ ] **Step 3: Write the minimal implementation**

Create `src/mml_cloud_transfer/store/repository.py`:

```python
"""All job and file state transitions live here.

Resume is not a special mode: it is simply ``iter_pending_files`` returning
everything that is not yet verified, skipped, or quarantined.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime

from mml_cloud_transfer.core.errors import ErrorCategory
from mml_cloud_transfer.core.models import (
    Direction,
    FileState,
    JobStatus,
    PlannedFile,
)
from mml_cloud_transfer.core.paths import to_object_name
from mml_cloud_transfer.core.slicing import choose_method

#: States that will never be retried by a resume.
_NOT_RETRIED = (
    FileState.VERIFIED.value,
    FileState.SKIPPED.value,
    FileState.QUARANTINED.value,
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class JobRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ---- jobs -----------------------------------------------------------

    def create_job(
        self,
        *,
        name: str,
        direction: Direction,
        source_root: str,
        dest_prefix: str,
        profile_id: int | None = None,
        audit_hash: bool = False,
        scheduled_start_at: str | None = None,
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO jobs (name, direction, profile_id, source_root, dest_prefix,"
            " status, audit_hash, scheduled_start_at, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                direction.value,
                profile_id,
                source_root,
                dest_prefix,
                JobStatus.PENDING.value,
                int(audit_hash),
                scheduled_start_at,
                _now(),
            ),
        )
        return int(cursor.lastrowid)

    def get_job(self, job_id: int) -> sqlite3.Row:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise LookupError(f"no job with id {job_id}")
        return row

    def set_job_status(self, job_id: int, status: JobStatus) -> None:
        self._conn.execute(
            "UPDATE jobs SET status = ? WHERE id = ?", (status.value, job_id)
        )

    # ---- planning -------------------------------------------------------

    def add_planned_files(self, job_id: int, files: Iterable[PlannedFile]) -> int:
        """Insert planned files, ignoring any already present. Returns rows added."""
        job = self.get_job(job_id)
        prefix = job["dest_prefix"]

        rows = [
            (
                job_id,
                f.relative_path,
                f.source_path,
                to_object_name(prefix, f.relative_path),
                f.size_bytes,
                f.mtime_ns,
                choose_method(f.size_bytes).value,
                FileState.PENDING.value,
            )
            for f in files
        ]

        self._conn.execute("BEGIN")
        try:
            before = self._conn.total_changes
            self._conn.executemany(
                "INSERT OR IGNORE INTO job_files (job_id, relative_path, source_path,"
                " object_name, size_bytes, mtime_ns, method, state)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            added = self._conn.total_changes - before
            self._conn.execute(
                "UPDATE jobs SET"
                " planned_files = (SELECT COUNT(*) FROM job_files WHERE job_id = ?),"
                " planned_bytes = (SELECT COALESCE(SUM(size_bytes), 0) FROM job_files"
                "                  WHERE job_id = ?)"
                " WHERE id = ?",
                (job_id, job_id, job_id),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return added

    def get_files(self, job_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM job_files WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()

    def iter_pending_files(self, job_id: int) -> Iterator[sqlite3.Row]:
        placeholders = ", ".join("?" for _ in _NOT_RETRIED)
        yield from self._conn.execute(
            f"SELECT * FROM job_files WHERE job_id = ?"
            f" AND state NOT IN ({placeholders}) ORDER BY id",
            (job_id, *_NOT_RETRIED),
        )

    # ---- file state transitions ----------------------------------------

    def mark_transferring(self, file_id: int) -> None:
        now = _now()
        self._conn.execute(
            "UPDATE job_files SET state = ?, heartbeat_at = ?,"
            " started_at = COALESCE(started_at, ?) WHERE id = ?",
            (FileState.TRANSFERRING.value, now, now, file_id),
        )

    def heartbeat(self, file_id: int, bytes_transferred: int) -> None:
        self._conn.execute(
            "UPDATE job_files SET heartbeat_at = ?, bytes_transferred = ? WHERE id = ?",
            (_now(), bytes_transferred, file_id),
        )

    def mark_verified(
        self,
        file_id: int,
        *,
        local_crc32c: int,
        remote_crc32c: int,
        generation: int,
        sha256: str | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE job_files SET state = ?, local_crc32c = ?, remote_crc32c = ?,"
            " generation = ?, sha256 = ?, error_category = NULL, error_message = NULL,"
            " heartbeat_at = NULL, finished_at = ? WHERE id = ?",
            (
                FileState.VERIFIED.value,
                local_crc32c,
                remote_crc32c,
                generation,
                sha256,
                _now(),
                file_id,
            ),
        )

    def mark_skipped(self, file_id: int) -> None:
        self._conn.execute(
            "UPDATE job_files SET state = ?, heartbeat_at = NULL, finished_at = ?"
            " WHERE id = ?",
            (FileState.SKIPPED.value, _now(), file_id),
        )

    def mark_failed(self, file_id: int, category: ErrorCategory, message: str) -> None:
        self._conn.execute(
            "UPDATE job_files SET state = ?, attempts = attempts + 1,"
            " error_category = ?, error_message = ?, heartbeat_at = NULL WHERE id = ?",
            (FileState.FAILED.value, category.value, message, file_id),
        )

    def mark_changed(self, file_id: int, size_bytes: int, mtime_ns: int) -> None:
        self._conn.execute(
            "UPDATE job_files SET state = ?, size_bytes = ?, mtime_ns = ?,"
            " local_crc32c = NULL, bytes_transferred = 0, heartbeat_at = NULL"
            " WHERE id = ?",
            (FileState.CHANGED.value, size_bytes, mtime_ns, file_id),
        )

    def quarantine(self, file_id: int) -> None:
        self._conn.execute(
            "UPDATE job_files SET state = ?, heartbeat_at = NULL, finished_at = ?"
            " WHERE id = ?",
            (FileState.QUARANTINED.value, _now(), file_id),
        )

    def reset_stale_transfers(self, job_id: int, *, stale_after_seconds: int = 300) -> int:
        """Return files stranded in 'transferring' by a crash to 'pending'."""
        cursor = self._conn.execute(
            "UPDATE job_files SET state = ?, heartbeat_at = NULL, bytes_transferred = 0"
            " WHERE job_id = ? AND state = ?"
            " AND (heartbeat_at IS NULL"
            "      OR (julianday('now') - julianday(heartbeat_at)) * 86400.0 >= ?)",
            (
                FileState.PENDING.value,
                job_id,
                FileState.TRANSFERRING.value,
                stale_after_seconds,
            ),
        )
        return cursor.rowcount

    # ---- reporting ------------------------------------------------------

    def count_by_state(self, job_id: int) -> dict[FileState, int]:
        rows = self._conn.execute(
            "SELECT state, COUNT(*) AS n FROM job_files WHERE job_id = ? GROUP BY state",
            (job_id,),
        ).fetchall()
        return {FileState(r["state"]): r["n"] for r in rows}

    def job_verdict(self, job_id: int) -> JobStatus:
        """COMPLETE only when every planned file is verified or skipped."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM job_files WHERE job_id = ? AND state NOT IN (?, ?)",
            (job_id, FileState.VERIFIED.value, FileState.SKIPPED.value),
        ).fetchone()
        return JobStatus.COMPLETE if row["n"] == 0 else JobStatus.INCOMPLETE

    # ---- events ---------------------------------------------------------

    def record_event(
        self, job_id: int, kind: str, detail: str | None = None, file_id: int | None = None
    ) -> None:
        self._conn.execute(
            "INSERT INTO events (job_id, file_id, at, kind, detail) VALUES (?, ?, ?, ?, ?)",
            (job_id, file_id, _now(), kind, detail),
        )

    def get_events(self, job_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM events WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/store/test_repository.py -v`
Expected: PASS, 16 tests

- [ ] **Step 5: Run the whole suite**

Run: `.venv/Scripts/python -m pytest -v`
Expected: PASS, all tests from Tasks 1–9

- [ ] **Step 6: Commit**

```bash
git add src/mml_cloud_transfer/store/repository.py tests/store/test_repository.py
git commit -m "feat: add job repository with crash-recovery and verdict rules"
```

---

### Task 10: The `mmlct scan` command

Ties everything together into the phase deliverable: walk a tree, persist a manifest, print a summary, and export the manifest as CSV.

**Files:**
- Create: `src/mml_cloud_transfer/cli/__init__.py`
- Create: `src/mml_cloud_transfer/cli/__main__.py`
- Create: `src/mml_cloud_transfer/cli/scan_command.py`
- Create: `tests/cli/__init__.py`
- Test: `tests/cli/test_scan_command.py`

**Interfaces:**
- Consumes: `iter_source` from `core.scanner`; `JobRepository` from `store.repository`; `connect` from `store.db`; `resolve_mapped_drive` from `core.paths`; `PlannedFile` from `core.models`; `ScanError` from `core.errors`
- Produces: `run_scan(*, db_path, source_root, dest_prefix, job_name, job_id=None, csv_path=None, resolver=default_drive_resolver, follow_extended=True) -> ScanOutcome` (every argument is keyword-only); `ScanOutcome(job_id: int, file_count: int, byte_count: int, errors: list[ScanError])`; `CSV_COLUMNS: list[str]`; `main(argv: Sequence[str] | None = None) -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/cli/__init__.py` (empty) and `tests/cli/test_scan_command.py`:

```python
import csv

import pytest

from mml_cloud_transfer.cli.scan_command import run_scan
from mml_cloud_transfer.cli.__main__ import main
from mml_cloud_transfer.core.models import FileState
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


@pytest.fixture
def tree(tmp_path):
    src = tmp_path / "src"
    (src / "run47").mkdir(parents=True)
    (src / "top.txt").write_bytes(b"a" * 10)
    (src / "run47" / "a.tif").write_bytes(b"b" * 20)
    return src


def test_scan_persists_a_manifest(tmp_path, tree):
    db = tmp_path / "jobs.db"
    outcome = run_scan(
        db_path=db,
        source_root=str(tree),
        dest_prefix="archive/run47",
        job_name="Run 47",
        follow_extended=False,
    )

    assert outcome.file_count == 2
    assert outcome.byte_count == 30
    assert outcome.errors == []

    conn = connect(db)
    repo = JobRepository(conn)
    rows = repo.get_files(outcome.job_id)
    assert sorted(r["object_name"] for r in rows) == [
        "archive/run47/run47/a.tif",
        "archive/run47/top.txt",
    ]
    assert all(r["state"] == FileState.PENDING.value for r in rows)
    conn.close()


def test_scan_records_start_and_finish_events(tmp_path, tree):
    db = tmp_path / "jobs.db"
    outcome = run_scan(
        db_path=db,
        source_root=str(tree),
        dest_prefix="",
        job_name="j",
        follow_extended=False,
    )
    conn = connect(db)
    kinds = [e["kind"] for e in JobRepository(conn).get_events(outcome.job_id)]
    assert kinds == ["scan_started", "scan_finished"]
    conn.close()


def test_scan_is_resumable_and_does_not_duplicate_rows(tmp_path, tree):
    db = tmp_path / "jobs.db"
    first = run_scan(
        db_path=db,
        source_root=str(tree),
        dest_prefix="",
        job_name="j",
        follow_extended=False,
    )
    second = run_scan(
        db_path=db,
        source_root=str(tree),
        dest_prefix="",
        job_name="j",
        follow_extended=False,
        job_id=first.job_id,
    )

    assert second.job_id == first.job_id
    conn = connect(db)
    assert len(JobRepository(conn).get_files(first.job_id)) == 2
    conn.close()


def test_scan_exports_csv(tmp_path, tree):
    db = tmp_path / "jobs.db"
    csv_path = tmp_path / "manifest.csv"
    run_scan(
        db_path=db,
        source_root=str(tree),
        dest_prefix="p",
        job_name="j",
        csv_path=csv_path,
        follow_extended=False,
    )

    with csv_path.open(newline="", encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))

    assert len(rows) == 2
    assert set(rows[0]) == {
        "relative_path",
        "object_name",
        "size_bytes",
        "mtime_ns",
        "method",
        "state",
    }


def test_scan_reports_a_missing_source_without_crashing(tmp_path):
    outcome = run_scan(
        db_path=tmp_path / "jobs.db",
        source_root=str(tmp_path / "nope"),
        dest_prefix="",
        job_name="j",
        follow_extended=False,
    )
    assert outcome.file_count == 0
    assert len(outcome.errors) == 1


def test_main_returns_zero_on_success(tmp_path, tree, capsys):
    code = main(
        [
            "scan",
            "--db", str(tmp_path / "jobs.db"),
            "--source", str(tree),
            "--prefix", "archive",
            "--name", "Run 47",
            "--no-extended-paths",
        ]
    )
    assert code == 0
    assert "2 files" in capsys.readouterr().out


def test_main_returns_nonzero_when_the_scan_had_errors(tmp_path, capsys):
    code = main(
        [
            "scan",
            "--db", str(tmp_path / "jobs.db"),
            "--source", str(tmp_path / "nope"),
            "--prefix", "",
            "--name", "j",
            "--no-extended-paths",
        ]
    )
    assert code == 1
    assert "error" in capsys.readouterr().out.lower()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/cli/test_scan_command.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mml_cloud_transfer.cli'`

- [ ] **Step 3: Write the scan command**

Create `src/mml_cloud_transfer/cli/__init__.py` (empty) and `src/mml_cloud_transfer/cli/scan_command.py`:

```python
"""The scan half of a transfer job: build the manifest before any bytes move."""

from __future__ import annotations

import csv
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mml_cloud_transfer.core.errors import ScanError
from mml_cloud_transfer.core.models import Direction, JobStatus, PlannedFile
from mml_cloud_transfer.core.paths import default_drive_resolver, resolve_mapped_drive
from mml_cloud_transfer.core.scanner import iter_source
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

_BATCH_SIZE = 5000

CSV_COLUMNS = [
    "relative_path",
    "object_name",
    "size_bytes",
    "mtime_ns",
    "method",
    "state",
]


@dataclass(slots=True)
class ScanOutcome:
    job_id: int
    file_count: int
    byte_count: int
    errors: list[ScanError] = field(default_factory=list)


def run_scan(
    *,
    db_path: str | os.PathLike[str],
    source_root: str,
    dest_prefix: str,
    job_name: str,
    job_id: int | None = None,
    csv_path: str | os.PathLike[str] | None = None,
    resolver: Callable[[str], str | None] = default_drive_resolver,
    follow_extended: bool = True,
) -> ScanOutcome:
    """Scan ``source_root`` into a job manifest, creating the job if needed."""
    root = resolve_mapped_drive(source_root, resolver)

    conn = connect(db_path)
    try:
        repo = JobRepository(conn)
        if job_id is None:
            job_id = repo.create_job(
                name=job_name,
                direction=Direction.UPLOAD,
                source_root=root,
                dest_prefix=dest_prefix,
            )

        repo.set_job_status(job_id, JobStatus.SCANNING)
        repo.record_event(job_id, "scan_started", f"root={root}")

        errors: list[ScanError] = []
        file_count = 0
        byte_count = 0
        batch: list[PlannedFile] = []

        for entry in iter_source(root, follow_extended=follow_extended):
            if isinstance(entry, ScanError):
                errors.append(entry)
                continue
            batch.append(entry)
            file_count += 1
            byte_count += entry.size_bytes
            if len(batch) >= _BATCH_SIZE:
                repo.add_planned_files(job_id, batch)
                batch.clear()

        if batch:
            repo.add_planned_files(job_id, batch)

        repo.record_event(
            job_id, "scan_finished", f"files={file_count} bytes={byte_count} errors={len(errors)}"
        )
        repo.set_job_status(job_id, JobStatus.PENDING)

        if csv_path is not None:
            _write_csv(repo, job_id, csv_path)

        return ScanOutcome(
            job_id=job_id, file_count=file_count, byte_count=byte_count, errors=errors
        )
    finally:
        conn.close()


def _write_csv(repo: JobRepository, job_id: int, csv_path: str | os.PathLike[str]) -> None:
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in repo.get_files(job_id):
            writer.writerow({column: row[column] for column in CSV_COLUMNS})
```

- [ ] **Step 4: Write the CLI entry point**

Create `src/mml_cloud_transfer/cli/__main__.py`:

```python
"""Command-line entry point.

Exists so the engine is testable and scriptable long before there is a GUI.
Later phases add transfer, resume, and report subcommands here.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from mml_cloud_transfer.cli.scan_command import run_scan


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mmlct", description="MML Cloud Transfer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Build a transfer manifest from a folder")
    scan.add_argument("--db", required=True, help="Path to the job database")
    scan.add_argument("--source", required=True, help="Folder to scan")
    scan.add_argument("--prefix", default="", help="Destination object-name prefix")
    scan.add_argument("--name", required=True, help="Job name")
    scan.add_argument("--job-id", type=int, default=None, help="Re-scan an existing job")
    scan.add_argument("--csv", default=None, help="Also write the manifest to this CSV path")
    scan.add_argument(
        "--no-extended-paths",
        action="store_true",
        help=r"Do not rewrite paths to \\?\ form (used by tests on non-Windows hosts)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "scan":
        outcome = run_scan(
            db_path=args.db,
            source_root=args.source,
            dest_prefix=args.prefix,
            job_name=args.name,
            job_id=args.job_id,
            csv_path=args.csv,
            follow_extended=not args.no_extended_paths,
        )
        gib = outcome.byte_count / 1024**3
        print(f"Job {outcome.job_id}: {outcome.file_count} files, {gib:.2f} GiB")
        if outcome.errors:
            print(f"{len(outcome.errors)} error(s) during scan:")
            for error in outcome.errors[:20]:
                print(f"  [{error.category.value}] {error.message}")
            if len(outcome.errors) > 20:
                print(f"  ... and {len(outcome.errors) - 20} more")
            return 1
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/cli/test_scan_command.py -v`
Expected: PASS, 7 tests

- [ ] **Step 6: Verify the command works end to end**

Run:

```bash
.venv/Scripts/mmlct scan --db .\scratch\jobs.db --source .\src --prefix archive/src --name "Source tree" --csv .\scratch\manifest.csv
```

Expected: prints a job id, a file count, and a size in GiB; `scratch/manifest.csv` contains one row per Python file with `state` = `pending`.

- [ ] **Step 7: Run the whole suite with coverage**

Run: `.venv/Scripts/python -m pytest --cov=mml_cloud_transfer --cov-report=term-missing`
Expected: PASS. `core/crc32c_combine.py`, `core/slicing.py`, and `store/repository.py` should each be at or near 100% — these are the correctness-critical modules. Anything materially below that means a behaviour is untested.

- [ ] **Step 8: Commit**

```bash
git add src/mml_cloud_transfer/cli/ tests/cli/
git commit -m "feat: add mmlct scan command producing a persisted manifest"
```

---

## Phase Complete

At this point `mmlct scan` walks a tree of any size and writes a complete, queryable manifest, and the correctness-critical logic — CRC32C combination, slice planning, verdict rules, crash recovery — is under test without needing a network or a bucket.

**Plan 2 picks up from here** with the transfer engine and the Windows Service: `gcs/` uploader, downloader, and verifier across all three size paths, the job runner, report generation, and the interrupt-and-resume test against a real bucket.
