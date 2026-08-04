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
