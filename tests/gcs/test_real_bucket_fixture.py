"""Self-checks for the release-gate fixture.

These are cheap (a few bytes) and run first, so the fixture's prefix
construction and cleanup are proven before the 2.6 GiB test trusts them.
"""

import re

import pytest

from mml_cloud_transfer.gcs.objects import get_meta, list_prefix

from tests.conftest import _gate_run_prefix

PREFIX_SHAPE = re.compile(
    r"^(?:[^/]+/)*mmlct-gate/\d{8}T\d{6}Z-[0-9a-f]{8}/$"
)


def test_the_gate_segment_is_never_operator_supplied():
    """No MMLCT_TEST_PREFIX value can produce a prefix without mmlct-gate/.

    Teardown recursively deletes everything under the run prefix. This is the
    assertion standing between a typo in that variable and someone's data.
    Runs without a bucket, so it guards every machine, not just the gate host.
    """
    for base in ("", "/", "scratch", "scratch/", "/scratch/mmlct/", "a/b/c"):
        prefix = _gate_run_prefix(base)
        assert "/mmlct-gate/" in f"/{prefix}", prefix
        assert PREFIX_SHAPE.match(prefix), prefix
        assert not prefix.startswith("/"), prefix


def test_a_prefix_confines_the_run_to_the_scratch_folder():
    assert _gate_run_prefix("scratch/mmlct").startswith("scratch/mmlct/mmlct-gate/")
    assert _gate_run_prefix("").startswith("mmlct-gate/")


@pytest.mark.real_bucket
def test_run_prefix_is_unique_and_well_formed(real_bucket_ctx):
    ctx, run_prefix = real_bucket_ctx
    assert PREFIX_SHAPE.match(run_prefix), run_prefix
    assert ctx.bucket, "the context must name the bucket under test"


@pytest.mark.real_bucket
def test_the_run_prefix_starts_empty(real_bucket_ctx):
    ctx, run_prefix = real_bucket_ctx
    # Anything here would mean a prefix collision with another run.
    assert [m.name for m in list_prefix(ctx, run_prefix)] == []


@pytest.mark.real_bucket
def test_objects_written_under_the_prefix_are_reachable(real_bucket_ctx):
    ctx, run_prefix = real_bucket_ctx
    name = f"{run_prefix}reachable.bin"
    ctx.client.bucket(ctx.bucket).blob(name).upload_from_string(b"probe")
    meta = get_meta(ctx, name)
    assert meta is not None
    assert meta.size == 5
    # Deliberately not deleted — the fixture's teardown must remove it. If
    # teardown is broken, the next session's emptiness check fails loudly.
