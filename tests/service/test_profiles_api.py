"""Profile CRUD over the REST surface. Emulator profiles exercise the full
route (validate -> store -> row) against fake-gcs-server; the DPAPI wiring
test uses a stubbed preflight so no real network is touched."""

import sys

import pytest
from fastapi.testclient import TestClient

from mml_cloud_transfer.auth.preflight import PreflightResult
from mml_cloud_transfer.core.models import Direction
from mml_cloud_transfer.service import app as app_module
from mml_cloud_transfer.service.app import create_app
from mml_cloud_transfer.service.config import load_config
from mml_cloud_transfer.service.controller import JobController
from mml_cloud_transfer.service.security import read_token
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


def _make_client(tmp_path, preflight_fn=None):
    config = load_config(tmp_path / "data")
    kwargs = {"preflight_fn": preflight_fn} if preflight_fn else {}
    app = create_app(config, JobController(), **kwargs)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {read_token(config.token_path)}"})
    return client, config


def _emulator_profile(name="lab", **overrides):
    payload = {
        "name": name, "bucket": None, "auth_type": "service_account_key",
        "credential": {"type": "service_account"}, "default_prefix": "data",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def emulator_api(tmp_path, emulator, emulator_client):
    _, bucket_name = emulator_client
    client, config = _make_client(tmp_path)
    def create(name="lab", **overrides):
        body = _emulator_profile(
            name=name, bucket=bucket_name,
            emulator_endpoint=emulator.endpoint, **overrides,
        )
        return client.post("/profiles", json=body)
    return client, config, create


@pytest.mark.emulator
def test_create_validates_against_the_bucket_and_reports_capabilities(emulator_api):
    client, config, create = emulator_api
    response = create()
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["auth_type"] == "emulator"          # emulator affordance
    assert body["validated_at"] is not None
    assert "credential_ref" not in body             # internals stay internal
    assert body["preflight"]["can_write"] is True
    assert "can list" in body["summary"]


@pytest.mark.emulator
def test_duplicate_name_is_409(emulator_api):
    _, _, create = emulator_api
    assert create().status_code == 201
    response = create()
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


@pytest.mark.emulator
def test_list_and_check_and_delete(emulator_api):
    client, config, create = emulator_api
    profile_id = create().json()["id"]

    listed = client.get("/profiles").json()
    assert [p["id"] for p in listed] == [profile_id]
    assert all("credential_ref" not in p for p in listed)

    checked = client.post(f"/profiles/{profile_id}/check",
                          json={"direction": "upload"})
    assert checked.status_code == 200
    assert checked.json()["ok"] is True

    assert client.delete(f"/profiles/{profile_id}").json() == {"deleted": profile_id}
    assert client.get("/profiles").json() == []


@pytest.mark.emulator
def test_delete_refuses_while_a_job_references_the_profile(emulator_api):
    client, config, create = emulator_api
    profile_id = create().json()["id"]
    conn = connect(config.db_path)
    try:
        JobRepository(conn).create_job(
            name="j", direction=Direction.UPLOAD, source_root=r"C:\x",
            dest_prefix="p", profile_id=profile_id,
        )
    finally:
        conn.close()
    response = client.delete(f"/profiles/{profile_id}")
    assert response.status_code == 409
    assert "job" in response.json()["detail"]


def test_check_unknown_profile_is_404(tmp_path):
    client, _ = _make_client(tmp_path)
    assert client.post("/profiles/99/check", json={}).status_code == 404
    assert client.delete("/profiles/99").status_code == 404


def test_wrong_credential_json_type_is_422(tmp_path):
    client, _ = _make_client(tmp_path)
    response = client.post("/profiles", json={
        "name": "x", "bucket": "b", "auth_type": "oauth_user",
        "credential": {"type": "service_account"},
    })
    assert response.status_code == 422
    assert "authorized_user" in response.json()["detail"]


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_a_real_key_payload_is_dpapi_stored_and_loads_back(tmp_path, sa_key_json):
    """Stubbed preflight (no network); everything else real: context built
    from the key, payload DPAPI-encrypted on disk, row points at the blob."""
    from mml_cloud_transfer.auth.credential_store import CredentialStore

    ok = PreflightResult(bucket="b", prefix="", can_list=True, can_read=True,
                         can_write=True, can_compose=True, can_delete=True,
                         messages=())
    client, config = _make_client(tmp_path, preflight_fn=lambda ctx, prefix: ok)
    response = client.post("/profiles", json={
        "name": "keyed", "bucket": "b", "auth_type": "service_account_key",
        "credential": sa_key_json,
    })
    assert response.status_code == 201, response.text

    conn = connect(config.db_path)
    try:
        row = JobRepository(conn).find_profile_by_name("keyed")
    finally:
        conn.close()
    assert row["auth_type"] == "service_account_key"
    store = CredentialStore(config.credentials_dir)
    assert store.load(row["credential_ref"]) == sa_key_json
    raw = store.path_for(row["credential_ref"]).read_bytes()
    assert b"PRIVATE KEY" not in raw   # encrypted at rest


@pytest.mark.emulator
def test_submit_job_by_profile_name(emulator_api, tmp_path):
    client, config, create = emulator_api
    create(name="lab")  # default_prefix "data"
    src = tmp_path / "src"; src.mkdir()
    response = client.post("/jobs", json={
        "name": "j", "direction": "upload", "source_root": str(src),
        "profile": "lab",
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["profile_id"] is not None
    assert "can list" in body["preflight_summary"]

    conn = connect(config.db_path)
    try:
        job = JobRepository(conn).get_job(body["job_id"])
        assert job["dest_prefix"] == "data"       # inherited default_prefix
        assert job["profile_id"] == body["profile_id"]
    finally:
        conn.close()


@pytest.mark.emulator
def test_submit_with_explicit_prefix_overrides_the_default(emulator_api, tmp_path):
    client, config, create = emulator_api
    create(name="lab")
    src = tmp_path / "src"; src.mkdir()
    response = client.post("/jobs", json={
        "name": "j", "direction": "upload", "source_root": str(src),
        "profile": "lab", "dest_prefix": "elsewhere",
    })
    conn = connect(config.db_path)
    try:
        job = JobRepository(conn).get_job(response.json()["job_id"])
        assert job["dest_prefix"] == "elsewhere"
    finally:
        conn.close()


@pytest.mark.emulator
def test_submit_by_unknown_profile_is_404(emulator_api, tmp_path):
    client, _, _ = emulator_api
    src = tmp_path / "src"; src.mkdir()
    response = client.post("/jobs", json={
        "name": "j", "direction": "upload", "source_root": str(src),
        "profile": "ghost",
    })
    assert response.status_code == 404


def test_submit_needs_exactly_one_of_profile_and_bucket(tmp_path):
    client, _ = _make_client(tmp_path)
    src = tmp_path / "src"; src.mkdir()
    both = client.post("/jobs", json={
        "name": "j", "direction": "upload", "source_root": str(src),
        "profile": "lab", "bucket": "b",
    })
    neither = client.post("/jobs", json={
        "name": "j", "direction": "upload", "source_root": str(src),
    })
    assert both.status_code == 422
    assert neither.status_code == 422


def test_an_upload_that_cannot_write_is_rejected_with_the_summary(tmp_path):
    """Direction-appropriate preflight at submission: a read-only profile
    can download but not upload."""
    read_only = PreflightResult(
        bucket="b", prefix="data", can_list=True, can_read=True,
        can_write=False, can_compose=False, can_delete=False,
        messages=("cannot write to gs://b/data: Access to this file was denied.",),
    )
    client, config = _make_client(tmp_path, preflight_fn=lambda ctx, prefix: read_only)
    # An emulator profile so context building needs no real credentials.
    conn = connect(config.db_path)
    try:
        JobRepository(conn).create_profile(
            name="ro", bucket="b", auth_type="emulator",
            credential_ref="http://127.0.0.1:9",
        )
    finally:
        conn.close()
    src = tmp_path / "src"; src.mkdir()
    up = client.post("/jobs", json={
        "name": "j", "direction": "upload", "source_root": str(src),
        "profile": "ro",
    })
    assert up.status_code == 400
    assert "cannot write" in up.json()["detail"]
    down = client.post("/jobs", json={
        "name": "j", "direction": "download",
        "source_root": str(tmp_path / "dl"), "profile": "ro",
    })
    assert down.status_code == 201, down.text


@pytest.mark.emulator
def test_resubmitting_the_same_destination_is_409(emulator_api, tmp_path):
    client, config, create = emulator_api
    create(name="lab")
    src = tmp_path / "src"; src.mkdir()
    payload = {
        "name": "j", "direction": "upload", "source_root": str(src),
        "profile": "lab",
    }
    first = client.post("/jobs", json=payload)
    assert first.status_code == 201
    second = client.post("/jobs", json=payload)
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert str(first.json()["job_id"]) in detail
    assert "resume" in detail

    # Cancelling the first job releases the destination.
    client.post(f"/jobs/{first.json()['job_id']}/cancel")
    assert client.post("/jobs", json=payload).status_code == 201


@pytest.mark.emulator
def test_resubmitting_with_a_slash_variant_prefix_is_still_409(emulator_api, tmp_path):
    """"data" and "/data/" are the same GCS destination once to_object_name
    strips slashes (final-review finding 1) — a re-issued transfer must not
    walk past the guard by respelling the prefix."""
    client, config, create = emulator_api
    create(name="lab")
    src = tmp_path / "src"; src.mkdir()
    first = client.post("/jobs", json={
        "name": "j", "direction": "upload", "source_root": str(src),
        "profile": "lab",
    })
    assert first.status_code == 201
    second = client.post("/jobs", json={
        "name": "j2", "direction": "upload", "source_root": str(src),
        "profile": "lab", "dest_prefix": "/data/",
    })
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert str(first.json()["job_id"]) in detail


def test_check_with_corrupted_auth_type_is_a_400_naming_the_type(tmp_path):
    client, config = _make_client(tmp_path)
    conn = connect(config.db_path)
    profile_id = JobRepository(conn).create_profile(
        name="bad", bucket="b", auth_type="mystery", credential_ref=None,
    )
    conn.close()
    response = client.post(f"/profiles/{profile_id}/check", json={})
    assert response.status_code == 400
    assert "mystery" in response.json()["detail"]


@pytest.mark.emulator
def test_preview_counts_objects_and_skips_preflight_probes(emulator_api, emulator_client):
    client, config, create = emulator_api
    storage_client, bucket_name = emulator_client
    profile_id = create(name="prev", default_prefix="data").json()["id"]
    bucket = storage_client.bucket(bucket_name)
    bucket.blob("data/a.bin").upload_from_string(b"12345")
    bucket.blob("data/sub/b.bin").upload_from_string(b"1234567")
    bucket.blob("data/.mmlct-preflight/zz/probe.bin").upload_from_string(b"x")
    bucket.blob("elsewhere/c.bin").upload_from_string(b"xx")

    response = client.post(f"/profiles/{profile_id}/preview", json={})
    assert response.status_code == 200
    assert response.json() == {"objects": 2, "bytes": 12, "truncated": False}

    response = client.post(f"/profiles/{profile_id}/preview",
                           json={"prefix": "elsewhere"})
    assert response.json()["objects"] == 1


@pytest.mark.emulator
def test_preview_exactly_at_cap_is_not_truncated(emulator_api, emulator_client, monkeypatch):
    """A bucket with exactly PREVIEW_MAX_OBJECTS non-probe objects must NOT
    be reported truncated — nothing was actually cut off."""
    monkeypatch.setattr(app_module, "PREVIEW_MAX_OBJECTS", 3)
    client, config, create = emulator_api
    storage_client, bucket_name = emulator_client
    profile_id = create(name="atcap", default_prefix="data").json()["id"]
    bucket = storage_client.bucket(bucket_name)
    for i in range(3):
        bucket.blob(f"data/o{i}.bin").upload_from_string(b"x")

    response = client.post(f"/profiles/{profile_id}/preview", json={})
    assert response.status_code == 200
    assert response.json() == {"objects": 3, "bytes": 3, "truncated": False}


@pytest.mark.emulator
def test_preview_one_past_cap_is_truncated_and_excludes_the_extra_object(
    emulator_api, emulator_client, monkeypatch,
):
    """cap+1 objects: only the first `cap` are counted (and their bytes
    summed) — the (cap+1)th trips truncated without being counted."""
    monkeypatch.setattr(app_module, "PREVIEW_MAX_OBJECTS", 3)
    client, config, create = emulator_api
    storage_client, bucket_name = emulator_client
    profile_id = create(name="pastcap", default_prefix="data").json()["id"]
    bucket = storage_client.bucket(bucket_name)
    for i in range(3):
        bucket.blob(f"data/o{i}.bin").upload_from_string(b"x")
    bucket.blob("data/o3-extra.bin").upload_from_string(b"x" * 999)

    response = client.post(f"/profiles/{profile_id}/preview", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["objects"] == 3
    assert body["truncated"] is True
    assert body["bytes"] == 3   # the 999-byte 4th object must not be counted
