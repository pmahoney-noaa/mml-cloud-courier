"""The CLI drives jobs entirely over the local API (Phase 3 gate). The
running_host fixture lives in tests/service/conftest.py, so these tests
import it explicitly."""

import io
import os
from contextlib import redirect_stdout

import pytest
import requests

from mml_cloud_transfer.cli.__main__ import main
from mml_cloud_transfer.cli.service_client import ApiClient, ServiceError
from mml_cloud_transfer.core.models import JobStatus
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

from tests.service.conftest import free_port, running_host  # noqa: F401


def _run(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(argv)
    return code, out.getvalue()


def test_sse_parser_yields_progress_payloads():
    class _Response:
        status_code = 200

        def iter_lines(self, decode_unicode=False):
            yield "event: progress"
            yield 'data: {"status": "running", "n": 1}'
            yield ""
            yield "event: progress"
            yield 'data: {"status": "complete", "n": 2}'
            yield ""

    class _Session:
        headers = {}

        def get(self, url, stream=False, timeout=None):
            return _Response()

    client = ApiClient("http://x", "t", session=_Session())
    events = list(client.stream(1))
    assert [e["status"] for e in events] == ["running", "complete"]


@pytest.mark.emulator
def test_transfer_over_the_service_api(emulator, emulator_client, running_host, tmp_path):
    _, bucket = emulator_client
    host, config, token = running_host
    token_file = config.token_path
    src = tmp_path / "src"
    src.mkdir()
    for n in range(5):
        (src / f"f{n}.bin").write_bytes(os.urandom(4096))

    code, out = _run([
        "transfer", "--db", "unused-in-service-mode",
        "--name", "api-job", "--source", str(src), "--prefix", "night",
        "--bucket", bucket, "--emulator-endpoint", emulator.endpoint,
        "--service-url", config.base_url, "--token-file", str(token_file),
    ])
    assert code == 0, out
    assert "COMPLETE" in out
    assert "Report:" in out

    conn = connect(config.db_path)
    repo = JobRepository(conn)
    job = repo.list_jobs()[-1]
    states = {r["state"] for r in repo.get_files(job["id"])}
    conn.close()
    assert job["status"] == JobStatus.COMPLETE.value
    assert states == {"verified"}

    code, out = _run([
        "status", "--db", "unused",
        "--service-url", config.base_url, "--token-file", str(token_file),
    ])
    assert code == 0
    assert "api-job" in out


def test_service_unreachable_prints_friendly_error(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("tok", encoding="utf-8")
    code, out = _run([
        "status", "--db", "unused",
        "--service-url", f"http://127.0.0.1:{free_port()}",
        "--token-file", str(token_file),
    ])
    assert code == 1
    assert "not reachable" in out


def test_direct_mode_connection_errors_are_not_masked(monkeypatch, tmp_path):
    """A genuine direct-mode GCS ConnectionError (AuthorizedSession is
    itself a requests.Session subclass) must propagate untouched — it is
    not the service's ConnectionError and must not be reported as
    'service not reachable'."""
    monkeypatch.setattr(
        "mml_cloud_transfer.cli.__main__.run_transfer",
        lambda args: (_ for _ in ()).throw(
            requests.exceptions.ConnectionError("gcs down")
        ),
    )
    src = tmp_path / "src"
    src.mkdir()
    with pytest.raises(requests.exceptions.ConnectionError):
        main([
            "transfer", "--db", str(tmp_path / "jobs.db"),
            "--name", "j", "--source", str(src), "--bucket", "b",
        ])


def test_scheduled_at_is_rejected_in_direct_mode(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    code, out = _run([
        "transfer", "--db", str(tmp_path / "jobs.db"),
        "--name", "j", "--source", str(src), "--bucket", "b",
        "--scheduled-at", "2100-01-01T00:00",
    ])
    assert code == 2
    assert "--service-url" in out


@pytest.mark.emulator
def test_scheduled_submit_returns_immediately(emulator, emulator_client, running_host, tmp_path):
    _, bucket = emulator_client
    host, config, token = running_host
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.bin").write_bytes(b"x")

    code, out = _run([
        "transfer", "--db", "unused", "--name", "tonight",
        "--source", str(src), "--bucket", bucket,
        "--emulator-endpoint", emulator.endpoint,
        "--scheduled-at", "2100-01-01T00:00:00+00:00",
        "--service-url", config.base_url, "--token-file", str(config.token_path),
    ])
    assert code == 0
    assert "2100-01-01" in out
    client = ApiClient(config.base_url, token)
    assert client.list_jobs()[-1]["status"] == JobStatus.PENDING.value
