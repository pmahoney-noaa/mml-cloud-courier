import pytest

from mml_cloud_courier.core.errors import ErrorCategory, classify
from mml_cloud_courier.core.hashing import crc32c_to_base64, hash_file
from mml_cloud_courier.gcs.client import make_context
from mml_cloud_courier.gcs.objects import (
    GcsHttpError,
    ObjectMeta,
    delete_object,
    get_meta,
    list_prefix,
)


@pytest.fixture
def ctx(emulator, emulator_client):
    _, bucket_name = emulator_client
    return make_context(bucket_name, emulator_endpoint=emulator.endpoint)


@pytest.mark.emulator
def test_get_meta_returns_size_crc_and_generation(ctx, tmp_path):
    payload = b"0123456789" * 100
    ctx.client.bucket(ctx.bucket).blob("a/b.bin").upload_from_string(payload)

    meta = get_meta(ctx, "a/b.bin")
    assert isinstance(meta, ObjectMeta)
    assert meta.name == "a/b.bin"
    assert meta.size == 1000
    assert meta.generation > 0
    # CRC comes back as our integer form and matches a local hash.
    local = tmp_path / "local.bin"
    local.write_bytes(payload)
    assert meta.crc32c == hash_file(local).crc32c


@pytest.mark.emulator
def test_get_meta_returns_none_for_a_missing_object(ctx):
    assert get_meta(ctx, "does/not/exist.bin") is None


@pytest.mark.emulator
def test_list_prefix_yields_only_matching_objects(ctx):
    bucket = ctx.client.bucket(ctx.bucket)
    bucket.blob("run47/a.bin").upload_from_string(b"a")
    bucket.blob("run47/sub/b.bin").upload_from_string(b"bb")
    bucket.blob("other/c.bin").upload_from_string(b"ccc")

    names = {m.name: m.size for m in list_prefix(ctx, "run47/")}
    assert names == {"run47/a.bin": 1, "run47/sub/b.bin": 2}


@pytest.mark.emulator
def test_delete_object_is_idempotent(ctx):
    ctx.client.bucket(ctx.bucket).blob("gone.bin").upload_from_string(b"x")
    delete_object(ctx, "gone.bin")
    assert get_meta(ctx, "gone.bin") is None
    delete_object(ctx, "gone.bin")  # second call must not raise


@pytest.mark.emulator
def test_delete_object_with_explicit_generation_removes_it(ctx):
    ctx.client.bucket(ctx.bucket).blob("gen/scoped.bin").upload_from_string(b"x")
    meta = get_meta(ctx, "gen/scoped.bin")
    assert meta is not None

    delete_object(ctx, "gen/scoped.bin", generation=meta.generation)

    assert get_meta(ctx, "gen/scoped.bin") is None


# The companion proof -- that a WRONG generation must NOT delete the current
# object -- is deliberately not an emulator test. fake-gcs-server does not
# enforce the `generation` query param on DELETE at all: a raw
# `DELETE .../o/<name>?generation=<wrong>` still returns 200 and removes the
# live object regardless of mismatch (confirmed directly against the running
# emulator, both through the client library and a raw HTTP request, and with
# the emulator bucket's versioning enabled or not). That is exactly the kind
# of behaviour this file's sibling, test_real_bucket_protocol.py, exists to
# cover -- see test_delete_object_generation_scoping_on_a_versioned_bucket
# there for the real-GCS proof.


def test_gcs_http_error_classifies_by_status_code():
    for code, category in ((403, ErrorCategory.CREDENTIAL), (429, ErrorCategory.QUOTA),
                           (503, ErrorCategory.NETWORK), (412, ErrorCategory.CONFLICT)):
        assert classify(GcsHttpError(code, "boom")).category is category
