# MML Cloud Transfer — Plan 2: Transfer Engine

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the verified, resumable transfer engine: `mmlct transfer` moves a directory tree to or from a GCS bucket across all three size paths, survives being killed mid-flight, resumes with `mmlct resume`, and ends every job with an audited COMPLETE/INCOMPLETE report.

**Architecture:** A `gcs` layer owns all network I/O: a client/session factory, object-metadata helpers, a hand-rolled resumable-session protocol (initiate via the client library, chunk PUTs via a raw authorized session — because we must persist session URIs across process death, resume from the server's committed offset, heartbeat per chunk, and fault-inject), an uploader for the three size paths, and a ranged resumable downloader. An `engine` layer orchestrates: per-file dispatch with retry/backoff, source-change detection, the three verification layers, the Layer-3 audit, and report generation. The CLI grows `transfer`, `resume`, `status`, and `report` subcommands. Plan 1's `core` and `store` are consumed unchanged except for small additive extensions (Task 1).

**Tech Stack:** Python 3.12, `google-cloud-storage` ≥3.0 (client, sessions, compose), `google-auth` (AuthorizedSession), `requests`, `google-crc32c`, stdlib `sqlite3`/`argparse`/`concurrent.futures`, pytest, fake-gcs-server (emulator).

**Spec:** [2026-08-04-gcs-transfer-manager-design.md](../specs/2026-08-04-gcs-transfer-manager-design.md) — Phase 2. The Windows Service is Plan 3; auth profiles/DPAPI are Plan 4. This plan authenticates via Application Default Credentials or an explicit `--credentials` key-file path.

## Global Constraints

These apply to every task. Do not restate them; do not violate them.

- **Python 3.12** (`py -3.12 -m venv`; interpreter `.venv/Scripts/python`).
- **`core` stays pure**: no imports from `google.cloud.storage`, `google.auth`, `requests`, PySide6, or `sqlite3` anywhere under `core/`.
- **`google.cloud.storage` / `google.auth` / `requests` are imported ONLY under `gcs/`** and in test files. `engine/` and `cli/` reach GCS exclusively through `gcs` functions.
- **Verification is non-negotiable.** A file reaches `verified` only after the finalized object's CRC32C equals the locally computed whole-file CRC32C AND its size matches AND its generation is recorded (Layer 2). A job is COMPLETE only if every planned file is `verified` or `skipped` AND the Layer-3 audit reconciles.
- **Skip rule:** destination exists with matching size and CRC32C → `skipped` (both directions).
- **Preconditions:** every finalize that creates or replaces a destination object carries `if_generation_match` from `job_files.precondition_generation` (0 = must not exist). A 412 is classified `conflict`, never blindly retried.
- **Retry:** transient errors retry with exponential backoff + full jitter, at most **5 attempts per file per run**; a credential error (401/403) **pauses the whole job**; a file whose cumulative `attempts` ≥ **15** is quarantined.
- **Timestamps** are UTC ISO-8601 with seconds precision, matching `store.repository._now()`.
- **Threads, not processes** — a documented deviation from the spec's "process pool" line: all heavy I/O is HTTP (GIL released in sockets) and hashing uses the `google-crc32c` C extension; threads avoid pickling repository/session state. Each worker thread opens its own SQLite connection (`store.db.connect`) — WAL plus `busy_timeout=30000` makes this safe.
- **Resumable-session chunks** must be multiples of 256 KiB except the final chunk; the default chunk is 8 MiB.
- **Emulator tests** (marker `emulator`) run against fake-gcs-server and skip cleanly, with an actionable message, when `tools/fake-gcs-server.exe` is absent. **Real-bucket tests** (marker `real_bucket`) skip unless `MMLCT_TEST_BUCKET` is set; per the spec they are a **release gate, not optional** — run them before calling this plan done.
- **TDD throughout.** For network code, RED runs against the in-repo stub session or the emulator — never against a real bucket.

## File Structure

```text
src/mml_cloud_transfer/
  core/retry.py            backoff schedule + attempt bookkeeping (pure)
  core/paths.py            + display_path()                       (modify)
  core/slicing.py          + SizePolicy, policy-aware functions   (modify)
  core/crc32c_combine.py   + combine_all docstring note           (modify)
  store/repository.py      + policy passthrough, audit helpers    (modify)
  gcs/__init__.py
  gcs/client.py            GcsContext factory (client + session + endpoint)
  gcs/objects.py           ObjectMeta, get/list/delete, GcsHttpError
  gcs/resumable.py         session protocol: initiate / query_offset / put_chunk
  gcs/uploader.py          upload_file: single-shot / resumable / sliced+compose
  gcs/downloader.py        download_file: ranged, resumable, atomic rename
  engine/__init__.py
  engine/runner.py         run_job: dispatch, retry, changed-detection, audit, verdict
  engine/report.py         summary.json / manifest.csv / report.html
  cli/transfer_command.py  transfer / resume / status / report wiring
  cli/__main__.py          new subcommands                        (modify)
tests/
  conftest.py              emulator fixture + markers             (create)
  tools/get-fake-gcs-server.ps1
  gcs/  engine/  cli/      per-module test files
```

---

### Task 1: Core and store extensions

Small additive changes the rest of the plan builds on: a pure retry schedule, `display_path()` (final-review carry-forward — `\\?\`-prefixed paths must never reach users), a `SizePolicy` so tests can shrink the size thresholds without monkeypatching, and the repository passthrough for it.

**Files:**
- Create: `src/mml_cloud_transfer/core/retry.py`
- Modify: `src/mml_cloud_transfer/core/paths.py` (add `display_path`)
- Modify: `src/mml_cloud_transfer/core/slicing.py` (add `SizePolicy`; thread it through `choose_method` / `plan_slices`)
- Modify: `src/mml_cloud_transfer/core/crc32c_combine.py` (docstring only)
- Modify: `src/mml_cloud_transfer/store/repository.py` (policy passthrough on `add_planned_files`)
- Test: `tests/core/test_retry.py`, additions to `tests/core/test_paths.py`, `tests/core/test_slicing.py`, `tests/store/test_repository.py`

**Interfaces:**
- Consumes: existing `choose_method`, `plan_slices`, constants from `core.slicing`; `JobRepository.add_planned_files`.
- Produces: `RetrySchedule(max_attempts: int = 5, base_delay: float = 1.0, factor: float = 2.0, max_delay: float = 60.0)` with `delays(rng: random.Random) -> Iterator[float]` (yields `max_attempts - 1` jittered sleeps); `QUARANTINE_ATTEMPTS = 15`; `display_path(path: str) -> str`; `SizePolicy(single_shot_max: int, resumable_max: int, min_slice: int, max_components: int)` with classmethod `default()`; `choose_method(size_bytes, *, policy: SizePolicy | None = None)`; `plan_slices(size_bytes, *, policy: SizePolicy | None = None)`; `JobRepository.add_planned_files(job_id, files, *, policy: SizePolicy | None = None)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_retry.py`:

```python
import random

from mml_cloud_transfer.core.retry import QUARANTINE_ATTEMPTS, RetrySchedule


def test_defaults_match_the_spec():
    schedule = RetrySchedule()
    assert schedule.max_attempts == 5
    assert QUARANTINE_ATTEMPTS == 15


def test_yields_one_fewer_delays_than_attempts():
    delays = list(RetrySchedule().delays(random.Random(0)))
    assert len(delays) == 4


def test_delays_grow_exponentially_and_are_capped():
    schedule = RetrySchedule(max_attempts=6, base_delay=1.0, factor=2.0, max_delay=5.0)
    # With full jitter each delay is uniform in [0, min(cap, base * factor**n)].
    for seed in range(20):
        delays = list(schedule.delays(random.Random(seed)))
        assert len(delays) == 5
        for n, delay in enumerate(delays):
            assert 0.0 <= delay <= min(5.0, 1.0 * 2.0**n)


def test_jitter_actually_varies():
    a = list(RetrySchedule().delays(random.Random(1)))
    b = list(RetrySchedule().delays(random.Random(2)))
    assert a != b
```

Append to `tests/core/test_paths.py`:

```python
def test_display_path_strips_the_extended_prefix():
    from mml_cloud_transfer.core.paths import display_path

    assert display_path("\\\\?\\C:\\data\\run47") == "C:\\data\\run47"
    assert display_path("\\\\?\\UNC\\nas01\\imaging") == "\\\\nas01\\imaging"
    assert display_path(r"C:\data\run47") == "C:\\data\\run47"
    assert display_path("archive/run47/a.tif") == "archive/run47/a.tif"
```

Append to `tests/core/test_slicing.py`:

```python
def test_size_policy_default_matches_module_constants():
    from mml_cloud_transfer.core.slicing import (
        MAX_COMPONENTS,
        MIN_SLICE_BYTES,
        RESUMABLE_MAX_BYTES,
        SINGLE_SHOT_MAX_BYTES,
        SizePolicy,
    )

    policy = SizePolicy.default()
    assert policy.single_shot_max == SINGLE_SHOT_MAX_BYTES
    assert policy.resumable_max == RESUMABLE_MAX_BYTES
    assert policy.min_slice == MIN_SLICE_BYTES
    assert policy.max_components == MAX_COMPONENTS


def test_tiny_policy_reroutes_methods_and_slices():
    from mml_cloud_transfer.core.slicing import SizePolicy, choose_method, plan_slices

    tiny = SizePolicy(
        single_shot_max=64 * 1024,
        resumable_max=256 * 1024,
        min_slice=256 * 1024,
        max_components=32,
    )
    assert choose_method(64 * 1024, policy=tiny) is TransferMethod.SINGLE_SHOT
    assert choose_method(64 * 1024 + 1, policy=tiny) is TransferMethod.RESUMABLE
    assert choose_method(256 * 1024 + 1, policy=tiny) is TransferMethod.SLICED

    slices = plan_slices(1024 * 1024, policy=tiny)
    assert len(slices) == 4
    assert sum(s.length for s in slices) == 1024 * 1024
    assert all(s.length == 256 * 1024 for s in slices)


def test_omitting_policy_behaves_exactly_as_before():
    from mml_cloud_transfer.core.slicing import choose_method, plan_slices

    assert choose_method(8 * MIB) is TransferMethod.SINGLE_SHOT
    assert plan_slices(2 * GIB)[0].length == GIB
```

Append to `tests/store/test_repository.py`:

```python
def test_add_planned_files_honours_a_size_policy(repo):
    from mml_cloud_transfer.core.slicing import SizePolicy

    tiny = SizePolicy(
        single_shot_max=10,
        resumable_max=50,
        min_slice=50,
        max_components=32,
    )
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1, size=100), policy=tiny)
    assert repo.get_files(job_id)[0]["method"] == TransferMethod.SLICED.value
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/core/test_retry.py tests/core/test_paths.py tests/core/test_slicing.py tests/store/test_repository.py -v`
Expected: FAIL — `ModuleNotFoundError` for `core.retry`; `ImportError` for `display_path` and `SizePolicy`; `TypeError` for the `policy=` keyword.

- [ ] **Step 3: Implement**

Create `src/mml_cloud_transfer/core/retry.py`:

```python
"""Retry schedule — pure computation, no sleeping.

The engine decides *whether* to retry (via core.errors.classify) and does
the sleeping; this module only answers "how long". Full jitter: each delay
is uniform in [0, min(max_delay, base * factor**n)], which avoids thundering
herds when many files fail together.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass

#: Cumulative attempts across runs after which a file is quarantined
#: (5 attempts per run x 3 runs, per the spec).
QUARANTINE_ATTEMPTS = 15


@dataclass(frozen=True, slots=True)
class RetrySchedule:
    max_attempts: int = 5
    base_delay: float = 1.0
    factor: float = 2.0
    max_delay: float = 60.0

    def delays(self, rng: random.Random) -> Iterator[float]:
        """Yield the sleep before each retry: max_attempts - 1 values."""
        for n in range(self.max_attempts - 1):
            ceiling = min(self.max_delay, self.base_delay * self.factor**n)
            yield rng.uniform(0.0, ceiling)
```

Append to `src/mml_cloud_transfer/core/paths.py`:

```python
def display_path(path: str) -> str:
    """Return ``path`` without the ``\\\\?\\`` machinery, for human eyes.

    Storage and filesystem access keep the extended form; anything shown to
    a user (errors, reports, logs) goes through here.
    """
    if path.startswith(_EXTENDED_UNC_PREFIX):
        return "\\\\" + path[len(_EXTENDED_UNC_PREFIX) :]
    if path.startswith(_EXTENDED_PREFIX):
        return path[len(_EXTENDED_PREFIX) :]
    return path
```

In `src/mml_cloud_transfer/core/slicing.py`, add after the constants:

```python
@dataclass(frozen=True, slots=True)
class SizePolicy:
    """Size thresholds for method selection and slicing.

    Production always uses ``default()``. Tests inject tiny thresholds so a
    2 MB fixture exercises the sliced path without writing gigabytes.
    """

    single_shot_max: int
    resumable_max: int
    min_slice: int
    max_components: int

    @classmethod
    def default(cls) -> "SizePolicy":
        return cls(
            single_shot_max=SINGLE_SHOT_MAX_BYTES,
            resumable_max=RESUMABLE_MAX_BYTES,
            min_slice=MIN_SLICE_BYTES,
            max_components=MAX_COMPONENTS,
        )
```

Change the two function signatures to accept the policy (bodies keep their
logic but read thresholds from the policy):

```python
def choose_method(
    size_bytes: int, *, policy: SizePolicy | None = None
) -> TransferMethod:
    if size_bytes < 0:
        raise ValueError("size_bytes must not be negative")
    p = policy or SizePolicy.default()
    if size_bytes <= p.single_shot_max:
        return TransferMethod.SINGLE_SHOT
    if size_bytes <= p.resumable_max:
        return TransferMethod.RESUMABLE
    return TransferMethod.SLICED


def plan_slices(size_bytes: int, *, policy: SizePolicy | None = None) -> list[SliceSpec]:
    """Cut ``size_bytes`` into at most ``policy.max_components`` contiguous slices."""
    if size_bytes < 0:
        raise ValueError("size_bytes must not be negative")
    if size_bytes == 0:
        return [SliceSpec(index=0, offset=0, length=0)]

    p = policy or SizePolicy.default()
    slice_size = max(p.min_slice, -(-size_bytes // p.max_components))

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

In `src/mml_cloud_transfer/core/crc32c_combine.py`, extend the `combine_all`
docstring (no code change):

```python
def combine_all(pairs: Sequence[tuple[int, int]]) -> int:
    """Fold a sequence of ``(crc32c, length)`` pairs, in byte order, into one CRC32C.

    Pairs MUST be supplied in slice-index (byte) order. The first pair's
    length is unused — only the lengths of subsequent ranges shift the
    running CRC — so a wrong first length cannot be detected here.
    """
```

In `src/mml_cloud_transfer/store/repository.py`, extend `add_planned_files`
(import `SizePolicy` alongside `choose_method`):

```python
    def add_planned_files(
        self,
        job_id: int,
        files: Iterable[PlannedFile],
        *,
        policy: SizePolicy | None = None,
    ) -> int:
        """Insert planned files, ignoring any already present. Returns rows added."""
```

and pass the policy through where the method is chosen:

```python
                choose_method(f.size_bytes, policy=policy).value,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/core/test_retry.py tests/core/test_paths.py tests/core/test_slicing.py tests/store/test_repository.py -v`
Expected: PASS (4 new retry tests, 1 new paths test, 3 new slicing tests, 1 new repository test; all pre-existing tests still green).

- [ ] **Step 5: Run the whole suite, then commit**

Run: `.venv/Scripts/python -m pytest`
Expected: PASS (142 passed, 1 skipped).

```bash
git add src/mml_cloud_transfer/core/ src/mml_cloud_transfer/store/repository.py tests/
git commit -m "feat: add retry schedule, display_path, and injectable size policy"
```

---

### Task 2: GCS dependencies and the emulator test harness

Adds the `google-cloud-storage` dependency, a script that fetches the fake-gcs-server emulator binary, and the pytest fixtures/markers every network task after this one uses.

**Files:**
- Modify: `pyproject.toml` (dependencies + pytest markers)
- Create: `tests/tools/get-fake-gcs-server.ps1`
- Create: `tests/conftest.py`
- Test: `tests/gcs/__init__.py`, `tests/gcs/test_harness.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: pytest fixtures `emulator` (session-scoped; starts fake-gcs-server on a free port, yields `EmulatorInfo(endpoint: str, port: int)`, skips the test with an actionable message when the binary is missing) and `emulator_client` (function-scoped; yields a `google.cloud.storage.Client` bound to the emulator with a fresh uniquely-named bucket created, plus the bucket name — as a tuple `(client, bucket_name)`); markers `emulator` and `real_bucket`; env override `MMLCT_FAKE_GCS` for the binary path.

- [ ] **Step 1: Add dependencies and markers**

In `pyproject.toml`, change the `dependencies` list to:

```toml
dependencies = [
    "google-crc32c>=1.5.0",
    "google-cloud-storage>=3.0,<4",
    "requests>=2.31",
]
```

and extend `[tool.pytest.ini_options]`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
markers = [
    "emulator: requires fake-gcs-server (tools/fake-gcs-server.exe); skipped when absent",
    "real_bucket: requires MMLCT_TEST_BUCKET and real credentials; release gate",
]
```

Run: `.venv/Scripts/python -m pip install -e ".[dev]"`
Expected: installs `google-cloud-storage`, `google-auth`, `requests` as wheels.

- [ ] **Step 2: Write the fetch script**

Create `tests/tools/get-fake-gcs-server.ps1`:

```powershell
# Downloads the fake-gcs-server emulator binary into tools/fake-gcs-server.exe.
# Uses the GitHub API to find the Windows amd64 asset so release naming drift
# does not break us. Run once per machine:
#   pwsh tests/tools/get-fake-gcs-server.ps1
param([string]$Version = "latest")

$ErrorActionPreference = "Stop"
$toolsDir = Join-Path $PSScriptRoot "..\..\tools"
New-Item -ItemType Directory -Force $toolsDir | Out-Null
$exePath = Join-Path $toolsDir "fake-gcs-server.exe"

$api = if ($Version -eq "latest") {
    "https://api.github.com/repos/fsouza/fake-gcs-server/releases/latest"
} else {
    "https://api.github.com/repos/fsouza/fake-gcs-server/releases/tags/v$Version"
}
$release = Invoke-RestMethod -Uri $api
$asset = $release.assets | Where-Object { $_.name -match "Windows" -and $_.name -match "amd64" } | Select-Object -First 1
if (-not $asset) { throw "No Windows amd64 asset in release $($release.tag_name)" }

$archive = Join-Path $env:TEMP $asset.name
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $archive
$extractDir = Join-Path $env:TEMP "fake-gcs-server-extract"
if (Test-Path $extractDir) { Remove-Item -Recurse -Force $extractDir }
New-Item -ItemType Directory -Force $extractDir | Out-Null

if ($asset.name -like "*.zip") {
    Expand-Archive -Path $archive -DestinationPath $extractDir
} else {
    tar -xzf $archive -C $extractDir
}
$exe = Get-ChildItem -Recurse $extractDir -Filter "fake-gcs-server*.exe" | Select-Object -First 1
if (-not $exe) { throw "No exe found inside $($asset.name)" }
Copy-Item $exe.FullName $exePath -Force
Write-Host "Installed $($release.tag_name) -> $exePath"
```

Also add `tools/` to `.gitignore` (append the line `tools/` under the scratch section).

Run: `pwsh tests/tools/get-fake-gcs-server.ps1`
Expected: prints `Installed v<version> -> ...\tools\fake-gcs-server.exe`. If this machine cannot reach GitHub, note it in your report — the emulator tests will self-skip and the harness test below still verifies the skip path.

- [ ] **Step 3: Write the failing harness test**

Create `tests/gcs/__init__.py` (empty) and `tests/gcs/test_harness.py`:

```python
import uuid

import pytest


@pytest.mark.emulator
def test_emulator_round_trip(emulator_client):
    client, bucket_name = emulator_client
    bucket = client.bucket(bucket_name)
    name = f"probe-{uuid.uuid4().hex}.bin"
    bucket.blob(name).upload_from_string(b"hello emulator")

    fetched = bucket.get_blob(name)
    assert fetched is not None
    assert fetched.size == 14
    assert fetched.download_as_bytes() == b"hello emulator"


@pytest.mark.emulator
def test_each_test_gets_a_fresh_bucket(emulator_client):
    client, bucket_name = emulator_client
    assert list(client.list_blobs(bucket_name)) == []
```

- [ ] **Step 4: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/gcs/test_harness.py -v`
Expected: ERROR — `fixture 'emulator_client' not found`.

- [ ] **Step 5: Write the conftest**

Create `tests/conftest.py`:

```python
"""Shared fixtures: the fake-gcs-server emulator and marker gating.

