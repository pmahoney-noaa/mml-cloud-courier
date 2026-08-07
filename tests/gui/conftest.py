"""GUI fixtures: an in-process service host with env pointed at it, so
discover_session() and every ApiClient in the GUI resolve the ephemeral
test install — never the live one."""

import socket

import pytest

from mml_cloud_courier.service.config import load_config
from mml_cloud_courier.service.security import read_token


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def gui_host(tmp_path, monkeypatch):
    from mml_cloud_courier.service.host import ServiceHost

    monkeypatch.setenv("MMLCC_DATA_DIR", str(tmp_path / "data"))
    config = load_config(tmp_path / "data", port=free_port())
    monkeypatch.setenv("MMLCC_SERVICE_URL", config.base_url)
    host = ServiceHost(config)
    host.start()
    host.wait_ready()
    yield host, config, read_token(config.token_path)
    host.stop()
