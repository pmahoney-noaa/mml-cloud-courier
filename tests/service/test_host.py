import socket
import threading
import time

import pytest
import requests

from mml_cloud_transfer.service.config import load_config
from mml_cloud_transfer.service.host import ServiceHost


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_host_serves_health_and_guards_jobs(running_host):
    host, config, token = running_host
    health = requests.get(f"{config.base_url}/health", timeout=5)
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert requests.get(f"{config.base_url}/jobs", timeout=5).status_code == 401
    jobs = requests.get(
        f"{config.base_url}/jobs",
        headers={"Authorization": f"Bearer {token}"}, timeout=5,
    )
    assert jobs.status_code == 200
    assert jobs.json() == []


def test_host_stop_joins_its_threads(running_host):
    host, config, token = running_host
    assert host.stop() is True
    assert all(not t.is_alive() for t in host.threads)
    assert host.controller.service_stop.is_set()
    assert host.stop() is True  # safe to call twice


def test_host_stop_reports_false_when_a_thread_outlives_timeout(tmp_path):
    config = load_config(tmp_path / "data", port=_free_port())
    host = ServiceHost(config)
    straggler = threading.Thread(target=time.sleep, args=(3.0,), daemon=True)
    straggler.start()
    host.threads = [straggler]
    assert host.stop(timeout=0.1) is False


def test_second_instance_lock_is_refused_until_the_first_releases(tmp_path):
    """CRITICAL 1: a second service host on the same data dir must never be
    allowed to run startup_recovery against a live first instance — that
    resets the first instance's in-flight transfers out from under it. The
    byte-range lock is crash-safe: killing the first host's process (not
    exercised here, but true on Windows) releases it without any cleanup
    code running, so a retry after a crash always succeeds too."""
    data_dir = tmp_path / "data"
    config1 = load_config(data_dir, port=_free_port())
    host1 = ServiceHost(config1)
    host1._acquire_instance_lock()

    config2 = load_config(data_dir, port=_free_port())
    host2 = ServiceHost(config2)
    with pytest.raises(RuntimeError, match="second instance"):
        host2._acquire_instance_lock()

    assert host1.stop() is True  # releases the lock even though start() never ran

    host2._acquire_instance_lock()  # retry succeeds now that the first is gone
    assert host2.stop() is True