The emulator binary lives at tools/fake-gcs-server.exe (fetched by
tests/tools/get-fake-gcs-server.ps1, overridable via MMLCT_FAKE_GCS).
Emulator tests skip with an actionable message when it is absent.
real_bucket tests skip unless MMLCT_TEST_BUCKET is set.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_EXE = _REPO_ROOT / "tools" / "fake-gcs-server.exe"


@dataclass(frozen=True)
class EmulatorInfo:
    endpoint: str
    port: int


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def emulator():
    exe = Path(os.environ.get("MMLCT_FAKE_GCS", _DEFAULT_EXE))
    if not exe.exists():
        pytest.skip(
            f"fake-gcs-server not found at {exe} — run "
            "pwsh tests/tools/get-fake-gcs-server.ps1 (or set MMLCT_FAKE_GCS)"
        )

    port = _free_port()
    endpoint = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [
            str(exe),
            "-scheme", "http",
            "-host", "127.0.0.1",
            "-port", str(port),
            "-backend", "memory",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                requests.get(f"{endpoint}/storage/v1/b", timeout=1)
                break
            except requests.ConnectionError:
                if time.monotonic() > deadline:
                    raise RuntimeError("fake-gcs-server did not become ready")
                time.sleep(0.2)
        yield EmulatorInfo(endpoint=endpoint, port=port)
    finally:
        proc.kill()
        proc.wait(timeout=10)


@pytest.fixture
def emulator_client(emulator):
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import storage

    client = storage.Client(
        project="mmlct-test",
        credentials=AnonymousCredentials(),
        client_options={"api_endpoint": emulator.endpoint},
    )
    bucket_name = f"mmlct-{uuid.uuid4().hex[:12]}"
    client.create_bucket(bucket_name)
    yield client, bucket_name
```

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/gcs/test_harness.py -v`
Expected: PASS, 2 tests (or SKIP with the fetch-script message if the binary could not be downloaded — verify the skip message renders correctly in that case, and say so in your report).

- [ ] **Step 7: Run the whole suite, then commit**

Run: `.venv/Scripts/python -m pytest`
Expected: previous count plus 2 emulator tests passing (or skipping cleanly).

```bash
git add pyproject.toml .gitignore tests/
git commit -m "feat: add GCS dependency and fake-gcs-server test harness"
```

---

### Task 3: GCS client and session factory

One place that turns "how do I authenticate, and where is the API" into a context object every other `gcs` function receives. Three credential sources: an explicit service-account key file, Application Default Credentials, or the anonymous emulator.

**Files:**
- Create: `src/mml_cloud_transfer/gcs/__init__.py`
- Create: `src/mml_cloud_transfer/gcs/client.py`
- Test: `tests/gcs/test_client.py`

**Interfaces:**
- Consumes: fixtures from Task 2.
- Produces: `GcsContext(client: storage.Client, session: requests.Session, endpoint: str, bucket: str)` (frozen dataclass); `make_context(bucket: str, *, credentials_path: str | None = None, emulator_endpoint: str | None = None) -> GcsContext`. `session` is authorized (or plain for the emulator) and is what the resumable/download code PUTs and GETs through. `endpoint` is the API root without a trailing slash (default `https://storage.googleapis.com`).

- [ ] **Step 1: Write the failing test**

Create `tests/gcs/test_client.py`:

```python
import pytest

from mml_cloud_transfer.gcs.client import GcsContext, make_context


@pytest.mark.emulator
def test_emulator_context_is_anonymous_and_usable(emulator, emulator_client):
    _, bucket_name = emulator_client
    ctx = make_context(bucket_name, emulator_endpoint=emulator.endpoint)

    assert isinstance(ctx, GcsContext)
    assert ctx.endpoint == emulator.endpoint
    assert ctx.bucket == bucket_name
    # The storage client works against the emulator.
    ctx.client.bucket(bucket_name).blob("t.bin").upload_from_string(b"x")
    # The raw session reaches the same API.
    resp = ctx.session.get(f"{ctx.endpoint}/storage/v1/b/{bucket_name}/o")
    assert resp.status_code == 200
    assert resp.json()["items"][0]["name"] == "t.bin"


def test_missing_key_file_raises_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        make_context("bucket", credentials_path=str(tmp_path / "nope.json"))


def test_endpoint_never_has_a_trailing_slash():
    # Pure string behavior — building the context makes no network calls.
    ctx = make_context("b", emulator_endpoint="http://127.0.0.1:1/")
    assert ctx.endpoint == "http://127.0.0.1:1"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/gcs/test_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mml_cloud_transfer.gcs'`.

- [ ] **Step 3: Implement**

Create `src/mml_cloud_transfer/gcs/__init__.py` (empty) and
`src/mml_cloud_transfer/gcs/client.py`:

```python
"""Authenticated GCS context: client library handle + raw HTTP session.

The client library is used for what it does well (metadata, listing,
single-shot uploads, compose, session initiation). The raw session exists
because resumable chunk PUTs and ranged GETs are driven by us — we persist
session URIs across process death and resume from the server's committed
offset, which the library does not expose.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import requests
from google.cloud import storage

_DEFAULT_ENDPOINT = "https://storage.googleapis.com"


@dataclass(frozen=True)
class GcsContext:
    client: storage.Client
    session: requests.Session
    endpoint: str
    bucket: str


def make_context(
    bucket: str,
    *,
    credentials_path: str | None = None,
    emulator_endpoint: str | None = None,
) -> GcsContext:
    """Build a context from one of three credential sources.

    Priority: explicit emulator endpoint (anonymous) > explicit service
    account key file > Application Default Credentials.
    """
    if emulator_endpoint is not None:
        from google.auth.credentials import AnonymousCredentials

        endpoint = emulator_endpoint.rstrip("/")
        client = storage.Client(
            project="mmlct",
            credentials=AnonymousCredentials(),
            client_options={"api_endpoint": endpoint},
        )
        return GcsContext(
            client=client, session=requests.Session(), endpoint=endpoint, bucket=bucket
        )

    from google.auth.transport.requests import AuthorizedSession

    if credentials_path is not None:
        from google.oauth2 import service_account

        path = Path(credentials_path)
        if not path.exists():
            raise FileNotFoundError(f"credentials file not found: {credentials_path}")
        credentials = service_account.Credentials.from_service_account_file(
            str(path),
            scopes=["https://www.googleapis.com/auth/devstorage.read_write"],
        )
        project = credentials.project_id
    else:
        import google.auth

        credentials, project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/devstorage.read_write"]
        )

    client = storage.Client(project=project, credentials=credentials)
    return GcsContext(
        client=client,
        session=AuthorizedSession(credentials),
        endpoint=_DEFAULT_ENDPOINT,
        bucket=bucket,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/gcs/test_client.py -v`
Expected: PASS, 3 tests (1 emulator-marked).

- [ ] **Step 5: Commit**

```bash
git add src/mml_cloud_transfer/gcs/ tests/gcs/test_client.py
git commit -m "feat: add GCS context factory for key-file, ADC, and emulator auth"
```

---

### Task 4: Object metadata helpers

The small vocabulary the uploader, downloader, and Layer-3 audit all share: fetch one object's metadata, list a prefix, delete, and a typed error for raw-HTTP failures that `core.errors.classify` understands.

**Files:**
- Create: `src/mml_cloud_transfer/gcs/objects.py`
- Test: `tests/gcs/test_objects.py`

**Interfaces:**
- Consumes: `GcsContext` from Task 3; `crc32c_from_base64` from `core.hashing`.
- Produces: `ObjectMeta(name: str, size: int, crc32c: int, generation: int)` (frozen dataclass); `get_meta(ctx, name) -> ObjectMeta | None` (None on 404); `list_prefix(ctx, prefix) -> Iterator[ObjectMeta]`; `delete_object(ctx, name, *, ignore_missing: bool = True) -> None`; `GcsHttpError(Exception)` with an integer `code` attribute (so `classify()` maps it by status) and `raise_for_status(response) -> None` helper that raises it for non-2xx/308 responses.

- [ ] **Step 1: Write the failing test**

Create `tests/gcs/test_objects.py`:

```python
import pytest

from mml_cloud_transfer.core.errors import ErrorCategory, classify
from mml_cloud_transfer.core.hashing import crc32c_to_base64, hash_file
from mml_cloud_transfer.gcs.client import make_context
from mml_cloud_transfer.gcs.objects import (
    GcsHttpError,
    ObjectMeta,
    delete_object,
    get_meta,
    list_prefix,
)


@pytest.fixture
def ctx(emulator, emulator_client):
    _, bucket_name = emulator_client
    return make_context(bucket_name, emulator_endpoint=emulator.endpoint)


@pytest.mark.emulator
def test_get_meta_returns_size_crc_and_generation(ctx, tmp_path):
    payload = b"0123456789" * 100
    ctx.client.bucket(ctx.bucket).blob("a/b.bin").upload_from_string(payload)

    meta = get_meta(ctx, "a/b.bin")
    assert isinstance(meta, ObjectMeta)
    assert meta.name == "a/b.bin"
    assert meta.size == 1000
    assert meta.generation > 0
    # CRC comes back as our integer form and matches a local hash.
    local = tmp_path / "local.bin"
    local.write_bytes(payload)
    assert meta.crc32c == hash_file(local).crc32c


@pytest.mark.emulator
def test_get_meta_returns_none_for_a_missing_object(ctx):
    assert get_meta(ctx, "does/not/exist.bin") is None


@pytest.mark.emulator
def test_list_prefix_yields_only_matching_objects(ctx):
    bucket = ctx.client.bucket(ctx.bucket)
    bucket.blob("run47/a.bin").upload_from_string(b"a")
    bucket.blob("run47/sub/b.bin").upload_from_string(b"bb")
    bucket.blob("other/c.bin").upload_from_string(b"ccc")

    names = {m.name: m.size for m in list_prefix(ctx, "run47/")}
    assert names == {"run47/a.bin": 1, "run47/sub/b.bin": 2}


@pytest.mark.emulator
def test_delete_object_is_idempotent(ctx):
    ctx.client.bucket(ctx.bucket).blob("gone.bin").upload_from_string(b"x")
    delete_object(ctx, "gone.bin")
    assert get_meta(ctx, "gone.bin") is None
    delete_object(ctx, "gone.bin")  # second call must not raise


def test_gcs_http_error_classifies_by_status_code():
    for code, category in ((403, ErrorCategory.CREDENTIAL), (429, ErrorCategory.QUOTA),
                           (503, ErrorCategory.NETWORK), (412, ErrorCategory.CONFLICT)):
        assert classify(GcsHttpError(code, "boom")).category is category
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/gcs/test_objects.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mml_cloud_transfer.gcs.objects'`.

- [ ] **Step 3: Implement**

Create `src/mml_cloud_transfer/gcs/objects.py`:

```python
"""Object metadata vocabulary shared by the uploader, downloader, and audit."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import requests

from mml_cloud_transfer.core.hashing import crc32c_from_base64
from mml_cloud_transfer.gcs.client import GcsContext


class GcsHttpError(Exception):
    """Raw-HTTP failure carrying the status code where classify() looks for it."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"HTTP {code}: {message}")
        self.code = code


def raise_for_status(response: requests.Response) -> None:
    """Raise GcsHttpError for anything that is not 2xx or 308."""
    if response.status_code == 308 or 200 <= response.status_code < 300:
        return
    raise GcsHttpError(response.status_code, response.text[:500])


@dataclass(frozen=True, slots=True)
class ObjectMeta:
    name: str
    size: int
    crc32c: int
    generation: int


def _to_meta(blob) -> ObjectMeta:
    return ObjectMeta(
        name=blob.name,
        size=int(blob.size),
        crc32c=crc32c_from_base64(blob.crc32c),
        generation=int(blob.generation),
    )


def get_meta(ctx: GcsContext, name: str) -> ObjectMeta | None:
    blob = ctx.client.bucket(ctx.bucket).get_blob(name)
    return None if blob is None else _to_meta(blob)


def list_prefix(ctx: GcsContext, prefix: str) -> Iterator[ObjectMeta]:
    for blob in ctx.client.list_blobs(ctx.bucket, prefix=prefix):
        yield _to_meta(blob)


def delete_object(ctx: GcsContext, name: str, *, ignore_missing: bool = True) -> None:
    from google.api_core.exceptions import NotFound

    try:
        ctx.client.bucket(ctx.bucket).blob(name).delete()
    except NotFound:
        if not ignore_missing:
            raise
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/gcs/test_objects.py -v`
Expected: PASS, 5 tests (4 emulator-marked).

- [ ] **Step 5: Commit**

```bash
git add src/mml_cloud_transfer/gcs/objects.py tests/gcs/test_objects.py
git commit -m "feat: add GCS object metadata helpers and typed HTTP error"
```

---

### Task 5: Single-shot uploader and the skip rule

The ≤ single_shot_max path, plus two pieces every upload path reuses: the skip rule and Layer-2 verification against a fetched `ObjectMeta`.

**Files:**
- Create: `src/mml_cloud_transfer/gcs/uploader.py`
- Test: `tests/gcs/test_uploader_single_shot.py`

**Interfaces:**
- Consumes: `GcsContext`, `ObjectMeta`, `get_meta` from Tasks 3-4; `hash_file`, `crc32c_to_base64` from `core.hashing`; `HashResult`.
- Produces: `UploadResult(state: str, local_crc32c: int, remote_crc32c: int, generation: int, sha256: str | None, bytes_sent: int)` where `state` is `"verified"` or `"skipped"` (frozen dataclass; failures raise); `should_skip(meta: ObjectMeta | None, size: int, local_crc32c: int) -> bool`; `verify_layer2(meta: ObjectMeta, size: int, local_crc32c: int) -> None` (raises `ChecksumMismatch(Exception)` — a class whose *name* `classify()` does not special-case but which the runner maps via `ErrorCategory.CHECKSUM_MISMATCH`; define `class ChecksumMismatch(Exception)` carrying a message with both CRCs); `upload_single_shot(ctx, source_path: str, object_name: str, *, precondition_generation: int | None, with_sha256: bool = False) -> UploadResult`. Later upload tasks import `should_skip`, `verify_layer2`, `ChecksumMismatch`, and `UploadResult` from this module.

- [ ] **Step 1: Write the failing test**

Create `tests/gcs/test_uploader_single_shot.py`:

```python
import pytest

from mml_cloud_transfer.core.errors import ErrorCategory, classify
from mml_cloud_transfer.core.hashing import hash_file
from mml_cloud_transfer.gcs.client import make_context
from mml_cloud_transfer.gcs.objects import get_meta
from mml_cloud_transfer.gcs.uploader import (
    ChecksumMismatch,
    UploadResult,
    should_skip,
    upload_single_shot,
    verify_layer2,
)


@pytest.fixture
def ctx(emulator, emulator_client):
    _, bucket_name = emulator_client
    return make_context(bucket_name, emulator_endpoint=emulator.endpoint)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "small.bin"
    path.write_bytes(b"payload " * 512)  # 4096 bytes
    return path


@pytest.mark.emulator
def test_upload_verifies_and_reports_generation(ctx, source):
    result = upload_single_shot(
        ctx, str(source), "archive/small.bin", precondition_generation=0
    )
    assert isinstance(result, UploadResult)
    assert result.state == "verified"
    assert result.bytes_sent == 4096
    assert result.local_crc32c == result.remote_crc32c == hash_file(source).crc32c
    meta = get_meta(ctx, "archive/small.bin")
    assert meta.generation == result.generation


@pytest.mark.emulator
def test_sha256_is_computed_only_when_asked(ctx, source):
    import hashlib

    without = upload_single_shot(
        ctx, str(source), "a.bin", precondition_generation=0
    )
    with_hash = upload_single_shot(
        ctx, str(source), "b.bin", precondition_generation=0, with_sha256=True
    )
    assert without.sha256 is None
    assert with_hash.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    # The audit hash travels with the object (spec: custom metadata).
    stamped = ctx.client.bucket(ctx.bucket).get_blob("b.bin")
    assert stamped.metadata == {"mmlct-sha256": with_hash.sha256}
    plain = ctx.client.bucket(ctx.bucket).get_blob("a.bin")
    assert not plain.metadata


@pytest.mark.emulator
def test_matching_destination_is_skipped_without_sending_bytes(ctx, source):
    first = upload_single_shot(ctx, str(source), "c.bin", precondition_generation=0)
    again = upload_single_shot(
        ctx, str(source), "c.bin", precondition_generation=first.generation
    )
    assert again.state == "skipped"
    assert again.bytes_sent == 0
    assert again.generation == first.generation


@pytest.mark.emulator
def test_changed_destination_is_overwritten_under_its_generation(ctx, source, tmp_path):
    other = tmp_path / "other.bin"
    other.write_bytes(b"different content")
    first = upload_single_shot(ctx, str(other), "d.bin", precondition_generation=0)

    replaced = upload_single_shot(
        ctx, str(source), "d.bin", precondition_generation=first.generation
    )
    assert replaced.state == "verified"
    assert replaced.generation != first.generation


@pytest.mark.emulator
def test_stale_precondition_raises_conflict(ctx, source, tmp_path):
    other = tmp_path / "other.bin"
    other.write_bytes(b"different content")
    upload_single_shot(ctx, str(other), "e.bin", precondition_generation=0)

    with pytest.raises(Exception) as excinfo:
        upload_single_shot(ctx, str(source), "e.bin", precondition_generation=0)
    assert classify(excinfo.value).category is ErrorCategory.CONFLICT


def test_should_skip_needs_size_and_crc_to_match():
    from mml_cloud_transfer.gcs.objects import ObjectMeta

    meta = ObjectMeta(name="x", size=10, crc32c=42, generation=1)
    assert should_skip(meta, size=10, local_crc32c=42)
    assert not should_skip(meta, size=11, local_crc32c=42)
    assert not should_skip(meta, size=10, local_crc32c=43)
    assert not should_skip(None, size=10, local_crc32c=42)


def test_verify_layer2_raises_on_any_mismatch():
    from mml_cloud_transfer.gcs.objects import ObjectMeta

    good = ObjectMeta(name="x", size=10, crc32c=42, generation=1)
    verify_layer2(good, size=10, local_crc32c=42)  # no raise
    with pytest.raises(ChecksumMismatch):
        verify_layer2(good, size=10, local_crc32c=41)
    with pytest.raises(ChecksumMismatch):
        verify_layer2(good, size=9, local_crc32c=42)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/gcs/test_uploader_single_shot.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mml_cloud_transfer.gcs.uploader'`.

- [ ] **Step 3: Implement**

Create `src/mml_cloud_transfer/gcs/uploader.py`:

```python
"""Upload paths. This task: single-shot plus the shared skip/verify rules.

Tasks 7 and 8 extend this module with the resumable and sliced paths.
"""

from __future__ import annotations

from dataclasses import dataclass

from mml_cloud_transfer.core.hashing import crc32c_to_base64, hash_file
from mml_cloud_transfer.gcs.client import GcsContext
from mml_cloud_transfer.gcs.objects import ObjectMeta, get_meta


class ChecksumMismatch(Exception):
    """Layer-2 failure: the finalized object does not match the local file."""


@dataclass(frozen=True, slots=True)
class UploadResult:
    state: str  # "verified" | "skipped"
    local_crc32c: int
    remote_crc32c: int
    generation: int
    sha256: str | None
    bytes_sent: int


def should_skip(meta: ObjectMeta | None, size: int, local_crc32c: int) -> bool:
    """The spec's skip rule: destination exists with matching size AND CRC32C."""
    return meta is not None and meta.size == size and meta.crc32c == local_crc32c


def verify_layer2(meta: ObjectMeta, size: int, local_crc32c: int) -> None:
    """Whole-object verification. Raises ChecksumMismatch on any disagreement."""
    if meta.size != size or meta.crc32c != local_crc32c:
        raise ChecksumMismatch(
            f"{meta.name}: remote size={meta.size} crc={meta.crc32c} vs "
            f"local size={size} crc={local_crc32c}"
        )


def upload_single_shot(
    ctx: GcsContext,
    source_path: str,
    object_name: str,
    *,
    precondition_generation: int | None,
    with_sha256: bool = False,
) -> UploadResult:
    local = hash_file(source_path, with_sha256=with_sha256)

    existing = get_meta(ctx, object_name)
    if should_skip(existing, local.bytes_read, local.crc32c):
        return UploadResult(
            state="skipped",
            local_crc32c=local.crc32c,
            remote_crc32c=existing.crc32c,
            generation=existing.generation,
            sha256=local.sha256,
            bytes_sent=0,
        )

    blob = ctx.client.bucket(ctx.bucket).blob(object_name)
    blob.crc32c = crc32c_to_base64(local.crc32c)  # Layer 1: server rejects a bad write
    if local.sha256 is not None:
        blob.metadata = {"mmlct-sha256": local.sha256}  # audit hash travels with the object
    blob.upload_from_filename(
        source_path,
        checksum=None,  # we set blob.crc32c ourselves — whole-file, not per-chunk
        if_generation_match=precondition_generation,
    )

    meta = get_meta(ctx, object_name)
    if meta is None:
        raise ChecksumMismatch(f"{object_name}: object missing immediately after upload")
    verify_layer2(meta, local.bytes_read, local.crc32c)

    return UploadResult(
        state="verified",
        local_crc32c=local.crc32c,
        remote_crc32c=meta.crc32c,
        generation=meta.generation,
        sha256=local.sha256,
        bytes_sent=local.bytes_read,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/gcs/test_uploader_single_shot.py -v`
Expected: PASS, 7 tests (5 emulator-marked). If `test_stale_precondition_raises_conflict` fails because fake-gcs-server does not enforce `ifGenerationMatch` on this endpoint, mark that one test `@pytest.mark.real_bucket` instead of `emulator`, note it in your report, and keep the rest emulator-marked — precondition enforcement then rides the release-gate suite.

- [ ] **Step 5: Commit**

```bash
git add src/mml_cloud_transfer/gcs/uploader.py tests/gcs/test_uploader_single_shot.py
git commit -m "feat: add single-shot upload with skip rule and layer-2 verify"
```

---

### Task 6: Resumable-session protocol

The heart of resume. GCS resumable uploads are a documented HTTP protocol: initiate → session URI; PUT chunks with `Content-Range: bytes S-E/T`; the server answers `308 Resume Incomplete` with a `Range: bytes=0-N` header naming the committed prefix, or 200/201 with the object JSON on finalize; an empty PUT with `Content-Range: bytes */T` queries the committed offset. We drive it ourselves because the session URI must survive process death, and an injected session object is our fault-injection surface. **This module is unit-tested against a scripted stub session — no network.**

**Files:**
- Create: `src/mml_cloud_transfer/gcs/resumable.py`
- Test: `tests/gcs/test_resumable_protocol.py`

**Interfaces:**
- Consumes: `GcsContext` (Task 3); `ObjectMeta`, `GcsHttpError`, `raise_for_status` (Task 4); `crc32c_from_base64` from `core.hashing`.
- Produces: `SessionExpired(Exception)`; `PutResult(committed: int, finalized: ObjectMeta | None)` (frozen dataclass — `finalized` is non-None exactly when the upload completed); `initiate_upload(ctx, object_name: str, total_size: int, *, precondition_generation: int | None = None) -> str`; `put_chunk(session, uri: str, data: bytes, start: int, total: int) -> PutResult`; `query_offset(session, uri: str, total: int) -> PutResult`; constant `CHUNK_ALIGN = 256 * 1024`. `session` is any `requests.Session`-shaped object with `.put(url, data=..., headers=...) -> response`.

- [ ] **Step 1: Write the failing test**

Create `tests/gcs/test_resumable_protocol.py`:

```python
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
            put_chunk(session, "http://s/u", b"x", start=0, total=10)


def test_server_errors_surface_as_gcs_http_error():
    session = StubSession([StubResponse(503, body="try later")])
    with pytest.raises(GcsHttpError) as excinfo:
        put_chunk(session, "http://s/u", b"x", start=0, total=10)
    assert excinfo.value.code == 503


def test_non_final_chunks_must_be_256kib_aligned():
    session = StubSession([])
    with pytest.raises(ValueError, match="256 KiB"):
        put_chunk(session, "http://s/u", b"x" * 1000, start=0, total=10_000_000)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/gcs/test_resumable_protocol.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mml_cloud_transfer.gcs.resumable'`.

- [ ] **Step 3: Implement**

Create `src/mml_cloud_transfer/gcs/resumable.py`:

```python
"""GCS resumable-upload session protocol, driven by us.

Why hand-rolled: the session URI is persisted to SQLite so an upload can
resume after the process dies, the committed offset is re-queried from the
server on resume, and tests inject a scripted session to exercise every
protocol branch without a network.

Protocol reference: https://cloud.google.com/storage/docs/performing-resumable-uploads
"""

from __future__ import annotations

from dataclasses import dataclass

from mml_cloud_transfer.core.hashing import crc32c_from_base64
from mml_cloud_transfer.gcs.client import GcsContext
from mml_cloud_transfer.gcs.objects import GcsHttpError, ObjectMeta

#: Non-final chunks must be a multiple of this.
CHUNK_ALIGN = 256 * 1024


class SessionExpired(Exception):
    """The session URI is dead (404/410) — restart the transfer from zero."""


@dataclass(frozen=True, slots=True)
class PutResult:
    committed: int
    finalized: ObjectMeta | None


def initiate_upload(
    ctx: GcsContext,
    object_name: str,
    total_size: int,
    *,
    precondition_generation: int | None = None,
) -> str:
    """Open a resumable session and return its URI (persist it immediately)."""
    blob = ctx.client.bucket(ctx.bucket).blob(object_name)
    return blob.create_resumable_upload_session(
        size=total_size,
        content_type="application/octet-stream",
        if_generation_match=precondition_generation,
    )


def _parse_committed(headers) -> int:
    header = headers.get("Range") or headers.get("range")
    if not header:
        return 0
    # Format: "bytes=0-N" — N is the last committed byte index.
    return int(header.split("-", 1)[1]) + 1


def _finalized_meta(body: dict) -> ObjectMeta:
    return ObjectMeta(
        name=body["name"],
        size=int(body["size"]),
        crc32c=crc32c_from_base64(body["crc32c"]),
        generation=int(body["generation"]),
    )


def _handle(response, total: int) -> PutResult:
    if response.status_code == 308:
        return PutResult(committed=_parse_committed(response.headers), finalized=None)
    if response.status_code in (200, 201):
        meta = _finalized_meta(response.json())
        return PutResult(committed=total, finalized=meta)
    if response.status_code in (404, 410):
        raise SessionExpired(f"upload session is gone (HTTP {response.status_code})")
    raise GcsHttpError(response.status_code, response.text[:500])


def put_chunk(session, uri: str, data: bytes, start: int, total: int) -> PutResult:
    """Send one chunk. The final chunk is the one where start+len == total."""
    end = start + len(data) - 1
    is_final = start + len(data) == total
    if not is_final and len(data) % CHUNK_ALIGN != 0:
        raise ValueError(f"non-final chunks must be a multiple of 256 KiB, got {len(data)}")
    response = session.put(
        uri,
        data=data,
        headers={"Content-Range": f"bytes {start}-{end}/{total}"},
    )
    return _handle(response, total)


def query_offset(session, uri: str, total: int) -> PutResult:
    """Ask the server how much it has committed (used on resume)."""
    response = session.put(
        uri,
        data=b"",
        headers={"Content-Range": f"bytes */{total}"},
    )
    return _handle(response, total)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/gcs/test_resumable_protocol.py -v`
Expected: PASS, 9 tests, none needing a network.

- [ ] **Step 5: Commit**

```bash
git add src/mml_cloud_transfer/gcs/resumable.py tests/gcs/test_resumable_protocol.py
git commit -m "feat: add hand-rolled resumable session protocol with stub tests"
```

---

### Task 7: Resumable uploader (medium files)

Drives Task 6's protocol for one whole file: hash-as-you-send in a single read pass, report progress after every chunk so the runner can persist `(session_uri, committed)`, and resume from the server's committed offset after a crash — re-hashing only the already-committed prefix.

**Files:**
- Modify: `src/mml_cloud_transfer/gcs/uploader.py` (add the resumable path)
- Test: `tests/gcs/test_uploader_resumable.py`

**Interfaces:**
- Consumes: `initiate_upload`, `put_chunk`, `query_offset`, `SessionExpired`, `CHUNK_ALIGN` (Task 6); `should_skip`, `verify_layer2`, `ChecksumMismatch`, `UploadResult`, `get_meta` (Tasks 4-5).
- Produces: `ProgressFn = Callable[[str, int], None]` (type alias — args are `session_uri, committed_bytes`); `upload_resumable(ctx, source_path: str, object_name: str, size_bytes: int, *, precondition_generation: int | None, session_uri: str | None = None, with_sha256: bool = False, chunk_size: int = 8 * 1024 * 1024, on_progress: ProgressFn | None = None) -> UploadResult`. Passing a stored `session_uri` resumes; `None` starts fresh. On `SessionExpired` the function transparently restarts once with a new session (reporting it via `on_progress`).

- [ ] **Step 1: Write the failing test**

Create `tests/gcs/test_uploader_resumable.py`:

```python
import pytest

from mml_cloud_transfer.core.hashing import hash_file
from mml_cloud_transfer.gcs.client import make_context
from mml_cloud_transfer.gcs.objects import get_meta
from mml_cloud_transfer.gcs.uploader import upload_resumable

CHUNK = 256 * 1024


@pytest.fixture
def ctx(emulator, emulator_client):
    _, bucket_name = emulator_client
    return make_context(bucket_name, emulator_endpoint=emulator.endpoint)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "medium.bin"
    path.write_bytes(bytes(range(256)) * 4096)  # 1 MiB, non-uniform content
    return path


class DyingSession:
    """Delegates to a real session but raises after N successful puts."""

    def __init__(self, real, live_puts):
        self.real = real
        self.remaining = live_puts

    def put(self, *args, **kwargs):
        if self.remaining == 0:
            raise ConnectionResetError("injected mid-upload failure")
        self.remaining -= 1
        return self.real.put(*args, **kwargs)


@pytest.mark.emulator
def test_uploads_and_verifies_in_one_pass(ctx, source):
    events = []
    result = upload_resumable(
        ctx, str(source), "r/medium.bin", 1024 * 1024,
        precondition_generation=0, chunk_size=CHUNK,
        on_progress=lambda uri, committed: events.append((uri, committed)),
    )
    assert result.state == "verified"
    assert result.bytes_sent == 1024 * 1024
    assert result.local_crc32c == hash_file(source).crc32c
    assert get_meta(ctx, "r/medium.bin").generation == result.generation
    # Progress was reported with a stable session URI and growing offsets.
    uris = {uri for uri, _ in events}
    assert len(uris) == 1
    offsets = [c for _, c in events]
    assert offsets == sorted(offsets)
    assert offsets[-1] == 1024 * 1024


@pytest.mark.emulator
def test_resumes_from_the_committed_offset_after_a_crash(ctx, source):
    recorded = []
    dying = DyingSession(ctx.session, live_puts=2)
    broken_ctx = type(ctx)(
        client=ctx.client, session=dying, endpoint=ctx.endpoint, bucket=ctx.bucket
    )
    with pytest.raises(ConnectionResetError):
        upload_resumable(
            broken_ctx, str(source), "r/resumed.bin", 1024 * 1024,
            precondition_generation=0, chunk_size=CHUNK,
            on_progress=lambda uri, committed: recorded.append((uri, committed)),
        )
    assert recorded, "progress must have been reported before the crash"
    uri, committed = recorded[-1]
    assert 0 < committed < 1024 * 1024

    result = upload_resumable(
        ctx, str(source), "r/resumed.bin", 1024 * 1024,
        precondition_generation=0, session_uri=uri, chunk_size=CHUNK,
    )
    assert result.state == "verified"
    assert result.local_crc32c == hash_file(source).crc32c
    # Only the remainder was re-sent.
    assert result.bytes_sent == 1024 * 1024 - committed


@pytest.mark.emulator
def test_matching_destination_is_skipped(ctx, source):
    first = upload_resumable(
        ctx, str(source), "r/skip.bin", 1024 * 1024,
        precondition_generation=0, chunk_size=CHUNK,
    )
    again = upload_resumable(
        ctx, str(source), "r/skip.bin", 1024 * 1024,
        precondition_generation=first.generation, chunk_size=CHUNK,
    )
    assert again.state == "skipped"
    assert again.bytes_sent == 0


@pytest.mark.emulator
def test_sha256_survives_a_resume(ctx, source):
    import hashlib

    recorded = []
    dying = DyingSession(ctx.session, live_puts=2)
    broken_ctx = type(ctx)(
        client=ctx.client, session=dying, endpoint=ctx.endpoint, bucket=ctx.bucket
    )
    with pytest.raises(ConnectionResetError):
        upload_resumable(
            broken_ctx, str(source), "r/sha.bin", 1024 * 1024,
            precondition_generation=0, chunk_size=CHUNK, with_sha256=True,
            on_progress=lambda uri, committed: recorded.append((uri, committed)),
        )
    uri, _ = recorded[-1]
    result = upload_resumable(
        ctx, str(source), "r/sha.bin", 1024 * 1024,
        precondition_generation=0, session_uri=uri, chunk_size=CHUNK, with_sha256=True,
    )
    assert result.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    stamped = ctx.client.bucket(ctx.bucket).get_blob("r/sha.bin")
    assert stamped.metadata == {"mmlct-sha256": result.sha256}


def test_chunk_size_must_be_aligned(tmp_path):
    path = tmp_path / "x.bin"
    path.write_bytes(b"x")
    with pytest.raises(ValueError, match="256 KiB"):
        upload_resumable(
            None, str(path), "x", 1,
            precondition_generation=0, chunk_size=100_000,
        )
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/gcs/test_uploader_resumable.py -v`
Expected: FAIL with `ImportError: cannot import name 'upload_resumable'`.

- [ ] **Step 3: Implement**

Append to `src/mml_cloud_transfer/gcs/uploader.py` (new imports at top:
`import hashlib`, `import google_crc32c`, `from collections.abc import Callable`,
`from pathlib import Path`, and from `mml_cloud_transfer.gcs.resumable import
CHUNK_ALIGN, SessionExpired, initiate_upload, put_chunk, query_offset`):

```python
ProgressFn = Callable[[str, int], None]


class _StreamHashes:
    """Incremental CRC32C (+ optional SHA-256) fed once per chunk."""

    def __init__(self, with_sha256: bool) -> None:
        self._crc = google_crc32c.Checksum()
        self._sha = hashlib.sha256() if with_sha256 else None

    def update(self, chunk: bytes) -> None:
        self._crc.update(chunk)
        if self._sha is not None:
            self._sha.update(chunk)

    @property
    def crc32c(self) -> int:
        return int.from_bytes(self._crc.digest(), "big")

    @property
    def sha256(self) -> str | None:
        return self._sha.hexdigest() if self._sha is not None else None


def stamp_sha256(ctx: GcsContext, object_name: str, sha256: str) -> None:
    """Record the audit hash in the object's custom metadata (spec requirement)."""
    blob = ctx.client.bucket(ctx.bucket).blob(object_name)
    blob.metadata = {"mmlct-sha256": sha256}
    blob.patch()


def _hash_prefix(fp, hashes: _StreamHashes, length: int, chunk_size: int) -> None:
    """Feed the first ``length`` bytes into ``hashes`` (used on resume)."""
    remaining = length
    while remaining > 0:
        chunk = fp.read(min(chunk_size, remaining))
        if not chunk:
            raise ValueError(f"file shorter than committed offset {length}")
        hashes.update(chunk)
        remaining -= len(chunk)


def upload_resumable(
    ctx: GcsContext,
    source_path: str,
    object_name: str,
    size_bytes: int,
    *,
    precondition_generation: int | None,
    session_uri: str | None = None,
    with_sha256: bool = False,
    chunk_size: int = 8 * 1024 * 1024,
    on_progress: ProgressFn | None = None,
) -> UploadResult:
    if chunk_size % CHUNK_ALIGN != 0:
        raise ValueError(f"chunk_size must be a multiple of 256 KiB, got {chunk_size}")
    if size_bytes <= 0:
        # Method selection routes empty files to single-shot; a zero total
        # would also produce a malformed Content-Range here.
        raise ValueError("upload_resumable requires size_bytes > 0")

    def report(uri: str, committed: int) -> None:
        if on_progress is not None:
            on_progress(uri, committed)

    # Skip rule — only worth a full local read if the destination exists.
    existing = get_meta(ctx, object_name)
    if existing is not None and existing.size == size_bytes:
        local = hash_file(source_path, with_sha256=with_sha256)
        if should_skip(existing, size_bytes, local.crc32c):
            return UploadResult(
                state="skipped",
                local_crc32c=local.crc32c,
                remote_crc32c=existing.crc32c,
                generation=existing.generation,
                sha256=local.sha256,
                bytes_sent=0,
            )

    hashes = _StreamHashes(with_sha256)
    committed = 0

    if session_uri is not None:
        try:
            status = query_offset(ctx.session, session_uri, size_bytes)
        except SessionExpired:
            session_uri = None
        else:
            if status.finalized is not None:
                # Finished before the crash was noticed — verify and return.
                local = hash_file(source_path, with_sha256=with_sha256)
                verify_layer2(status.finalized, size_bytes, local.crc32c)
                return UploadResult(
                    state="verified",
                    local_crc32c=local.crc32c,
                    remote_crc32c=status.finalized.crc32c,
                    generation=status.finalized.generation,
                    sha256=local.sha256,
                    bytes_sent=0,
                )
            committed = status.committed

    if session_uri is None:
        session_uri = initiate_upload(
            ctx, object_name, size_bytes,
            precondition_generation=precondition_generation,
        )
        committed = 0
        report(session_uri, 0)

    bytes_sent = 0
    with Path(source_path).open("rb") as fp:
        if committed:
            _hash_prefix(fp, hashes, committed, chunk_size)

        offset = committed
        finalized = None
        while offset < size_bytes:
            data = fp.read(min(chunk_size, size_bytes - offset))
            hashes.update(data)
            result = put_chunk(ctx.session, session_uri, data, offset, size_bytes)
            offset += len(data)
            bytes_sent += len(data)
            report(session_uri, result.committed)
            if result.finalized is not None:
                finalized = result.finalized
                break

    if finalized is None:
        raise ChecksumMismatch(f"{object_name}: session ended without finalizing")
    verify_layer2(finalized, size_bytes, hashes.crc32c)
    if hashes.sha256 is not None:
        stamp_sha256(ctx, object_name, hashes.sha256)

    return UploadResult(
        state="verified",
        local_crc32c=hashes.crc32c,
        remote_crc32c=finalized.crc32c,
        generation=finalized.generation,
        sha256=hashes.sha256,
        bytes_sent=bytes_sent,
    )
```

Note the restart-once path: when a stored `session_uri` turns out expired,
the code falls through to `initiate_upload` naturally — that IS the
"transparently restart once" behavior, and `report` hands the new URI to the
caller for persistence.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/gcs/test_uploader_resumable.py -v`
Expected: PASS, 5 tests (4 emulator-marked).

- [ ] **Step 5: Run the whole suite, then commit**

Run: `.venv/Scripts/python -m pytest`

```bash
git add src/mml_cloud_transfer/gcs/uploader.py tests/gcs/test_uploader_resumable.py
git commit -m "feat: add resumable upload path with crash resume and single-pass hashing"
```

---

### Task 8: Sliced uploader (large files)

Files above `resumable_max` upload as parallel per-slice resumable sessions to temporary objects, then one `compose` assembles them under the destination precondition. The whole-file CRC comes from `combine_all` over the slice CRCs — no second read — and Layer 2 compares it against the composed object.

**Temp-object naming:** `{object_name}.mmlct.tmp/{index:04d}`. The spec's safety net (a bucket lifecycle rule on leftover temp objects) is deployment documentation, noted in the CLI task; the code always attempts its own cleanup.

**Files:**
- Modify: `src/mml_cloud_transfer/gcs/uploader.py` (add the sliced path)
- Test: `tests/gcs/test_uploader_sliced.py`

**Interfaces:**
- Consumes: `plan_slices`, `SizePolicy` from `core.slicing`; `combine_all` from `core.crc32c_combine`; `hash_range` from `core.hashing`; `upload_resumable` internals — reuses `_StreamHashes`, `initiate_upload`, `put_chunk`, `query_offset`, `SessionExpired` (Tasks 6-7); `delete_object`, `get_meta` (Task 4); `should_skip`, `verify_layer2`, `ChecksumMismatch`, `UploadResult` (Task 5).
- Produces: `SliceProgressFn = Callable[[int, str | None, int, int | None], None]` — args `(slice_index, session_uri, committed_bytes, slice_crc32c)`; `slice_temp_name(object_name: str, index: int) -> str`; `upload_slice(ctx, source_path, object_name, spec: SliceSpec, *, session_uri: str | None = None, chunk_size: int = 8 * 1024 * 1024, on_progress: SliceProgressFn | None = None) -> tuple[int, ObjectMeta]` (returns `(slice_crc32c, temp_object_meta)`); `compose_slices(ctx, object_name: str, slice_metas: list[ObjectMeta], expected_crc32c: int, total_size: int, *, precondition_generation: int | None) -> UploadResult` (composes in list order, verifies Layer 2, deletes temp objects); `upload_sliced(ctx, source_path, object_name, size_bytes, *, precondition_generation, policy: SizePolicy | None = None, slice_states: dict[int, tuple[str | None, int | None]] | None = None, max_workers: int = 4, chunk_size: int = 8 * 1024 * 1024, with_sha256: bool = False, on_progress: SliceProgressFn | None = None) -> UploadResult` — `slice_states` maps `slice_index -> (session_uri, known_crc32c)` from a previous run; slices with a known CRC and an existing temp object are not re-uploaded.

- [ ] **Step 1: Write the failing test**

Create `tests/gcs/test_uploader_sliced.py`:

```python
import pytest

from mml_cloud_transfer.core.hashing import hash_file
from mml_cloud_transfer.core.slicing import SizePolicy, plan_slices
from mml_cloud_transfer.gcs.client import make_context
from mml_cloud_transfer.gcs.objects import get_meta, list_prefix
from mml_cloud_transfer.gcs.uploader import (
    slice_temp_name,
    upload_slice,
    upload_sliced,
)

CHUNK = 256 * 1024
TINY = SizePolicy(
    single_shot_max=64 * 1024,
    resumable_max=256 * 1024,
    min_slice=256 * 1024,
    max_components=32,
)


@pytest.fixture
def ctx(emulator, emulator_client):
    _, bucket_name = emulator_client
    return make_context(bucket_name, emulator_endpoint=emulator.endpoint)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "large.bin"
    path.write_bytes(bytes(range(256)) * 4096)  # 1 MiB -> 4 slices under TINY
    return path


def test_slice_temp_name_is_stable_and_ordered():
    assert slice_temp_name("archive/big.bin", 0) == "archive/big.bin.mmlct.tmp/0000"
    assert slice_temp_name("archive/big.bin", 31) == "archive/big.bin.mmlct.tmp/0031"


@pytest.mark.emulator
def test_sliced_upload_composes_and_verifies(ctx, source):
    result = upload_sliced(
        ctx, str(source), "s/large.bin", 1024 * 1024,
        precondition_generation=0, policy=TINY, chunk_size=CHUNK,
    )
    assert result.state == "verified"
    assert result.local_crc32c == hash_file(source).crc32c
    meta = get_meta(ctx, "s/large.bin")
    assert meta.size == 1024 * 1024
    assert meta.crc32c == result.remote_crc32c


@pytest.mark.emulator
def test_temp_objects_are_deleted_after_compose(ctx, source):
    upload_sliced(
        ctx, str(source), "s/clean.bin", 1024 * 1024,
        precondition_generation=0, policy=TINY, chunk_size=CHUNK,
    )
    leftovers = list(list_prefix(ctx, "s/clean.bin.mmlct.tmp/"))
    assert leftovers == []


@pytest.mark.emulator
def test_progress_reports_slice_uris_and_crcs(ctx, source):
    events = []
    upload_sliced(
        ctx, str(source), "s/progress.bin", 1024 * 1024,
        precondition_generation=0, policy=TINY, chunk_size=CHUNK,
        on_progress=lambda idx, uri, committed, crc: events.append((idx, uri, committed, crc)),
    )
    finished = {idx for idx, _, _, crc in events if crc is not None}
    assert finished == {0, 1, 2, 3}


@pytest.mark.emulator
def test_completed_slices_are_not_reuploaded_on_resume(ctx, source):
    # First: upload two of the four slices by driving upload_slice directly,
    # recording their CRCs — simulating a run that died halfway.
    slices = plan_slices(1024 * 1024, policy=TINY)
    states: dict[int, tuple[str | None, int | None]] = {}
    for spec in slices[:2]:
        crc, meta = upload_slice(
            ctx, str(source), "s/resume.bin", spec, chunk_size=CHUNK
        )
        states[spec.index] = (None, crc)

    events = []
    result = upload_sliced(
        ctx, str(source), "s/resume.bin", 1024 * 1024,
        precondition_generation=0, policy=TINY, chunk_size=CHUNK,
        slice_states=states,
        on_progress=lambda idx, uri, committed, crc: events.append(idx),
    )
    assert result.state == "verified"
    # Slices 0 and 1 were reused: no upload progress events for them.
    assert set(events) <= {2, 3}


@pytest.mark.emulator
def test_skip_rule_applies_before_any_slicing(ctx, source):
    first = upload_sliced(
        ctx, str(source), "s/skip.bin", 1024 * 1024,
        precondition_generation=0, policy=TINY, chunk_size=CHUNK,
    )
    again = upload_sliced(
        ctx, str(source), "s/skip.bin", 1024 * 1024,
        precondition_generation=first.generation, policy=TINY, chunk_size=CHUNK,
    )
    assert again.state == "skipped"
    assert again.bytes_sent == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/gcs/test_uploader_sliced.py -v`
Expected: FAIL with `ImportError: cannot import name 'upload_sliced'`.

- [ ] **Step 3: Implement**

Append to `src/mml_cloud_transfer/gcs/uploader.py` (new imports:
`from concurrent.futures import ThreadPoolExecutor`,
`from mml_cloud_transfer.core.crc32c_combine import combine_all`,
`from mml_cloud_transfer.core.hashing import hash_range`,
`from mml_cloud_transfer.core.slicing import SizePolicy, SliceSpec, plan_slices`,
`from mml_cloud_transfer.gcs.objects import delete_object, list_prefix`):

```python
SliceProgressFn = Callable[[int, str | None, int, int | None], None]


def slice_temp_name(object_name: str, index: int) -> str:
    return f"{object_name}.mmlct.tmp/{index:04d}"


def upload_slice(
    ctx: GcsContext,
    source_path: str,
    object_name: str,
    spec: SliceSpec,
    *,
    session_uri: str | None = None,
    chunk_size: int = 8 * 1024 * 1024,
    on_progress: SliceProgressFn | None = None,
) -> tuple[int, ObjectMeta]:
    """Upload one slice to its temp object; returns (slice_crc32c, temp meta)."""
    temp_name = slice_temp_name(object_name, spec.index)

    def report(uri: str | None, committed: int, crc: int | None) -> None:
        if on_progress is not None:
            on_progress(spec.index, uri, committed, crc)

    hashes = _StreamHashes(with_sha256=False)
    committed = 0

    if session_uri is not None:
        try:
            status = query_offset(ctx.session, session_uri, spec.length)
        except SessionExpired:
            session_uri = None
        else:
            if status.finalized is not None:
                crc = hash_range(source_path, spec.offset, spec.length).crc32c
                verify_layer2(status.finalized, spec.length, crc)
                report(session_uri, spec.length, crc)
                return crc, status.finalized
            committed = status.committed

    if session_uri is None:
        session_uri = initiate_upload(ctx, temp_name, spec.length)
        committed = 0
        report(session_uri, 0, None)

    with Path(source_path).open("rb") as fp:
        fp.seek(spec.offset)
        if committed:
            _hash_prefix(fp, hashes, committed, chunk_size)

        offset = committed
        finalized = None
        while offset < spec.length:
            data = fp.read(min(chunk_size, spec.length - offset))
            hashes.update(data)
            result = put_chunk(ctx.session, session_uri, data, offset, spec.length)
            offset += len(data)
            report(session_uri, result.committed, None)
            if result.finalized is not None:
                finalized = result.finalized
                break

    if finalized is None:
        raise ChecksumMismatch(f"{temp_name}: slice session ended without finalizing")
    verify_layer2(finalized, spec.length, hashes.crc32c)
    report(session_uri, spec.length, hashes.crc32c)
    return hashes.crc32c, finalized


def compose_slices(
    ctx: GcsContext,
    object_name: str,
    slice_metas: list[ObjectMeta],
    expected_crc32c: int,
    total_size: int,
    *,
    precondition_generation: int | None,
) -> UploadResult:
    """Compose temp objects (in list order) into the destination and verify."""
    bucket = ctx.client.bucket(ctx.bucket)
    destination = bucket.blob(object_name)
    sources = [bucket.blob(meta.name) for meta in slice_metas]
    destination.compose(sources, if_generation_match=precondition_generation)

    meta = get_meta(ctx, object_name)
    if meta is None:
        raise ChecksumMismatch(f"{object_name}: object missing after compose")
    verify_layer2(meta, total_size, expected_crc32c)

    for slice_meta in slice_metas:
        delete_object(ctx, slice_meta.name)

    return UploadResult(
        state="verified",
        local_crc32c=expected_crc32c,
        remote_crc32c=meta.crc32c,
        generation=meta.generation,
        sha256=None,
        bytes_sent=0,
    )


def upload_sliced(
    ctx: GcsContext,
    source_path: str,
    object_name: str,
    size_bytes: int,
    *,
    precondition_generation: int | None,
    policy: SizePolicy | None = None,
    slice_states: dict[int, tuple[str | None, int | None]] | None = None,
    max_workers: int = 4,
    chunk_size: int = 8 * 1024 * 1024,
    with_sha256: bool = False,
    on_progress: SliceProgressFn | None = None,
) -> UploadResult:
    slice_states = slice_states or {}
    specs = plan_slices(size_bytes, policy=policy)

    # Skip rule: one full local read only when the destination looks plausible.
    existing = get_meta(ctx, object_name)
    if existing is not None and existing.size == size_bytes:
        local = hash_file(source_path, with_sha256=with_sha256)
        if should_skip(existing, size_bytes, local.crc32c):
            return UploadResult(
                state="skipped",
                local_crc32c=local.crc32c,
                remote_crc32c=existing.crc32c,
                generation=existing.generation,
                sha256=local.sha256,
                bytes_sent=0,
            )

    results: dict[int, tuple[int, ObjectMeta]] = {}
    to_upload: list[SliceSpec] = []
    for spec in specs:
        uri, known_crc = slice_states.get(spec.index, (None, None))
        if known_crc is not None:
            temp_meta = get_meta(ctx, slice_temp_name(object_name, spec.index))
            if temp_meta is not None and temp_meta.crc32c == known_crc:
                results[spec.index] = (known_crc, temp_meta)
                continue
        to_upload.append(spec)

    bytes_sent = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                upload_slice,
                ctx,
                source_path,
                object_name,
                spec,
                session_uri=slice_states.get(spec.index, (None, None))[0],
                chunk_size=chunk_size,
                on_progress=on_progress,
            ): spec
            for spec in to_upload
        }
        for future, spec in futures.items():
            crc, temp_meta = future.result()  # re-raises worker failures
            results[spec.index] = (crc, temp_meta)
            bytes_sent += spec.length

    ordered = [results[spec.index] for spec in specs]
    whole_crc = combine_all([(crc, spec.length) for (crc, _), spec in zip(ordered, specs)])
    composed = compose_slices(
        ctx,
        object_name,
        [meta for _, meta in ordered],
        whole_crc,
        size_bytes,
        precondition_generation=precondition_generation,
    )

    sha256 = None
    if with_sha256:
        sha256 = hash_file(source_path, with_sha256=True).sha256
        stamp_sha256(ctx, object_name, sha256)

    return UploadResult(
        state="verified",
        local_crc32c=whole_crc,
        remote_crc32c=composed.remote_crc32c,
        generation=composed.generation,
        sha256=sha256,
        bytes_sent=bytes_sent,
    )
