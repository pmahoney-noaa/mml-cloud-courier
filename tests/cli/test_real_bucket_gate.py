"""Release gate: the defining test at real scale.

Unlike tests/cli/test_interrupt_resume.py, this passes NO --size-policy. The
default thresholds mean big.bin is cut into real 1 GiB slices, each with its
own long-lived resumable session -- the conditions an overnight transfer
actually meets, and the ones shrunken test thresholds cannot reproduce.
"""

import csv
import json
import os
import random
import shutil
import subprocess
import sys
import time

import pytest

from mml_cloud_transfer.core.hashing import hash_file
from mml_cloud_transfer.core.models import FileState, JobStatus
from mml_cloud_transfer.gcs.objects import get_meta, list_prefix
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository

MIB = 1024 * 1024
BIG_BYTES = 2560 * MIB      # 2.5 GiB -> 3 slices: 1 GiB, 1 GiB, 0.5 GiB
MID_BYTES = 64 * MIB        # one resumable session
SMALL_COUNT = 8
SMALL_BYTES = 1 * MIB       # single-shot
FILE_COUNT = SMALL_COUNT + 2
NEEDED_FREE_BYTES = 6 * 1024**3
KILL_DEADLINE_SECONDS = 900


def _cli(*args):
    return [sys.executable, "-m", "mml_cloud_transfer.cli", *args]


def _write_blocks(path, block_count: int, seed: int) -> None:
    """Write `block_count` 1 MiB blocks, block N tagged with N.

    Distinct blocks are the point: identical ones would let a compose that
    stitched slices out of order produce a byte-identical object. Cheap to
    generate -- one PRNG block, then a per-block header.
    """
    template = random.Random(seed).randbytes(MIB)
    with open(path, "wb") as fp:
        for n in range(block_count):
            fp.write(n.to_bytes(16, "big") + template[16:])


@pytest.fixture(scope="module")
def big_tree(tmp_path_factory):
    root = tmp_path_factory.mktemp("gate-src")
    free = shutil.disk_usage(root).free
    if free < NEEDED_FREE_BYTES:
        pytest.skip(
            f"needs {NEEDED_FREE_BYTES // 1024**3} GiB free on {root.drive or root}, "
            f"found {free // 1024**3} GiB"
        )
    _write_blocks(root / "big.bin", BIG_BYTES // MIB, seed=11)
    _write_blocks(root / "mid.bin", MID_BYTES // MIB, seed=12)
    for n in range(SMALL_COUNT):
        _write_blocks(root / f"small-{n:02d}.bin", SMALL_BYTES // MIB, seed=100 + n)
    return root


def _slice_in_flight(db_path) -> bool:
    """True once some slice has a live session and a partial commit."""
    if not db_path.exists():
        return False
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM file_slices "
            "WHERE session_uri IS NOT NULL "
            "  AND bytes_transferred > 0 "
            "  AND bytes_transferred < length_bytes"
        ).fetchone()
        return row["n"] > 0
    except Exception:
        # The schema may not exist yet in the first moments of the scan.
        return False
    finally:
        conn.close()


@pytest.mark.real_bucket
@pytest.mark.slow
def test_multi_gigabyte_kill_and_resume(real_bucket_ctx, big_tree, tmp_path):
    ctx, run_prefix = real_bucket_ctx
    prefix = f"{run_prefix}scale"
    db = tmp_path / "gate.db"

    proc = subprocess.Popen(
        _cli(
            "transfer", "--db", str(db), "--bucket", ctx.bucket,
            "--name", "release-gate", "--source", str(big_tree), "--prefix", prefix,
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + KILL_DEADLINE_SECONDS
        while time.monotonic() < deadline:
            if _slice_in_flight(db):
                break
            if proc.poll() is not None:
                pytest.fail("transfer finished before any slice was mid-flight")
            time.sleep(1.0)
        else:
            pytest.fail(
                f"no slice reached a partial commit within "
                f"{KILL_DEADLINE_SECONDS}s — is the uplink saturated?"
            )
    finally:
        proc.kill()
        proc.wait(timeout=30)

    conn = connect(db)
    try:
        repo = JobRepository(conn)
        job = conn.execute("SELECT * FROM jobs ORDER BY id").fetchone()
        job_id = job["id"]
        counts = repo.count_by_state(job_id)
    finally:
        conn.close()
    assert job["status"] != JobStatus.COMPLETE.value
    assert counts.get(FileState.VERIFIED, 0) < FILE_COUNT, (
        "the kill landed after everything finished — it proves nothing"
    )

    resumed = subprocess.run(
        _cli(
            "resume", "--db", str(db), "--job-id", str(job_id),
            "--bucket", ctx.bucket, "--report-dir", str(tmp_path / "report"),
        ),
        capture_output=True, text=True, timeout=3600,
    )
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr

    conn = connect(db)
    try:
        repo = JobRepository(conn)
        assert repo.get_job(job_id)["status"] == JobStatus.COMPLETE.value
        rows = repo.get_files(job_id)
    finally:
        conn.close()
    assert len(rows) == FILE_COUNT
    assert all(
        r["state"] in (FileState.VERIFIED.value, FileState.SKIPPED.value) for r in rows
    ), [dict(r) for r in rows if r["state"] not in
        (FileState.VERIFIED.value, FileState.SKIPPED.value)]

    # The sliced file end-to-end: the composed object matches a fresh hash of
    # the source, which is the whole promise of Layer 2 over real slices.
    meta = get_meta(ctx, f"{prefix}/big.bin")
    assert meta is not None
    assert meta.size == BIG_BYTES
    assert meta.crc32c == hash_file(big_tree / "big.bin").crc32c

    # No orphaned slice temps anywhere under the run.
    orphans = [m.name for m in list_prefix(ctx, prefix) if ".mmlct.tmp/" in m.name]
    assert orphans == [], f"slice temp objects survived compose: {orphans}"

    # The report is the artifact a user is handed in the morning.
    summary = json.loads((tmp_path / "report" / "summary.json").read_text("utf-8"))
    assert summary["verdict"] == "COMPLETE"
    with (tmp_path / "report" / "manifest.csv").open(encoding="utf-8") as fp:
        assert len(list(csv.DictReader(fp))) == FILE_COUNT
