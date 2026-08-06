"""Profile CRUD over the REST surface. Emulator profiles exercise the full
route (validate -> store -> row) against fake-gcs-server; the DPAPI wiring
test uses a stubbed preflight so no real network is touched."""

import sys

import pytest
from fastapi.testclient import TestClient

from mml_cloud_transfer.auth.preflight import PreflightResult
from mml_cloud_transfer.core.models import Direction
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
