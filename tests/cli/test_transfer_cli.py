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
    from mml_cloud_transfer.core.models import Direction
    from mml_cloud_transfer.store.db import connect
    from mml_cloud_transfer.store.repository import JobRepository

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
