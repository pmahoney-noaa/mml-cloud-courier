"""CLI glue over a real running service host (ephemeral port, temp data
dir) with profiles backed by the emulator. The OAuth browser flow is
stubbed at run_login; everything below it is real."""

import json
import socket
from types import SimpleNamespace

import pytest

from mml_cloud_courier.cli import profile_command
from mml_cloud_courier.cli.__main__ import main
from mml_cloud_courier.service.config import load_config


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]  # nothing listens once the socket closes


@pytest.fixture
def oauth_client_json(tmp_path, monkeypatch):
    path = tmp_path / "client_secret.json"
    path.write_text(json.dumps({"installed": {
        "client_id": "x", "client_secret": "y",
        "token_uri": "https://oauth2.googleapis.com/token",
    }}))
    monkeypatch.setenv("MMLCT_OAUTH_CLIENT", str(path))
    return path


@pytest.fixture
def host(tmp_path):
    """Same shape as tests/service/conftest.py::running_host (that fixture
    is directory-scoped and not importable from tests/cli)."""
    from mml_cloud_courier.service.host import ServiceHost

    config = load_config(tmp_path / "data", port=_free_port())
    service_host = ServiceHost(config)
    service_host.start()
    service_host.wait_ready()
    yield config
    service_host.stop()


def _profile_args(config, *extra):
    return [
        "--service-url", config.base_url,
        "--token-file", str(config.token_path),
        *extra,
    ]


def test_profile_commands_require_the_service(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("MMLCT_SERVICE_URL", raising=False)
    code = main(["profile", "list"])
    assert code == 2
    assert "--service-url" in capsys.readouterr().out


def test_add_key_rejects_a_non_key_file(tmp_path, capsys):
    bad = tmp_path / "not-a-key.json"
    bad.write_text(json.dumps({"type": "authorized_user"}), encoding="utf-8")
    code = main([
        "profile", "add-key", "--name", "x", "--bucket", "b",
        "--key-file", str(bad), "--service-url", "http://127.0.0.1:9",
        "--token-file", str(tmp_path / "absent"),
    ])
    assert code == 2
    assert "service_account" in capsys.readouterr().out


@pytest.mark.emulator
def test_login_list_check_remove_round_trip(host, emulator, emulator_client,
                                            tmp_path, capsys, monkeypatch):
    from mml_cloud_courier.cli import profile_command

    _, bucket_name = emulator_client
    payload = {
        "type": "authorized_user", "client_id": "c", "client_secret": "s",
        "refresh_token": "rt", "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": [],
    }
    monkeypatch.setattr(profile_command, "run_login", lambda config, **kw: payload)
    monkeypatch.setattr(
        profile_command, "load_client_config", lambda path: {"installed": {}}
    )

    code = main([
        "profile", "login", "--name", "lab", "--bucket", bucket_name,
        "--prefix", "data", "--emulator-endpoint", emulator.endpoint,
        *_profile_args(host),
    ])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "can list" in out                     # the preflight summary
    assert "service account" in out.lower()      # least-privilege tip

    code = main(["profile", "list", *_profile_args(host)])
    out = capsys.readouterr().out
    assert code == 0
    assert "lab" in out and bucket_name in out

    code = main(["profile", "check", "--name", "lab",
                 "--direction", "upload", *_profile_args(host)])
    out = capsys.readouterr().out
    assert code == 0
    assert "can list" in out

    code = main(["profile", "remove", "--name", "lab", *_profile_args(host)])
    assert code == 0
    code = main(["profile", "check", "--name", "lab", *_profile_args(host)])
    out = capsys.readouterr().out
    assert code == 1
    assert "no profile named" in out


@pytest.mark.emulator
def test_add_key_prints_the_you_may_delete_message(host, emulator, emulator_client,
                                                   tmp_path, capsys):
    """Spec product copy: after validation the user is told the service
    holds an encrypted copy and the original may be deleted."""
    _, bucket_name = emulator_client
    key_file = tmp_path / "svc.json"
    key_file.write_text(json.dumps({"type": "service_account", "project_id": "p"}),
                        encoding="utf-8")
    code = main([
        "profile", "add-key", "--name", "keyed", "--bucket", bucket_name,
        "--key-file", str(key_file), "--emulator-endpoint", emulator.endpoint,
        *_profile_args(host),
    ])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "delete the original" in out
    assert str(key_file) in out


def test_login_checks_the_service_before_any_browser_opens(
    oauth_client_json, monkeypatch, capsys, tmp_path
):
    def browser_must_not_open(*a, **k):
        raise AssertionError("run_login called before the service was verified")
    monkeypatch.setattr(profile_command, "run_login", browser_must_not_open)
    token = tmp_path / "token"; token.write_text("t")

    code = main([
        "profile", "login", "--name", "n", "--bucket", "b",
        "--service-url", f"http://127.0.0.1:{_closed_port()}",
        "--token-file", str(token),
    ])

    assert code == 1
    assert "not reachable" in capsys.readouterr().out


def test_login_refuses_a_duplicate_name_before_the_browser(
    oauth_client_json, monkeypatch, capsys
):
    monkeypatch.setattr(profile_command, "run_login",
                        lambda *a, **k: pytest.fail("browser opened"))
    fake = SimpleNamespace(
        health=lambda: {"status": "ok"},
        list_profiles=lambda: [{"name": "taken", "id": 1}],
    )
    monkeypatch.setattr(profile_command, "_api_client", lambda args: fake)

    code = main(["profile", "login", "--name", "taken", "--bucket", "b",
                 "--service-url", "http://127.0.0.1:1"])

    assert code == 1
    assert "already exists" in capsys.readouterr().out
