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
from datetime import datetime, timezone
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


#: The one path segment an operator can never supply. Teardown deletes
#: everything under the run prefix, so this segment is what makes that
#: deletion safe -- see _gate_run_prefix and the guard in real_bucket_ctx.
GATE_SEGMENT = "mmlct-gate"


def _gate_run_prefix(base: str) -> str:
    """Build a unique run prefix under `base`, always inside GATE_SEGMENT.

    `base` is the operator's MMLCT_TEST_PREFIX -- a scratch folder inside a
    bucket that may hold real data. It is normalised, never trusted, and can
    never displace the gate segment.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run = f"{GATE_SEGMENT}/{stamp}-{uuid.uuid4().hex[:8]}/"
    base = base.strip().strip("/")
    return f"{base}/{run}" if base else run


@pytest.fixture(scope="session")
def real_bucket_ctx():
    """The release gate's context: a real bucket and a unique run prefix.

    Session-scoped so one prefix covers the whole gate run. Teardown deletes
    every version -- live and noncurrent alike -- of every object under the
    prefix, including <name>.mmlct.tmp/<nnnn> slice temps (which live under
    it by construction, gcs.uploader.slice_temp_name), and fails the session
    if anything survives, so a leak surfaces as a red test rather than a
    surprise bill.

    This matters because the target bucket has object versioning enabled
    (proven empirically against afsc_mml_ccep on 2026-08-05: a deleted object
    still shows up under `gcloud storage ls --all-versions`). Deleting only
    the live object -- or checking emptiness with a live-only listing --
    would leave a noncurrent version behind while reporting "clean". Teardown
    therefore lists and deletes with versions=True and an explicit generation
    per blob; a plain delete on a versioning-enabled bucket only creates
    another noncurrent version rather than removing the one just listed.

    MMLCT_TEST_PREFIX confines the run to a scratch folder, so an in-use
    bucket is a valid target.
    """
    bucket = os.environ.get("MMLCT_TEST_BUCKET")
    if not bucket:
        pytest.skip(
            "set MMLCT_TEST_BUCKET (and ADC credentials) to run the release gate - "
            "see docs/superpowers/gates/2026-08-05-plan2-release-gate.md"
        )

    from mml_cloud_transfer.gcs.client import make_context

    run_prefix = _gate_run_prefix(os.environ.get("MMLCT_TEST_PREFIX", ""))
    ctx = make_context(bucket)
    # Collision check, before any test can write: a fresh <stamp>-<uuid8>/
    # prefix must be virgin. Anything here means we collided with a
    # concurrent or abandoned run, and teardown would delete objects that
    # are not ours. This CANNOT live in a test -- the prefix is session
    # scoped, so only the first test to run would ever see it empty.
    existing = [
        f"{b.name}#{b.generation}"
        for b in ctx.client.list_blobs(bucket, prefix=run_prefix, versions=True)
    ]
    assert not existing, f"run prefix {run_prefix!r} is not virgin: {existing}"
    try:
        yield ctx, run_prefix
    finally:
        # The guard. Everything below deletes recursively, and MMLCT_TEST_PREFIX
        # points into a bucket that may hold real data -- so refuse to delete
        # anything whose path is not demonstrably ours. A typo fails a test
        # instead of destroying data.
        assert f"/{GATE_SEGMENT}/" in f"/{run_prefix}", (
            f"refusing to delete under {run_prefix!r} — it is not a gate prefix"
        )
        bucket_handle = ctx.client.bucket(ctx.bucket)
        delete_errors: list[str] = []
        # versions=True is what makes this correct on a versioning-enabled
        # bucket: without it we would delete only live objects and then
        # "verify" emptiness against a listing that cannot see what survived.
        for blob in list(
            ctx.client.list_blobs(ctx.bucket, prefix=run_prefix, versions=True)
        ):
            # One failed delete (transient 503, 403, an object hold, ...) must
            # not abort the sweep -- that would leak every remaining object
            # and skip the emptiness re-check below. Accumulate instead, and
            # let the final assertion report everything at once.
            try:
                bucket_handle.delete_blob(blob.name, generation=blob.generation)
            except Exception as exc:
                delete_errors.append(f"{blob.name}#{blob.generation}: {exc}")
        survivors = [
            f"{b.name}#{b.generation}"
            for b in ctx.client.list_blobs(ctx.bucket, prefix=run_prefix, versions=True)
        ]
        assert not survivors and not delete_errors, (
            f"release gate leaked objects: {survivors}; delete errors: {delete_errors}"
        )