```

Implementation notes the reviewer will check:
- `combine_all` receives slice CRCs **in spec/index order** — the zip over `specs` guarantees it (final-review carry-forward from Plan 1).
- `future.result()` re-raises the first worker exception; remaining futures are cancelled by the executor's context exit. Leftover temp objects and live sessions are picked up by the next resume (matching temp CRCs are reused) or the bucket lifecycle rule.
- SHA-256 for sliced files costs one extra full read at the end (per-slice SHA cannot be combined the way CRC can); the spec accepts audit-hash CPU cost as opt-in per job. Bytes are read from disk, not the network.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/gcs/test_uploader_sliced.py -v`
Expected: PASS, 7 tests (6 emulator-marked). If fake-gcs-server rejects `compose` with a precondition, apply the same fallback as Task 5 Step 4: move only the affected assertion to a `real_bucket`-marked test and record it in your report.

- [ ] **Step 5: Run the whole suite, then commit**

Run: `.venv/Scripts/python -m pytest`

```bash
git add src/mml_cloud_transfer/gcs/uploader.py tests/gcs/test_uploader_sliced.py
git commit -m "feat: add sliced upload with parallel sessions, compose, and combined CRC"
```

---

### Task 9: Downloader (ranged, resumable, atomic)

Downloads mirror uploads: fixed-size ranged GETs into a `.part` file, completed ranges recorded so resume re-fetches only what is missing, whole-file CRC assembled via `combine_all` and checked against the object's metadata, and an atomic rename only after verification. Downloads have no compose step, so ranges are not bound by the 32-component cap — a fixed 128 MiB range bounds the worst-case loss on a crash.

