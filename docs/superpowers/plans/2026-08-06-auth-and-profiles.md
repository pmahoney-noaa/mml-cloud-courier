# Auth and Profiles (Plan 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** First-class connection profiles with service-usable credentials — service-account keys and user OAuth refresh tokens, DPAPI-encrypted at machine scope in an ACL-restricted `%ProgramData%` store — validated by real operations against the target bucket, with per-direction preflight and path-reachability checks at job creation.

**Architecture:** A new `auth/` package (DPAPI primitives, credential store, context building, preflight probes, OAuth flow) sits between `store/` (profile rows) and `gcs/` (client factory). The service API grows a `/profiles` resource; job submission becomes profile-aware while the existing ad-hoc bucket+ADC path keeps working (the live install's bridge config). The CLI hosts the interactive OAuth flow and the new `mmlct profile` commands; the service holds and refreshes credentials autonomously thereafter.

**Tech Stack:** Python 3.12, pywin32 (`win32crypt` for DPAPI), `google-auth`/`google-cloud-storage` (already present), `google-auth-oauthlib` (added in Task 11), FastAPI, SQLite.

**Spec:** `docs/superpowers/specs/2026-08-04-gcs-transfer-manager-design.md` — Phase 4 plus the "Authentication and Credentials" and "Path Handling" sections. GUI (Phase 5) and packaging (Phase 6) are later plans; nothing here builds UI or installers.

**Binding gate records:**
- `docs/superpowers/2026-08-05-phase3-gate-record.md` (Findings bind this plan)
- `docs/superpowers/gates/2026-08-05-plan2-release-gate.md`

## Global Constraints

- Python pinned to **3.12** (`py -3.12 -m venv`); `requires-python = ">=3.12,<3.13"`.
- A **LIVE service install exists**: auto-start, running as `.\pmaho`, port **47821**, data in `%ProgramData%\MML Cloud Transfer`. Tests must always use ephemeral ports and temp data dirs (`MMLCT_DATA_DIR` / `load_config(tmp_path)`), and must **never** touch the live data dir or the default port.
- ACL grants are **by process SID, never account names** (gate fix 6e45d4a: icacls error 1332 on workgroup machines). Every secret file follows the token-file pattern **exactly**: create empty → cut ACL → write (`service/security.py::ensure_token` is the reference).
- Fault-injection tests use **REAL exception types** from the real libraries (`google.auth.exceptions`, `google.api_core.exceptions`, `requests`) — never builtin stand-ins. Gate lesson: `classify()` gaps only surface with real types (fixes 669bb4b, b1a5d4b).
- Real-bucket testing: bucket `afsc_mml_ccep` with `MMLCT_TEST_BUCKET` + `MMLCT_TEST_PREFIX=scratch`. Versioning is **ON**; `storage.buckets.get` is **denied** (probes must be object-level only); all cleanup must be **version-aware** (delete by explicit generation, verify with `versions=True`). Do not depend on the gcloud CLI (its token needs interactive reauth); use ADC via the client library.
- The **bridge config keeps working**: service running as a user account + ADC, jobs submitted with `bucket` and no profile. DPAPI profiles are the permanent replacement; LocalSystem+key stays the packaged default (Phase 6).
- Emulator at `tools/fake-gcs-server.exe` (refetch: `pwsh tests/tools/get-fake-gcs-server.ps1`). Emulator tests auto-skip when it is absent; `real_bucket` tests auto-skip without `MMLCT_TEST_BUCKET`.
- New runtime dependency allowed by this plan: `google-auth-oauthlib` only (Task 11).
- TDD per task; **one commit per task; never amend**. Full local suite green before every commit (`.venv/Scripts/python -m pytest` — expect every non-skipped test passing; `real_bucket` tests stay skipped without env vars).

## Worktree discipline (binding for every dispatch)

Plan 3 subagents twice committed to the main checkout from the launch directory. Therefore:

1. Before Task 1 dispatches, the orchestrating session creates the worktree (EnterWorktree) and prepares it:
   ```powershell
   py -3.12 -m venv .venv
   .venv/Scripts/python -m pip install -e ".[dev]"
   Copy-Item <main-checkout>\tools\fake-gcs-server.exe tools\fake-gcs-server.exe
   ```
2. Every subagent dispatch makes `cd <worktree>` its **FIRST** command and immediately verifies `git rev-parse --show-toplevel` prints the worktree path.
3. Immediately before **each** commit, the subagent re-verifies `git rev-parse --show-toplevel` (worktree path) **and** `git rev-parse HEAD` equals the parent commit named in its dispatch.

## Carried-over items → tasks

| # | Item | Task |
| --- | --- | --- |
| 1 | RefreshError classification by cause chain (transport → NETWORK, invalid_grant → CREDENTIAL), tests with real google.auth types | Task 1 |
| 2 | `get_or_create_profile` count-based naming races across connections | Task 4 |
| 3 | Duplicate-destination guard: same-(source_root, dest_prefix, bucket) check at job creation | Task 9 |
| 4 | Bridge config (user-account service + ADC) keeps working | Task 8 (regression pin) + Global Constraints |

## Decisions locked during planning

- **`auth_type` vocabulary.** Existing stored values `emulator`, `key_file`, `adc` are untouched. New stored values: `service_account_key` and `oauth_user` — both mean "`credential_ref` names a blob in the DPAPI store".
- **DPAPI machine scope** (`CRYPTPROTECT_LOCAL_MACHINE`), per spec: the file ACL is the real access control; machine scope survives the installer changing the service account. `CRYPTPROTECT_UI_FORBIDDEN` is always set (session 0 must never prompt).
- **Validation bar at profile creation:** `can_list and can_read` (both directions need list+read — uploads use `objects.get` for Layer 2 verify and the Layer 3 audit). Upload capability (`write`+`compose`+`delete`) is enforced per-direction at job submission, so a deliberately read-only download profile is still creatable.
- **Profile deletion refuses while any job references it** (clear 409), because jobs keep their profile for report/bucket lookups and rows are never cascaded.
- **Duplicate guard is conservative:** a candidate job matches an active job with the same destination and source even when the active job has no profile row (direct-CLI jobs, bucket unknowable) — blocking with a clear message is safe; silently double-writing is not.
- **`ProfileCreate.emulator_endpoint`** is a test affordance mirroring the existing `JobSubmission.emulator_endpoint` precedent; it stores an `emulator` profile and lets the full route (validate → store → row) run against fake-gcs-server.
- **`project` fallback for user-OAuth clients** is the placeholder `"mmlct"` (object-level operations never send it; requester-pays buckets are out of scope). Profiles can store a real `project_id`.

## File structure

```text
src/mml_cloud_transfer/
  auth/                      NEW package (spec: "profiles, DPAPI credential storage, OAuth flow helpers")
    __init__.py
    dpapi.py                 protect()/unprotect(), machine scope         (Task 2)
    credential_store.py      CredentialStore: DPAPI blobs + ACLs          (Task 3)
    context.py               profile row -> GcsContext dispatch           (Task 5)
    preflight.py             direction-aware permission probes            (Task 6)
    oauth_flow.py            installed-app flow, injectable client config (Task 11)
  core/errors.py             refresh/transport classification             (Task 1)
  core/paths.py              canonical_source_key()                       (Task 9)
  gcs/client.py              make_context(credentials_info=, project=)    (Task 5)
  service/security.py        restrict_acl(inheritable=)                   (Task 3)
  service/config.py          credentials_dir property                     (Task 3)
  service/worker.py          _context() delegates to auth.context         (Task 5)
  service/app.py             /profiles routes; profile-aware /jobs        (Tasks 7, 8, 9, 10)
  store/schema.py            v2: profiles.validated_at                    (Task 4)
  store/repository.py        race-free naming, CRUD, duplicate guard      (Tasks 4, 9)
  cli/service_client.py      ApiClient profile methods                    (Task 7)
  cli/profile_command.py     NEW: mmlct profile subcommands               (Task 12)
  cli/transfer_command.py    mapped-drive resolve; direct-mode dup guard  (Tasks 9, 10)
  cli/__main__.py            profile parser; --profile on transfer        (Tasks 8, 12)
pyproject.toml               + google-auth-oauthlib                       (Task 11)
docs/superpowers/gates/2026-08-06-phase4-manual-gate.md                   (Task 13)
```

---

### Task 1: Refresh and transport failure classification

A token refresh can fail two ways: the grant is dead (`invalid_grant` — a human must re-authenticate; pause the job) or the refresh HTTP request failed in transit (network down — retry/stall, exactly like any other outage). Today `classify()` sends every `RefreshError` to CREDENTIAL and does not recognise `google.auth.exceptions.TransportError` at all (it falls to UNKNOWN). With stored refresh tokens, an overnight VPN blip would pause the job instead of stalling it.

Ground truth from the installed google-auth (verified in `.venv`): the token-endpoint request in `google/oauth2/_client.py::_token_endpoint_request_no_throw` is **not** wrapped — a network failure propagates as `TransportError` raised `from` the `requests` exception (`google/auth/transport/requests.py` line ~193). HTTP-level errors raise `RefreshError(error_details, response_data, retryable=...)`, where `retryable=True` marks 5xx/`server_error`-class failures.

**Files:**
- Modify: `src/mml_cloud_transfer/core/errors.py` (the `google.auth` branch, currently lines 205–208)
- Test: `tests/core/test_errors.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `classify()` behavior relied on by the engine, worker stall path, and Task 6–8 error reporting:
  - `TransportError` (module `google.auth.*`) → `NETWORK` (transient, does not pause)
  - `RefreshError` with `retryable=True` **or** a transport exception in its `__cause__`/`__context__` chain → `NETWORK`
  - `RefreshError` otherwise (e.g. `invalid_grant`) → `CREDENTIAL` (pauses job)
  - `DefaultCredentialsError` → `CREDENTIAL` (unchanged)
  - Module-private helper `_has_transport_cause(exc: BaseException) -> bool`

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_errors.py`. The exceptions are harvested from the **real** google-auth code paths: a real refresh attempt against a local token endpoint returning `invalid_grant`, and against a closed port (nothing listening) for the transport case. No sleeps, no live network.

```python
# ---- refresh-failure classification (Plan 4 Task 1) -----------------------
#
# Gate lesson (fixes 669bb4b, b1a5d4b): classify() gaps only surface with
# REAL exception types, so these tests drive google-auth's actual refresh
# machinery instead of hand-building exceptions wherever possible.

import json as _json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


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
    exc = _harvest_refresh_exception(invalid_grant_endpoint)
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
```

Note: `tests/core/test_errors.py` already imports `classify` and `ErrorCategory` at the top — do not re-import them; only add the new stdlib/pytest imports shown.

- [ ] **Step 2: Run the tests to verify the new ones fail**

Run: `.venv/Scripts/python -m pytest tests/core/test_errors.py -v`
Expected: the two transport-classification tests and the retryable test FAIL (current classifier returns CREDENTIAL or UNKNOWN, not NETWORK); the invalid_grant and DefaultCredentialsError tests PASS (current behavior, kept).

- [ ] **Step 3: Implement the classification**

In `src/mml_cloud_transfer/core/errors.py`, add this helper above `classify()`:

```python
def _has_transport_cause(exc: BaseException) -> bool:
    """True when the exception's cause chain contains a transport failure.

    google-auth chains the underlying requests exception onto the error it
    raises (``raise new_exc from caught_exc``), so the chain — not the
    outermost type — says whether the network or the credential failed.
    Matched by module/class name to keep this module free of cloud imports.
    Bounded and cycle-safe.
    """
    seen: set[int] = set()
    node: BaseException | None = exc
    while node is not None and id(node) not in seen and len(seen) < 20:
        seen.add(id(node))
        module = type(node).__module__
        if module.startswith(("requests", "urllib3")):
            return True
        if type(node).__name__ == "TransportError" and module.startswith("google.auth"):
            return True
        if isinstance(node, (ConnectionError, TimeoutError)):
            return True
        node = node.__cause__ or node.__context__
    return False
```

Then replace this block in `classify()`:

```python
    if type(exc).__name__ in ("RefreshError", "DefaultCredentialsError") and module.startswith(
        "google.auth"
    ):
        return _build(ErrorCategory.CREDENTIAL)
```

with:

```python
    if module.startswith("google.auth"):
        name = type(exc).__name__
        if name == "TransportError":
            # google-auth wraps the requests exception raised when the
            # token endpoint is unreachable. The network failed, not the
            # credential.
            return _build(ErrorCategory.NETWORK)
        if name == "RefreshError":
            # A refresh fails either because the grant is dead
            # (invalid_grant — a human must re-authenticate) or because
            # the refresh REQUEST failed in transit. Only the former is a
            # credential problem; with stored refresh tokens the latter
            # would otherwise pause overnight jobs on every outage.
            # retryable=True is google-auth's own verdict on token-endpoint
            # 5xx / server_error responses.
            if getattr(exc, "retryable", False) or _has_transport_cause(exc):
                return _build(ErrorCategory.NETWORK)
            return _build(ErrorCategory.CREDENTIAL)
        if name == "DefaultCredentialsError":
            return _build(ErrorCategory.CREDENTIAL)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/core/test_errors.py -v`
Expected: ALL PASS, including every pre-existing test in the file.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python -m pytest`
Expected: all non-skipped tests pass (baseline 354 passed + 12 skipped, plus the new tests).

- [ ] **Step 6: Commit**

```bash
git add src/mml_cloud_transfer/core/errors.py tests/core/test_errors.py
git commit -m "fix: classify refresh failures by cause - transport outage is NETWORK, dead grant stays CREDENTIAL"
```

---

### Task 2: DPAPI primitives

**Files:**
- Create: `src/mml_cloud_transfer/auth/__init__.py`
- Create: `src/mml_cloud_transfer/auth/dpapi.py`
- Create: `tests/auth/__init__.py`
- Test: `tests/auth/test_dpapi.py`

**Interfaces:**
- Consumes: `win32crypt` (pywin32, already a dependency), `pywintypes`.
- Produces (used by Task 3):
  - `protect(data: bytes, *, description: str = "MML Cloud Transfer credential") -> bytes`
  - `unprotect(blob: bytes) -> bytes` — raises `ValueError` on a corrupt or foreign-machine blob, `NotImplementedError` off Windows.

- [ ] **Step 1: Write the failing tests**

`tests/auth/__init__.py` is empty. `tests/auth/test_dpapi.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/auth/test_dpapi.py -v`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'mml_cloud_transfer.auth'`.

- [ ] **Step 3: Implement**

`src/mml_cloud_transfer/auth/__init__.py` (docstring only):

```python
"""Profiles, DPAPI credential storage, and OAuth flow helpers (spec: auth/)."""
```

`src/mml_cloud_transfer/auth/dpapi.py`:

```python
"""Windows DPAPI encryption at machine scope.

Machine scope — not user scope — because the encrypting process is the
service, and the installer may change which account the service runs as;
a user-scoped blob would die with that account. The spec states the limit
plainly: at machine scope the file ACL is the real access control, and the
encryption protects a stolen backup or disk image, not a local admin.

UI_FORBIDDEN is always set: this code runs in session 0, where a DPAPI
prompt would hang forever.
"""

from __future__ import annotations

import sys

CRYPTPROTECT_UI_FORBIDDEN = 0x01
CRYPTPROTECT_LOCAL_MACHINE = 0x04
_FLAGS = CRYPTPROTECT_LOCAL_MACHINE | CRYPTPROTECT_UI_FORBIDDEN


def protect(data: bytes, *, description: str = "MML Cloud Transfer credential") -> bytes:
    if sys.platform != "win32":
        raise NotImplementedError("DPAPI is Windows-only")
    import win32crypt

    return win32crypt.CryptProtectData(data, description, None, None, None, _FLAGS)


def unprotect(blob: bytes) -> bytes:
    if sys.platform != "win32":
        raise NotImplementedError("DPAPI is Windows-only")
    import pywintypes
    import win32crypt

    try:
        _description, data = win32crypt.CryptUnprotectData(blob, None, None, None, _FLAGS)
    except pywintypes.error as exc:
        raise ValueError(
            "credential blob is corrupt or was encrypted on another machine"
        ) from exc
    return data
```

Note: if `CryptUnprotectData` raises something other than `pywintypes.error` for the garbage-blob test (pywin32 versions differ), widen the except to `(pywintypes.error, ValueError, TypeError)` — but verify with the test first, do not guess.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/auth/test_dpapi.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python -m pytest`
Expected: all non-skipped tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/mml_cloud_transfer/auth/__init__.py src/mml_cloud_transfer/auth/dpapi.py tests/auth/__init__.py tests/auth/test_dpapi.py
git commit -m "feat: DPAPI protect/unprotect at machine scope (auth package)"
```

---

### Task 3: ACL-restricted credential store

**Files:**
- Modify: `src/mml_cloud_transfer/service/security.py` (`_acl_grants`, `restrict_acl` gain `inheritable=`)
- Modify: `src/mml_cloud_transfer/service/config.py` (add `credentials_dir` property)
- Create: `src/mml_cloud_transfer/auth/credential_store.py`
- Test: `tests/service/test_security.py` (extend), `tests/service/test_config.py` (extend), `tests/auth/test_credential_store.py`

**Interfaces:**
- Consumes: `auth.dpapi.protect/unprotect` (Task 2), `service.security.restrict_acl`.
- Produces (used by Tasks 5, 7):
  - `service.security.restrict_acl(path: Path, *, inheritable: bool = False) -> None` — `inheritable=True` writes `(OI)(CI)(F)` grants so files created inside a restricted directory inherit the restriction.
  - `ServiceConfig.credentials_dir: Path` = `data_dir / "credentials"`.
  - `class CredentialStore:`
    - `__init__(self, root: Path)`
    - `save(self, payload: dict) -> str` — returns a ref like `cred-3f9a1b2c4d5e.dpapi`
    - `load(self, ref: str) -> dict` — raises `ValueError` on a bad ref or corrupt blob, `FileNotFoundError` if absent
    - `delete(self, ref: str) -> None` — idempotent
    - `path_for(self, ref: str) -> Path` — validates the ref shape (no path traversal)

- [ ] **Step 1: Write the failing tests**

Append to `tests/service/test_security.py`:

```python
def test_acl_grants_can_be_inheritable(monkeypatch):
    """The credentials directory needs (OI)(CI) grants so files created
    inside inherit the restriction instead of ProgramData's default ACL."""
    monkeypatch.setattr(
        security, "_current_sid", lambda: "S-1-5-21-111-222-333-1001"
    )
    grants = _acl_grants(inheritable=True)
    assert "*S-1-5-18:(OI)(CI)(F)" in grants
    assert "*S-1-5-21-111-222-333-1001:(OI)(CI)(F)" in grants
    assert not any(g.endswith(":(F)") for g in grants if g.startswith("*"))


def test_acl_grants_default_stays_non_inheritable(monkeypatch):
    monkeypatch.setattr(security, "_current_sid", lambda: None)
    grants = _acl_grants()
    assert "*S-1-5-18:(F)" in grants
```

Append to `tests/service/test_config.py`:

```python
def test_credentials_dir_lives_under_the_data_dir(tmp_path):
    from mml_cloud_transfer.service.config import load_config

    config = load_config(tmp_path / "data")
    assert config.credentials_dir == (tmp_path / "data") / "credentials"
```

`tests/auth/test_credential_store.py`:

```python
"""The DPAPI credential store: token-file pattern (create empty -> cut ACL
-> write), grants by process SID, machine-scope encryption at rest."""

import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI/ACLs are Windows-only")

from mml_cloud_transfer.auth.credential_store import CredentialStore

PAYLOAD = {
    "type": "authorized_user",
    "client_id": "c",
    "client_secret": "s",
    "refresh_token": "1//THE-SECRET",
    "token_uri": "https://oauth2.googleapis.com/token",
}


def test_save_load_round_trip(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    ref = store.save(PAYLOAD)
    assert ref.startswith("cred-") and ref.endswith(".dpapi")
    assert store.load(ref) == PAYLOAD


def test_secret_is_not_on_disk_in_plaintext(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    ref = store.save(PAYLOAD)
    raw = store.path_for(ref).read_bytes()
    assert b"THE-SECRET" not in raw
    assert b"refresh_token" not in raw


def test_delete_is_idempotent(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    ref = store.save(PAYLOAD)
    store.delete(ref)
    store.delete(ref)  # second delete must not raise
    with pytest.raises(FileNotFoundError):
        store.load(ref)


@pytest.mark.parametrize("bad", ["../../etc", "cred-zzz.dpapi/..", "x.dpapi", ""])
def test_refs_that_are_not_ours_are_rejected(tmp_path, bad):
    store = CredentialStore(tmp_path / "credentials")
    with pytest.raises(ValueError):
        store.path_for(bad)


def test_credential_file_acl_drops_inheritance(tmp_path):
    """Same check shape as test_token_file_acl_drops_inheritance: the blob
    file itself carries a cut ACL — no inherited ACEs survive."""
    store = CredentialStore(tmp_path / "credentials")
    ref = store.save(PAYLOAD)
    out = subprocess.run(
        ["icacls", str(store.path_for(ref))],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "(I)" not in out
    assert "SYSTEM" in out or "S-1-5-18" in out


def test_credentials_directory_acl_is_cut_and_inheritable(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    store.save(PAYLOAD)
    out = subprocess.run(
        ["icacls", str(tmp_path / "credentials")],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "(I)" not in out
    assert "(OI)(CI)" in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/auth/test_credential_store.py tests/service/test_security.py tests/service/test_config.py -v`
Expected: new tests FAIL (`_acl_grants() got an unexpected keyword argument`, missing `credentials_dir`, `ModuleNotFoundError: ...credential_store`); existing security/config tests PASS.

- [ ] **Step 3: Implement**

`service/security.py` — change `_acl_grants` and `restrict_acl` (docstrings stay; only the grant suffix becomes parametric):

```python
def _acl_grants(*, inheritable: bool = False) -> list[str]:
    suffix = "(OI)(CI)(F)" if inheritable else "(F)"
    grants = [
        "/grant:r", f"{_SYSTEM_SID}:{suffix}",
        "/grant:r", f"{_ADMINISTRATORS_SID}:{suffix}",
    ]
    sid = _current_sid()
    if sid and f"*{sid}" not in (_SYSTEM_SID, _ADMINISTRATORS_SID):
        grants += ["/grant:r", f"*{sid}:{suffix}"]
    return grants


def restrict_acl(path: Path, *, inheritable: bool = False) -> None:
    """Drop inherited ACEs; grant only SYSTEM, Administrators, this account.

    inheritable=True is for directories whose future children must inherit
    the restriction (the credential store)."""
    if sys.platform != "win32":
        return  # ACLs are Windows-only; POSIX dev runs skip this
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", *_acl_grants(inheritable=inheritable)],
        check=True, capture_output=True, text=True,
    )
```

Keep the existing docstring content on `_acl_grants` (move it into the new signature unchanged).

`service/config.py` — add below `token_path`:

```python
    @property
    def credentials_dir(self) -> Path:
        return self.data_dir / "credentials"
```

`src/mml_cloud_transfer/auth/credential_store.py`:

```python
"""DPAPI-encrypted credential payloads under the service data directory.

Follows the token-file pattern exactly (service/security.py::ensure_token):
create the empty file, cut its ACL, then write — the secret bytes never
exist on disk under a permissive ACL. Grants are by process SID, never
account names (gate fix 6e45d4a). The directory itself gets an inheritable
cut ACL as defence in depth.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from mml_cloud_transfer.auth.dpapi import protect, unprotect
from mml_cloud_transfer.service.security import restrict_acl

_REF_PATTERN = re.compile(r"cred-[0-9a-f]{12}\.dpapi")


class CredentialStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def path_for(self, ref: str) -> Path:
        """The blob path for a ref. Refs come from the profiles table, but a
        path separator smuggled into one must never escape the store."""
        if not _REF_PATTERN.fullmatch(ref):
            raise ValueError(f"not a credential ref: {ref!r}")
        return self._root / ref

    def save(self, payload: dict) -> str:
        self._root.mkdir(parents=True, exist_ok=True)
        restrict_acl(self._root, inheritable=True)
        ref = f"cred-{uuid.uuid4().hex[:12]}.dpapi"
        path = self._root / ref
        path.touch()
        restrict_acl(path)
        path.write_bytes(protect(json.dumps(payload).encode("utf-8")))
        return ref

    def load(self, ref: str) -> dict:
        blob = self.path_for(ref).read_bytes()
        return json.loads(unprotect(blob).decode("utf-8"))

    def delete(self, ref: str) -> None:
        self.path_for(ref).unlink(missing_ok=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/auth/test_credential_store.py tests/service/test_security.py tests/service/test_config.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python -m pytest`
Expected: all non-skipped tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/mml_cloud_transfer/auth/credential_store.py src/mml_cloud_transfer/service/security.py src/mml_cloud_transfer/service/config.py tests/auth/test_credential_store.py tests/service/test_security.py tests/service/test_config.py
git commit -m "feat: ACL-restricted DPAPI credential store under the service data dir"
```

---

### Task 4: Schema v2 and first-class profile repository methods

Two jobs: (a) fix the carried-over race — `get_or_create_profile` names rows with `COUNT(*) + 1`, so two connections can race to the same name and crash on `UNIQUE(name)`; (b) give profiles the CRUD surface the API needs, plus a `validated_at` column (schema v2 — the first real migration).

**Files:**
- Modify: `src/mml_cloud_transfer/store/schema.py`
- Modify: `src/mml_cloud_transfer/store/repository.py`
- Test: `tests/store/test_schema.py` (extend), `tests/store/test_repository_profiles.py` (new)

**Interfaces:**
- Consumes: existing `JobRepository`, `_now()`.
- Produces (used by Tasks 7, 8, 9):
  - `SCHEMA_VERSION = 2`; `profiles.validated_at TEXT` (nullable) on both fresh and migrated databases.
  - `JobRepository.get_or_create_profile(*, bucket, auth_type, credential_ref=None) -> int` — same signature, race-free.
  - `JobRepository.find_profile_by_name(name: str) -> sqlite3.Row | None`
  - `JobRepository.list_profiles() -> list[sqlite3.Row]`
  - `JobRepository.set_profile_validated(profile_id: int) -> None` — stamps `validated_at = _now()`; `LookupError` on a bogus id.
  - `JobRepository.delete_profile(profile_id: int) -> None` — raises `ProfileInUse` while jobs reference it, `LookupError` on a bogus id.
  - `class ProfileInUse(Exception)` exported from `store.repository`.
  - `create_profile` gains `validated_at` passthrough? **No** — it stays as is; validation is stamped separately.

- [ ] **Step 1: Write the failing tests**

Append to `tests/store/test_schema.py`:

```python
def test_fresh_database_is_version_2_with_validated_at(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 2
        columns = {r[1] for r in conn.execute("PRAGMA table_info(profiles)")}
        assert "validated_at" in columns
    finally:
        conn.close()


def test_a_v1_database_is_migrated_in_place(tmp_path):
    """Build a database exactly as schema v1 wrote it (no validated_at,
    version=1), then connect(): the column appears, the version bumps,
    and existing rows survive."""
    import sqlite3

    db = tmp_path / "jobs.db"
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version (version) VALUES (1);
        CREATE TABLE profiles (
            id             INTEGER PRIMARY KEY,
            name           TEXT NOT NULL UNIQUE,
            project_id     TEXT NOT NULL,
            bucket         TEXT NOT NULL,
            auth_type      TEXT NOT NULL,
            credential_ref TEXT,
            default_prefix TEXT NOT NULL DEFAULT '',
            created_at     TEXT NOT NULL
        );
        INSERT INTO profiles (name, project_id, bucket, auth_type, created_at)
        VALUES ('legacy', '', 'b', 'adc', '2026-08-05T00:00:00+00:00');
        """
    )
    raw.commit()
    raw.close()

    conn = connect(db)
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 2
        row = conn.execute("SELECT * FROM profiles WHERE name = 'legacy'").fetchone()
        assert row["validated_at"] is None  # new column, old row intact
    finally:
        conn.close()
```

(`tests/store/test_schema.py` already imports `connect` — check its imports and reuse them.)

`tests/store/test_repository_profiles.py`:

```python
"""First-class profile methods, including the Plan 3 deferred race fix:
name allocation must be arbitrated by the UNIQUE index, not COUNT(*)."""

import threading

import pytest

from mml_cloud_transfer.core.models import Direction
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository, ProfileInUse


@pytest.fixture
def repo(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    yield JobRepository(conn)
    conn.close()


def test_get_or_create_is_idempotent_per_triple(repo):
    a = repo.get_or_create_profile(bucket="b", auth_type="adc")
    b = repo.get_or_create_profile(bucket="b", auth_type="adc")
    assert a == b


def test_name_collision_with_a_different_credential_gets_a_suffix(repo):
    a = repo.get_or_create_profile(bucket="b", auth_type="key_file", credential_ref="k1.json")
    b = repo.get_or_create_profile(bucket="b", auth_type="key_file", credential_ref="k2.json")
    assert a != b
    names = {repo.get_profile(a)["name"], repo.get_profile(b)["name"]}
    assert names == {"b [key_file]", "b [key_file] (2)"}


def test_concurrent_get_or_create_converges_on_one_row(tmp_path):
    """The Plan 3 race: two connections, same triple, interleaved. The
    COUNT-based name made one crash; now the UNIQUE index arbitrates and
    both get the same row."""
    db = tmp_path / "jobs.db"
    connect(db).close()  # create schema before threads race on it
    results: list[int] = []
    errors: list[Exception] = []

    def worker():
        conn = connect(db)
        try:
            for _ in range(20):
                results.append(
                    JobRepository(conn).get_or_create_profile(bucket="b", auth_type="adc")
                )
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(set(results)) == 1
    check = connect(db)
    try:
        assert check.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 1
    finally:
        check.close()


def test_find_by_name_and_list(repo):
    pid = repo.create_profile(name="lab", bucket="b", auth_type="oauth_user",
                              credential_ref="cred-000000000000.dpapi")
    assert repo.find_profile_by_name("lab")["id"] == pid
    assert repo.find_profile_by_name("nope") is None
    assert [r["name"] for r in repo.list_profiles()] == ["lab"]


def test_set_profile_validated_stamps_a_timestamp(repo):
    pid = repo.create_profile(name="lab", bucket="b", auth_type="adc")
    assert repo.get_profile(pid)["validated_at"] is None
    repo.set_profile_validated(pid)
    assert repo.get_profile(pid)["validated_at"] is not None
    with pytest.raises(LookupError):
        repo.set_profile_validated(999)


def test_delete_profile_refuses_while_jobs_reference_it(repo):
    pid = repo.create_profile(name="lab", bucket="b", auth_type="adc")
    repo.create_job(name="j", direction=Direction.UPLOAD, source_root=r"C:\x",
                    dest_prefix="p", profile_id=pid)
    with pytest.raises(ProfileInUse):
        repo.delete_profile(pid)
    assert repo.get_profile(pid) is not None  # still there


def test_delete_profile_removes_an_unreferenced_row(repo):
    pid = repo.create_profile(name="lab", bucket="b", auth_type="adc")
    repo.delete_profile(pid)
    with pytest.raises(LookupError):
        repo.get_profile(pid)
    with pytest.raises(LookupError):
        repo.delete_profile(pid)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/store/test_schema.py tests/store/test_repository_profiles.py -v`
Expected: FAIL — version is 1, `validated_at` missing, `ImportError: cannot import name 'ProfileInUse'`, missing methods.

- [ ] **Step 3: Implement the schema migration**

In `store/schema.py`: set `SCHEMA_VERSION = 2`, add `validated_at TEXT` to the `profiles` DDL (after `default_prefix`), and replace `apply_migrations`:

```python
CREATE TABLE IF NOT EXISTS profiles (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE,
    project_id     TEXT NOT NULL,
    bucket         TEXT NOT NULL,
    auth_type      TEXT NOT NULL,
    credential_ref TEXT,
    default_prefix TEXT NOT NULL DEFAULT '',
    validated_at   TEXT,
    created_at     TEXT NOT NULL
);
```

```python
def apply_migrations(conn: sqlite3.Connection) -> None:
    """Create or upgrade the schema. Safe to call on every connect.

    The DDL is CREATE IF NOT EXISTS, so a fresh database is born at the
    current version; an existing database keeps its tables and gets the
    per-version ALTERs below.
    """
    conn.executescript(_DDL)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
        )
        conn.commit()
        return
    version = row[0]
    if version < 2:
        # v1 -> v2: profiles.validated_at (when preflight last passed)
        conn.execute("ALTER TABLE profiles ADD COLUMN validated_at TEXT")
        conn.execute("UPDATE schema_version SET version = 2")
    conn.commit()
```

(`row[0]` not `row["version"]`: `apply_migrations` may be handed a connection without `row_factory` set.)

- [ ] **Step 4: Implement the repository methods**

In `store/repository.py`, add near the top (after the imports):

```python
class ProfileInUse(Exception):
    """Deleting a profile that jobs still reference."""
```

Replace `get_or_create_profile` and extend the profiles section:

```python
    def get_or_create_profile(
        self, *, bucket: str, auth_type: str, credential_ref: str | None = None
    ) -> int:
        """Find-or-create without COUNT-based naming: the UNIQUE(name)
        index is the arbiter, so two connections racing here converge on
        one row instead of crashing (Plan 3 deferred fix). A name taken by
        a *different* credential gets the next suffix; a name taken by our
        twin is found on the next loop."""
        base = f"{bucket} [{auth_type}]"
        for attempt in range(1, 101):
            row = self.find_profile(
                bucket=bucket, auth_type=auth_type, credential_ref=credential_ref
            )
            if row is not None:
                return int(row["id"])
            candidate = base if attempt == 1 else f"{base} ({attempt})"
            try:
                return self.create_profile(
                    name=candidate, bucket=bucket, auth_type=auth_type,
                    credential_ref=credential_ref,
                )
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError(f"could not allocate a profile name from {base!r}")

    def find_profile_by_name(self, name: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM profiles WHERE name = ?", (name,)
        ).fetchone()

    def list_profiles(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM profiles ORDER BY id").fetchall()

    def set_profile_validated(self, profile_id: int) -> None:
        self.get_profile(profile_id)  # LookupError on a bogus id
        self._conn.execute(
            "UPDATE profiles SET validated_at = ? WHERE id = ?",
            (_now(), profile_id),
        )

    def delete_profile(self, profile_id: int) -> None:
        """Refuses while jobs reference the profile: jobs keep it for
        report/bucket lookups, and the FK would reject the delete anyway —
        this just says so in words."""
        self.get_profile(profile_id)  # LookupError on a bogus id
        used = self._conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE profile_id = ?", (profile_id,)
        ).fetchone()["n"]
        if used:
            raise ProfileInUse(
                f"profile {profile_id} is used by {used} job(s) and cannot be"
                " deleted while they exist"
            )
        self._conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/store/ -v`
Expected: ALL PASS (including every pre-existing store test — the migration must not disturb them).

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python -m pytest`
Expected: all non-skipped tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/mml_cloud_transfer/store/schema.py src/mml_cloud_transfer/store/repository.py tests/store/test_schema.py tests/store/test_repository_profiles.py
git commit -m "feat: schema v2 (profiles.validated_at) and race-free first-class profile CRUD"
```

---

### Task 5: GcsContext from stored credentials

`make_context` learns to build clients from an **in-memory** credential dict (a service-account key JSON or an authorized-user payload), and a new `auth/context.py` maps a profile row + credential store to a context. The worker delegates to it, so profile-backed jobs run unattended: `google.oauth2.credentials.Credentials` refreshes itself from the stored refresh token on every job, no user session required.

**Files:**
- Modify: `src/mml_cloud_transfer/gcs/client.py`
- Create: `src/mml_cloud_transfer/auth/context.py`
- Modify: `src/mml_cloud_transfer/service/worker.py` (`_context`)
- Test: `tests/gcs/test_client.py` (extend), `tests/auth/test_context.py` (new), `tests/service/test_worker.py` (extend)

**Interfaces:**
- Consumes: `CredentialStore` (Task 3), existing `make_context` / `GcsContext`.
- Produces (used by Tasks 7, 8):
  - `make_context(bucket, *, credentials_path=None, emulator_endpoint=None, credentials_info: dict | None = None, project: str | None = None) -> GcsContext` — `credentials_info` dispatches on its `"type"` field: `"service_account"` or `"authorized_user"`; anything else raises `ValueError`.
  - `auth.context.context_for_profile(profile: Mapping, store: CredentialStore, *, make_context_fn=make_context) -> GcsContext` — handles all five `auth_type` values.

- [ ] **Step 1: Write the failing tests**

Append to `tests/gcs/test_client.py` (check the file's existing imports; add the ones below that are missing):

```python
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa as crypto_rsa


@pytest.fixture(scope="module")
def sa_key_json() -> dict:
    """A syntactically valid service-account key: real RSA PEM, fake
    identity. Construction-only tests — nothing here talks to Google."""
    key = crypto_rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return {
        "type": "service_account",
        "project_id": "mmlct-test",
        "private_key_id": "0" * 40,
        "private_key": pem,
        "client_email": "probe@mmlct-test.iam.gserviceaccount.com",
        "client_id": "0",
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def test_make_context_from_service_account_info(sa_key_json):
    from google.oauth2 import service_account

    ctx = make_context("bkt", credentials_info=sa_key_json)
    assert isinstance(ctx.client._credentials, service_account.Credentials)
    assert ctx.client.project == "mmlct-test"
    assert ctx.bucket == "bkt"


def test_make_context_from_authorized_user_info():
    from google.oauth2.credentials import Credentials as UserCredentials

    info = {
        "type": "authorized_user",
        "client_id": "c", "client_secret": "s",
        "refresh_token": "rt",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    ctx = make_context("bkt", credentials_info=info, project="real-project")
    assert isinstance(ctx.client._credentials, UserCredentials)
    assert ctx.client.project == "real-project"


def test_make_context_authorized_user_without_project_uses_placeholder():
    info = {
        "type": "authorized_user",
        "client_id": "c", "client_secret": "s", "refresh_token": "rt",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    ctx = make_context("bkt", credentials_info=info)
    assert ctx.client.project == "mmlct"


def test_make_context_rejects_an_unknown_credential_type():
    with pytest.raises(ValueError, match="unsupported credential type"):
        make_context("bkt", credentials_info={"type": "mystery"})
```

`tests/auth/test_context.py`:

```python
"""Profile row -> GcsContext dispatch, including the DPAPI-backed types."""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")

from mml_cloud_transfer.auth.context import context_for_profile
from mml_cloud_transfer.auth.credential_store import CredentialStore

PAYLOAD = {
    "type": "authorized_user",
    "client_id": "c", "client_secret": "s", "refresh_token": "rt",
    "token_uri": "https://oauth2.googleapis.com/token",
}


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, bucket, **kwargs):
        self.calls.append((bucket, kwargs))
        return "CTX"


def _profile(**overrides):
    profile = {
        "bucket": "bkt", "auth_type": "adc", "credential_ref": None,
        "project_id": "",
    }
    profile.update(overrides)
    return profile


def test_oauth_user_profiles_load_the_stored_payload(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    ref = store.save(PAYLOAD)
    recorder = Recorder()
    ctx = context_for_profile(
        _profile(auth_type="oauth_user", credential_ref=ref, project_id="proj"),
        store, make_context_fn=recorder,
    )
    assert ctx == "CTX"
    assert recorder.calls == [("bkt", {"credentials_info": PAYLOAD, "project": "proj"})]


def test_service_account_key_profiles_load_the_stored_payload(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    key = {"type": "service_account", "project_id": "p"}
    ref = store.save(key)
    recorder = Recorder()
    context_for_profile(
        _profile(auth_type="service_account_key", credential_ref=ref),
        store, make_context_fn=recorder,
    )
    assert recorder.calls[0][1]["credentials_info"] == key


def test_legacy_auth_types_dispatch_exactly_as_the_worker_did(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    recorder = Recorder()
    context_for_profile(
        _profile(auth_type="emulator", credential_ref="http://127.0.0.1:9"),
        store, make_context_fn=recorder,
    )
    context_for_profile(
        _profile(auth_type="key_file", credential_ref=r"C:\k.json"),
        store, make_context_fn=recorder,
    )
    context_for_profile(_profile(auth_type="adc"), store, make_context_fn=recorder)
    assert recorder.calls == [
        ("bkt", {"emulator_endpoint": "http://127.0.0.1:9"}),
        ("bkt", {"credentials_path": r"C:\k.json"}),
        ("bkt", {}),
    ]
```

Append to `tests/service/test_worker.py` (reuse its existing fixtures/imports where possible):

```python
def test_worker_context_builds_from_a_dpapi_profile(tmp_path):
    """A profile-backed job must reach make_context with the decrypted
    payload — the unattended-after-logoff path."""
    import sys
    if sys.platform != "win32":
        pytest.skip("DPAPI is Windows-only")
    from mml_cloud_transfer.auth.credential_store import CredentialStore
    from mml_cloud_transfer.service.config import load_config
    from mml_cloud_transfer.service.controller import JobController
    from mml_cloud_transfer.service.worker import QueueWorker

    config = load_config(tmp_path / "data")
    payload = {
        "type": "authorized_user", "client_id": "c", "client_secret": "s",
        "refresh_token": "rt", "token_uri": "https://x/token",
    }
    ref = CredentialStore(config.credentials_dir).save(payload)
    seen = {}

    def recorder(bucket, **kwargs):
        seen.update({"bucket": bucket, **kwargs})
        return "CTX"

    worker = QueueWorker(config, JobController(), make_context_fn=recorder)
    ctx = worker._context({
        "bucket": "bkt", "auth_type": "oauth_user",
        "credential_ref": ref, "project_id": "",
    })
    assert ctx == "CTX"
    assert seen["credentials_info"] == payload
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/gcs/test_client.py tests/auth/test_context.py tests/service/test_worker.py -v`
Expected: new tests FAIL (`TypeError: make_context() got an unexpected keyword argument 'credentials_info'`, `ModuleNotFoundError: ...auth.context`); existing tests PASS.

- [ ] **Step 3: Implement `make_context`**

In `gcs/client.py`, hoist the scope list and add the new parameters:

```python
_SCOPES = ["https://www.googleapis.com/auth/devstorage.read_write"]


def make_context(
    bucket: str,
    *,
    credentials_path: str | None = None,
    emulator_endpoint: str | None = None,
    credentials_info: dict | None = None,
    project: str | None = None,
) -> GcsContext:
    """Build a context from one of four credential sources.

    Priority: explicit emulator endpoint (anonymous) > in-memory credential
    dict (the DPAPI store hands these over) > explicit service account key
    file > Application Default Credentials. ``credentials_info`` dispatches
    on its "type" field, matching Google's own file formats:
    "service_account" (a key JSON) or "authorized_user" (a stored OAuth
    refresh token — the client refreshes it autonomously, which is what
    makes profile jobs run unattended). ``project`` only matters for
    authorized_user, which carries no project of its own; object-level
    operations never send it, so the placeholder is harmless.
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

    if credentials_info is not None:
        kind = credentials_info.get("type")
        if kind == "service_account":
            from google.oauth2 import service_account

            credentials = service_account.Credentials.from_service_account_info(
                credentials_info, scopes=_SCOPES
            )
            client_project = credentials.project_id
        elif kind == "authorized_user":
            from google.oauth2.credentials import Credentials as UserCredentials

            credentials = UserCredentials.from_authorized_user_info(
                credentials_info, scopes=_SCOPES
            )
            client_project = project or "mmlct"
        else:
            raise ValueError(f"unsupported credential type: {kind!r}")
        client = storage.Client(project=client_project, credentials=credentials)
        return GcsContext(
            client=client,
            session=AuthorizedSession(credentials),
            endpoint=_DEFAULT_ENDPOINT,
            bucket=bucket,
        )

    if credentials_path is not None:
        from google.oauth2 import service_account

        path = Path(credentials_path)
        if not path.exists():
            raise FileNotFoundError(f"credentials file not found: {credentials_path}")
        credentials = service_account.Credentials.from_service_account_file(
            str(path), scopes=_SCOPES
        )
        project = credentials.project_id
    else:
        import google.auth

        credentials, project = google.auth.default(scopes=_SCOPES)

    client = storage.Client(project=project, credentials=credentials)
    return GcsContext(
        client=client,
        session=AuthorizedSession(credentials),
        endpoint=_DEFAULT_ENDPOINT,
        bucket=bucket,
    )
```

- [ ] **Step 4: Implement `auth/context.py` and the worker delegation**

`src/mml_cloud_transfer/auth/context.py`:

```python
"""Profile row -> authenticated GcsContext.

One dispatch for both the worker and the API, so a profile behaves
identically at job runtime and at preflight time. The DPAPI-backed types
load their payload from the store; the legacy types (emulator, key_file,
adc) dispatch exactly as service/worker.py did before Plan 4.
"""

from __future__ import annotations

from collections.abc import Mapping

from mml_cloud_transfer.auth.credential_store import CredentialStore
from mml_cloud_transfer.gcs.client import GcsContext, make_context


def context_for_profile(
    profile: Mapping,
    store: CredentialStore,
    *,
    make_context_fn=make_context,
) -> GcsContext:
    auth_type = profile["auth_type"]
    bucket = profile["bucket"]
    if auth_type == "emulator":
        return make_context_fn(bucket, emulator_endpoint=profile["credential_ref"])
    if auth_type == "key_file":
        return make_context_fn(bucket, credentials_path=profile["credential_ref"])
    if auth_type in ("service_account_key", "oauth_user"):
        payload = store.load(profile["credential_ref"])
        return make_context_fn(
            bucket,
            credentials_info=payload,
            project=profile["project_id"] or None,
        )
    return make_context_fn(bucket)  # adc
```

In `service/worker.py`, replace `_context` and add the imports:

```python
from mml_cloud_transfer.auth.context import context_for_profile
from mml_cloud_transfer.auth.credential_store import CredentialStore
```

```python
    def _context(self, profile):
        return context_for_profile(
            profile,
            CredentialStore(self._config.credentials_dir),
            make_context_fn=self._make_context,
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/gcs/test_client.py tests/auth/test_context.py tests/service/test_worker.py -v`
Expected: ALL PASS (existing worker tests still pass — the legacy dispatch is byte-for-byte preserved in `context_for_profile`).

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python -m pytest`
Expected: all non-skipped tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/mml_cloud_transfer/gcs/client.py src/mml_cloud_transfer/auth/context.py src/mml_cloud_transfer/service/worker.py tests/gcs/test_client.py tests/auth/test_context.py tests/service/test_worker.py
git commit -m "feat: build GcsContext from stored credential payloads; worker dispatches via auth.context"
```

---

### Task 6: Direction-aware preflight in product code

The release gate's preflight probes (write, compose, delete — with version-aware cleanup) move from `tests/tools/preflight-gcs.ps1` into `auth/preflight.py`, producing the spec's plain-language capability report: *"This credential can list and read but cannot write to gs://bucket/prefix."* Probes are object-level only — `storage.buckets.get` is denied on the reference bucket, so bucket-metadata reads must never be required (gate Finding 3).

**Files:**
- Create: `src/mml_cloud_transfer/auth/preflight.py`
- Test: `tests/auth/test_preflight.py`

**Interfaces:**
- Consumes: `GcsContext`, `gcs.objects.get_meta/delete_object`, `core.errors.classify`, `core.models.Direction`.
- Produces (used by Tasks 7, 8):
  - `@dataclass(frozen=True, slots=True) class PreflightResult:` fields `bucket: str`, `prefix: str`, `can_list: bool`, `can_read: bool`, `can_write: bool`, `can_compose: bool`, `can_delete: bool`, `messages: tuple[str, ...]`; methods `ok_for(direction: Direction) -> bool`, `summary() -> str`.
  - `run_preflight(ctx: GcsContext, prefix: str) -> PreflightResult`

- [ ] **Step 1: Write the failing tests**

`tests/auth/test_preflight.py`:

```python
"""Capability probes. The emulator proves the wiring end-to-end; failure
paths use REAL google.api_core exception types on a stub (fake-gcs-server
is authless, so it can never produce a 403 itself); the real_bucket test
is the truth-teller for versioned-delete semantics."""

import pytest

from mml_cloud_transfer.core.models import Direction
from mml_cloud_transfer.auth.preflight import PreflightResult, run_preflight
from mml_cloud_transfer.gcs.client import make_context


def _result(**overrides) -> PreflightResult:
    fields = dict(bucket="b", prefix="p", can_list=True, can_read=True,
                  can_write=True, can_compose=True, can_delete=True, messages=())
    fields.update(overrides)
    return PreflightResult(**fields)


def test_summary_names_what_works_and_what_does_not():
    result = _result(can_write=False, can_compose=False, can_delete=False)
    assert result.summary() == (
        "This credential can list and read but cannot write, compose"
        " and delete to gs://b/p."
    )


def test_summary_when_everything_works():
    assert "can list, read, write, compose and delete" in _result().summary()


def test_upload_needs_the_full_set_download_needs_list_and_read():
    read_only = _result(can_write=False, can_compose=False, can_delete=False)
    assert read_only.ok_for(Direction.DOWNLOAD) is True
    assert read_only.ok_for(Direction.UPLOAD) is False
    assert _result().ok_for(Direction.UPLOAD) is True


@pytest.mark.emulator
def test_probes_pass_and_clean_up_against_the_emulator(emulator, emulator_client):
    client, bucket_name = emulator_client
    ctx = make_context(bucket_name, emulator_endpoint=emulator.endpoint)
    result = run_preflight(ctx, "data")
    assert result.can_list and result.can_read and result.can_write
    assert result.can_compose and result.can_delete
    assert result.messages == ()
    leftovers = list(client.list_blobs(bucket_name, prefix="data/.mmlct-preflight/"))
    assert leftovers == []


@pytest.mark.emulator
def test_a_real_forbidden_write_reads_as_cannot_write(emulator, emulator_client, monkeypatch):
    """The failure path with the REAL exception type a locked-down bucket
    returns (google.api_core Forbidden, code 403)."""
    from google.api_core.exceptions import Forbidden
    from google.cloud.storage.blob import Blob

    client, bucket_name = emulator_client
    ctx = make_context(bucket_name, emulator_endpoint=emulator.endpoint)

    def deny(self, *args, **kwargs):
        raise Forbidden("probe@x does not have storage.objects.create access")

    monkeypatch.setattr(Blob, "upload_from_string", deny)
    result = run_preflight(ctx, "data")
    assert result.can_write is False
    assert result.can_compose is False       # nothing written to compose
    assert result.can_list and result.can_read
    assert result.ok_for(Direction.UPLOAD) is False
    assert result.ok_for(Direction.DOWNLOAD) is True
    assert any("write" in m for m in result.messages)


@pytest.mark.real_bucket
def test_preflight_against_the_real_bucket_leaves_no_versions(real_bucket_ctx):
    """afsc_mml_ccep: versioning ON, buckets.get denied. All five probes
    must pass using object-level operations only, and cleanup must remove
    every VERSION it wrote (a live-only 'clean' is not clean)."""
    ctx, run_prefix = real_bucket_ctx
    probe_prefix = f"{run_prefix}preflight"
    result = run_preflight(ctx, probe_prefix)
    assert result.can_list and result.can_read and result.can_write
    assert result.can_compose and result.can_delete, result.messages
    survivors = list(
        ctx.client.list_blobs(ctx.bucket, prefix=probe_prefix, versions=True)
    )
    assert survivors == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/auth/test_preflight.py -v`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'mml_cloud_transfer.auth.preflight'`. (`real_bucket` test skips without env vars — that is correct.)

- [ ] **Step 3: Implement**

`src/mml_cloud_transfer/auth/preflight.py`:

```python
"""Permission probes against a real bucket, in plain language.

Adapted from the release gate's preflight (tests/tools/preflight-gcs.ps1),
which proved the shape: bucket-metadata reads are a separate permission the
product must never require (storage.buckets.get is denied on the reference
bucket), so capabilities are established by DOING the operations, inside a
throwaway probe prefix, and cleaning up by explicit generation so a
versioning-enabled bucket is left with nothing — not even noncurrent
versions (gate Finding 5).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from mml_cloud_transfer.core.errors import classify
from mml_cloud_transfer.core.models import Direction
from mml_cloud_transfer.gcs.client import GcsContext
from mml_cloud_transfer.gcs.objects import delete_object, get_meta


def _join(words: list[str]) -> str:
    if len(words) == 1:
        return words[0]
    return ", ".join(words[:-1]) + f" and {words[-1]}"


@dataclass(frozen=True, slots=True)
class PreflightResult:
    bucket: str
    prefix: str
    can_list: bool
    can_read: bool
    can_write: bool
    can_compose: bool
    can_delete: bool
    messages: tuple[str, ...]

    def ok_for(self, direction: Direction) -> bool:
        """Uploads need the full set: write obviously; read for Layer 2
        verification and the Layer 3 audit; compose and delete for sliced
        files' temp objects. Requiring everything at creation beats
        discovering a gap overnight. Downloads read and list only."""
        if direction is Direction.UPLOAD:
            return all((self.can_list, self.can_read, self.can_write,
                        self.can_compose, self.can_delete))
        return self.can_list and self.can_read

    def summary(self) -> str:
        caps = {
            "list": self.can_list, "read": self.can_read, "write": self.can_write,
            "compose": self.can_compose, "delete": self.can_delete,
        }
        target = f"gs://{self.bucket}/{self.prefix}".rstrip("/")
        can = [name for name, ok in caps.items() if ok]
        cannot = [name for name, ok in caps.items() if not ok]
        if not cannot:
            return f"This credential can {_join(can)} to {target}."
        if not can:
            return f"This credential cannot access {target} at all."
        return (
            f"This credential can {_join(can)} but cannot {_join(cannot)}"
            f" to {target}."
        )


def run_preflight(ctx: GcsContext, prefix: str) -> PreflightResult:
    base = prefix.strip("/")
    probe = (f"{base}/" if base else "") + f".mmlct-preflight/{uuid.uuid4().hex[:8]}"
    target = f"gs://{ctx.bucket}/{base}".rstrip("/")
    messages: list[str] = []
    written: list[tuple[str, int]] = []  # (name, generation): version-aware cleanup
    bucket_handle = ctx.client.bucket(ctx.bucket)

    def fail(operation: str, exc: Exception) -> None:
        messages.append(f"cannot {operation} to {target}: {classify(exc).message}")

    try:
        list(ctx.client.list_blobs(ctx.bucket, prefix=probe, max_results=1))
        can_list = True
    except Exception as exc:
        can_list = False
        fail("list", exc)

    can_write = True
    for name in (f"{probe}/a.bin", f"{probe}/b.bin"):
        try:
            blob = bucket_handle.blob(name)
            blob.upload_from_string(b"mmlct preflight probe", checksum="crc32c")
            written.append((name, int(blob.generation)))
        except Exception as exc:
            can_write = False
            fail("write", exc)
            break

    try:
        # With nothing written (read-only credential), a metadata GET on an
        # absent name still proves objects.get: a 404 means the server
        # consulted the ACL and answered; a 403 means it refused to — the
        # same distinction worker._probe relies on.
        read_name = written[0][0] if written else f"{probe}/absent.bin"
        get_meta(ctx, read_name)
        can_read = True
    except Exception as exc:
        can_read = False
        fail("read", exc)

    can_compose = False
    if len(written) == 2:
        try:
            composed = bucket_handle.blob(f"{probe}/composed.bin")
            composed.compose([bucket_handle.blob(name) for name, _ in written])
            written.append((f"{probe}/composed.bin", int(composed.generation)))
            can_compose = True
        except Exception as exc:
            fail("compose", exc)

    can_delete = bool(written)
    for name, generation in written:
        try:
            delete_object(ctx, name, generation=generation, ignore_missing=False)
        except Exception as exc:
            can_delete = False
            fail("delete", exc)

    return PreflightResult(
        bucket=ctx.bucket, prefix=base,
        can_list=can_list, can_read=can_read, can_write=can_write,
        can_compose=can_compose, can_delete=can_delete,
        messages=tuple(messages),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/auth/test_preflight.py -v`
Expected: unit + emulator tests PASS; real_bucket test SKIPPED (no env vars).

- [ ] **Step 5: Run the real-bucket preflight test once (if credentials are available in this session)**

Run:
```powershell
$env:MMLCT_TEST_BUCKET = "afsc_mml_ccep"; $env:MMLCT_TEST_PREFIX = "scratch"
.venv/Scripts/python -m pytest tests/auth/test_preflight.py -m real_bucket -v
Remove-Item Env:MMLCT_TEST_BUCKET; Remove-Item Env:MMLCT_TEST_PREFIX
```
Expected: 1 PASS. If ADC is unavailable in the execution environment, note that in the task report — the Phase 4 manual gate (Task 13) runs it.

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python -m pytest`
Expected: all non-skipped tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/mml_cloud_transfer/auth/preflight.py tests/auth/test_preflight.py
git commit -m "feat: direction-aware permission preflight with plain-language capability report"
```

---

### Task 7: Profiles API

The service grows a `/profiles` resource. Creation validates the candidate credential by **real operations against the target bucket** (spec: "a wrong key or missing IAM grant surfaces during setup rather than overnight"), then DPAPI-stores the payload and creates the row. `create_app` gains an injectable `preflight_fn` so route tests can stub the network while everything else runs for real.

**Files:**
- Modify: `src/mml_cloud_transfer/service/app.py`
- Modify: `src/mml_cloud_transfer/cli/service_client.py`
- Test: `tests/service/test_profiles_api.py` (new)

**Interfaces:**
- Consumes: `CredentialStore` (3), repository profile methods (4), `make_context(credentials_info=)` + `context_for_profile` (5), `run_preflight`/`PreflightResult` (6), `classify` (1).
- Produces (used by Tasks 8, 12):
  - `create_app(config, controller, *, preflight_fn=run_preflight) -> FastAPI`
  - `POST /profiles` (201) body `ProfileCreate {name, bucket, auth_type: "service_account_key"|"oauth_user", credential: dict, project_id: str = "", default_prefix: str = "", emulator_endpoint: str|None}` → `{**profile_row_sans_credential_ref, "preflight": {...}, "summary": str}`; 400 = credential rejected/insufficient; 409 = name exists; 422 = wrong credential JSON type.
  - `GET /profiles` → list of profile dicts (never `credential_ref`).
  - `POST /profiles/{id}/check` body `{direction?: "upload"|"download", prefix?: str}` → `{"ok": bool, "summary": str, "preflight": {...}}`; stamps `validated_at` when ok.
  - `DELETE /profiles/{id}` → `{"deleted": id}`; 409 while jobs reference it; removes the DPAPI blob.
  - `ApiClient.create_profile(payload: dict) -> dict`, `.list_profiles() -> list[dict]`, `.check_profile(profile_id, *, direction=None, prefix=None) -> dict`, `.delete_profile(profile_id) -> dict`.

- [ ] **Step 1: Write the failing tests**

`tests/service/test_profiles_api.py`:

```python
"""Profile CRUD over the REST surface. Emulator profiles exercise the full
route (validate -> store -> row) against fake-gcs-server; the DPAPI wiring
test uses a stubbed preflight so no real network is touched."""

import sys

import pytest
from fastapi.testclient import TestClient

from mml_cloud_transfer.auth.preflight import PreflightResult
from mml_cloud_transfer.core.models import Direction
from mml_cloud_transfer.service.app import create_app
from mml_cloud_transfer.service.config import load_config
from mml_cloud_transfer.service.controller import JobController
from mml_cloud_transfer.service.security import read_token
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


def _make_client(tmp_path, preflight_fn=None):
    config = load_config(tmp_path / "data")
    kwargs = {"preflight_fn": preflight_fn} if preflight_fn else {}
    app = create_app(config, JobController(), **kwargs)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {read_token(config.token_path)}"})
    return client, config


def _emulator_profile(name="lab", **overrides):
    payload = {
        "name": name, "bucket": None, "auth_type": "service_account_key",
        "credential": {"type": "service_account"}, "default_prefix": "data",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def emulator_api(tmp_path, emulator, emulator_client):
    _, bucket_name = emulator_client
    client, config = _make_client(tmp_path)
    def create(name="lab", **overrides):
        body = _emulator_profile(
            name=name, bucket=bucket_name,
            emulator_endpoint=emulator.endpoint, **overrides,
        )
        return client.post("/profiles", json=body)
    return client, config, create


@pytest.mark.emulator
def test_create_validates_against_the_bucket_and_reports_capabilities(emulator_api):
    client, config, create = emulator_api
    response = create()
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["auth_type"] == "emulator"          # emulator affordance
    assert body["validated_at"] is not None
    assert "credential_ref" not in body             # internals stay internal
    assert body["preflight"]["can_write"] is True
    assert "can list" in body["summary"]


@pytest.mark.emulator
def test_duplicate_name_is_409(emulator_api):
    _, _, create = emulator_api
    assert create().status_code == 201
    response = create()
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


@pytest.mark.emulator
def test_list_and_check_and_delete(emulator_api):
    client, config, create = emulator_api
    profile_id = create().json()["id"]

    listed = client.get("/profiles").json()
    assert [p["id"] for p in listed] == [profile_id]
    assert all("credential_ref" not in p for p in listed)

    checked = client.post(f"/profiles/{profile_id}/check",
                          json={"direction": "upload"})
    assert checked.status_code == 200
    assert checked.json()["ok"] is True

    assert client.delete(f"/profiles/{profile_id}").json() == {"deleted": profile_id}
    assert client.get("/profiles").json() == []


@pytest.mark.emulator
def test_delete_refuses_while_a_job_references_the_profile(emulator_api):
    client, config, create = emulator_api
    profile_id = create().json()["id"]
    conn = connect(config.db_path)
    try:
        JobRepository(conn).create_job(
            name="j", direction=Direction.UPLOAD, source_root=r"C:\x",
            dest_prefix="p", profile_id=profile_id,
        )
    finally:
        conn.close()
    response = client.delete(f"/profiles/{profile_id}")
    assert response.status_code == 409
    assert "job" in response.json()["detail"]


def test_check_unknown_profile_is_404(tmp_path):
    client, _ = _make_client(tmp_path)
    assert client.post("/profiles/99/check", json={}).status_code == 404
    assert client.delete("/profiles/99").status_code == 404


def test_wrong_credential_json_type_is_422(tmp_path):
    client, _ = _make_client(tmp_path)
    response = client.post("/profiles", json={
        "name": "x", "bucket": "b", "auth_type": "oauth_user",
        "credential": {"type": "service_account"},
    })
    assert response.status_code == 422
    assert "authorized_user" in response.json()["detail"]


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_a_real_key_payload_is_dpapi_stored_and_loads_back(tmp_path, sa_key_json):
    """Stubbed preflight (no network); everything else real: context built
    from the key, payload DPAPI-encrypted on disk, row points at the blob."""
    from mml_cloud_transfer.auth.credential_store import CredentialStore

    ok = PreflightResult(bucket="b", prefix="", can_list=True, can_read=True,
                         can_write=True, can_compose=True, can_delete=True,
                         messages=())
    client, config = _make_client(tmp_path, preflight_fn=lambda ctx, prefix: ok)
    response = client.post("/profiles", json={
        "name": "keyed", "bucket": "b", "auth_type": "service_account_key",
        "credential": sa_key_json,
    })
    assert response.status_code == 201, response.text

    conn = connect(config.db_path)
    try:
        row = JobRepository(conn).find_profile_by_name("keyed")
    finally:
        conn.close()
    assert row["auth_type"] == "service_account_key"
    store = CredentialStore(config.credentials_dir)
    assert store.load(row["credential_ref"]) == sa_key_json
    raw = store.path_for(row["credential_ref"]).read_bytes()
    assert b"PRIVATE KEY" not in raw   # encrypted at rest
```

Also move the `sa_key_json` fixture from `tests/gcs/test_client.py` into `tests/conftest.py` so both files share it: delete the fixture (and the two `cryptography` imports) from `tests/gcs/test_client.py`, and add to `tests/conftest.py`:

```python
@pytest.fixture(scope="session")
def sa_key_json() -> dict:
    """A syntactically valid service-account key: real RSA PEM, fake
    identity. Construction-only tests — nothing here talks to Google."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa as crypto_rsa

    key = crypto_rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return {
        "type": "service_account",
        "project_id": "mmlct-test",
        "private_key_id": "0" * 40,
        "private_key": pem,
        "client_email": "probe@mmlct-test.iam.gserviceaccount.com",
        "client_id": "0",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/service/test_profiles_api.py -v`
Expected: FAIL — 404s on `/profiles` routes (`assert response.status_code == 201` gets 404), `create_app() got an unexpected keyword argument 'preflight_fn'`.

- [ ] **Step 3: Implement the routes**

In `service/app.py`:

Add imports:

```python
import sqlite3

from mml_cloud_transfer.auth.context import context_for_profile
from mml_cloud_transfer.auth.credential_store import CredentialStore
from mml_cloud_transfer.auth.preflight import run_preflight
from mml_cloud_transfer.core.errors import classify
from mml_cloud_transfer.gcs.client import make_context
from mml_cloud_transfer.store.repository import ProfileInUse
```

Add models next to `JobSubmission`:

```python
class ProfileCreate(BaseModel):
    name: str = Field(min_length=1)
    bucket: str = Field(min_length=1)
    auth_type: Literal["service_account_key", "oauth_user"]
    credential: dict
    project_id: str = ""
    default_prefix: str = ""
    emulator_endpoint: str | None = None  # tests only, like JobSubmission's


class ProfileCheck(BaseModel):
    direction: Literal["upload", "download"] | None = None
    prefix: str | None = None
```

Change the factory signature:

```python
def create_app(
    config: ServiceConfig,
    controller: JobController,
    *,
    preflight_fn=run_preflight,
) -> FastAPI:
```

Add helpers inside `create_app` (next to `_open`/`_job_or_404`):

```python
    def _profile_dict(row) -> dict:
        data = _row_dict(row)
        data.pop("credential_ref", None)  # an internal filename, not API surface
        return data

    def _profile_or_404(repo: JobRepository, profile_id: int):
        try:
            return repo.get_profile(profile_id)
        except LookupError:
            raise HTTPException(
                status_code=404, detail=f"no profile with id {profile_id}"
            ) from None
```

Add the routes on the existing `router` (they inherit the bearer-token dependency):

```python
    @router.post("/profiles", status_code=201)
    def create_profile(body: ProfileCreate) -> dict:
        expected = (
            "service_account" if body.auth_type == "service_account_key"
            else "authorized_user"
        )
        if body.emulator_endpoint is None and body.credential.get("type") != expected:
            raise HTTPException(status_code=422, detail=(
                f"credential JSON has type {body.credential.get('type')!r};"
                f" a {body.auth_type} profile needs {expected!r}"
            ))

        # Validate by real operations against the target bucket BEFORE
        # anything is stored (spec: a wrong key or missing IAM grant
        # surfaces during setup rather than overnight).
        try:
            if body.emulator_endpoint:
                ctx = make_context(
                    body.bucket, emulator_endpoint=body.emulator_endpoint
                )
            else:
                ctx = make_context(
                    body.bucket,
                    credentials_info=body.credential,
                    project=body.project_id or None,
                )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=(
                f"credential rejected before reaching the bucket:"
                f" {classify(exc).message}"
            )) from exc
        result = preflight_fn(ctx, body.default_prefix)
        if not (result.can_list and result.can_read):
            raise HTTPException(status_code=400, detail=result.summary())

        if body.emulator_endpoint:
            auth_type, credential_ref, store = "emulator", body.emulator_endpoint, None
        else:
            store = CredentialStore(config.credentials_dir)
            auth_type, credential_ref = body.auth_type, store.save(body.credential)

        conn, repo = _open()
        try:
            try:
                profile_id = repo.create_profile(
                    name=body.name, bucket=body.bucket, auth_type=auth_type,
                    credential_ref=credential_ref, project_id=body.project_id,
                    default_prefix=body.default_prefix,
                )
            except sqlite3.IntegrityError:
                if store is not None:
                    store.delete(credential_ref)
                raise HTTPException(status_code=409, detail=(
                    f"a profile named {body.name!r} already exists"
                )) from None
            repo.set_profile_validated(profile_id)
            row = repo.get_profile(profile_id)
        finally:
            conn.close()
        return {
            **_profile_dict(row),
            "preflight": asdict(result),
            "summary": result.summary(),
        }

    @router.get("/profiles")
    def list_profiles() -> list[dict]:
        conn, repo = _open()
        try:
            return [_profile_dict(row) for row in repo.list_profiles()]
        finally:
            conn.close()

    @router.post("/profiles/{profile_id}/check")
    def check_profile(profile_id: int, body: ProfileCheck) -> dict:
        conn, repo = _open()
        try:
            row = _profile_or_404(repo, profile_id)
        finally:
            conn.close()
        try:
            ctx = context_for_profile(row, CredentialStore(config.credentials_dir))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=(
                f"stored credential could not be loaded: {classify(exc).message}"
            )) from exc
        prefix = body.prefix if body.prefix is not None else row["default_prefix"]
        result = preflight_fn(ctx, prefix)
        if body.direction is not None:
            ok = result.ok_for(Direction(body.direction))
        else:
            ok = result.can_list and result.can_read
        if ok:
            conn, repo = _open()
            try:
                repo.set_profile_validated(profile_id)
            finally:
                conn.close()
        return {"ok": ok, "summary": result.summary(), "preflight": asdict(result)}

    @router.delete("/profiles/{profile_id}")
    def delete_profile(profile_id: int) -> dict:
        conn, repo = _open()
        try:
            row = _profile_or_404(repo, profile_id)
            try:
                repo.delete_profile(profile_id)
            except ProfileInUse as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
        finally:
            conn.close()
        # Blob removal AFTER the row is gone: an in-use profile must keep
        # its credential. delete() is idempotent, so a crash between the
        # two leaves only an orphaned encrypted blob, never a broken profile.
        if row["auth_type"] in ("service_account_key", "oauth_user") and row["credential_ref"]:
            CredentialStore(config.credentials_dir).delete(row["credential_ref"])
        return {"deleted": profile_id}
```

- [ ] **Step 4: Add the ApiClient methods**

Append to `cli/service_client.py::ApiClient` (preflight does several network round-trips — give creation/check a generous timeout):

```python
    def create_profile(self, payload: dict) -> dict:
        return self._check(
            self._session.post(f"{self._base}/profiles", json=payload, timeout=120)
        )

    def list_profiles(self) -> list[dict]:
        return self._check(self._session.get(f"{self._base}/profiles", timeout=30))

    def check_profile(
        self, profile_id: int, *, direction: str | None = None, prefix: str | None = None
    ) -> dict:
        return self._check(
            self._session.post(
                f"{self._base}/profiles/{profile_id}/check",
                json={"direction": direction, "prefix": prefix}, timeout=120,
            )
        )

    def delete_profile(self, profile_id: int) -> dict:
        return self._check(
            self._session.delete(f"{self._base}/profiles/{profile_id}", timeout=30)
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/service/test_profiles_api.py tests/service/test_api.py tests/gcs/test_client.py -v`
Expected: ALL PASS (including the pre-existing API tests — no existing route changed).

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python -m pytest`
Expected: all non-skipped tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/mml_cloud_transfer/service/app.py src/mml_cloud_transfer/cli/service_client.py tests/service/test_profiles_api.py tests/conftest.py tests/gcs/test_client.py
git commit -m "feat: /profiles API - create validates against the real bucket, DPAPI-stores, check/list/delete"
```

---

### Task 8: Profile-aware job submission

`POST /jobs` accepts `profile` (a profile name) as an alternative to `bucket`. Profile submissions run the **direction-appropriate** preflight before the job is created — the spec's "per-profile preflight permission check appropriate to the job direction" — and inherit the profile's `default_prefix`. The ad-hoc `bucket` path (the live install's bridge config) is pinned by a regression test.

**Files:**
- Modify: `src/mml_cloud_transfer/service/app.py` (`JobSubmission`, `submit_job`)
- Modify: `src/mml_cloud_transfer/cli/__main__.py`, `src/mml_cloud_transfer/cli/transfer_command.py` (`--profile` passthrough)
- Test: `tests/service/test_profiles_api.py` (extend), `tests/service/test_api.py` (bridge pin), `tests/cli/test_transfer_cli.py` (extend)

**Interfaces:**
- Consumes: Task 7 routes/models, `context_for_profile`, `run_preflight`, repository methods.
- Produces (used by Tasks 9, 12):
  - `JobSubmission.profile: str | None = None`; `JobSubmission.bucket: str | None` (now optional; exactly one of the two must be set → 422 otherwise).
  - `POST /jobs` with `profile`: 404 unknown profile; 400 with the capability summary when `ok_for(direction)` fails; on success response gains `"profile_id"` and `"preflight_summary"`.
  - CLI: `mmlct transfer --profile <name>` (service mode only; `--bucket` no longer required when `--profile` given).

- [ ] **Step 1: Write the failing tests**

Append to `tests/service/test_profiles_api.py`:

```python
@pytest.mark.emulator
def test_submit_job_by_profile_name(emulator_api, tmp_path):
    client, config, create = emulator_api
    create(name="lab")  # default_prefix "data"
    src = tmp_path / "src"; src.mkdir()
    response = client.post("/jobs", json={
        "name": "j", "direction": "upload", "source_root": str(src),
        "profile": "lab",
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["profile_id"] is not None
    assert "can list" in body["preflight_summary"]

    conn = connect(config.db_path)
    try:
        job = JobRepository(conn).get_job(body["job_id"])
        assert job["dest_prefix"] == "data"       # inherited default_prefix
        assert job["profile_id"] == body["profile_id"]
    finally:
        conn.close()


@pytest.mark.emulator
def test_submit_with_explicit_prefix_overrides_the_default(emulator_api, tmp_path):
    client, config, create = emulator_api
    create(name="lab")
    src = tmp_path / "src"; src.mkdir()
    response = client.post("/jobs", json={
        "name": "j", "direction": "upload", "source_root": str(src),
        "profile": "lab", "dest_prefix": "elsewhere",
    })
    conn = connect(config.db_path)
    try:
        job = JobRepository(conn).get_job(response.json()["job_id"])
        assert job["dest_prefix"] == "elsewhere"
    finally:
        conn.close()


@pytest.mark.emulator
def test_submit_by_unknown_profile_is_404(emulator_api, tmp_path):
    client, _, _ = emulator_api
    src = tmp_path / "src"; src.mkdir()
    response = client.post("/jobs", json={
        "name": "j", "direction": "upload", "source_root": str(src),
        "profile": "ghost",
    })
    assert response.status_code == 404


def test_submit_needs_exactly_one_of_profile_and_bucket(tmp_path):
    client, _ = _make_client(tmp_path)
    src = tmp_path / "src"; src.mkdir()
    both = client.post("/jobs", json={
        "name": "j", "direction": "upload", "source_root": str(src),
        "profile": "lab", "bucket": "b",
    })
    neither = client.post("/jobs", json={
        "name": "j", "direction": "upload", "source_root": str(src),
    })
    assert both.status_code == 422
    assert neither.status_code == 422


def test_an_upload_that_cannot_write_is_rejected_with_the_summary(tmp_path):
    """Direction-appropriate preflight at submission: a read-only profile
    can download but not upload."""
    read_only = PreflightResult(
        bucket="b", prefix="data", can_list=True, can_read=True,
        can_write=False, can_compose=False, can_delete=False,
        messages=("cannot write to gs://b/data: Access to this file was denied.",),
    )
    client, config = _make_client(tmp_path, preflight_fn=lambda ctx, prefix: read_only)
    # An emulator profile so context building needs no real credentials.
    conn = connect(config.db_path)
    try:
        JobRepository(conn).create_profile(
            name="ro", bucket="b", auth_type="emulator",
            credential_ref="http://127.0.0.1:9",
        )
    finally:
        conn.close()
    src = tmp_path / "src"; src.mkdir()
    up = client.post("/jobs", json={
        "name": "j", "direction": "upload", "source_root": str(src),
        "profile": "ro",
    })
    assert up.status_code == 400
    assert "cannot write" in up.json()["detail"]
    down = client.post("/jobs", json={
        "name": "j", "direction": "download",
        "source_root": str(tmp_path / "dl"), "profile": "ro",
    })
    assert down.status_code == 201, down.text
```

Append to `tests/service/test_api.py`:

```python
def test_bridge_config_submission_shape_is_pinned(api, tmp_path):
    """Carried-over item 4: the live install submits with bucket + ADC and
    no profile field. That shape must keep working verbatim — DPAPI
    profiles replace it eventually; they do not break it."""
    client, config, _ = api
    job_id = _submit(client, tmp_path)  # bucket-only payload, as before
    conn = connect(config.db_path)
    try:
        repo = JobRepository(conn)
        job = repo.get_job(job_id)
        profile = repo.get_profile(job["profile_id"])
        assert profile["auth_type"] == "adc"
        assert profile["bucket"] == "b"
    finally:
        conn.close()
```

Append to `tests/cli/test_transfer_cli.py` (match the file's existing style for invoking `main`):

```python
def test_transfer_profile_requires_service_url(tmp_path, capsys, monkeypatch):
    # The env var default would silently turn this into service mode on a
    # machine (like the dev box) where the live install exports it.
    monkeypatch.delenv("MMLCT_SERVICE_URL", raising=False)
    code = main([
        "transfer", "--db", str(tmp_path / "j.db"), "--name", "j",
        "--source", str(tmp_path), "--profile", "lab",
    ])
    assert code == 2
    assert "--service-url" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/service/test_profiles_api.py tests/service/test_api.py tests/cli/test_transfer_cli.py -v`
Expected: new tests FAIL (422 from pydantic for the unknown `profile` field is NOT the expected 404/201 paths; `--profile` unknown argument); existing PASS.

- [ ] **Step 3: Implement the API side**

In `service/app.py`:

`JobSubmission` changes:

```python
class JobSubmission(BaseModel):
    name: str = Field(min_length=1)
    direction: Literal["upload", "download"]
    source_root: str = Field(min_length=1)
    dest_prefix: str = ""
    profile: str | None = None
    bucket: str | None = Field(default=None, min_length=1)
    credentials_path: str | None = None
    emulator_endpoint: str | None = None
    audit_hash: bool = False
    scheduled_start_at: str | None = None
```

In `submit_job`, right after the existing path checks and schedule normalization, replace the auth-type/profile block and the create block with:

```python
        if (submission.profile is None) == (submission.bucket is None):
            raise HTTPException(
                status_code=422,
                detail="provide exactly one of 'profile' or 'bucket'",
            )

        preflight_summary = None
        if submission.profile is not None:
            conn, repo = _open()
            try:
                row = repo.find_profile_by_name(submission.profile)
            finally:
                conn.close()
            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"no profile named {submission.profile!r}",
                )
            profile_id = int(row["id"])
            bucket = row["bucket"]
            dest_prefix = submission.dest_prefix or row["default_prefix"]
            # The spec's per-profile preflight, appropriate to the job
            # direction, at the one moment a human is present to see it.
            try:
                ctx = context_for_profile(
                    row, CredentialStore(config.credentials_dir)
                )
            except Exception as exc:
                raise HTTPException(status_code=400, detail=(
                    f"stored credential could not be loaded:"
                    f" {classify(exc).message}"
                )) from exc
            result = preflight_fn(ctx, dest_prefix)
            if not result.ok_for(Direction(submission.direction)):
                raise HTTPException(status_code=400, detail=result.summary())
            preflight_summary = result.summary()
            conn, repo = _open()
            try:
                repo.set_profile_validated(profile_id)
            finally:
                conn.close()
        else:
            profile_id = None
            bucket = submission.bucket
            dest_prefix = submission.dest_prefix

        conn, repo = _open()
        try:
            if profile_id is None:
                if submission.emulator_endpoint:
                    auth_type, credential_ref = "emulator", submission.emulator_endpoint
                elif submission.credentials_path:
                    auth_type, credential_ref = "key_file", submission.credentials_path
                else:
                    auth_type, credential_ref = "adc", None
                profile_id = repo.get_or_create_profile(
                    bucket=bucket, auth_type=auth_type,
                    credential_ref=credential_ref,
                )
            job_id = repo.create_job(
                name=submission.name,
                direction=Direction(submission.direction),
                source_root=submission.source_root,
                dest_prefix=dest_prefix,
                profile_id=profile_id,
                audit_hash=submission.audit_hash,
                scheduled_start_at=scheduled,
            )
            repo.record_event(
                job_id, "job_submitted", f"direction={submission.direction}"
            )
        finally:
            conn.close()
        return {
            "job_id": job_id,
            "scheduled_start_at": scheduled,
            "profile_id": profile_id,
            "preflight_summary": preflight_summary,
        }
```

- [ ] **Step 4: Implement the CLI passthrough**

`cli/__main__.py`: in the `transfer` parser, change `--bucket` from required to optional inside `add_gcs_options` **only for transfer** — simplest: add to the transfer parser after `add_gcs_options(transfer)`:

```python
    transfer.add_argument(
        "--profile", default=None,
        help="Use a named connection profile (requires --service-url)",
    )
```

and change `add_gcs_options`'s bucket line to:

```python
        sub.add_argument("--bucket", required=False, default=None,
                         help="Destination bucket name (or use --profile)")
```

`resume`/`report` direct modes still need a bucket; `run_resume` already receives it from args — add an explicit check in `transfer_command._context`:

```python
def _context(args):
    if not args.bucket:
        raise ValueError("--bucket is required in direct mode (no --service-url)")
    return make_context(
        args.bucket,
        credentials_path=args.credentials,
        emulator_endpoint=args.emulator_endpoint,
    )
```

In `transfer_command.run_transfer`, add at the top (before the `--scheduled-at` check):

```python
    if getattr(args, "profile", None) and not args.service_url:
        raise ValueError(
            "--profile needs the service (profiles live in the service"
            " database); pass --service-url"
        )
    if not getattr(args, "profile", None) and not args.bucket:
        raise ValueError("provide --bucket, or --profile with --service-url")
```

In `run_transfer_via_service`, build the payload with both fields:

```python
    job_id = client.submit_job({
        "name": args.name,
        "direction": args.direction,
        "source_root": args.source,
        "dest_prefix": args.prefix,
        "profile": getattr(args, "profile", None),
        "bucket": args.bucket,
        "credentials_path": args.credentials,
        "emulator_endpoint": args.emulator_endpoint,
        "audit_hash": args.audit_hash,
        "scheduled_start_at": args.scheduled_at,
    })
```

(`ValueError` from `run_transfer` already maps to exit code 2 in `__main__.py`.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/service/ tests/cli/ -v`
Expected: ALL PASS.

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python -m pytest`
Expected: all non-skipped tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/mml_cloud_transfer/service/app.py src/mml_cloud_transfer/cli/__main__.py src/mml_cloud_transfer/cli/transfer_command.py tests/service/test_profiles_api.py tests/service/test_api.py tests/cli/test_transfer_cli.py
git commit -m "feat: profile-aware job submission with direction-appropriate preflight; bridge config pinned"
```

---

### Task 9: Duplicate-destination guard

Release-gate follow-up 3 residual: the per-job lock cannot stop a second `mmlct transfer` (which always creates a NEW job id) from putting two writers on one destination after a crash. Guard at job creation: a not-finished job with the same `(source_root, dest_prefix, bucket)` blocks creating another — the operator is told to resume or cancel it instead.

**Files:**
- Modify: `src/mml_cloud_transfer/core/paths.py` (`canonical_source_key`)
- Modify: `src/mml_cloud_transfer/store/repository.py` (`find_active_duplicate`)
- Modify: `src/mml_cloud_transfer/service/app.py` (guard inside the create transaction)
- Modify: `src/mml_cloud_transfer/cli/transfer_command.py` (direct mode)
- Test: `tests/core/test_paths.py` (extend), `tests/store/test_repository_profiles.py` (extend), `tests/service/test_profiles_api.py` (extend), `tests/cli/test_transfer_cli.py` (extend)

**Interfaces:**
- Consumes: repository, `resolve_mapped_drive`/`extended_path`/`display_path`.
- Produces:
  - `core.paths.canonical_source_key(path: str) -> str` — equality key: mapped drive resolved, `\\?\` machinery stripped, separators normalized, trailing separator stripped, casefolded.
  - `JobRepository.find_active_duplicate(*, source_root: str, dest_prefix: str, bucket: str) -> sqlite3.Row | None` — active = status in {pending, scanning, running, paused, stalled, incomplete}; a profile-less active job (bucket unknowable) matches conservatively.
  - `POST /jobs` → 409 naming the blocking job; CLI direct `transfer` → prints the resume command, exit 3.

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_paths.py`:

```python
def test_canonical_source_key_equates_the_same_folder_spelled_differently():
    from mml_cloud_transfer.core.paths import canonical_source_key

    assert (
        canonical_source_key(r"C:\Data\Run47")
        == canonical_source_key(r"c:/data/run47/")
        == canonical_source_key("\\\\?\\C:\\DATA\\RUN47")
    )
    assert canonical_source_key(r"C:\a") != canonical_source_key(r"C:\b")


def test_canonical_source_key_resolves_mapped_drives():
    from mml_cloud_transfer.core.paths import canonical_source_key

    key = canonical_source_key(
        r"Z:\imaging", resolver=lambda drive: r"\\server\share"
    )
    assert key == canonical_source_key(
        r"\\server\share\imaging", resolver=lambda drive: None
    )
```

Append to `tests/store/test_repository_profiles.py`:

```python
def test_find_active_duplicate_blocks_and_releases(repo):
    from mml_cloud_transfer.core.models import JobStatus

    pid = repo.create_profile(name="lab", bucket="bkt", auth_type="adc")
    job_id = repo.create_job(name="j", direction=Direction.UPLOAD,
                             source_root=r"C:\data\run47", dest_prefix="p",
                             profile_id=pid)

    hit = repo.find_active_duplicate(
        source_root=r"C:/DATA/run47/", dest_prefix="p", bucket="bkt")
    assert hit is not None and hit["id"] == job_id

    # A different bucket, prefix, or source is a different destination.
    assert repo.find_active_duplicate(
        source_root=r"C:\data\run47", dest_prefix="p", bucket="other") is None
    assert repo.find_active_duplicate(
        source_root=r"C:\data\run47", dest_prefix="q", bucket="bkt") is None
    assert repo.find_active_duplicate(
        source_root=r"C:\data\other", dest_prefix="p", bucket="bkt") is None

    # Finished jobs stop blocking.
    for status in (JobStatus.COMPLETE, JobStatus.CANCELLED):
        repo.set_job_status(job_id, status)
        assert repo.find_active_duplicate(
            source_root=r"C:\data\run47", dest_prefix="p", bucket="bkt") is None

    # INCOMPLETE blocks: the honest answer is "resume job N", not a twin.
    repo.set_job_status(job_id, JobStatus.INCOMPLETE)
    assert repo.find_active_duplicate(
        source_root=r"C:\data\run47", dest_prefix="p", bucket="bkt") is not None


def test_find_active_duplicate_is_conservative_about_profileless_jobs(repo):
    """Direct-CLI jobs have no profile row, so their bucket is unknowable.
    Same source + same prefix + unknown bucket blocks: double-writing is
    the expensive mistake, a clear 'resume or cancel job N' is cheap."""
    repo.create_job(name="j", direction=Direction.UPLOAD,
                    source_root=r"C:\data", dest_prefix="p")
    assert repo.find_active_duplicate(
        source_root=r"C:\data", dest_prefix="p", bucket="any") is not None
```

Append to `tests/service/test_profiles_api.py`:

```python
@pytest.mark.emulator
def test_resubmitting_the_same_destination_is_409(emulator_api, tmp_path):
    client, config, create = emulator_api
    create(name="lab")
    src = tmp_path / "src"; src.mkdir()
    payload = {
        "name": "j", "direction": "upload", "source_root": str(src),
        "profile": "lab",
    }
    first = client.post("/jobs", json=payload)
    assert first.status_code == 201
    second = client.post("/jobs", json=payload)
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert str(first.json()["job_id"]) in detail
    assert "resume" in detail

    # Cancelling the first job releases the destination.
    client.post(f"/jobs/{first.json()['job_id']}/cancel")
    assert client.post("/jobs", json=payload).status_code == 201
```

Append to `tests/cli/test_transfer_cli.py`:

```python
def test_direct_mode_reissue_after_crash_is_refused(tmp_path, capsys, monkeypatch):
    """The release-gate residual, closed: re-issuing `transfer` (not
    `resume`) for a destination an active job owns exits 3 with the
    resume command — no second writer. The guard must fire before any
    GCS context is built, so this needs no credentials and no network."""
    monkeypatch.delenv("MMLCT_SERVICE_URL", raising=False)  # force direct mode

    db = tmp_path / "jobs.db"
    src = tmp_path / "src"; src.mkdir()
    (src / "f.bin").write_bytes(b"x")
    conn = connect(db)
    try:
        repo = JobRepository(conn)
        pid = repo.create_profile(name="p", bucket="bkt", auth_type="adc")
        job_id = repo.create_job(name="j", direction=Direction.UPLOAD,
                                 source_root=str(src), dest_prefix="pre",
                                 profile_id=pid)
    finally:
        conn.close()

    code = main([
        "transfer", "--db", str(db), "--name", "j2", "--source", str(src),
        "--prefix", "pre", "--bucket", "bkt",
    ])
    out = capsys.readouterr().out
    assert code == 3
    assert f"--job-id {job_id}" in out
```

(The guard must fire before any GCS context is built, so this test needs no credentials and no network.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/core/test_paths.py tests/store/test_repository_profiles.py tests/service/test_profiles_api.py tests/cli/test_transfer_cli.py -v`
Expected: new tests FAIL (`ImportError: cannot import name 'canonical_source_key'`, `AttributeError: ... find_active_duplicate`, second submit returns 201 not 409).

- [ ] **Step 3: Implement `canonical_source_key`**

Append to `core/paths.py`:

```python
def canonical_source_key(
    path: str,
    resolver: Callable[[str], str | None] = default_drive_resolver,
) -> str:
    """Equality key for "the same source folder".

    Job rows store paths as submitted (drive-resolved by the CLI, raw from
    the API), so equality must survive spelling differences: mapped drive
    vs UNC, forward vs backward slashes, ``\\\\?\\`` prefix or not, trailing
    separator, and NTFS case-insensitivity.
    """
    resolved = resolve_mapped_drive(path, resolver)
    plain = display_path(extended_path(resolved))
    return plain.replace("/", "\\").rstrip("\\").casefold()
```

- [ ] **Step 4: Implement `find_active_duplicate`**

In `store/repository.py`, add the import `from mml_cloud_transfer.core.paths import canonical_source_key, to_object_name` (extend the existing paths import) and near the job methods:

```python
    #: Statuses that can still write to (or be resumed onto) a destination.
    _ACTIVE_STATUSES = (
        JobStatus.PENDING.value, JobStatus.SCANNING.value,
        JobStatus.RUNNING.value, JobStatus.PAUSED.value,
        JobStatus.STALLED.value, JobStatus.INCOMPLETE.value,
    )

    def find_active_duplicate(
        self, *, source_root: str, dest_prefix: str, bucket: str
    ) -> sqlite3.Row | None:
        """A not-finished job already targeting this destination, or None.

        Two writers composing to one destination corrupt each other's slice
        temps and precondition bookkeeping (release-gate follow-up 3). The
        candidate set is filtered in SQL (destination prefix + active
        status); source equality needs canonical_source_key, so it runs in
        Python over the handful of active rows. A job with no profile row
        (direct-CLI history) has an unknowable bucket and matches
        conservatively — blocking is safe, double-writing is not."""
        placeholders = ", ".join("?" for _ in self._ACTIVE_STATUSES)
        rows = self._conn.execute(
            f"SELECT j.*, p.bucket AS profile_bucket FROM jobs j"
            f" LEFT JOIN profiles p ON p.id = j.profile_id"
            f" WHERE j.dest_prefix = ? AND j.status IN ({placeholders})",
            (dest_prefix, *self._ACTIVE_STATUSES),
        ).fetchall()
        wanted = canonical_source_key(source_root)
        for row in rows:
            if row["profile_bucket"] not in (None, bucket):
                continue
            if canonical_source_key(row["source_root"]) == wanted:
                return row
        return None
```

- [ ] **Step 5: Wire the guard into `submit_job` and direct-mode `run_transfer`**

In `service/app.py::submit_job`, replace the entire final create block (the `conn, repo = _open()` block Task 8 left) with an immediate transaction carrying the guard, so check-and-create is atomic against a concurrent submit (a failed INSERT inside the transaction aborts only that statement, so `get_or_create_profile`'s retry loop still works):

```python
        conn, repo = _open()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                duplicate = repo.find_active_duplicate(
                    source_root=submission.source_root,
                    dest_prefix=dest_prefix, bucket=bucket,
                )
                if duplicate is not None:
                    raise HTTPException(status_code=409, detail=(
                        f"job {duplicate['id']} ({duplicate['status']}) already"
                        f" transfers this source to"
                        f" gs://{bucket}/{dest_prefix or ''} — resume it"
                        f" (mmlct resume --job-id {duplicate['id']}) or cancel"
                        " it instead of creating a second writer"
                    ))
                if profile_id is None:
                    if submission.emulator_endpoint:
                        auth_type, credential_ref = "emulator", submission.emulator_endpoint
                    elif submission.credentials_path:
                        auth_type, credential_ref = "key_file", submission.credentials_path
                    else:
                        auth_type, credential_ref = "adc", None
                    profile_id = repo.get_or_create_profile(
                        bucket=bucket, auth_type=auth_type,
                        credential_ref=credential_ref,
                    )
                job_id = repo.create_job(
                    name=submission.name,
                    direction=Direction(submission.direction),
                    source_root=submission.source_root,
                    dest_prefix=dest_prefix,
                    profile_id=profile_id,
                    audit_hash=submission.audit_hash,
                    scheduled_start_at=scheduled,
                )
                repo.record_event(
                    job_id, "job_submitted", f"direction={submission.direction}"
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
        return {
            "job_id": job_id,
            "scheduled_start_at": scheduled,
            "profile_id": profile_id,
            "preflight_summary": preflight_summary,
        }
```

In `cli/transfer_command.py::run_transfer`, in the **direct-mode** branch (after the `--profile`/`--bucket` validation, before any `make_context`/scan):

```python
    conn = connect(args.db)
    try:
        duplicate = JobRepository(conn).find_active_duplicate(
            source_root=args.source, dest_prefix=args.prefix, bucket=args.bucket,
        )
    finally:
        conn.close()
    if duplicate is not None:
        print(
            f"job {duplicate['id']} ({duplicate['status']}) already transfers"
            f" this source to gs://{args.bucket}/{args.prefix or ''} — run:"
            f" mmlct resume --db {args.db} --job-id {duplicate['id']}"
            f" --bucket {args.bucket}"
        )
        return 3
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/core/test_paths.py tests/store/ tests/service/ tests/cli/ -v`
Expected: ALL PASS (existing submission tests unaffected: distinct destinations per test).

- [ ] **Step 7: Run the full suite**

Run: `.venv/Scripts/python -m pytest`
Expected: all non-skipped tests pass. If any pre-existing test submits the same (source, prefix, bucket) twice, fix the TEST by cancelling the first job or varying the prefix — that is the guard working.

- [ ] **Step 8: Commit**

```bash
git add src/mml_cloud_transfer/core/paths.py src/mml_cloud_transfer/store/repository.py src/mml_cloud_transfer/service/app.py src/mml_cloud_transfer/cli/transfer_command.py tests/core/test_paths.py tests/store/test_repository_profiles.py tests/service/test_profiles_api.py tests/cli/test_transfer_cli.py
git commit -m "feat: refuse a second writer on one destination at job creation (release-gate follow-up 3)"
```

---

### Task 10: Path reachability at job creation

The service runs under its own identity: it cannot see the user's mapped drives or per-user VPN. The CLI (the interactive side, like the GUI later) resolves mapped drives to UNC before submitting; the service tests reachability with `\\?\`-extended paths and, on failure, says **who** it is and **why** the path is invisible — instead of today's bare "not found".

**Files:**
- Modify: `src/mml_cloud_transfer/service/app.py` (submit path checks)
- Modify: `src/mml_cloud_transfer/cli/transfer_command.py` (resolve before submit)
- Test: `tests/service/test_api.py` (extend), `tests/cli/test_transfer_cli.py` (extend)

**Interfaces:**
- Consumes: `core.paths.extended_path/is_unc/display_path/resolve_mapped_drive`.
- Produces: clearer 400 details (message content pinned by tests); CLI submits UNC paths.

- [ ] **Step 1: Write the failing tests**

Append to `tests/service/test_api.py`:

```python
def _free_drive_letter() -> str:
    import os
    import string

    for letter in reversed(string.ascii_uppercase):
        if not os.path.exists(f"{letter}:\\"):
            return letter
    pytest.skip("every drive letter exists on this machine")


def test_unreachable_drive_letter_explains_mapped_drives(api):
    """A drive that does not exist for the service is (almost always) the
    user's mapped drive. The error must teach, not just refuse."""
    client, _, _ = api
    response = client.post("/jobs", json={
        "name": "j", "direction": "upload",
        "source_root": f"{_free_drive_letter()}:\\imaging\\run47", "bucket": "b",
    })
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "running as" in detail
    assert "mapped drive" in detail


def test_unreachable_unc_share_names_the_service_identity(api, monkeypatch):
    """UNC checks against a dead host can hang for many seconds — the
    filesystem answer is stubbed; the MESSAGE is what this test pins."""
    import os

    client, _, _ = api
    real_isdir = os.path.isdir
    monkeypatch.setattr(
        os.path, "isdir",
        lambda p: False if "unreachable-host" in str(p) else real_isdir(p),
    )
    response = client.post("/jobs", json={
        "name": "j", "direction": "upload",
        "source_root": r"\\unreachable-host\share\data", "bucket": "b",
    })
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "running as" in detail
    assert "VPN" in detail


def test_deep_download_destination_is_created_via_extended_path(api, tmp_path):
    """Spec: \\\\?\\-prefixed paths so >260-char destinations do not fail."""
    client, _, _ = api
    deep = tmp_path
    for i in range(12):
        deep = deep / ("d" * 24)
    assert len(str(deep)) > 260
    response = client.post("/jobs", json={
        "name": "j", "direction": "download",
        "source_root": str(deep), "bucket": "b",
    })
    assert response.status_code == 201, response.text
    import os
    from mml_cloud_transfer.core.paths import extended_path
    assert os.path.isdir(extended_path(str(deep)))
```

Append to `tests/cli/test_transfer_cli.py`:

```python
def test_service_submission_resolves_mapped_drives(tmp_path, monkeypatch, capsys):
    """The CLI is the interactive side: it must hand the service a UNC
    path, because the service cannot resolve the user's drive letters."""
    from mml_cloud_transfer.cli import transfer_command

    submitted = {}

    class StubClient:
        def submit_job(self, payload):
            submitted.update(payload)
            return 1

    monkeypatch.setattr(transfer_command, "_api_client", lambda args: StubClient())
    monkeypatch.setattr(
        transfer_command, "resolve_mapped_drive",
        lambda path, resolver=None: path.replace("Z:", r"\\server\share"),
    )
    monkeypatch.setattr(
        transfer_command, "_watch_until_settled", lambda client, job_id: None
    )
    monkeypatch.setattr(
        transfer_command, "_finish_via_service",
        lambda client, job_id, status: 0,
    )

    import argparse
    args = argparse.Namespace(
        name="j", direction="upload", source=r"Z:\imaging\run47", prefix="",
        profile=None, bucket="b", credentials=None, emulator_endpoint=None,
        audit_hash=False, scheduled_at=None, service_url="http://x",
        token_file=None,
    )
    code = transfer_command.run_transfer_via_service(args)
    assert code == 0
    assert submitted["source_root"] == r"\\server\share\imaging\run47"
    assert "mapped" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/service/test_api.py tests/cli/test_transfer_cli.py -v`
Expected: new tests FAIL (current detail says "not found", no identity/VPN text; CLI submits the drive-letter path unchanged); existing PASS.

- [ ] **Step 3: Implement the service side**

In `service/app.py`, add imports `import getpass` and `from mml_cloud_transfer.core.paths import display_path, extended_path, is_unc`, a module-level helper:

```python
def _service_identity() -> str:
    try:
        domain = os.environ.get("USERDOMAIN", "")
        user = getpass.getuser()
        return f"{domain}\\{user}" if domain else user
    except Exception:  # session-0 oddities must not break an error message
        return "the service account"


def _unreachable_detail(root: str) -> str:
    where = display_path(root)
    base = f"{where} is not reachable by the service (running as {_service_identity()})"
    if is_unc(root):
        return base + (
            ": check the share path and that this account has been granted"
            " access; folders on a per-user VPN are not visible to the service"
        )
    drive = root[:2] + "\\" if len(root) >= 2 and root[1] == ":" else None
    if drive is not None and not os.path.exists(drive):
        return base + (
            f": drive {root[:2]} does not exist for the service — if it is a"
            " mapped drive, use the full \\\\server\\share form (the CLI and"
            " GUI resolve mapped drives automatically)"
        )
    return f"source folder not found or not readable by the service account: {where}"
```

then replace the body of the path checks in `submit_job`:

```python
        root = submission.source_root
        if not os.path.isabs(root) or root.startswith("\\\\.\\"):
            raise HTTPException(status_code=400, detail=(
                "source_root must be an absolute path (drive or UNC),"
                f" not {root!r}"
            ))
        if submission.direction == "upload":
            if not os.path.isdir(extended_path(root)):
                raise HTTPException(status_code=400, detail=_unreachable_detail(root))
        else:
            # Spec: reachability is tested at creation, under the service's
            # own identity. Creating the destination folder IS that test —
            # via the extended path so >260-char trees work.
            try:
                os.makedirs(extended_path(root), exist_ok=True)
            except OSError as exc:
                raise HTTPException(status_code=400, detail=(
                    f"{_unreachable_detail(root)} ({exc.strerror or exc})"
                )) from exc
```

Note: the existing test `test_submit_upload_validates_the_source_folder` asserts `"not found" in detail` — the local-path fallback message above keeps the words "not found"; verify it still passes.

- [ ] **Step 4: Implement the CLI side**

In `cli/transfer_command.py`, add `resolve_mapped_drive` to the `core.paths` import, and at the top of `run_transfer_via_service`:

```python
    resolved = resolve_mapped_drive(args.source)
    if resolved != args.source:
        print(
            f"{args.source} is a mapped drive -> submitting {resolved}"
            " (the service cannot see your drive letters)"
        )
        args.source = resolved
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/service/test_api.py tests/cli/test_transfer_cli.py -v`
Expected: ALL PASS.

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python -m pytest`
Expected: all non-skipped tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/mml_cloud_transfer/service/app.py src/mml_cloud_transfer/cli/transfer_command.py tests/service/test_api.py tests/cli/test_transfer_cli.py
git commit -m "feat: identity-aware path reachability at job creation; CLI resolves mapped drives to UNC"
```

---

### Task 11: OAuth installed-app flow

The interactive half of the OAuth path: the installed-app flow with a loopback redirect, run in the user's session (CLI-hosted until the GUI exists). No desktop OAuth client ID exists yet, so the client config is **injectable** (`--client-config` file / `MMLCT_OAUTH_CLIENT` env var); the real-credential end-to-end is a Task 13 manual gate step.

**Files:**
- Modify: `pyproject.toml` (add `google-auth-oauthlib>=1.2`)
- Create: `src/mml_cloud_transfer/auth/oauth_flow.py`
- Test: `tests/auth/test_oauth_flow.py`

**Interfaces:**
- Consumes: `google_auth_oauthlib.flow.InstalledAppFlow` (new dep), `google.oauth2.credentials.Credentials`.
- Produces (used by Task 12):
  - `SCOPES = ["https://www.googleapis.com/auth/devstorage.read_write"]` (matches `gcs.client._SCOPES`)
  - `load_client_config(path: str | None) -> dict` — path arg, else `MMLCT_OAUTH_CLIENT`, else `ValueError` with instructions; validates the `"installed"` key.
  - `run_login(client_config: dict, *, open_browser: bool = True, port: int = 0, flow_factory=None) -> dict` — returns an authorized-user payload; raises `ValueError` when Google returns no refresh token.
  - `authorized_user_payload(creds) -> dict` — `{type, client_id, client_secret, refresh_token, token_uri, scopes}`.

- [ ] **Step 1: Add the dependency and reinstall**

In `pyproject.toml` `dependencies`, after the `requests` line, add:

```toml
    "google-auth-oauthlib>=1.2",
```

Run: `.venv/Scripts/python -m pip install -e ".[dev]"`
Expected: installs `google-auth-oauthlib` (plus `requests-oauthlib`/`oauthlib`); exit 0.

- [ ] **Step 2: Write the failing tests**

`tests/auth/test_oauth_flow.py`:

```python
"""The installed-app flow, minus the browser. The flow object is faked at
the factory seam; the Credentials object is the REAL google.oauth2 type,
so payload extraction is tested against the true shape. The full
browser-to-bucket path is the Phase 4 manual gate."""

import json

import pytest
from google.oauth2.credentials import Credentials

from mml_cloud_transfer.auth.oauth_flow import (
    SCOPES,
    authorized_user_payload,
    load_client_config,
    run_login,
)

CLIENT_CONFIG = {
    "installed": {
        "client_id": "abc.apps.googleusercontent.com",
        "client_secret": "notsecret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}


def _real_credentials(refresh_token="1//refresh"):
    return Credentials(
        token="ya29.access",
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id="abc.apps.googleusercontent.com",
        client_secret="notsecret",
        scopes=SCOPES,
    )


class FakeFlow:
    def __init__(self, creds):
        self._creds = creds
        self.kwargs = None

    def run_local_server(self, **kwargs):
        self.kwargs = kwargs
        return self._creds


def test_run_login_returns_a_service_usable_payload():
    flow = FakeFlow(_real_credentials())
    seen = {}

    def factory(config, scopes):
        seen.update(config=config, scopes=scopes)
        return flow

    payload = run_login(CLIENT_CONFIG, open_browser=False, flow_factory=factory)
    assert seen["config"] is CLIENT_CONFIG
    assert seen["scopes"] == SCOPES
    assert flow.kwargs["open_browser"] is False
    # offline access + forced consent are what guarantee a refresh token
    assert flow.kwargs["access_type"] == "offline"
    assert flow.kwargs["prompt"] == "consent"
    assert payload == {
        "type": "authorized_user",
        "client_id": "abc.apps.googleusercontent.com",
        "client_secret": "notsecret",
        "refresh_token": "1//refresh",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": SCOPES,
    }
    json.dumps(payload)  # must be JSON-serializable for the API call


def test_run_login_without_a_refresh_token_is_an_error():
    factory = lambda config, scopes: FakeFlow(_real_credentials(refresh_token=None))
    with pytest.raises(ValueError, match="refresh token"):
        run_login(CLIENT_CONFIG, flow_factory=factory)


def test_authorized_user_payload_from_the_real_type():
    payload = authorized_user_payload(_real_credentials())
    assert payload["type"] == "authorized_user"
    assert payload["refresh_token"] == "1//refresh"


def test_load_client_config_reads_a_file(tmp_path):
    path = tmp_path / "client.json"
    path.write_text(json.dumps(CLIENT_CONFIG), encoding="utf-8")
    assert load_client_config(str(path)) == CLIENT_CONFIG


def test_load_client_config_falls_back_to_the_env_var(tmp_path, monkeypatch):
    path = tmp_path / "client.json"
    path.write_text(json.dumps(CLIENT_CONFIG), encoding="utf-8")
    monkeypatch.setenv("MMLCT_OAUTH_CLIENT", str(path))
    assert load_client_config(None) == CLIENT_CONFIG


def test_load_client_config_without_any_source_explains_how(monkeypatch):
    monkeypatch.delenv("MMLCT_OAUTH_CLIENT", raising=False)
    with pytest.raises(ValueError, match="--client-config"):
        load_client_config(None)


def test_load_client_config_rejects_a_non_installed_app_config(tmp_path):
    path = tmp_path / "web.json"
    path.write_text(json.dumps({"web": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="installed"):
        load_client_config(str(path))
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/auth/test_oauth_flow.py -v`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'mml_cloud_transfer.auth.oauth_flow'`.

- [ ] **Step 4: Implement**

`src/mml_cloud_transfer/auth/oauth_flow.py`:

```python
"""User OAuth: the installed-app flow with a loopback redirect.

This half runs in the interactive session (CLI now, GUI in Phase 5) —
the browser cannot open in session 0. The resulting refresh token is
handed to the service, which refreshes access tokens autonomously
thereafter; that hand-off is what makes user-OAuth profiles work
unattended after logoff.

No desktop OAuth client ID ships yet, so the client configuration is
injected (a client_secret_*.json from Google Cloud Console) via
--client-config or MMLCT_OAUTH_CLIENT. For installed apps the
"client secret" is not genuinely secret — standard, and stated plainly
in the spec. Phase 6 packages a default client ID.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/devstorage.read_write"]


def load_client_config(path: str | None) -> dict:
    source = path or os.environ.get("MMLCT_OAUTH_CLIENT")
    if not source:
        raise ValueError(
            "no OAuth client configuration: pass --client-config (or set"
            " MMLCT_OAUTH_CLIENT) to a client_secret_*.json downloaded from"
            " Google Cloud Console > APIs & Services > Credentials >"
            " Create credentials > OAuth client ID > Desktop app"
        )
    config = json.loads(Path(source).read_text(encoding="utf-8-sig"))
    if "installed" not in config:
        raise ValueError(
            f"{source} is not an installed-app OAuth client configuration"
            " (missing the 'installed' key); create a 'Desktop app' client"
        )
    return config


def authorized_user_payload(creds) -> dict:
    """The service-side format: exactly what
    google.oauth2.credentials.Credentials.from_authorized_user_info eats."""
    return {
        "type": "authorized_user",
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "scopes": list(creds.scopes or SCOPES),
    }


def run_login(
    client_config: dict,
    *,
    open_browser: bool = True,
    port: int = 0,
    flow_factory=None,
) -> dict:
    """Run the browser flow; return an authorized-user payload.

    access_type=offline and prompt=consent force Google to (re)issue a
    refresh token — without them a re-consenting user gets access tokens
    only, and the profile would die at the first refresh after logoff.
    """
    if flow_factory is None:
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow_factory = InstalledAppFlow.from_client_config
    flow = flow_factory(client_config, scopes=SCOPES)
    creds = flow.run_local_server(
        port=port,
        open_browser=open_browser,
        access_type="offline",
        prompt="consent",
    )
    if not creds.refresh_token:
        raise ValueError(
            "Google did not return a refresh token; remove this app's access"
            " at https://myaccount.google.com/permissions and sign in again"
        )
    return authorized_user_payload(creds)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/auth/test_oauth_flow.py -v`
Expected: ALL PASS.

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python -m pytest`
Expected: all non-skipped tests pass.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/mml_cloud_transfer/auth/oauth_flow.py tests/auth/test_oauth_flow.py
git commit -m "feat: installed-app OAuth flow behind an injectable client config"
```

---

### Task 12: CLI profile commands

`mmlct profile add-key | login | list | check | remove` — the operator surface for everything above, including the spec's product copy: after `add-key`, the user is told they may delete the original key file; `login` prints the least-privilege recommendation.

**Files:**
- Create: `src/mml_cloud_transfer/cli/profile_command.py`
- Modify: `src/mml_cloud_transfer/cli/__main__.py`
- Test: `tests/cli/test_profile_cli.py`

**Interfaces:**
- Consumes: `ApiClient` profile methods (7), `oauth_flow` (11), `add_service_options`/`_dispatch_via_service` (existing).
- Produces: `run_profile(args) -> int` — exit 0 ok; 1 service refused / not found / check failed; 2 usage error (no service URL, bad key file, no client config).

  CLI grammar:
  ```
  mmlct profile add-key --name N --bucket B --key-file PATH [--prefix P] [--project ID] --service-url URL
  mmlct profile login   --name N --bucket B [--prefix P] [--project ID] [--client-config PATH] --service-url URL
  mmlct profile list    --service-url URL
  mmlct profile check   --name N [--direction upload|download] [--prefix P] --service-url URL
  mmlct profile remove  --name N --service-url URL
  ```
  `add-key`/`login` also accept hidden `--emulator-endpoint` (tests only, `argparse.SUPPRESS`).

- [ ] **Step 1: Write the failing tests**

`tests/cli/test_profile_cli.py`:

```python
"""CLI glue over a real running service host (ephemeral port, temp data
dir) with profiles backed by the emulator. The OAuth browser flow is
stubbed at run_login; everything below it is real."""

import json
import socket

import pytest

from mml_cloud_transfer.cli.__main__ import main
from mml_cloud_transfer.service.config import load_config


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def host(tmp_path):
    """Same shape as tests/service/conftest.py::running_host (that fixture
    is directory-scoped and not importable from tests/cli)."""
    from mml_cloud_transfer.service.host import ServiceHost

    config = load_config(tmp_path / "data", port=_free_port())
    service_host = ServiceHost(config)
    service_host.start()
    service_host.wait_ready()
    yield config
    service_host.stop()


def _profile_args(config, *extra):
    return [
        "--service-url", config.base_url,
        "--token-file", str(config.token_path),
        *extra,
    ]


def test_profile_commands_require_the_service(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("MMLCT_SERVICE_URL", raising=False)
    code = main(["profile", "list"])
    assert code == 2
    assert "--service-url" in capsys.readouterr().out


def test_add_key_rejects_a_non_key_file(tmp_path, capsys):
    bad = tmp_path / "not-a-key.json"
    bad.write_text(json.dumps({"type": "authorized_user"}), encoding="utf-8")
    code = main([
        "profile", "add-key", "--name", "x", "--bucket", "b",
        "--key-file", str(bad), "--service-url", "http://127.0.0.1:9",
        "--token-file", str(tmp_path / "absent"),
    ])
    assert code == 2
    assert "service_account" in capsys.readouterr().out


@pytest.mark.emulator
def test_login_list_check_remove_round_trip(host, emulator, emulator_client,
                                            tmp_path, capsys, monkeypatch):
    from mml_cloud_transfer.cli import profile_command

    _, bucket_name = emulator_client
    payload = {
        "type": "authorized_user", "client_id": "c", "client_secret": "s",
        "refresh_token": "rt", "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": [],
    }
    monkeypatch.setattr(profile_command, "run_login", lambda config, **kw: payload)
    monkeypatch.setattr(
        profile_command, "load_client_config", lambda path: {"installed": {}}
    )

    code = main([
        "profile", "login", "--name", "lab", "--bucket", bucket_name,
        "--prefix", "data", "--emulator-endpoint", emulator.endpoint,
        *_profile_args(host),
    ])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "can list" in out                     # the preflight summary
    assert "service account" in out.lower()      # least-privilege tip

    code = main(["profile", "list", *_profile_args(host)])
    out = capsys.readouterr().out
    assert code == 0
    assert "lab" in out and bucket_name in out

    code = main(["profile", "check", "--name", "lab",
                 "--direction", "upload", *_profile_args(host)])
    out = capsys.readouterr().out
    assert code == 0
    assert "can list" in out

    code = main(["profile", "remove", "--name", "lab", *_profile_args(host)])
    assert code == 0
    code = main(["profile", "check", "--name", "lab", *_profile_args(host)])
    out = capsys.readouterr().out
    assert code == 1
    assert "no profile named" in out


@pytest.mark.emulator
def test_add_key_prints_the_you_may_delete_message(host, emulator, emulator_client,
                                                   tmp_path, capsys):
    """Spec product copy: after validation the user is told the service
    holds an encrypted copy and the original may be deleted."""
    _, bucket_name = emulator_client
    key_file = tmp_path / "svc.json"
    key_file.write_text(json.dumps({"type": "service_account", "project_id": "p"}),
                        encoding="utf-8")
    code = main([
        "profile", "add-key", "--name", "keyed", "--bucket", bucket_name,
        "--key-file", str(key_file), "--emulator-endpoint", emulator.endpoint,
        *_profile_args(host),
    ])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "delete the original" in out
    assert str(key_file) in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/cli/test_profile_cli.py -v`
Expected: FAIL — `argparse` errors (`invalid choice: 'profile'`).

- [ ] **Step 3: Implement `profile_command.py`**

`src/mml_cloud_transfer/cli/profile_command.py`:

```python
"""mmlct profile subcommands.

Always a client of the service API: credentials live in the service's
DPAPI store, so there is no direct-engine mode here. The OAuth browser
flow is the one interactive step (spec: it must run in the user's
session); its result is handed to the service, which refreshes tokens
autonomously thereafter.
"""

from __future__ import annotations

import json
from pathlib import Path

from mml_cloud_transfer.auth.oauth_flow import load_client_config, run_login
from mml_cloud_transfer.cli.service_client import ApiClient
from mml_cloud_transfer.cli.transfer_command import _api_client


def _find_profile(client: ApiClient, name: str) -> dict | None:
    for profile in client.list_profiles():
        if profile["name"] == name:
            return profile
    return None


def _print_created(result: dict) -> None:
    print(f"Profile {result['name']!r} created and validated against"
          f" gs://{result['bucket']}.")
    print(result["summary"])


def run_profile(args) -> int:
    if not args.service_url:
        print("profile commands need the service: pass --service-url"
              " (or set MMLCT_SERVICE_URL)")
        return 2

    command = args.profile_command

    if command == "add-key":
        try:
            key = json.loads(Path(args.key_file).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            print(f"cannot read key file: {exc}")
            return 2
        if key.get("type") != "service_account":
            print(f"{args.key_file} is not a service-account key"
                  f" (type={key.get('type')!r}; expected 'service_account')")
            return 2
        client = _api_client(args)
        result = client.create_profile({
            "name": args.name, "bucket": args.bucket,
            "auth_type": "service_account_key", "credential": key,
            "project_id": args.project or key.get("project_id", ""),
            "default_prefix": args.prefix,
            "emulator_endpoint": args.emulator_endpoint,
        })
        _print_created(result)
        print("The service now holds an encrypted copy of this key."
              " You may delete the original file:")
        print(f"  {args.key_file}")
        return 0

    if command == "login":
        config = load_client_config(args.client_config)
        print("Tip: for unattended, recurring transfers a least-privilege"
              " service account key (object access to one bucket) is"
              " recommended; Google sign-in suits interactive use.")
        print("A browser window will open for Google sign-in...")
        credential = run_login(config)
        client = _api_client(args)
        result = client.create_profile({
            "name": args.name, "bucket": args.bucket,
            "auth_type": "oauth_user", "credential": credential,
            "project_id": args.project or "",
            "default_prefix": args.prefix,
            "emulator_endpoint": args.emulator_endpoint,
        })
        _print_created(result)
        return 0

    client = _api_client(args)

    if command == "list":
        profiles = client.list_profiles()
        if not profiles:
            print("No profiles.")
            return 0
        for profile in profiles:
            target = f"gs://{profile['bucket']}/{profile['default_prefix']}".rstrip("/")
            checked = profile["validated_at"] or "never"
            print(f"{profile['name']}: {target} [{profile['auth_type']}]"
                  f" last check: {checked}")
        return 0

    if command in ("check", "remove"):
        profile = _find_profile(client, args.name)
        if profile is None:
            print(f"no profile named {args.name!r}")
            return 1
        if command == "remove":
            client.delete_profile(profile["id"])
            print(f"Profile {args.name!r} removed.")
            return 0
        result = client.check_profile(
            profile["id"], direction=args.direction, prefix=args.prefix
        )
        print(result["summary"])
        return 0 if result["ok"] else 1

    return 2
```

- [ ] **Step 4: Wire the parser and dispatch**

In `cli/__main__.py`, after the `report` parser block, add:

```python
    profile = subparsers.add_parser("profile", help="Manage connection profiles")
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)

    def add_profile_target_options(sub):
        sub.add_argument("--name", required=True, help="Profile name")
        sub.add_argument("--bucket", required=True, help="Bucket this profile targets")
        sub.add_argument("--prefix", default="", help="Default object-name prefix")
        sub.add_argument("--project", default=None, help="GCP project id (optional)")
        sub.add_argument("--emulator-endpoint", default=None, help=argparse.SUPPRESS)

    add_key = profile_sub.add_parser(
        "add-key", help="Create a profile from a service-account key file"
    )
    add_profile_target_options(add_key)
    add_key.add_argument("--key-file", required=True,
                         help="Path to the service-account .json key")
    add_service_options(add_key)

    login = profile_sub.add_parser(
        "login", help="Create a profile by signing in with Google"
    )
    add_profile_target_options(login)
    login.add_argument("--client-config", default=None,
                       help="OAuth desktop client JSON (default: MMLCT_OAUTH_CLIENT)")
    add_service_options(login)

    plist = profile_sub.add_parser("list", help="List profiles")
    add_service_options(plist)

    pcheck = profile_sub.add_parser("check", help="Re-run a profile's preflight")
    pcheck.add_argument("--name", required=True)
    pcheck.add_argument("--direction", choices=["upload", "download"], default=None)
    pcheck.add_argument("--prefix", default=None,
                        help="Check against this prefix (default: the profile's)")
    add_service_options(pcheck)

    premove = profile_sub.add_parser("remove", help="Delete a profile")
    premove.add_argument("--name", required=True)
    add_service_options(premove)
```

and in `main()`, before the final `return 2`:

```python
    if args.command == "profile":
        from mml_cloud_transfer.cli.profile_command import run_profile
        try:
            return _dispatch_via_service(run_profile, args)
        except ValueError as exc:  # e.g. no --client-config for login
            print(str(exc))
            return 2
```

(`_dispatch_via_service` already turns `ServiceError` and connection failures into a message + exit 1 when `--service-url` is set. For the `run_profile` early-return when `service_url` is empty, `_dispatch_via_service` calls straight through — correct.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/cli/test_profile_cli.py -v`
Expected: ALL PASS.

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python -m pytest`
Expected: all non-skipped tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/mml_cloud_transfer/cli/profile_command.py src/mml_cloud_transfer/cli/__main__.py tests/cli/test_profile_cli.py
git commit -m "feat: mmlct profile add-key/login/list/check/remove"
```

---

### Task 13: Phase 4 manual gate document and final verification

The spec's done-when — "a profile created from either credential type works unattended after logoff" — cannot be proven by the automated suite (no OAuth desktop client ID exists yet; no service-account key is obtainable near-term, Phase 3 gate B1). Write the manual gate checklist that proves it when the credentials exist, then verify the whole branch.

**Files:**
- Create: `docs/superpowers/gates/2026-08-06-phase4-manual-gate.md`

**Interfaces:** none (documentation + verification).

- [ ] **Step 1: Write the gate document**

`docs/superpowers/gates/2026-08-06-phase4-manual-gate.md`:

```markdown
# Phase 4 Manual Gate — Auth and Profiles

**Status: OPEN**

**Done-when under test (spec Phase 4):** a profile created from either
credential type works unattended after logoff.

**Environment:** the LIVE service install (auto-start, running as
`.\pmaho`, port 47821, data in `%ProgramData%\MML Cloud Transfer`) — this
gate deliberately runs against the live install; it is the deployment
being certified. Bucket `afsc_mml_ccep`, prefix `scratch/phase4-gate/`
(versioning ON; `storage.buckets.get` denied — preflight metadata is
object-level only, so no WARNs are expected from the product preflight).
All bucket cleanup MUST be version-aware (`--all-versions` / delete by
explicit generation) — a live-only "clean" is not clean.

## A. OAuth profile (blocked only on creating a client ID — do first)

- [ ] A1. Create a desktop OAuth client: Google Cloud Console > APIs &
      Services > Credentials > Create credentials > OAuth client ID >
      Application type "Desktop app". Download the JSON;
      `$env:MMLCT_OAUTH_CLIENT = "<path>"`. (Any project the operator can
      use works; the client ID only identifies the app, not the bucket.)
- [ ] A2. `mmlct profile login --name gate-oauth --bucket afsc_mml_ccep
      --prefix scratch/phase4-gate --service-url http://127.0.0.1:47821`
      — browser opens, sign in as the operator account. Expect: profile
      created; summary says the credential can list, read, write, compose
      and delete.
- [ ] A3. At-rest check (elevated): the newest file under
      `%ProgramData%\MML Cloud Transfer\credentials\` contains NO plaintext
      (`findstr /I "refresh_token" <file>` finds nothing), and
      `icacls <file>` shows no `(I)` entries.
- [ ] A4. `mmlct transfer --profile gate-oauth --name p4-oauth --source
      C:\gate-data-small --service-url http://127.0.0.1:47821` — then
      **sign out immediately**. Sign back in after the expected duration:
      job COMPLETE, audit clean, report written. Corroborate the logoff
      window with `quser` (Phase 3 C2 technique).
- [ ] A5. Restart the service (`mmlct-service restart` or sc) and run a
      second job with the same profile: the stored credential survives a
      service restart (machine-scope DPAPI, not session-bound).
- [ ] A6. Revoke the app's access at myaccount.google.com/permissions,
      then submit a job with the profile: submission is refused with the
      capability summary (or the job pauses with a CREDENTIAL error if
      revocation lands mid-run) — and NOT a stall loop. This is the
      invalid_grant path of Task 1 live.

## B. Service-account key profile (blocked until a key is obtainable)

- [ ] B1. `mmlct profile add-key --name gate-key --bucket <bucket>
      --key-file <key.json> --service-url http://127.0.0.1:47821` —
      expect validation summary + "you may delete the original file".
- [ ] B2. Repeat A3–A5 with `gate-key`.
- [ ] B3. Record here if no key was obtainable and B stayed blocked; the
      OAuth path (A) alone satisfies "either credential type" only if B is
      re-run when a key exists — the spec says both paths converge, and A
      proves the DPAPI/unattended machinery either way.

## C. Automated real-bucket pass

- [ ] C1. `$env:MMLCT_TEST_BUCKET="afsc_mml_ccep";
      $env:MMLCT_TEST_PREFIX="scratch"` then
      `.venv/Scripts/python -m pytest -m "real_bucket and not slow" -v` —
      all pass (now includes the Task 6 preflight probe test).

## Teardown

- [ ] Remove gate profiles: `mmlct profile remove --name gate-oauth ...`
      (and `gate-key`); confirm their blobs are gone from
      `%ProgramData%\MML Cloud Transfer\credentials\`.
- [ ] `gcloud storage ls --all-versions --recursive
      "gs://afsc_mml_ccep/scratch/phase4-gate/**"` (or the ADC
      version-aware listing if gcloud needs reauth) — matches no objects.
- [ ] Local gate data dirs removed.
- [ ] Revoke the gate OAuth grant at myaccount.google.com/permissions if
      the profile is not being kept.
```

- [ ] **Step 2: Verify the whole branch**

Run: `.venv/Scripts/python -m pytest`
Expected: all non-skipped tests pass, 0 failures. Record the final counts (they should exceed the 354 passed / 12 skipped baseline; the skip count grows only by the new `real_bucket` preflight test when env vars are unset).

Run: `.venv/Scripts/python -m pytest -m emulator`
Expected: emulator subset passes (proves the binary was copied into the worktree).

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/gates/2026-08-06-phase4-manual-gate.md
git commit -m "docs: Phase 4 manual gate checklist (unattended-after-logoff for both credential types)"
```

---

## Verification of plan completeness (spec ↔ tasks)

| Spec requirement (Phase 4 + Auth/Path sections) | Task |
| --- | --- |
| Service-account key: validate by real op at setup, store, "you may delete the original" | 6, 7, 12 |
| User OAuth: installed-app flow, loopback redirect, interactive session, refresh token handed to service, service refreshes autonomously | 11, 12, 5 |
| DPAPI machine scope, ACL-restricted `%ProgramData%` directory | 2, 3 |
| Per-profile preflight appropriate to job direction, plain-language message | 6, 8 |
| Path reachability at creation under the service identity; UNC storage; `\\?\` paths | 10 |
| Least-privilege recommendation surfaced | 12 (`login` tip; GUI copy is Phase 5) |
| Done-when: either credential type works unattended after logoff | 13 (manual gate; machinery in 2–8) |
| Carried-over 1: RefreshError cause-chain classification, real types | 1 |
| Carried-over 2: naming race in `get_or_create_profile` | 4 |
| Carried-over 3: duplicate-destination guard | 9 |
| Carried-over 4: bridge config keeps working | 8 pin + Global Constraints |
