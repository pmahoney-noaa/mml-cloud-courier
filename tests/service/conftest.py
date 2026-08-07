"""Service-level fixtures: an in-process host on an ephemeral port."""

import socket

import pytest

from mml_cloud_courier.service.config import load_config
from mml_cloud_courier.service.security import read_token


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def running_host(tmp_path):
    from mml_cloud_courier.service.host import ServiceHost

    config = load_config(tmp_path / "data", port=free_port())
    host = ServiceHost(config)
    host.start()
    host.wait_ready()
    yield host, config, read_token(config.token_path)
    host.stop()