**Files:**
- Create: `src/mml_cloud_transfer/gcs/downloader.py`
- Test: `tests/gcs/test_downloader.py`

**Interfaces:**
- Consumes: `GcsContext` (Task 3); `ObjectMeta`, `get_meta`, `GcsHttpError`, `raise_for_status` (Task 4); `combine_all`; `hash_file` from `core.hashing`; `SliceSpec` from `core.slicing`; `ChecksumMismatch` from `gcs.uploader`.
- Produces: `DOWNLOAD_RANGE_BYTES = 128 * 1024 * 1024`; `DownloadResult(state: str, local_crc32c: int, remote_crc32c: int, generation: int, sha256: str | None, bytes_received: int)` (`state` is `"verified"` or `"skipped"`); `plan_ranges(size_bytes: int, *, range_bytes: int = DOWNLOAD_RANGE_BYTES) -> list[SliceSpec]`; `RangeProgressFn = Callable[[int, int, int | None], None]` — args `(range_index, bytes_done, range_crc32c_when_complete)`; `download_file(ctx, object_name: str, dest_path: str, *, range_states: dict[int, int] | None = None, range_bytes: int = DOWNLOAD_RANGE_BYTES, max_workers: int = 4, with_sha256: bool = False, on_progress: RangeProgressFn | None = None) -> DownloadResult`. `range_states` maps completed `range_index -> range_crc32c` from a previous run; the `.part` path is always `dest_path + ".part"`.

