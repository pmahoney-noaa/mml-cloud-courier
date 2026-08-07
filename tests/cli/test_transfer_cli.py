import json
import sys

import pytest

import mml_cloud_courier.cli.transfer_command as transfer_command
from mml_cloud_courier.cli.__main__ import main
from mml_cloud_courier.cli.transfer_command import (
    _finish_via_service,
    _watch_until_settled,
    parse_size_policy,
)
from mml_cloud_courier.core.models import Direction, JobStatus
from mml_cloud_courier.core.slicing import SizePolicy
from mml_cloud_courier.engine.joblock import JobAlreadyRunning
from mml_cloud_courier.store.db import connect
from mml_cloud_courier.store.repository import JobRepository

POLICY_ARG = "65536,262144,262144"


def test_resume_reports_job_already_running_as_a_clean_error(tmp_path, monkeypatch, capsys):
    """CRITICAL: `resume` against a job whose lock another process already
    holds must print an actionable message and exit 3 — not a traceback,
    and not the generic exit code 2 used for ValueError."""
    db = tmp_path / "jobs.db"
    conn = connect(db)
    try:
        job_id = JobRepository(conn).create_job(
            name="locked", direction=Direction.UPLOAD,
            source_root=str(tmp_path), dest_prefix="p",
        )
    finally:
        conn.close()

    def fake_run_job(*args, **kwargs):
        raise JobAlreadyRunning(
            f"job {job_id} is already running in another process"
            f" (lock: {db}.job-{job_id}.lock). If that process is gone, retry."
        )

    monkeypatch.setattr(transfer_command, "run_job", fake_run_job)

    code = main([
        "resume", "--db", str(db), "--job-id", str(job_id),
        "--bucket", "irrelevant", "--emulator-endpoint", "http://127.0.0.1:1",
    ])
    out = capsys.readouterr().out
    assert code == 3
    assert f"job {job_id} is already running" in out


@pytest.mark.skipif(
    sys.platform != "win32", reason="msvcrt byte-range locks are Windows-only"
)
def test_resume_against_a_genuinely_held_lock_exits_3_with_a_clear_message(
    tmp_path, capsys
):
    """M3: the test above only proves the CLI is *wired* to JobAlreadyRunning
    (run_job is faked). This drives the real code path — a REAL lock, held
    by this same test process, contending against the real `job_run_lock`
    inside the real `run_job` — end to end through `main()`."""
    from mml_cloud_courier.engine.joblock import job_run_lock

    db = tmp_path / "jobs.db"
    conn = connect(db)
    try:
        job_id = JobRepository(conn).create_job(
            name="locked", direction=Direction.UPLOAD,
            source_root=str(tmp_path), dest_prefix="p",
        )
    finally:
        conn.close()

    with job_run_lock(db, job_id):
        code = main([
            "resume", "--db", str(db), "--job-id", str(job_id),
            "--bucket", "irrelevant", "--emulator-endpoint", "http://127.0.0.1:1",
        ])
    out = capsys.readouterr().out
    assert code == 3
    assert f"job {job_id}" in out
    assert "could not acquire the run lock" in out


class _StubReportEventsClient:
    """Duck-typed client for _finish_via_service — no HTTP involved."""

    def __init__(self, events):
        self._events = events

    def report(self, job_id):
        return {"report_html": "report.html"}

    def events(self, job_id, after_id=0):
        return self._events


def test_finish_via_service_flags_scan_errors_even_on_a_complete_job(capsys):
    """IMPORTANT 3 regression: service mode must not report success for a
    COMPLETE job whose scan silently skipped files — those files never
    entered the manifest, so the report alone can't reveal the gap."""
    client = _StubReportEventsClient([{"kind": "scan_error", "detail": "boom"}])
    code = _finish_via_service(client, 1, JobStatus.COMPLETE)
    assert code == 1
    assert "scan error" in capsys.readouterr().out.lower()


def test_finish_via_service_returns_zero_when_no_scan_errors(capsys):
    client = _StubReportEventsClient([])
    code = _finish_via_service(client, 1, JobStatus.COMPLETE)
    assert code == 0


