import pytest

from mml_cloud_courier.auth.preflight import PROBE_SEGMENT
from mml_cloud_courier.core.models import Direction, JobStatus
from mml_cloud_courier.core.retry import RetrySchedule
from mml_cloud_courier.core.slicing import SizePolicy
from mml_cloud_courier.engine.runner import EngineOptions, run_job, scan_remote
from mml_cloud_courier.gcs.client import make_context
from mml_cloud_courier.cli.scan_command import run_scan
from mml_cloud_courier.store.db import connect
from mml_cloud_courier.store.repository import JobRepository

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


@pytest.mark.emulator
def test_scan_remote_skips_zero_byte_directory_marker_objects(ctx, tmp_path):
    """MINOR 9 regression: zero-byte objects whose name ends in '/' (the
    "directory" placeholders written by gsutil/Console when you create an
    empty folder) are not real files and must not be planned for download.
    """
    bucket = ctx.client.bucket(ctx.bucket)
    bucket.blob("rt/sub/").upload_from_string(b"")
    bucket.blob("rt/sub/real.bin").upload_from_string(b"hello")

    db = tmp_path / "jobs.db"
    conn = connect(db)
    repo = JobRepository(conn)
    job_id = repo.create_job(
        name="down", direction=Direction.DOWNLOAD,
        source_root=str(tmp_path / "down"), dest_prefix="rt",
    )
    conn.close()

    count = scan_remote(ctx, db, job_id)
    assert count == 1

    conn = connect(db)
    rows = JobRepository(conn).get_files(job_id)
    conn.close()
    assert [r["relative_path"] for r in rows] == ["sub/real.bin"]


@pytest.mark.emulator
def test_scan_remote_skips_transient_preflight_probe_objects(ctx, tmp_path):
    """Final-review finding 2: a preflight running concurrently with a scan
    (profile check or another submission) writes throwaway probe objects
    under <prefix>/.mmlct-preflight/<hex8>/... and deletes them seconds
    later. If scan_remote planned one, the deletion would leave a row that
    fails NOT_FOUND on every attempt and resume, permanently stuck
    INCOMPLETE. The probe segment must never enter the manifest."""
    bucket = ctx.client.bucket(ctx.bucket)
    bucket.blob("rt/real.bin").upload_from_string(b"hello")
    bucket.blob(f"rt/{PROBE_SEGMENT}/abcd1234/probe.bin").upload_from_string(b"probe")

    db = tmp_path / "jobs.db"
    conn = connect(db)
    repo = JobRepository(conn)
    job_id = repo.create_job(
        name="down", direction=Direction.DOWNLOAD,
        source_root=str(tmp_path / "down"), dest_prefix="rt",
    )
    conn.close()

    count = scan_remote(ctx, db, job_id)
    assert count == 1

    conn = connect(db)
    rows = JobRepository(conn).get_files(job_id)
    conn.close()
    assert [r["relative_path"] for r in rows] == ["real.bin"]