- [ ] **Step 1: Write the failing test**

Create `tests/gcs/test_downloader.py`:

```python
import pytest

from mml_cloud_transfer.core.errors import ErrorCategory, classify
from mml_cloud_transfer.core.hashing import hash_file
from mml_cloud_transfer.gcs.client import make_context
from mml_cloud_transfer.gcs.downloader import (
    DownloadResult,
    download_file,
    plan_ranges,
)

RANGE = 256 * 1024


@pytest.fixture
def ctx(emulator, emulator_client):
    _, bucket_name = emulator_client
    return make_context(bucket_name, emulator_endpoint=emulator.endpoint)


@pytest.fixture
def remote(ctx):
    payload = bytes(range(256)) * 4096  # 1 MiB
    ctx.client.bucket(ctx.bucket).blob("d/big.bin").upload_from_string(payload)
    return payload


def test_plan_ranges_covers_everything_without_a_component_cap():
    ranges = plan_ranges(10 * RANGE + 5, range_bytes=RANGE)
    assert len(ranges) == 11
    assert sum(r.length for r in ranges) == 10 * RANGE + 5
    assert ranges[0].offset == 0
    for a, b in zip(ranges, ranges[1:]):
        assert b.offset == a.offset + a.length


@pytest.mark.emulator
def test_downloads_verifies_and_renames_atomically(ctx, remote, tmp_path):
    dest = tmp_path / "out" / "big.bin"
    result = download_file(
        ctx, "d/big.bin", str(dest), range_bytes=RANGE
    )
    assert isinstance(result, DownloadResult)
    assert result.state == "verified"
    assert result.bytes_received == len(remote)
    assert dest.read_bytes() == remote
    assert not (tmp_path / "out" / "big.bin.part").exists()
    assert hash_file(dest).crc32c == result.remote_crc32c


@pytest.mark.emulator
def test_progress_reports_completed_range_crcs(ctx, remote, tmp_path):
    events = []
    download_file(
        ctx, "d/big.bin", str(tmp_path / "p.bin"), range_bytes=RANGE,
        on_progress=lambda idx, done, crc: events.append((idx, done, crc)),
    )
    finished = {idx for idx, _, crc in events if crc is not None}
    assert finished == {0, 1, 2, 3}


@pytest.mark.emulator
def test_completed_ranges_are_not_refetched_on_resume(ctx, remote, tmp_path):
    dest = tmp_path / "r.bin"
    # First pass: fetch only ranges 0 and 1 by faking a prior run's states,
    # then confirm the resumed run fetches just 2 and 3.
    first_events = []
    download_file(
        ctx, "d/big.bin", str(dest), range_bytes=RANGE,
        on_progress=lambda idx, done, crc: first_events.append((idx, crc)),
    )
    states = {idx: crc for idx, crc in first_events if crc is not None and idx < 2}
    dest.unlink()  # remove the finished file; .part must be rebuilt

    second_events = []
    result = download_file(
        ctx, "d/big.bin", str(dest), range_bytes=RANGE,
        range_states=states,
        on_progress=lambda idx, done, crc: second_events.append(idx),
    )
    assert result.state == "verified"
    assert set(second_events) <= {2, 3}
    assert result.bytes_received == 2 * RANGE
    assert dest.read_bytes() == remote


@pytest.mark.emulator
def test_matching_local_file_is_skipped(ctx, remote, tmp_path):
    dest = tmp_path / "s.bin"
    download_file(ctx, "d/big.bin", str(dest), range_bytes=RANGE)
    again = download_file(ctx, "d/big.bin", str(dest), range_bytes=RANGE)
    assert again.state == "skipped"
    assert again.bytes_received == 0


@pytest.mark.emulator
def test_missing_object_classifies_not_found(ctx, tmp_path):
    with pytest.raises(Exception) as excinfo:
        download_file(ctx, "d/absent.bin", str(tmp_path / "x.bin"))
    assert classify(excinfo.value).category is ErrorCategory.NOT_FOUND


@pytest.mark.emulator
def test_zero_byte_object_downloads_cleanly(ctx, tmp_path):
    ctx.client.bucket(ctx.bucket).blob("d/empty.bin").upload_from_string(b"")
    dest = tmp_path / "empty.bin"
    result = download_file(ctx, "d/empty.bin", str(dest))
    assert result.state == "verified"
    assert dest.read_bytes() == b""
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/gcs/test_downloader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mml_cloud_transfer.gcs.downloader'`.

- [ ] **Step 3: Implement**

Create `src/mml_cloud_transfer/gcs/downloader.py`:

```python
"""Ranged, resumable downloads with atomic finalization.

The object's generation is pinned on every range request, so a file that
is replaced mid-download produces a clean failure instead of a chimera of
two generations. The ``.part`` file becomes the destination only after the
combined CRC matches the object's metadata.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import google_crc32c

from mml_cloud_transfer.core.crc32c_combine import combine_all
from mml_cloud_transfer.core.hashing import hash_file
from mml_cloud_transfer.core.slicing import SliceSpec
from mml_cloud_transfer.gcs.client import GcsContext
from mml_cloud_transfer.gcs.objects import GcsHttpError, get_meta, raise_for_status
from mml_cloud_transfer.gcs.uploader import ChecksumMismatch, should_skip

DOWNLOAD_RANGE_BYTES = 128 * 1024 * 1024

RangeProgressFn = Callable[[int, int, int | None], None]


@dataclass(frozen=True, slots=True)
class DownloadResult:
    state: str  # "verified" | "skipped"
    local_crc32c: int
    remote_crc32c: int
    generation: int
    sha256: str | None
    bytes_received: int


def plan_ranges(
    size_bytes: int, *, range_bytes: int = DOWNLOAD_RANGE_BYTES
) -> list[SliceSpec]:
    """Fixed-size ranges — no component cap applies to downloads."""
    if size_bytes < 0:
        raise ValueError("size_bytes must not be negative")
    if size_bytes == 0:
        return []
    ranges = []
    offset = 0
    index = 0
    while offset < size_bytes:
        length = min(range_bytes, size_bytes - offset)
        ranges.append(SliceSpec(index=index, offset=offset, length=length))
        offset += length
        index += 1
    return ranges


def _media_url(ctx: GcsContext, object_name: str, generation: int) -> str:
    encoded = quote(object_name, safe="")
    return (
        f"{ctx.endpoint}/download/storage/v1/b/{ctx.bucket}/o/{encoded}"
        f"?alt=media&generation={generation}"
    )


def _fetch_range(
    ctx: GcsContext,
    url: str,
    part_path: Path,
    spec: SliceSpec,
    on_progress: RangeProgressFn | None,
) -> int:
    """Stream one range into the part file; returns the range CRC32C."""
    crc = google_crc32c.Checksum()
    done = 0
    response = ctx.session.get(
        url,
        headers={"Range": f"bytes={spec.offset}-{spec.offset + spec.length - 1}"},
        stream=True,
    )
    raise_for_status(response)
    # A separate handle per worker: seek + write is safe across threads
    # because ranges never overlap.
    with part_path.open("r+b") as fp:
        fp.seek(spec.offset)
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            fp.write(chunk)
            crc.update(chunk)
            done += len(chunk)
            if on_progress is not None:
                on_progress(spec.index, done, None)
    if done != spec.length:
        raise GcsHttpError(500, f"range {spec.index}: got {done} of {spec.length} bytes")
    range_crc = int.from_bytes(crc.digest(), "big")
    if on_progress is not None:
        on_progress(spec.index, done, range_crc)
    return range_crc


def download_file(
    ctx: GcsContext,
    object_name: str,
    dest_path: str,
    *,
    range_states: dict[int, int] | None = None,
    range_bytes: int = DOWNLOAD_RANGE_BYTES,
    max_workers: int = 4,
    with_sha256: bool = False,
    on_progress: RangeProgressFn | None = None,
) -> DownloadResult:
    range_states = range_states or {}
    meta = get_meta(ctx, object_name)
    if meta is None:
        raise GcsHttpError(404, f"object not found: {object_name}")

    dest = Path(dest_path)

    # Skip rule: the local file already matches the object.
    if dest.exists() and dest.stat().st_size == meta.size:
        local = hash_file(dest, with_sha256=with_sha256)
        if should_skip(meta, local.bytes_read, local.crc32c):
            return DownloadResult(
                state="skipped",
                local_crc32c=local.crc32c,
                remote_crc32c=meta.crc32c,
                generation=meta.generation,
                sha256=local.sha256,
                bytes_received=0,
            )

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = Path(str(dest) + ".part")

    ranges = plan_ranges(meta.size, range_bytes=range_bytes)

    # Create/size the part file. A part file of the wrong size belongs to a
    # different generation or range layout — start over in that case.
    if not part.exists() or part.stat().st_size != meta.size:
        with part.open("wb") as fp:
            if meta.size:
                fp.seek(meta.size - 1)
                fp.write(b"\0")
        range_states = {}

    url = _media_url(ctx, object_name, meta.generation)
    crcs: dict[int, int] = dict(range_states)
    to_fetch = [spec for spec in ranges if spec.index not in crcs]

    bytes_received = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_fetch_range, ctx, url, part, spec, on_progress): spec
            for spec in to_fetch
        }
        for future, spec in futures.items():
            crcs[spec.index] = future.result()
            bytes_received += spec.length

    whole_crc = (
        combine_all([(crcs[spec.index], spec.length) for spec in ranges])
        if ranges
        else 0
    )
    if whole_crc != meta.crc32c or sum(s.length for s in ranges) != meta.size:
        raise ChecksumMismatch(
            f"{object_name}: assembled crc={whole_crc} vs remote crc={meta.crc32c}"
        )

    sha256 = hash_file(part, with_sha256=True).sha256 if with_sha256 else None
    os.replace(part, dest)

    return DownloadResult(
        state="verified",
        local_crc32c=whole_crc,
        remote_crc32c=meta.crc32c,
        generation=meta.generation,
        sha256=sha256,
        bytes_received=bytes_received,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/gcs/test_downloader.py -v`
Expected: PASS, 8 tests (7 emulator-marked).

- [ ] **Step 5: Run the whole suite, then commit**

Run: `.venv/Scripts/python -m pytest`

```bash
git add src/mml_cloud_transfer/gcs/downloader.py tests/gcs/test_downloader.py
git commit -m "feat: add ranged resumable downloader with atomic verified rename"
```

---

### Task 10: Store extensions for the engine

Plan 1's repository has no `file_slices` accessors, no precondition capture, and no job start/finish timestamps — the engine needs all three. Pure store work, no network.

**Files:**
- Modify: `src/mml_cloud_transfer/store/repository.py`
- Test: `tests/store/test_repository_engine.py`

**Interfaces:**
- Consumes: existing `JobRepository`, schema (which already has `file_slices` and `job_files.precondition_generation`), `SliceState` from `core.models`.
- Produces, on `JobRepository`: `start_job(job_id) -> None` (status RUNNING, `started_at = COALESCE(started_at, now)`); `finish_job(job_id, status: JobStatus) -> None` (status + `finished_at = now`); `get_file(file_id) -> sqlite3.Row` (single-row fetch, raises `LookupError` when absent); `set_precondition(file_id, generation: int) -> None`; `get_precondition(file_id) -> int | None`; `upsert_slice(file_id, slice_index, offset, length, *, session_uri: str | None = None, crc32c: int | None = None, state: SliceState = SliceState.PENDING, bytes_transferred: int = 0) -> None` (INSERT on first call, UPDATE thereafter, keyed on `(file_id, slice_index)`); `get_slices(file_id) -> list[sqlite3.Row]` (ordered by `slice_index`); `clear_slices(file_id) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/store/test_repository_engine.py`:

```python
import pytest

from mml_cloud_transfer.core.models import Direction, JobStatus, SliceState
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

from tests.store.test_repository import make_files


@pytest.fixture
def repo(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    yield JobRepository(conn)
    conn.close()


@pytest.fixture
def file_id(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1))
    return repo.get_files(job_id)[0]["id"]


def test_start_job_sets_running_and_preserves_the_first_start(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.start_job(job_id)
    first = repo.get_job(job_id)
    assert first["status"] == JobStatus.RUNNING.value
    assert first["started_at"] is not None

    repo.start_job(job_id)  # resume: started_at must not move
    assert repo.get_job(job_id)["started_at"] == first["started_at"]


def test_finish_job_records_status_and_finished_at(repo):
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.finish_job(job_id, JobStatus.INCOMPLETE)
    job = repo.get_job(job_id)
    assert job["status"] == JobStatus.INCOMPLETE.value
    assert job["finished_at"] is not None


def test_get_file_fetches_one_row_or_raises(repo, file_id):
    assert repo.get_file(file_id)["id"] == file_id
    with pytest.raises(LookupError):
        repo.get_file(99999)


def test_precondition_round_trip(repo, file_id):
    assert repo.get_precondition(file_id) is None
    repo.set_precondition(file_id, 0)
    assert repo.get_precondition(file_id) == 0
    repo.set_precondition(file_id, 12345)
    assert repo.get_precondition(file_id) == 12345


def test_upsert_slice_inserts_then_updates(repo, file_id):
    repo.upsert_slice(file_id, 0, offset=0, length=100)
    repo.upsert_slice(file_id, 1, offset=100, length=100)
    repo.upsert_slice(
        file_id, 0, offset=0, length=100,
        session_uri="http://s/u", state=SliceState.UPLOADING, bytes_transferred=40,
    )

    rows = repo.get_slices(file_id)
    assert [r["slice_index"] for r in rows] == [0, 1]
    assert rows[0]["session_uri"] == "http://s/u"
    assert rows[0]["state"] == SliceState.UPLOADING.value
    assert rows[0]["bytes_transferred"] == 40
    assert rows[1]["state"] == SliceState.PENDING.value


def test_slice_crc_survives_a_reopen(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    repo = JobRepository(conn)
    job_id = repo.create_job(
        name="j", direction=Direction.UPLOAD, source_root=r"C:\data", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1))
    fid = repo.get_files(job_id)[0]["id"]
    repo.upsert_slice(fid, 3, offset=300, length=100, crc32c=42, state=SliceState.UPLOADED)
    conn.close()

    conn2 = connect(tmp_path / "jobs.db")
    rows = JobRepository(conn2).get_slices(fid)
    assert rows[0]["crc32c"] == 42
    assert rows[0]["state"] == SliceState.UPLOADED.value
    conn2.close()


def test_clear_slices_removes_all_rows(repo, file_id):
    repo.upsert_slice(file_id, 0, offset=0, length=10)
    repo.upsert_slice(file_id, 1, offset=10, length=10)
    repo.clear_slices(file_id)
    assert repo.get_slices(file_id) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/store/test_repository_engine.py -v`
Expected: FAIL with `AttributeError: 'JobRepository' object has no attribute 'start_job'`.

- [ ] **Step 3: Implement**

Add to `src/mml_cloud_transfer/store/repository.py` (import `SliceState` from
`core.models`; place the job methods with the other job methods, the slice
methods in a new `# ---- slices ----` section):

```python
    def start_job(self, job_id: int) -> None:
        now = _now()
        self._conn.execute(
            "UPDATE jobs SET status = ?, started_at = COALESCE(started_at, ?)"
            " WHERE id = ?",
            (JobStatus.RUNNING.value, now, job_id),
        )

    def finish_job(self, job_id: int, status: JobStatus) -> None:
        self._conn.execute(
            "UPDATE jobs SET status = ?, finished_at = ? WHERE id = ?",
            (status.value, _now(), job_id),
        )

    def get_file(self, file_id: int) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM job_files WHERE id = ?", (file_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"no file with id {file_id}")
        return row

    def set_precondition(self, file_id: int, generation: int) -> None:
        self._conn.execute(
            "UPDATE job_files SET precondition_generation = ? WHERE id = ?",
            (generation, file_id),
        )

    def get_precondition(self, file_id: int) -> int | None:
        row = self._conn.execute(
            "SELECT precondition_generation FROM job_files WHERE id = ?", (file_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"no file with id {file_id}")
        value = row["precondition_generation"]
        return None if value is None else int(value)

    # ---- slices ---------------------------------------------------------

    def upsert_slice(
        self,
        file_id: int,
        slice_index: int,
        *,
        offset: int,
        length: int,
        session_uri: str | None = None,
        crc32c: int | None = None,
        state: SliceState = SliceState.PENDING,
        bytes_transferred: int = 0,
    ) -> None:
        self._conn.execute(
            "INSERT INTO file_slices (file_id, slice_index, offset_bytes,"
            " length_bytes, state, session_uri, crc32c, bytes_transferred)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (file_id, slice_index) DO UPDATE SET"
            " offset_bytes = excluded.offset_bytes,"
            " length_bytes = excluded.length_bytes,"
            " state = excluded.state,"
            " session_uri = excluded.session_uri,"
            " crc32c = excluded.crc32c,"
            " bytes_transferred = excluded.bytes_transferred",
            (
                file_id,
                slice_index,
                offset,
                length,
                state.value,
                session_uri,
                crc32c,
                bytes_transferred,
            ),
        )

    def get_slices(self, file_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM file_slices WHERE file_id = ? ORDER BY slice_index",
            (file_id,),
        ).fetchall()

    def clear_slices(self, file_id: int) -> None:
        self._conn.execute("DELETE FROM file_slices WHERE file_id = ?", (file_id,))
```

Note `upsert_slice` uses keyword-only arguments after `slice_index` — the
test calls `repo.upsert_slice(file_id, 0, offset=0, length=100)`.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/store/test_repository_engine.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Run the whole suite, then commit**

Run: `.venv/Scripts/python -m pytest`

```bash
git add src/mml_cloud_transfer/store/repository.py tests/store/test_repository_engine.py
git commit -m "feat: add slice persistence, precondition capture, and job timestamps"
```

---

### Task 11: The job runner

The orchestrator: takes a scanned job and drives it to a verdict. Per-file dispatch by method, within-run retry with backoff, credential-pause, quarantine, source-change detection with the requeue-once rule, remote scanning for download jobs, the Layer-3 audit, and the final COMPLETE/INCOMPLETE status. All orchestration logic is unit-tested by stubbing the transfer functions at module level — the emulator test at the end proves the wiring.

**Documented deviation:** the spec says the `if_generation_match` precondition is "captured at plan time"; the runner captures it immediately before a file's *first transfer attempt* and persists it (`precondition_generation`), after which every retry and resume reuses the stored value. The protection is identical — a destination that changes after capture still fails with 412 — and it spares the scan phase a metadata round-trip per file.

**Files:**
- Create: `src/mml_cloud_transfer/engine/__init__.py`
- Create: `src/mml_cloud_transfer/engine/runner.py`
- Test: `tests/engine/__init__.py`, `tests/engine/test_runner.py`, `tests/engine/test_runner_emulator.py`