class _StubWatchClient:
    """`stream` yields a terminal INCOMPLETE tick first (masking a stall the
    service is about to decide on), then a genuine COMPLETE tick. `get_job`
    mirrors that: STALLED on the first re-check, COMPLETE on the second."""

    def __init__(self):
        self._stream_calls = 0
        self._get_job_calls = 0

    def stream(self, job_id):
        self._stream_calls += 1
        if self._stream_calls == 1:
            yield {
                "status": "incomplete",
                "progress": {
                    "files_done": 1, "files_total": 2,
                    "bytes_done": 1, "bytes_total": 2,
                },
                "events": [],
            }
        else:
            yield {
                "status": "complete",
                "progress": {
                    "files_done": 2, "files_total": 2,
                    "bytes_done": 2, "bytes_total": 2,
                },
                "events": [],
            }

    def get_job(self, job_id):
        self._get_job_calls += 1
        return {"status": "stalled" if self._get_job_calls == 1 else "complete"}


def test_watch_until_settled_reconciles_a_stall_masked_as_incomplete(capsys):
    """IMPORTANT 7 regression: an in-flight watcher receives the terminal
    INCOMPLETE tick before the service decides to stall the job. The CLI
    must re-check after the stream closes and keep watching rather than
    reporting a false failure."""
    client = _StubWatchClient()
    status = _watch_until_settled(client, 1)
    assert status is JobStatus.COMPLETE
    assert client._stream_calls == 2
    assert "moved to stalled" in capsys.readouterr().out


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


@pytest.mark.emulator
def test_transfer_returns_nonzero_when_the_scan_had_errors(
    emulator, emulator_client, tmp_path, capsys
):
    """IMPORTANT 4 regression: a job can reach COMPLETE with files silently
    unplanned if the source could not be fully enumerated. The transfer
    command must not report success in that case, even though every file
    that WAS planned transferred fine (here: zero files, trivially COMPLETE).
    """
    _, bucket = emulator_client
    db = tmp_path / "jobs.db"
    code = main([
        "transfer",
        "--db", str(db), "--bucket", bucket, "--name", "broken-scan",
        "--source", str(tmp_path / "does-not-exist"), "--prefix", "x",
        "--emulator-endpoint", emulator.endpoint,
    ])
    out = capsys.readouterr().out
    assert code == 1
    assert "scan error" in out.lower()


def test_resume_of_unknown_job_fails_cleanly(tmp_path, capsys):
    code = main([
        "resume", "--db", str(tmp_path / "jobs.db"), "--job-id", "42",
        "--bucket", "b",
    ])
    assert code == 1
    assert "no job with id 42" in capsys.readouterr().out


def test_workers_zero_is_rejected_before_any_scan_or_network(tmp_path, capsys):
    """MINOR 6 regression: --workers 0 must not crash ThreadPoolExecutor deep
    inside a running job — it must be rejected up front, cleanly, before any
    scan or GCS context is created.
    """
    code = main([
        "transfer",
        "--db", str(tmp_path / "jobs.db"), "--bucket", "b", "--name", "j",
        "--source", str(tmp_path / "does-not-exist"), "--prefix", "",
        "--workers", "0",
    ])
    out = capsys.readouterr().out
    assert code == 2
    assert "--workers must be >= 1" in out
    assert "Traceback" not in out
    # No job database should have been touched — the scan never ran.
    assert not (tmp_path / "jobs.db").exists()


def test_workers_negative_is_rejected_on_resume_before_any_network(tmp_path, capsys):
    """Same guard, resume path: options must be validated before the GCS
    context is built, or a bad --workers value on resume would still crash
    deep inside ThreadPoolExecutor after touching credentials/network.
    """
    from mml_cloud_courier.core.models import Direction
    from mml_cloud_courier.store.db import connect
    from mml_cloud_courier.store.repository import JobRepository

    db = tmp_path / "jobs.db"
    conn = connect(db)
    job_id = JobRepository(conn).create_job(
        name="j", direction=Direction.UPLOAD, source_root=str(tmp_path), dest_prefix=""
    )
    conn.close()

    code = main([
        "resume", "--db", str(db), "--job-id", str(job_id),
        "--bucket", "b", "--workers", "-3",
    ])
    out = capsys.readouterr().out
    assert code == 2
    assert "--workers must be >= 1" in out
    assert "Traceback" not in out


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


def test_service_submission_resolves_mapped_drives(tmp_path, monkeypatch, capsys):
    """The CLI is the interactive side: it must hand the service a UNC
    path, because the service cannot resolve the user's drive letters."""
    from mml_cloud_courier.cli import transfer_command

    submitted = {}

    class StubClient:
        def submit_job(self, payload):
            submitted.update(payload)
            return {"job_id": 1, "scheduled_start_at": None,
                    "profile_id": None, "preflight_summary": None}

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
