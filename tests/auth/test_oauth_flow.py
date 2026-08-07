"""The installed-app flow, minus the browser. The flow object is faked at
the factory seam; the Credentials object is the REAL google.oauth2 type,
so payload extraction is tested against the true shape. The full
browser-to-bucket path is the Phase 4 manual gate."""

import json
from types import SimpleNamespace

import pytest
from google.oauth2.credentials import Credentials

from mml_cloud_courier.auth.oauth_flow import (
    SCOPES,
    authorized_user_payload,
    load_client_config,
    run_login,
)

CLIENT_CONFIG = {
    "installed": {
        "client_id": "abc.apps.googleusercontent.com",
        "client_secret": "notsecret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}


def _real_credentials(refresh_token="1//refresh"):
    return Credentials(
        token="ya29.access",
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id="abc.apps.googleusercontent.com",
        client_secret="notsecret",
        scopes=SCOPES,
    )


class FakeFlow:
    def __init__(self, creds):
        self._creds = creds
        self.kwargs = None

    def run_local_server(self, **kwargs):
        self.kwargs = kwargs
        return self._creds


def test_run_login_returns_a_service_usable_payload():
    flow = FakeFlow(_real_credentials())
    seen = {}

    def factory(config, scopes):
        seen.update(config=config, scopes=scopes)
        return flow

    payload = run_login(CLIENT_CONFIG, open_browser=False, flow_factory=factory)
    assert seen["config"] is CLIENT_CONFIG
    assert seen["scopes"] == SCOPES
    assert flow.kwargs["open_browser"] is False
    # offline access + forced consent are what guarantee a refresh token
    assert flow.kwargs["access_type"] == "offline"
    assert flow.kwargs["prompt"] == "consent"
    assert payload == {
        "type": "authorized_user",
        "client_id": "abc.apps.googleusercontent.com",
        "client_secret": "notsecret",
        "refresh_token": "1//refresh",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": SCOPES,
    }
    json.dumps(payload)  # must be JSON-serializable for the API call


def test_run_login_without_a_refresh_token_is_an_error():
    factory = lambda config, scopes: FakeFlow(_real_credentials(refresh_token=None))
    with pytest.raises(ValueError, match="refresh token"):
        run_login(CLIENT_CONFIG, flow_factory=factory)


def test_authorized_user_payload_from_the_real_type():
    payload = authorized_user_payload(_real_credentials())
    assert payload["type"] == "authorized_user"
    assert payload["refresh_token"] == "1//refresh"


def test_load_client_config_reads_a_file(tmp_path):
    path = tmp_path / "client.json"
    path.write_text(json.dumps(CLIENT_CONFIG), encoding="utf-8")
    assert load_client_config(str(path)) == CLIENT_CONFIG


def test_load_client_config_falls_back_to_the_env_var(tmp_path, monkeypatch):
    path = tmp_path / "client.json"
    path.write_text(json.dumps(CLIENT_CONFIG), encoding="utf-8")
    monkeypatch.setenv("MMLCC_OAUTH_CLIENT", str(path))
    assert load_client_config(None) == CLIENT_CONFIG


def test_load_client_config_without_any_source_explains_how(monkeypatch):
    monkeypatch.delenv("MMLCC_OAUTH_CLIENT", raising=False)
    with pytest.raises(ValueError, match="--client-config"):
        load_client_config(None)


def test_load_client_config_rejects_a_non_installed_app_config(tmp_path):
    path = tmp_path / "web.json"
    path.write_text(json.dumps({"web": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="installed"):
        load_client_config(str(path))


def test_run_login_forwards_the_timeout():
    captured = {}

    class FakeFlow:
        def run_local_server(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                client_id="i", client_secret="s", refresh_token="r",
                token_uri="t", scopes=["x"],
            )

    run_login({"installed": {}}, flow_factory=lambda cfg, scopes: FakeFlow(),
              timeout_seconds=300)
    assert captured["timeout_seconds"] == 300