**Interfaces:**
- Consumes: everything — `JobRepository` (incl. Task 10 additions), `RetrySchedule`, `QUARANTINE_ATTEMPTS`, `SizePolicy`, `plan_slices`, `classify`, `ErrorCategory`, `PlannedFile`, `FileState`, `JobStatus`, `Direction`, `TransferMethod`, `SliceState`, `extended_path`, `connect`; `upload_single_shot`, `upload_resumable`, `upload_sliced`, `ChecksumMismatch` from `gcs.uploader`; `download_file` from `gcs.downloader`; `get_meta`, `list_prefix` from `gcs.objects`.
- Produces: `JobPaused(Exception)`; `EngineOptions(policy: SizePolicy | None = None, file_workers: int = 4, slice_workers: int = 4, chunk_size: int = 8 * 1024 * 1024, download_range_bytes: int = 128 * 1024 * 1024, retry: RetrySchedule = RetrySchedule(), audit: bool = True)` (plain dataclass, not frozen); `scan_remote(ctx, db_path, job_id, *, policy: SizePolicy | None = None) -> int` (lists the bucket prefix into the manifest for download jobs; returns file count); `run_job(db_path, job_id, ctx, *, options: EngineOptions | None = None, sleep: Callable[[float], None] = time.sleep, rng: random.Random | None = None) -> JobStatus`; `_stat_source(path: str) -> tuple[int, int]` (module-level `(size, mtime_ns)` helper — public-ish so tests can monkeypatch scripted stat sequences).

- [ ] **Step 1: Write the failing orchestration tests**

Create `tests/engine/__init__.py` (empty) and `tests/engine/test_runner.py`:

```python
"""Orchestration tests — no network. Transfer functions are stubbed at the
runner's module level; a temp SQLite DB and real source files are used so
state transitions are exercised for real."""

import pytest

import mml_cloud_transfer.engine.runner as runner
from mml_cloud_transfer.core.errors import ErrorCategory
from mml_cloud_transfer.core.models import Direction, FileState, JobStatus
from mml_cloud_transfer.core.retry import RetrySchedule
from mml_cloud_transfer.engine.runner import EngineOptions, run_job
from mml_cloud_transfer.gcs.objects import ObjectMeta
from mml_cloud_transfer.gcs.uploader import UploadResult
from mml_cloud_transfer.cli.scan_command import run_scan
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


class FakeApiError(Exception):
    def __init__(self, code):
        super().__init__(f"api {code}")
        self.code = code


def verified(size):
    return UploadResult(
        state="verified", local_crc32c=1, remote_crc32c=1,
        generation=7, sha256=None, bytes_sent=size,
    )


@pytest.fixture
def job(tmp_path):
    """A scanned upload job over two small real files."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.bin").write_bytes(b"a" * 100)
    (src / "b.bin").write_bytes(b"b" * 200)
    db = tmp_path / "jobs.db"
    outcome = run_scan(
        db_path=db, source_root=str(src), dest_prefix="p", job_name="j",
        follow_extended=False,
    )
    return db, outcome.job_id


def opts(**kw):
    kw.setdefault("file_workers", 1)
    kw.setdefault("retry", RetrySchedule(max_attempts=3, base_delay=0.01))
    kw.setdefault("audit", False)
    return EngineOptions(**kw)


def files_by_state(db, job_id):
    conn = connect(db)
    rows = JobRepository(conn).get_files(job_id)
    conn.close()
    return {r["relative_path"]: r["state"] for r in rows}


def test_happy_path_reaches_complete(job, monkeypatch):
    db, job_id = job
    monkeypatch.setattr(runner, "upload_single_shot", lambda *a, **k: verified(100))
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)

    status = run_job(db, job_id, ctx=None, options=opts())
    assert status is JobStatus.COMPLETE
    assert set(files_by_state(db, job_id).values()) == {FileState.VERIFIED.value}


def test_transient_errors_retry_with_sleeps(job, monkeypatch):
    db, job_id = job
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise FakeApiError(503)
        return verified(100)

    slept = []
    monkeypatch.setattr(runner, "upload_single_shot", flaky)
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    run_job(db, job_id, ctx=None, options=opts(), sleep=slept.append)
    assert calls["n"] >= 3
    assert len(slept) == 2  # two retries for the first file, none for the second


def test_non_transient_errors_fail_without_retry(job, monkeypatch):
    db, job_id = job
    slept = []
    monkeypatch.setattr(
        runner, "upload_single_shot",
        lambda *a, **k: (_ for _ in ()).throw(FakeApiError(404)),
    )
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    status = run_job(db, job_id, ctx=None, options=opts(), sleep=slept.append)
    assert status is JobStatus.INCOMPLETE
    assert slept == []
    assert set(files_by_state(db, job_id).values()) == {FileState.FAILED.value}


def test_credential_errors_pause_the_job(job, monkeypatch):
    db, job_id = job
    monkeypatch.setattr(
        runner, "upload_single_shot",
        lambda *a, **k: (_ for _ in ()).throw(FakeApiError(403)),
    )
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    status = run_job(db, job_id, ctx=None, options=opts())
    assert status is JobStatus.PAUSED
    states = files_by_state(db, job_id)
    # The pause fires on the first credential failure; whether the second
    # file was attempted before the pool cancelled is a race, so assert
    # only that nothing progressed past FAILED/PENDING.
    assert FileState.FAILED.value in states.values()
    assert set(states.values()) <= {FileState.FAILED.value, FileState.PENDING.value}


def test_cumulative_attempts_reach_quarantine(job, monkeypatch):
    db, job_id = job
    conn = connect(db)
    repo = JobRepository(conn)
    first = repo.get_files(job_id)[0]["id"]
    for _ in range(14):
        repo.mark_failed(first, ErrorCategory.NETWORK, "past runs")
    conn.close()

    monkeypatch.setattr(
        runner, "upload_single_shot",
        lambda *a, **k: (_ for _ in ()).throw(FakeApiError(404)),
    )
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    run_job(db, job_id, ctx=None, options=opts())
    states = files_by_state(db, job_id)
    assert FileState.QUARANTINED.value in states.values()


def test_changed_source_is_requeued_once_then_transferred(job, monkeypatch, tmp_path):
    db, job_id = job
    # Grow a.bin after the scan: first pass must mark it changed with fresh
    # metadata, the second pass must transfer it.
    (tmp_path / "src" / "a.bin").write_bytes(b"a" * 150)

    sent = []
    monkeypatch.setattr(
        runner, "upload_single_shot",
        lambda ctx, path, name, **k: sent.append(name) or verified(100),
    )
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    status = run_job(db, job_id, ctx=None, options=opts())
    assert status is JobStatus.COMPLETE
    assert sorted(sent) == ["p/a.bin", "p/b.bin"]

    conn = connect(db)
    row = [r for r in JobRepository(conn).get_files(job_id) if r["relative_path"] == "a.bin"][0]
    conn.close()
    assert row["size_bytes"] == 150
    assert row["state"] == FileState.VERIFIED.value


def test_source_changing_twice_fails_as_source_changed(job, monkeypatch, tmp_path):
    db, job_id = job
    stats = {"calls": 0}
    real_stat = runner._stat_source

    def restless(path):
        if path.endswith("a.bin"):
            stats["calls"] += 1
            return (100 + stats["calls"], 1_700_000_000_000_000_000 + stats["calls"])
        return real_stat(path)

    monkeypatch.setattr(runner, "_stat_source", restless)
    monkeypatch.setattr(runner, "upload_single_shot", lambda *a, **k: verified(100))
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    status = run_job(db, job_id, ctx=None, options=opts())
    assert status is JobStatus.INCOMPLETE

    conn = connect(db)
    row = [r for r in JobRepository(conn).get_files(job_id) if r["relative_path"] == "a.bin"][0]
    conn.close()
    assert row["state"] == FileState.FAILED.value
    assert row["error_category"] == ErrorCategory.SOURCE_CHANGED.value


def test_missing_source_fails_as_not_found(job, monkeypatch, tmp_path):
    db, job_id = job
    (tmp_path / "src" / "a.bin").unlink()
    monkeypatch.setattr(runner, "upload_single_shot", lambda *a, **k: verified(200))
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    run_job(db, job_id, ctx=None, options=opts())
    states = files_by_state(db, job_id)
    assert states["a.bin"] == FileState.FAILED.value
    assert states["b.bin"] == FileState.VERIFIED.value


def test_audit_catches_an_object_missing_from_the_listing(job, monkeypatch):
    db, job_id = job
    monkeypatch.setattr(runner, "upload_single_shot", lambda *a, **k: verified(100))
    monkeypatch.setattr(runner, "get_meta", lambda ctx, name: None)
    # Listing knows only one of the two objects, with matching size/crc.
    monkeypatch.setattr(
        runner, "list_prefix",
        lambda ctx, prefix: iter(
            [ObjectMeta(name="p/a.bin", size=100, crc32c=1, generation=7)]
        ),
    )
    status = run_job(db, job_id, ctx=None, options=opts(audit=True))
    assert status is JobStatus.INCOMPLETE
    states = files_by_state(db, job_id)
    assert states["b.bin"] == FileState.FAILED.value
    assert states["a.bin"] == FileState.VERIFIED.value


def test_precondition_is_captured_before_the_first_attempt(job, monkeypatch):
    db, job_id = job
    seen = {}

    def capture(ctx, path, name, *, precondition_generation, **k):
        seen[name] = precondition_generation
        return verified(100)

    monkeypatch.setattr(runner, "upload_single_shot", capture)
    monkeypatch.setattr(
        runner, "get_meta",
        lambda ctx, name: (
            ObjectMeta(name=name, size=5, crc32c=9, generation=42)
            if name == "p/a.bin" else None
        ),
    )
    run_job(db, job_id, ctx=None, options=opts())
    assert seen == {"p/a.bin": 42, "p/b.bin": 0}
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/engine/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mml_cloud_transfer.engine'`.

- [ ] **Step 3: Implement the runner**

Create `src/mml_cloud_transfer/engine/__init__.py` (empty) and
`src/mml_cloud_transfer/engine/runner.py`:

```python
"""Job orchestration: scanned manifest in, COMPLETE/INCOMPLETE verdict out.

Threading model: a pool of file workers; each worker opens its own SQLite
connection (WAL + busy_timeout make this safe) and drives one file at a
time through retry/backoff. Slice-level parallelism happens inside
upload_sliced/download_file on top of this.
"""

from __future__ import annotations

import os
import random
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from mml_cloud_transfer.core.errors import ErrorCategory, classify
from mml_cloud_transfer.core.models import (
    Direction,
    FileState,
    JobStatus,
    PlannedFile,
    SliceState,
    TransferMethod,
)
from mml_cloud_transfer.core.paths import extended_path
from mml_cloud_transfer.core.retry import QUARANTINE_ATTEMPTS, RetrySchedule
from mml_cloud_transfer.core.slicing import SizePolicy, plan_slices
from mml_cloud_transfer.gcs.downloader import download_file
from mml_cloud_transfer.gcs.objects import get_meta, list_prefix
from mml_cloud_transfer.gcs.uploader import (
    ChecksumMismatch,
    upload_resumable,
    upload_single_shot,
    upload_sliced,
)
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


class JobPaused(Exception):
    """Raised inside a worker when retrying anything else is pointless."""


@dataclass
class EngineOptions:
    policy: SizePolicy | None = None
    file_workers: int = 4
    slice_workers: int = 4
    chunk_size: int = 8 * 1024 * 1024
    download_range_bytes: int = 128 * 1024 * 1024
    retry: RetrySchedule = field(default_factory=RetrySchedule)
    audit: bool = True


def _stat_source(path: str) -> tuple[int, int]:
    """(size, mtime_ns) — module-level so tests can script stat sequences."""
    stat = os.stat(path)
    return stat.st_size, stat.st_mtime_ns


def _download_dest(source_root: str, relative_path: str) -> str:
    return os.path.join(extended_path(source_root), *relative_path.split("/"))


def scan_remote(ctx, db_path, job_id: int, *, policy: SizePolicy | None = None) -> int:
    """Plan a download job by listing the bucket prefix into the manifest."""
    conn = connect(db_path)
    try:
        repo = JobRepository(conn)
        job = repo.get_job(job_id)
        prefix = job["dest_prefix"].strip("/")
        lead = f"{prefix}/" if prefix else ""
        repo.set_job_status(job_id, JobStatus.SCANNING)
        repo.record_event(job_id, "scan_started", f"prefix={lead}")

        batch: list[PlannedFile] = []
        count = 0
        for meta in list_prefix(ctx, lead):
            relative = meta.name[len(lead):]
            if not relative:  # a zero-byte "directory" placeholder object
                continue
            batch.append(
                PlannedFile(
                    relative_path=relative,
                    source_path=meta.name,
                    size_bytes=meta.size,
                    mtime_ns=0,
                )
            )
            count += 1
            if len(batch) >= 5000:
                repo.add_planned_files(job_id, batch, policy=policy)
                batch.clear()
        if batch:
            repo.add_planned_files(job_id, batch, policy=policy)

        repo.record_event(job_id, "scan_finished", f"files={count}")
        repo.set_job_status(job_id, JobStatus.PENDING)
        return count
    finally:
        conn.close()


def _classify_transfer_error(exc: Exception):
    if isinstance(exc, ChecksumMismatch):
        return ErrorCategory.CHECKSUM_MISMATCH, False, False
    cls = classify(exc)
    return cls.category, cls.transient, cls.pauses_job


def _transfer_once(ctx, repo: JobRepository, job, row, options: EngineOptions) -> None:
    """One attempt at one file. Raises on failure; records success itself."""
    file_id = row["id"]
    method = TransferMethod(row["method"])
    with_sha256 = bool(job["audit_hash"])

    if job["direction"] == Direction.UPLOAD.value:
        precondition = repo.get_precondition(file_id)
        if precondition is None:
            meta = get_meta(ctx, row["object_name"])
            precondition = meta.generation if meta is not None else 0
            repo.set_precondition(file_id, precondition)

        if method is TransferMethod.SINGLE_SHOT:
            result = upload_single_shot(
                ctx, row["source_path"], row["object_name"],
                precondition_generation=precondition, with_sha256=with_sha256,
            )
        elif method is TransferMethod.RESUMABLE:
            slices = repo.get_slices(file_id)
            uri = slices[0]["session_uri"] if slices else None

            def on_progress(session_uri: str, committed: int) -> None:
                repo.upsert_slice(
                    file_id, 0, offset=0, length=row["size_bytes"],
                    session_uri=session_uri, state=SliceState.UPLOADING,
                    bytes_transferred=committed,
                )
                repo.heartbeat(file_id, committed)

            result = upload_resumable(
                ctx, row["source_path"], row["object_name"], row["size_bytes"],
                precondition_generation=precondition, session_uri=uri,
                with_sha256=with_sha256, chunk_size=options.chunk_size,
                on_progress=on_progress,
            )
        else:
            specs = plan_slices(row["size_bytes"], policy=options.policy)
            spec_by_index = {s.index: s for s in specs}
            stored = {
                r["slice_index"]: (
                    r["session_uri"],
                    r["crc32c"] if r["state"] == SliceState.UPLOADED.value else None,
                )
                for r in repo.get_slices(file_id)
            }

            def on_slice(idx: int, uri: str | None, committed: int, crc: int | None) -> None:
                spec = spec_by_index[idx]
                repo.upsert_slice(
                    file_id, idx, offset=spec.offset, length=spec.length,
                    session_uri=uri, crc32c=crc,
                    state=SliceState.UPLOADED if crc is not None else SliceState.UPLOADING,
                    bytes_transferred=committed,
                )
                repo.heartbeat(file_id, committed)

            result = upload_sliced(
                ctx, row["source_path"], row["object_name"], row["size_bytes"],
                precondition_generation=precondition, policy=options.policy,
                slice_states=stored, max_workers=options.slice_workers,
                chunk_size=options.chunk_size, with_sha256=with_sha256,
                on_progress=on_slice,
            )
    else:
        from mml_cloud_transfer.gcs.downloader import plan_ranges

        dest = _download_dest(job["source_root"], row["relative_path"])
        stored_ranges = {
            r["slice_index"]: r["crc32c"]
            for r in repo.get_slices(file_id)
            if r["state"] == SliceState.UPLOADED.value and r["crc32c"] is not None
        }
        range_by_index = {
            s.index: s
            for s in plan_ranges(
                row["size_bytes"], range_bytes=options.download_range_bytes
            )
        }

        def on_range(idx: int, done: int, crc: int | None) -> None:
            if crc is not None:
                spec = range_by_index[idx]
                repo.upsert_slice(
                    file_id, idx, offset=spec.offset, length=spec.length,
                    crc32c=crc, state=SliceState.UPLOADED, bytes_transferred=done,
                )
            repo.heartbeat(file_id, done)

        result = download_file(
            ctx, row["object_name"], dest,
            range_states=stored_ranges,
            range_bytes=options.download_range_bytes,
            max_workers=options.slice_workers,
            with_sha256=with_sha256, on_progress=on_range,
        )

    if result.state == "skipped":
        repo.mark_skipped(file_id)
    else:
        repo.mark_verified(
            file_id,
            local_crc32c=result.local_crc32c,
            remote_crc32c=result.remote_crc32c,
            generation=result.generation,
            sha256=result.sha256,
        )
    repo.clear_slices(file_id)


def _process_file(
    db_path, ctx, job, row, options: EngineOptions,
    sleep: Callable[[float], None], rng: random.Random,
) -> None:
    conn = connect(db_path)
    try:
        repo = JobRepository(conn)
        file_id = row["id"]

        if job["direction"] == Direction.UPLOAD.value:
            try:
                size, mtime = _stat_source(row["source_path"])
            except OSError as exc:
                cls = classify(exc)
                repo.mark_failed(file_id, cls.category, str(exc)[:500])
                return
            if size != row["size_bytes"] or mtime != row["mtime_ns"]:
                if row["state"] == FileState.CHANGED.value:
                    repo.mark_failed(
                        file_id, ErrorCategory.SOURCE_CHANGED,
                        "source changed again while being transferred",
                    )
                else:
                    repo.mark_changed(file_id, size, mtime)
                    repo.record_event(
                        job["id"], "source_changed", row["relative_path"], file_id
                    )
                return

        delays = iter(options.retry.delays(rng))
        for attempt in range(options.retry.max_attempts):
            repo.mark_transferring(file_id)
            try:
                _transfer_once(ctx, repo, job, row, options)
                return
            except Exception as exc:
                category, transient, pauses = _classify_transfer_error(exc)
                repo.mark_failed(file_id, category, str(exc)[:500])
                cumulative = repo.get_file(file_id)["attempts"]
                if cumulative >= QUARANTINE_ATTEMPTS:
                    repo.quarantine(file_id)
                    repo.record_event(
                        job["id"], "quarantined", row["relative_path"], file_id
                    )
                    return
                if pauses:
                    raise JobPaused(str(exc)) from exc
                if not transient or attempt == options.retry.max_attempts - 1:
                    return
                sleep(next(delays))
    finally:
        conn.close()


def _audit(ctx, repo: JobRepository, job) -> None:
    job_id = job["id"]
    rows = [
        r for r in repo.get_files(job_id)
        if r["state"] in (FileState.VERIFIED.value, FileState.SKIPPED.value)
    ]
    mismatches = 0
    if job["direction"] == Direction.UPLOAD.value:
        prefix = job["dest_prefix"].strip("/")
        lead = f"{prefix}/" if prefix else ""
        remote = {m.name: m for m in list_prefix(ctx, lead)}
        for row in rows:
            meta = remote.get(row["object_name"])
            if meta is None:
                repo.mark_failed(
                    row["id"], ErrorCategory.NOT_FOUND, "missing at audit"
                )
                mismatches += 1
            elif meta.size != row["size_bytes"] or (
                row["remote_crc32c"] is not None
                and meta.crc32c != row["remote_crc32c"]
            ):
                repo.mark_failed(
                    row["id"], ErrorCategory.CHECKSUM_MISMATCH, "audit mismatch"
                )
                mismatches += 1
    else:
        for row in rows:
            dest = _download_dest(job["source_root"], row["relative_path"])
            try:
                size, _ = _stat_source(dest)
            except OSError:
                repo.mark_failed(
                    row["id"], ErrorCategory.NOT_FOUND, "missing at audit"
                )
                mismatches += 1
                continue
            if size != row["size_bytes"]:
                repo.mark_failed(
                    row["id"], ErrorCategory.CHECKSUM_MISMATCH, "audit size mismatch"
                )
                mismatches += 1
    repo.record_event(
        job_id, "audit_finished", f"checked={len(rows)} mismatches={mismatches}"
    )


def run_job(
    db_path,
    job_id: int,
    ctx,
    *,
    options: EngineOptions | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> JobStatus:
    options = options or EngineOptions()
    rng = rng or random.Random()

    conn = connect(db_path)
    try:
        repo = JobRepository(conn)
        job = dict(repo.get_job(job_id))
        repo.start_job(job_id)
        repo.record_event(job_id, "run_started")
        repo.reset_stale_transfers(job_id)

        paused = False
        for _pass in range(2):  # second pass picks up files marked `changed`
            pending = [dict(r) for r in repo.iter_pending_files(job_id)]
            if _pass == 1:
                pending = [
                    r for r in pending if r["state"] == FileState.CHANGED.value
                ]
            if not pending:
                break
            with ThreadPoolExecutor(max_workers=options.file_workers) as pool:
                futures = [
                    pool.submit(
                        _process_file, db_path, ctx, job, row, options, sleep, rng
                    )
                    for row in pending
                ]
                for future in futures:
                    try:
                        future.result()
                    except JobPaused as exc:
                        repo.record_event(job_id, "run_paused", str(exc)[:200])
                        pool.shutdown(cancel_futures=True)
                        paused = True
                        break
            if paused:
                break

        if paused:
            repo.finish_job(job_id, JobStatus.PAUSED)
            return JobStatus.PAUSED

        if options.audit:
            _audit(ctx, repo, job)

        status = repo.job_verdict(job_id)
        repo.finish_job(job_id, status)
        repo.record_event(job_id, "run_finished", status.value)
        return status
    finally:
        conn.close()
```

