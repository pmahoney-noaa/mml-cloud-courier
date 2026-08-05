import requests

from mml_cloud_transfer.core.models import JobStatus


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
    host.stop()
    assert all(not t.is_alive() for t in host.threads)
    assert host.controller.service_stop.is_set()
