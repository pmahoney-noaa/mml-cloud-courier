"""GET/PUT /settings: stored vs running config, restart_required honesty."""

import json

from fastapi.testclient import TestClient

from mml_cloud_transfer.service.app import create_app
from mml_cloud_transfer.service.config import load_config
from mml_cloud_transfer.service.controller import JobController
from mml_cloud_transfer.service.security import read_token


def _make_client(tmp_path):
    config = load_config(tmp_path / "data")
    app = create_app(config, JobController())
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {read_token(config.token_path)}"})
    return client, config


def test_get_settings_reports_running_defaults(tmp_path):
    client, config = _make_client(tmp_path)
    body = client.get("/settings").json()
    assert body["file_workers"] == 4
    assert body["size_policy"] is None
    assert body["auto_resume_on_startup"] is True
    assert body["restart_required"] is False


def test_put_settings_persists_and_flags_restart(tmp_path):
    client, config = _make_client(tmp_path)
    response = client.put("/settings", json={"file_workers": 8})
    assert response.status_code == 200
    body = response.json()
    assert body["stored"]["file_workers"] == 8
    assert body["file_workers"] == 4            # running value is unchanged
    assert body["restart_required"] is True
    assert json.loads(config.settings_path.read_text())["file_workers"] == 8


def test_put_settings_validates(tmp_path):
    client, _ = _make_client(tmp_path)
    assert client.put("/settings", json={"file_workers": 0}).status_code == 422
    assert client.put("/settings", json={"size_policy": "banana"}).status_code == 422
    assert client.put("/settings", json={"bogus_key": 1}).status_code == 422


def test_put_settings_empty_policy_clears_the_override(tmp_path):
    client, _ = _make_client(tmp_path)
    client.put("/settings", json={"size_policy": "1048576,2097152,1048576"})
    body = client.put("/settings", json={"size_policy": ""}).json()
    assert "size_policy" not in body["stored"]