- [ ] **Step 4: Run the orchestration tests**

Run: `.venv/Scripts/python -m pytest tests/engine/test_runner.py -v`
Expected: PASS, 10 tests, no network.

- [ ] **Step 5: Write the emulator round-trip test**

Create `tests/engine/test_runner_emulator.py`:

```python
import pytest

from mml_cloud_transfer.core.models import Direction, JobStatus
from mml_cloud_transfer.core.retry import RetrySchedule
from mml_cloud_transfer.core.slicing import SizePolicy
from mml_cloud_transfer.engine.runner import EngineOptions, run_job, scan_remote
from mml_cloud_transfer.gcs.client import make_context
from mml_cloud_transfer.cli.scan_command import run_scan
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

TINY = SizePolicy(
    single_shot_max=64 * 1024,
    resumable_max=256 * 1024,
    min_slice=256 * 1024,
    max_components=32,
)


@pytest.fixture
def ctx(emulator, emulator_client):
    _, bucket_name = emulator_client
    return make_context(bucket_name, emulator_endpoint=emulator.endpoint)


@pytest.mark.emulator
def test_upload_then_download_round_trip(ctx, tmp_path):
    # Three files, one per method under the tiny policy.
    src = tmp_path / "src"
    (src / "deep").mkdir(parents=True)
    (src / "tiny.bin").write_bytes(b"t" * 10_000)
    (src / "deep" / "medium.bin").write_bytes(bytes(range(256)) * 512)   # 128 KiB
    (src / "big.bin").write_bytes(bytes(range(256)) * 2400)              # 600 KiB

    db = tmp_path / "jobs.db"
    outcome = run_scan(
        db_path=db, source_root=str(src), dest_prefix="rt", job_name="up",
        follow_extended=False, policy=TINY,
    )
    options = EngineOptions(
        policy=TINY, file_workers=2, chunk_size=256 * 1024,
        download_range_bytes=256 * 1024,
        retry=RetrySchedule(max_attempts=2, base_delay=0.01),
    )
    status = run_job(db, outcome.job_id, ctx, options=options)
    assert status is JobStatus.COMPLETE

    # Download the prefix back to a fresh directory and compare bytes.
    conn = connect(db)
    repo = JobRepository(conn)
    down_root = tmp_path / "down"
    down_id = repo.create_job(
        name="down", direction=Direction.DOWNLOAD,
        source_root=str(down_root), dest_prefix="rt",
    )
    conn.close()

    assert scan_remote(ctx, db, down_id, policy=TINY) == 3
    status = run_job(db, down_id, ctx, options=options)
    assert status is JobStatus.COMPLETE

    for rel in ("tiny.bin", "deep/medium.bin", "big.bin"):
        original = (src / rel).read_bytes()
        fetched = (down_root / rel).read_bytes()
        assert fetched == original, f"{rel} did not round-trip"
```

Note `run_scan` gains a `policy` passthrough here — add the keyword-only
parameter `policy: SizePolicy | None = None` to `run_scan` in
`src/mml_cloud_transfer/cli/scan_command.py` and forward it to both
`repo.add_planned_files(job_id, batch, policy=policy)` call sites. That is
this task's only CLI-file change.

- [ ] **Step 6: Run the emulator test, then the whole suite**

Run: `.venv/Scripts/python -m pytest tests/engine/ -v`
Expected: PASS (10 orchestration + 1 emulator round-trip).

Run: `.venv/Scripts/python -m pytest`

- [ ] **Step 7: Commit**

```bash
git add src/mml_cloud_transfer/engine/ src/mml_cloud_transfer/cli/scan_command.py tests/engine/
git commit -m "feat: add job runner with retry, pause, changed-detection, and audit"
```

---

### Task 12: Validation reports

Every finished run writes the spec's three artifacts: `summary.json` (machine-readable), `manifest.csv` (one row per file), and a self-contained `report.html` a user can email. The verdict rule is presentation of what the runner already decided — never recomputed differently here.

**Files:**
- Create: `src/mml_cloud_transfer/engine/report.py`
- Test: `tests/engine/test_report.py`

**Interfaces:**
- Consumes: `JobRepository`, `connect`; `crc32c_to_base64` from `core.hashing`; `display_path` from `core.paths`; `FileState`, `JobStatus` from `core.models`.
- Produces: `ReportPaths(summary_json: Path, manifest_csv: Path, report_html: Path)` (frozen dataclass); `write_report(db_path, job_id: int, out_dir: str | os.PathLike[str], *, bucket: str | None = None) -> ReportPaths`. `out_dir` is created if needed; files are always named `summary.json`, `manifest.csv`, `report.html`.

- [ ] **Step 1: Write the failing test**

Create `tests/engine/test_report.py`:

```python
import csv
import json

import pytest

from mml_cloud_transfer.core.errors import ErrorCategory
from mml_cloud_transfer.core.hashing import crc32c_to_base64
from mml_cloud_transfer.core.models import Direction, JobStatus
from mml_cloud_transfer.engine.report import write_report
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

from tests.store.test_repository import make_files


@pytest.fixture
def finished_job(tmp_path):
    """A job with 2 verified files, 1 skipped, and 2 failures in one category."""
    db = tmp_path / "jobs.db"
    conn = connect(db)
    repo = JobRepository(conn)
    job_id = repo.create_job(
        name="Run 47", direction=Direction.UPLOAD,
        source_root=r"\\?\C:\data\run47", dest_prefix="archive/run47",
    )
    repo.add_planned_files(job_id, make_files(5, size=1000))
    rows = repo.get_files(job_id)
    repo.mark_verified(rows[0]["id"], local_crc32c=11, remote_crc32c=11, generation=1)
    repo.mark_verified(
        rows[1]["id"], local_crc32c=22, remote_crc32c=22, generation=2, sha256="ab" * 32
    )
    repo.mark_skipped(rows[2]["id"])
    repo.mark_failed(rows[3]["id"], ErrorCategory.FILE_LOCKED, "in use by EDITOR.EXE")
    repo.mark_failed(rows[4]["id"], ErrorCategory.FILE_LOCKED, "in use by EDITOR.EXE")
    repo.start_job(job_id)
    repo.finish_job(job_id, JobStatus.INCOMPLETE)
    conn.close()
    return db, job_id


def test_summary_json_carries_verdict_counts_and_identity(finished_job, tmp_path):
    db, job_id = finished_job
    paths = write_report(db, job_id, tmp_path / "out", bucket="mml-archive")

    summary = json.loads(paths.summary_json.read_text(encoding="utf-8"))
    assert summary["job_id"] == job_id
    assert summary["name"] == "Run 47"
    assert summary["direction"] == "upload"
    assert summary["bucket"] == "mml-archive"
    assert summary["dest_prefix"] == "archive/run47"
    assert summary["verdict"] == "INCOMPLETE"
    assert summary["counts"] == {"verified": 2, "skipped": 1, "failed": 2}
    assert summary["planned_files"] == 5
    assert summary["planned_bytes"] == 5000
    assert summary["errors_by_category"] == {"file_locked": 2}
    # The stored \\?\ prefix never reaches the report.
    assert summary["source_root"] == "C:\\data\\run47"


def test_manifest_csv_has_one_row_per_file_with_base64_crcs(finished_job, tmp_path):
    db, job_id = finished_job
    paths = write_report(db, job_id, tmp_path / "out")

    with paths.manifest_csv.open(newline="", encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))
    assert len(rows) == 5
    by_path = {r["relative_path"]: r for r in rows}
    verified = by_path["run47/file0.tif"]
    assert verified["state"] == "verified"
    assert verified["local_crc32c"] == crc32c_to_base64(11)
    assert verified["remote_crc32c"] == crc32c_to_base64(11)
    failed = by_path["run47/file3.tif"]
    assert failed["error_category"] == "file_locked"
    assert failed["local_crc32c"] == ""


def test_html_report_is_self_contained_and_groups_failures(finished_job, tmp_path):
    db, job_id = finished_job
    paths = write_report(db, job_id, tmp_path / "out")

    html = paths.report_html.read_text(encoding="utf-8")
    assert "INCOMPLETE" in html
    assert "file_locked" in html
    assert "in use by EDITOR.EXE" in html
    assert "run47/file3.tif" in html
    # Self-contained: no external references of any kind.
    assert "http://" not in html and "https://" not in html
    assert "<script src" not in html and "<link" not in html


def test_complete_job_reports_complete(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    repo = JobRepository(conn)
    job_id = repo.create_job(
        name="ok", direction=Direction.UPLOAD, source_root=r"C:\x", dest_prefix=""
    )
    repo.add_planned_files(job_id, make_files(1))
    repo.mark_verified(
        repo.get_files(job_id)[0]["id"], local_crc32c=1, remote_crc32c=1, generation=1
    )
    repo.finish_job(job_id, JobStatus.COMPLETE)
    conn.close()

    paths = write_report(db, job_id, tmp_path / "out")
    summary = json.loads(paths.summary_json.read_text(encoding="utf-8"))
    assert summary["verdict"] == "COMPLETE"
    assert "COMPLETE" in paths.report_html.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/engine/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mml_cloud_transfer.engine.report'`.

- [ ] **Step 3: Implement**

Create `src/mml_cloud_transfer/engine/report.py`:

```python
"""Job reports: summary.json, manifest.csv, and a self-contained report.html.

The verdict shown here is the job's stored status — reports present what
the runner decided, they never re-derive it.
"""

from __future__ import annotations

import csv
import html
import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mml_cloud_transfer.core.hashing import crc32c_to_base64
from mml_cloud_transfer.core.models import JobStatus
from mml_cloud_transfer.core.paths import display_path
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

_CSV_COLUMNS = [
    "relative_path", "object_name", "size_bytes", "method", "state",
    "local_crc32c", "remote_crc32c", "sha256", "generation", "attempts",
    "error_category", "error_message", "started_at", "finished_at",
]

_MAX_FAILURES_SHOWN = 50


@dataclass(frozen=True, slots=True)
class ReportPaths:
    summary_json: Path
    manifest_csv: Path
    report_html: Path


def _duration_seconds(started: str | None, finished: str | None) -> float | None:
    if not started or not finished:
        return None
    delta = datetime.fromisoformat(finished) - datetime.fromisoformat(started)
    return max(delta.total_seconds(), 0.0)


def _b64_or_empty(value: int | None) -> str:
    return crc32c_to_base64(value) if value is not None else ""


def write_report(
    db_path,
    job_id: int,
    out_dir: str | os.PathLike[str],
    *,
    bucket: str | None = None,
) -> ReportPaths:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    conn = connect(db_path)
    try:
        repo = JobRepository(conn)
        job = repo.get_job(job_id)
        rows = repo.get_files(job_id)
    finally:
        conn.close()

    counts = Counter(r["state"] for r in rows)
    failures = [r for r in rows if r["error_category"] is not None]
    errors_by_category = Counter(r["error_category"] for r in failures)
    verdict = (
        "COMPLETE" if job["status"] == JobStatus.COMPLETE.value else "INCOMPLETE"
    )
    duration = _duration_seconds(job["started_at"], job["finished_at"])
    verified_bytes = sum(
        r["size_bytes"] for r in rows if r["state"] in ("verified", "skipped")
    )

    summary = {
        "job_id": job_id,
        "name": job["name"],
        "direction": job["direction"],
        "bucket": bucket,
        "source_root": display_path(job["source_root"]),
        "dest_prefix": job["dest_prefix"],
        "status": job["status"],
        "verdict": verdict,
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "duration_seconds": duration,
        "planned_files": job["planned_files"],
        "planned_bytes": job["planned_bytes"],
        "verified_or_skipped_bytes": verified_bytes,
        "throughput_bytes_per_second": (
            verified_bytes / duration if duration else None
        ),
        "counts": dict(counts),
        "errors_by_category": dict(errors_by_category),
    }
    summary_path = out / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    csv_path = out / "manifest.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "relative_path": r["relative_path"],
                    "object_name": r["object_name"],
                    "size_bytes": r["size_bytes"],
                    "method": r["method"],
                    "state": r["state"],
                    "local_crc32c": _b64_or_empty(r["local_crc32c"]),
                    "remote_crc32c": _b64_or_empty(r["remote_crc32c"]),
                    "sha256": r["sha256"] or "",
                    "generation": r["generation"] or "",
                    "attempts": r["attempts"],
                    "error_category": r["error_category"] or "",
                    "error_message": r["error_message"] or "",
                    "started_at": r["started_at"] or "",
                    "finished_at": r["finished_at"] or "",
                }
            )

    html_path = out / "report.html"
    html_path.write_text(_render_html(summary, failures), encoding="utf-8")

    return ReportPaths(
        summary_json=summary_path, manifest_csv=csv_path, report_html=html_path
    )


def _render_html(summary: dict, failures) -> str:
    ok = summary["verdict"] == "COMPLETE"
    banner_color = "#166534" if ok else "#991b1b"
    banner_bg = "#dcfce7" if ok else "#fee2e2"

    def esc(value) -> str:
        return html.escape(str(value if value is not None else ""))

    stats = "".join(
        f"<tr><th>{esc(label)}</th><td>{esc(value)}</td></tr>"
        for label, value in [
            ("Job", f'#{summary["job_id"]} — {summary["name"]}'),
            ("Direction", summary["direction"]),
            ("Bucket", summary["bucket"] or "—"),
            ("Source", summary["source_root"]),
            ("Destination prefix", summary["dest_prefix"] or "(bucket root)"),
            ("Started", summary["started_at"] or "—"),
            ("Finished", summary["finished_at"] or "—"),
            ("Planned", f'{summary["planned_files"]} files, {summary["planned_bytes"]} bytes'),
            ("File states", ", ".join(f"{k}: {v}" for k, v in sorted(summary["counts"].items()))),
        ]
    )

    failure_sections = []
    by_category: dict[str, list] = {}
    for row in failures:
        by_category.setdefault(row["error_category"], []).append(row)
    for category, rows in sorted(by_category.items()):
        shown = rows[:_MAX_FAILURES_SHOWN]
        items = "".join(
            f"<li><code>{esc(r['relative_path'])}</code> — {esc(r['error_message'])}</li>"
            for r in shown
        )
        more = (
            f"<p>… and {len(rows) - len(shown)} more.</p>"
            if len(rows) > len(shown)
            else ""
        )
        failure_sections.append(
            f"<h3>{esc(category)} ({len(rows)})</h3><ul>{items}</ul>{more}"
        )
    failures_html = (
        "".join(failure_sections) if failure_sections else "<p>No failures.</p>"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Transfer report — {esc(summary["name"])}</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 60rem; margin: 2rem auto; padding: 0 1rem; color: #111; }}
.banner {{ background: {banner_bg}; color: {banner_color}; padding: 1rem 1.5rem; border-radius: 8px; font-size: 1.4rem; font-weight: 700; }}
table {{ border-collapse: collapse; margin: 1.5rem 0; }}
th {{ text-align: left; padding: .3rem 1rem .3rem 0; vertical-align: top; }}
td {{ padding: .3rem 0; }}
code {{ background: #f1f5f9; padding: .1rem .3rem; border-radius: 3px; }}
</style>
</head>
<body>
<div class="banner">{esc(summary["verdict"])}</div>
<table>{stats}</table>
<h2>Failures</h2>
{failures_html}
</body>
</html>
"""
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/engine/test_report.py -v`
Expected: PASS, 4 tests, no network.

- [ ] **Step 5: Commit**

```bash
git add src/mml_cloud_transfer/engine/report.py tests/engine/test_report.py
git commit -m "feat: add summary.json, manifest.csv, and self-contained HTML report"
```

---

### Task 13: CLI commands and the interrupt-and-resume test

The phase deliverable: `mmlct transfer` runs a job end-to-end and writes the report; `mmlct resume` continues an interrupted one; `mmlct status` shows jobs; `mmlct report` re-exports. The defining test kills a transfer subprocess mid-flight and proves resume completes with everything verified. A `real_bucket`-marked round-trip covers the same path against a real bucket.

**Files:**
- Create: `src/mml_cloud_transfer/cli/transfer_command.py`
- Modify: `src/mml_cloud_transfer/cli/__main__.py`
- Test: `tests/cli/test_transfer_cli.py`, `tests/cli/test_interrupt_resume.py`

