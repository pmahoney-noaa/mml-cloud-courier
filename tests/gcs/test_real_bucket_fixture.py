"""Self-checks for the release-gate fixture.

These are cheap (a few bytes) and run first, so the fixture's prefix
construction and cleanup are proven before the 2.6 GiB test trusts them.
"""

import re

import pytest

from mml_cloud_courier.gcs.objects import get_meta

from tests.conftest import _gate_run_prefix

PREFIX_SHAPE = re.compile(
    r"^(?:[^/]+/)*mmlcc-gate/\d{8}T\d{6}Z-[0-9a-f]{8}/$"
)


def test_the_gate_segment_is_never_operator_supplied():
    """No MMLCC_TEST_PREFIX value can produce a prefix without mmlcc-gate/.

    Teardown recursively deletes everything under the run prefix. This is the
    assertion standing between a typo in that variable and someone's data.
    Runs without a bucket, so it guards every machine, not just the gate host.
    """
    for base in ("", "/", "scratch", "scratch/", "/scratch/mmlcc/", "a/b/c"):
        prefix = _gate_run_prefix(base)
        assert "/mmlcc-gate/" in f"/{prefix}", prefix
        assert PREFIX_SHAPE.match(prefix), prefix
        assert not prefix.startswith("/"), prefix


def test_a_prefix_confines_the_run_to_the_scratch_folder():
    assert _gate_run_prefix("scratch/mmlcc").startswith("scratch/mmlcc/mmlcc-gate/")
    assert _gate_run_prefix("").startswith("mmlcc-gate/")


@pytest.mark.real_bucket
def test_run_prefix_is_unique_and_well_formed(real_bucket_ctx):
    ctx, run_prefix = real_bucket_ctx
    assert PREFIX_SHAPE.match(run_prefix), run_prefix
    assert ctx.bucket, "the context must name the bucket under test"
    # A hard-coded constant prefix would satisfy every assertion above.
    # Uniqueness is what stops two concurrent gate sessions from deleting
    # each other's in-flight objects. This line is bucket-free -- it calls
    # _gate_run_prefix directly rather than going through the fixture.
    assert _gate_run_prefix("scratch") != _gate_run_prefix("scratch")


@pytest.mark.real_bucket
def test_a_dirty_prefix_would_be_detected(real_bucket_ctx):
    """Prove the fixture's virgin-prefix check discriminates dirty from clean,
    including a noncurrent version left by a deleted object.

    The check itself lives in real_bucket_ctx's setup (tests/conftest.py),
    not here -- run_prefix is session-scoped, so a test asserting global
    emptiness would only ever be sound for whichever test happened to run
    first. That was the previous version of this test
    (test_the_run_prefix_starts_empty), and it broke the moment another
    real_bucket test collected earlier in the session and wrote under the
    shared prefix before this one ran.

    This version proves the *exact* listing call the production check uses --
    `list_blobs(..., versions=True)`, not the plain live-only list_prefix --
    actually discriminates dirty from clean. A live-only listing would pass
    this test too if it only checked "objects that exist are listed"; the
    decisive assertion is that after the probe's live object is deleted, a
    versions=True listing of its sub-path is still non-empty, because the
    bucket has versioning enabled. That is precisely the state a live-only
    check would misreport as clean and the production check must not.
    Holds regardless of collection order, because it only reasons about
    sub-paths this test itself owns.
    """
    ctx, run_prefix = real_bucket_ctx
    bucket_handle = ctx.client.bucket(ctx.bucket)
    sub_path = f"{run_prefix}collision-check/"
    written = f"{sub_path}probe.bin"
    absent = f"{run_prefix}collision-check-absent/"

    blob = bucket_handle.blob(written)
    blob.upload_from_string(b"probe")
    # A fresh Blob handle, not `blob` itself: `blob.generation` is now
    # populated from the upload response, and Blob.delete() forwards
    # `generation=self.generation` -- deleting through `blob` would therefore
    # be a generation-scoped hard delete that purges this exact version
    # outright (verified: it leaves nothing for a later versions=True
    # listing to find). A fresh handle has no known generation, so its
    # delete only clears the live pointer, which on a versioning-enabled
    # bucket leaves the deleted content behind as a noncurrent version --
    # the same live-pointer delete `gcloud storage rm` performs by default.
    bucket_handle.blob(written).delete()

    # Deliberately not swept further here -- left for the fixture's teardown
    # to remove, same as test_objects_written_under_the_prefix_are_reachable.
    # A live-only listing of sub_path would be empty at this point -- the
    # object was deleted -- so this assertion only holds for a versions=True
    # listing, exactly the call real_bucket_ctx's collision check makes.
    assert list(ctx.client.list_blobs(ctx.bucket, prefix=sub_path, versions=True))
    assert not list(ctx.client.list_blobs(ctx.bucket, prefix=absent, versions=True))


@pytest.mark.real_bucket
def test_objects_written_under_the_prefix_are_reachable(real_bucket_ctx):
    ctx, run_prefix = real_bucket_ctx
    name = f"{run_prefix}reachable.bin"
    ctx.client.bucket(ctx.bucket).blob(name).upload_from_string(b"probe")
    meta = get_meta(ctx, name)
    assert meta is not None
    assert meta.size == 5
    # Deliberately not deleted here -- left for the fixture's teardown to
    # remove. The proof of cleanup is teardown's own post-delete emptiness
    # assertion in real_bucket_ctx (tests/conftest.py), not the next session:
    # every session gets a fresh <stamp>-<uuid8>/ run_prefix, so the next
    # session's collision check (also in real_bucket_ctx) would pass
    # identically even if this teardown left this object behind.
