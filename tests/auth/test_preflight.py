"""Capability probes. The emulator proves the wiring end-to-end; failure
paths use REAL google.api_core exception types on a stub (fake-gcs-server
is authless, so it can never produce a 403 itself); the real_bucket test
is the truth-teller for versioned-delete semantics."""

import pytest

from mml_cloud_courier.core.models import Direction
from mml_cloud_courier.auth.preflight import PreflightResult, run_preflight
from mml_cloud_courier.gcs.client import make_context


def _result(**overrides) -> PreflightResult:
    fields = dict(bucket="b", prefix="p", can_list=True, can_read=True,
                  can_write=True, can_compose=True, can_delete=True, messages=())
    fields.update(overrides)
    return PreflightResult(**fields)


def test_summary_names_what_works_and_what_does_not():
    result = _result(can_write=False, can_compose=False, can_delete=False)
    assert result.summary() == (
        "This credential can list and read but cannot write, compose"
        " and delete to gs://b/p."
    )


def test_summary_when_everything_works():
    assert "can list, read, write, compose and delete" in _result().summary()


def test_upload_needs_the_full_set_download_needs_list_and_read():
    read_only = _result(can_write=False, can_compose=False, can_delete=False)
    assert read_only.ok_for(Direction.DOWNLOAD) is True
    assert read_only.ok_for(Direction.UPLOAD) is False
    assert _result().ok_for(Direction.UPLOAD) is True


@pytest.mark.emulator
def test_probes_pass_and_clean_up_against_the_emulator(emulator, emulator_client):
    client, bucket_name = emulator_client
    ctx = make_context(bucket_name, emulator_endpoint=emulator.endpoint)
    result = run_preflight(ctx, "data")
    assert result.can_list and result.can_read and result.can_write
    assert result.can_compose and result.can_delete
    assert result.messages == ()
    leftovers = list(client.list_blobs(bucket_name, prefix="data/.mmlct-preflight/"))
    assert leftovers == []


@pytest.mark.emulator
def test_a_real_forbidden_write_reads_as_cannot_write(emulator, emulator_client, monkeypatch):
    """The failure path with the REAL exception type a locked-down bucket
    returns (google.api_core Forbidden, code 403)."""
    from google.api_core.exceptions import Forbidden
    from google.cloud.storage.blob import Blob

    client, bucket_name = emulator_client
    ctx = make_context(bucket_name, emulator_endpoint=emulator.endpoint)

    def deny(self, *args, **kwargs):
        raise Forbidden("probe@x does not have storage.objects.create access")

    monkeypatch.setattr(Blob, "upload_from_string", deny)
    result = run_preflight(ctx, "data")
    assert result.can_write is False
    assert result.can_compose is False       # nothing written to compose
    assert result.can_list and result.can_read
    assert result.ok_for(Direction.UPLOAD) is False
    assert result.ok_for(Direction.DOWNLOAD) is True
    assert any("write" in m for m in result.messages)


@pytest.mark.real_bucket
def test_preflight_against_the_real_bucket_leaves_no_versions(real_bucket_ctx):
    """afsc_mml_ccep: versioning ON, buckets.get denied. All five probes
    must pass using object-level operations only, and cleanup must remove
    every VERSION it wrote (a live-only 'clean' is not clean)."""
    ctx, run_prefix = real_bucket_ctx
    probe_prefix = f"{run_prefix}preflight"
    result = run_preflight(ctx, probe_prefix)
    assert result.can_list and result.can_read and result.can_write
    assert result.can_compose and result.can_delete, result.messages
    survivors = list(
        ctx.client.list_blobs(ctx.bucket, prefix=probe_prefix, versions=True)
    )
    assert survivors == []