**Interfaces:**
- Consumes: `make_context`; `run_scan` (with its Task-11 `policy` parameter); `scan_remote`, `run_job`, `EngineOptions`; `write_report`; `SizePolicy`; `JobRepository`, `connect`; `Direction`, `JobStatus`; `display_path`.
- Produces: `parse_size_policy(text: str) -> SizePolicy` (format `"single_shot_max,resumable_max,min_slice"`, integers, max_components fixed at 32); `run_transfer(args) -> int`, `run_resume(args) -> int`, `run_status(args) -> int`, `run_report_cmd(args) -> int` (each takes the parsed argparse namespace and returns the exit code); `main` gains `transfer`, `resume`, `status`, `report` subcommands. Exit code 0 = COMPLETE, 1 = anything else. Default report directory: `<db-parent>/reports/job-<id>/`.

- [ ] **Step 1: Write the failing CLI tests**

Create `tests/cli/test_transfer_cli.py`:

```python
import json

import pytest

from mml_cloud_transfer.cli.__main__ import main
from mml_cloud_transfer.cli.transfer_command import parse_size_policy
from mml_cloud_transfer.core.slicing import SizePolicy

POLICY_ARG = "65536,262144,262144"


def test_parse_size_policy():
    policy = parse_size_policy(POLICY_ARG)
    assert policy == SizePolicy(
        single_shot_max=65536, resumable_max=262144,
        min_slice=262144, max_components=32,
    )
    with pytest.raises(ValueError):
        parse_size_policy("1,2")


@pytest.fixture
def tree(tmp_path):
    src = tmp_path / "src"
    (src / "deep").mkdir(parents=True)
    (src / "a.bin").write_bytes(b"a" * 10_000)
    (src / "deep" / "b.bin").write_bytes(bytes(range(256)) * 512)
    return src


@pytest.mark.emulator
def test_transfer_upload_end_to_end(emulator, emulator_client, tree, tmp_path, capsys):
    _, bucket = emulator_client
    db = tmp_path / "jobs.db"
    code = main([
        "transfer",
        "--db", str(db), "--bucket", bucket, "--name", "cli-up",
        "--source", str(tree), "--prefix", "cli",
        "--size-policy", POLICY_ARG,
        "--emulator-endpoint", emulator.endpoint,
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "COMPLETE" in out

    report_dir = db.parent / "reports" / "job-1"
    summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["verdict"] == "COMPLETE"
    assert summary["counts"]["verified"] == 2


@pytest.mark.emulator
def test_transfer_download_end_to_end(emulator, emulator_client, tree, tmp_path):
    _, bucket = emulator_client
    db = tmp_path / "jobs.db"
    assert main([
        "transfer", "--db", str(db), "--bucket", bucket, "--name", "up",
        "--source", str(tree), "--prefix", "rt",
        "--size-policy", POLICY_ARG, "--emulator-endpoint", emulator.endpoint,
    ]) == 0

    dest = tmp_path / "restored"
    assert main([
        "transfer", "--db", str(db), "--bucket", bucket, "--name", "down",
        "--direction", "download", "--source", str(dest), "--prefix", "rt",
        "--size-policy", POLICY_ARG, "--emulator-endpoint", emulator.endpoint,
    ]) == 0
    assert (dest / "a.bin").read_bytes() == (tree / "a.bin").read_bytes()
    assert (dest / "deep" / "b.bin").read_bytes() == (tree / "deep" / "b.bin").read_bytes()


@pytest.mark.emulator
def test_status_lists_jobs(emulator, emulator_client, tree, tmp_path, capsys):
    _, bucket = emulator_client
    db = tmp_path / "jobs.db"
    main([
        "transfer", "--db", str(db), "--bucket", bucket, "--name", "visible-job",
        "--source", str(tree), "--prefix", "s",
        "--size-policy", POLICY_ARG, "--emulator-endpoint", emulator.endpoint,
    ])
    capsys.readouterr()
    assert main(["status", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "visible-job" in out
    assert "complete" in out.lower()


def test_resume_of_unknown_job_fails_cleanly(tmp_path, capsys):
    code = main([
        "resume", "--db", str(tmp_path / "jobs.db"), "--job-id", "42",
        "--bucket", "b",
    ])
    assert code == 1
    assert "no job with id 42" in capsys.readouterr().out
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/cli/test_transfer_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mml_cloud_transfer.cli.transfer_command'`.

- [ ] **Step 3: Implement the command module**

Create `src/mml_cloud_transfer/cli/transfer_command.py`:

```python
"""transfer / resume / status / report subcommands.

Thin wiring: build a GcsContext, drive scan + run_job, write the report,
translate the verdict into an exit code. All logic lives in engine/ and gcs/.
"""

from __future__ import annotations

from pathlib import Path

from mml_cloud_transfer.core.models import Direction, JobStatus
from mml_cloud_transfer.core.paths import display_path
from mml_cloud_transfer.core.slicing import SizePolicy
from mml_cloud_transfer.engine.report import write_report
from mml_cloud_transfer.engine.runner import EngineOptions, run_job, scan_remote
from mml_cloud_transfer.gcs.client import make_context
from mml_cloud_transfer.cli.scan_command import run_scan
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


def parse_size_policy(text: str) -> SizePolicy:
    parts = text.split(",")
    if len(parts) != 3:
        raise ValueError(
            "size policy must be 'single_shot_max,resumable_max,min_slice'"
        )
    single, resumable, min_slice = (int(p) for p in parts)
    return SizePolicy(
        single_shot_max=single, resumable_max=resumable,
        min_slice=min_slice, max_components=32,
    )


def _options(args) -> EngineOptions:
    options = EngineOptions()
    if args.size_policy:
        options.policy = parse_size_policy(args.size_policy)
    if args.workers:
        options.file_workers = args.workers
    return options


def _context(args):
    return make_context(
        args.bucket,
        credentials_path=args.credentials,
        emulator_endpoint=args.emulator_endpoint,
    )


def _report_dir(args, job_id: int) -> Path:
    if args.report_dir:
        return Path(args.report_dir)
    return Path(args.db).resolve().parent / "reports" / f"job-{job_id}"


def _finish(args, db, job_id: int, status: JobStatus) -> int:
    paths = write_report(db, job_id, _report_dir(args, job_id), bucket=args.bucket)
    print(f"Job {job_id}: {status.value.upper()}")
    print(f"Report: {paths.report_html}")
    return 0 if status is JobStatus.COMPLETE else 1


def run_transfer(args) -> int:
    ctx = _context(args)
    options = _options(args)
    direction = Direction(args.direction)

    if direction is Direction.UPLOAD:
        outcome = run_scan(
            db_path=args.db, source_root=args.source, dest_prefix=args.prefix,
            job_name=args.name, policy=options.policy,
        )
        job_id = outcome.job_id
        print(f"Scanned {outcome.file_count} files")
        if outcome.errors:
            print(f"{len(outcome.errors)} scan error(s) — see the report")
    else:
        conn = connect(args.db)
        try:
            repo = JobRepository(conn)
            job_id = repo.create_job(
                name=args.name, direction=Direction.DOWNLOAD,
                source_root=args.source, dest_prefix=args.prefix,
                audit_hash=args.audit_hash,
            )
        finally:
            conn.close()
        count = scan_remote(ctx, args.db, job_id, policy=options.policy)
        print(f"Listed {count} objects")

    if direction is Direction.UPLOAD and args.audit_hash:
        conn = connect(args.db)
        try:
            conn.execute(
                "UPDATE jobs SET audit_hash = 1 WHERE id = ?", (job_id,)
            )
        finally:
            conn.close()

    status = run_job(args.db, job_id, ctx, options=options)
    return _finish(args, args.db, job_id, status)


def run_resume(args) -> int:
    conn = connect(args.db)
    try:
        repo = JobRepository(conn)
        try:
            job = repo.get_job(args.job_id)
        except LookupError as exc:
            print(str(exc))
            return 1
    finally:
        conn.close()

    ctx = _context(args)
    status = run_job(args.db, args.job_id, ctx, options=_options(args))
    return _finish(args, args.db, args.job_id, status)


def run_status(args) -> int:
    conn = connect(args.db)
    try:
        jobs = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        repo = JobRepository(conn)
        if not jobs:
            print("No jobs.")
            return 0
        for job in jobs:
            counts = repo.count_by_state(job["id"])
            states = ", ".join(f"{k.value}: {v}" for k, v in sorted(counts.items()))
            print(
                f"#{job['id']} {job['name']} [{job['direction']}] "
                f"{job['status']} — {display_path(job['source_root'])} -> "
                f"{job['dest_prefix'] or '(root)'} — {states or 'no files'}"
            )
        return 0
    finally:
        conn.close()


def run_report_cmd(args) -> int:
    conn = connect(args.db)
    try:
        JobRepository(conn).get_job(args.job_id)
    except LookupError as exc:
        print(str(exc))
        return 1
    finally:
        conn.close()
    paths = write_report(
        args.db, args.job_id, args.out or _report_dir(args, args.job_id),
        bucket=args.bucket,
    )
    print(f"Report: {paths.report_html}")
    return 0
```

- [ ] **Step 4: Wire the subcommands**

In `src/mml_cloud_transfer/cli/__main__.py`, import the new commands and add
the subparsers inside `_build_parser()` after the existing `scan` block
(`import argparse` is already present; add
`from mml_cloud_transfer.cli.transfer_command import run_report_cmd, run_resume, run_status, run_transfer`):

```python
    def add_gcs_options(sub):
        sub.add_argument("--bucket", required=True, help="Destination bucket name")
        sub.add_argument("--credentials", default=None,
                         help="Service-account key file (default: ADC)")
        sub.add_argument("--workers", type=int, default=None,
                         help="Concurrent file transfers (default 4)")
        sub.add_argument("--report-dir", default=None,
                         help="Report output directory (default: <db>/reports/job-N)")
        sub.add_argument("--size-policy", default=None, help=argparse.SUPPRESS)
        sub.add_argument("--emulator-endpoint", default=None, help=argparse.SUPPRESS)

    transfer = subparsers.add_parser("transfer", help="Scan and run a transfer job")
    transfer.add_argument("--db", required=True)
    transfer.add_argument("--name", required=True)
    transfer.add_argument("--direction", choices=["upload", "download"],
                          default="upload")
    transfer.add_argument("--source", required=True,
                          help="Local folder (upload: source; download: destination)")
    transfer.add_argument("--prefix", default="", help="Bucket object-name prefix")
    transfer.add_argument("--audit-hash", action="store_true",
                          help="Also compute SHA-256 per file")
    add_gcs_options(transfer)

    resume = subparsers.add_parser("resume", help="Resume an interrupted job")
    resume.add_argument("--db", required=True)
    resume.add_argument("--job-id", type=int, required=True)
    add_gcs_options(resume)

    status = subparsers.add_parser("status", help="List jobs and their state")
    status.add_argument("--db", required=True)

    report = subparsers.add_parser("report", help="Re-export a job's report")
    report.add_argument("--db", required=True)
    report.add_argument("--job-id", type=int, required=True)
    report.add_argument("--out", default=None)
    report.add_argument("--bucket", default=None)
    report.add_argument("--report-dir", default=None, help=argparse.SUPPRESS)
```

and dispatch them in `main()` after the `scan` branch:

```python
    if args.command == "transfer":
        return run_transfer(args)
    if args.command == "resume":
        return run_resume(args)
    if args.command == "status":
        return run_status(args)
    if args.command == "report":
        return run_report_cmd(args)
```

- [ ] **Step 5: Run the CLI tests**

Run: `.venv/Scripts/python -m pytest tests/cli/test_transfer_cli.py -v`
Expected: PASS, 5 tests (3 emulator-marked).

- [ ] **Step 6: Write the interrupt-and-resume test**

Create `tests/cli/test_interrupt_resume.py`:

```python
"""The defining test of the whole design: kill a transfer mid-flight,
resume it, and end COMPLETE with everything verified. Runs the CLI as a
real subprocess so the kill is a real process death, not an exception."""

import os
import subprocess
import sys
import time
import uuid

import pytest

from mml_cloud_transfer.core.models import FileState, JobStatus
from mml_cloud_transfer.core.hashing import hash_file
from mml_cloud_transfer.gcs.client import make_context
from mml_cloud_transfer.gcs.objects import get_meta
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

POLICY = "65536,262144,262144"


def _cli(*args):
    return [sys.executable, "-m", "mml_cloud_transfer.cli", *args]


@pytest.fixture
def big_tree(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for n in range(40):
        (src / f"small-{n:02d}.bin").write_bytes(os.urandom(8_192))
    (src / "medium.bin").write_bytes(os.urandom(200 * 1024))
    (src / "big.bin").write_bytes(os.urandom(600 * 1024))
    return src


@pytest.mark.emulator
def test_kill_and_resume_reaches_complete(emulator, emulator_client, big_tree, tmp_path):
    _, bucket = emulator_client
    db = tmp_path / "jobs.db"

    proc = subprocess.Popen(
        _cli(
            "transfer", "--db", str(db), "--bucket", bucket, "--name", "overnight",
            "--source", str(big_tree), "--prefix", "night",
            "--size-policy", POLICY, "--workers", "1",
            "--emulator-endpoint", emulator.endpoint,
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Wait until real progress exists, then kill without ceremony.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if db.exists():
                conn = connect(db)
                verified = conn.execute(
                    "SELECT COUNT(*) AS n FROM job_files WHERE state = ?",
                    (FileState.VERIFIED.value,),
                ).fetchone()["n"]
                conn.close()
                if verified >= 3:
                    break
            time.sleep(0.2)
        else:
            pytest.fail("transfer made no visible progress within 60s")
    finally:
        proc.kill()
        proc.wait(timeout=15)

    conn = connect(db)
    repo = JobRepository(conn)
    job = conn.execute("SELECT * FROM jobs ORDER BY id").fetchone()
    counts = repo.count_by_state(job["id"])
    conn.close()
    assert job["status"] != JobStatus.COMPLETE.value
    assert counts.get(FileState.VERIFIED, 0) < 42, "kill happened too late to prove anything"

    result = subprocess.run(
        _cli(
            "resume", "--db", str(db), "--job-id", str(job["id"]),
            "--bucket", bucket, "--size-policy", POLICY,
            "--emulator-endpoint", emulator.endpoint,
        ),
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    conn = connect(db)
    repo = JobRepository(conn)
    assert repo.get_job(job["id"])["status"] == JobStatus.COMPLETE.value
    rows = repo.get_files(job["id"])
    conn.close()
    assert len(rows) == 42
    assert all(
        r["state"] in (FileState.VERIFIED.value, FileState.SKIPPED.value) for r in rows
    )

    # Spot-check the sliced file end-to-end: remote CRC equals a fresh local hash.
    ctx = make_context(bucket, emulator_endpoint=emulator.endpoint)
    meta = get_meta(ctx, "night/big.bin")
    assert meta is not None
    assert meta.crc32c == hash_file(big_tree / "big.bin").crc32c


@pytest.mark.real_bucket
def test_real_bucket_round_trip(tmp_path):
    bucket = os.environ.get("MMLCT_TEST_BUCKET")
    if not bucket:
        pytest.skip("set MMLCT_TEST_BUCKET (and ADC credentials) to run")

    run_prefix = f"mmlct-test/{uuid.uuid4().hex[:12]}"
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.bin").write_bytes(os.urandom(100 * 1024))
    (src / "b.bin").write_bytes(os.urandom(400 * 1024))
    db = tmp_path / "jobs.db"

    up = subprocess.run(
        _cli(
            "transfer", "--db", str(db), "--bucket", bucket, "--name", "real-up",
            "--source", str(src), "--prefix", run_prefix,
            "--size-policy", POLICY,
        ),
        capture_output=True, text=True, timeout=600,
    )
    assert up.returncode == 0, up.stdout + up.stderr

    dest = tmp_path / "restored"
    down = subprocess.run(
        _cli(
            "transfer", "--db", str(db), "--bucket", bucket, "--name", "real-down",
            "--direction", "download", "--source", str(dest), "--prefix", run_prefix,
            "--size-policy", POLICY,
        ),
        capture_output=True, text=True, timeout=600,
    )
    assert down.returncode == 0, down.stdout + down.stderr
    assert (dest / "a.bin").read_bytes() == (src / "a.bin").read_bytes()
    assert (dest / "b.bin").read_bytes() == (src / "b.bin").read_bytes()

    # Clean up the run's objects.
    from mml_cloud_transfer.gcs.objects import delete_object, list_prefix

    ctx = make_context(bucket)
    for meta in list_prefix(ctx, f"{run_prefix}/"):
        delete_object(ctx, meta.name)
```

- [ ] **Step 7: Run it, then the whole suite with coverage**

Run: `.venv/Scripts/python -m pytest tests/cli/test_interrupt_resume.py -v`
Expected: PASS — 1 emulator test (the real_bucket one skips without `MMLCT_TEST_BUCKET`).

Run: `.venv/Scripts/python -m pytest --cov=mml_cloud_transfer --cov-report=term-missing`
Expected: PASS. Record in your report the coverage of `gcs/resumable.py`, `gcs/uploader.py`, `gcs/downloader.py`, and `engine/runner.py` — the correctness-critical modules of this plan.

- [ ] **Step 8: Commit**

```bash
git add src/mml_cloud_transfer/cli/ tests/cli/
git commit -m "feat: add transfer/resume/status/report commands and interrupt-resume test"
```

---

## Phase Complete

`mmlct transfer` moves a tree to or from a bucket across all three size paths with three-layer verification; killing the process mid-flight and running `mmlct resume` ends in an audited COMPLETE. Reports land beside the database.

**Release gate (spec requirement — manual, before calling Plan 2 shipped):**

1. Provision a test bucket (Standard class) and credentials, set `MMLCT_TEST_BUCKET`, run `.venv/Scripts/python -m pytest -m real_bucket -v` — must pass.
2. One manual multi-gigabyte interrupt-and-resume: `mmlct transfer` on a real ≥5 GB tree with the DEFAULT size policy against the test bucket; kill it mid-sliced-file; `mmlct resume`; verdict must be COMPLETE and the report's CRCs must match. (The automated tests shrink thresholds — this run exercises real 1 GiB+ slices and real session lifetimes.)

**Deployment notes for operators (documented here, applied in Plan 3+):**

- Add a bucket lifecycle rule deleting objects under any `*.mmlct.tmp/` prefix older than 7 days — the safety net for slice temp objects orphaned by a hard crash (`AbortIncompleteMultipartUpload` does not apply; these are ordinary composed-source objects).
- The spec's cold-storage warning applies: temp slice objects on Nearline or colder incur minimum-storage-duration charges.

**Plan 3 picks up from here** with the Windows Service (Phase 3): FastAPI on `127.0.0.1` with the bearer-token file, the job queue and scheduler, the service host with startup recovery (`reset_stale_transfers` + auto-resume), SSE progress streaming, and the survives-logoff test. The engine's `run_job` is the worker it wraps. The spec's `stalled` job state (still alive, retrying on a slow cadence through sustained network loss) also lands there — it needs a long-lived process; in this plan a CLI run that exhausts its retries ends INCOMPLETE and `mmlct resume` is the recovery.

