"""ApiClient methods against a live in-process host — the GUI's transport."""

from mml_cloud_courier.cli.service_client import ApiClient
from mml_cloud_courier.core.errors import ErrorCategory
from mml_cloud_courier.core.models import Direction, PlannedFile
from mml_cloud_courier.store.db import connect
from mml_cloud_courier.store.repository import JobRepository


def _seed_failed_job(config) -> int:
    conn = connect(config.db_path)
    repo = JobRepository(conn)
    job_id = repo.create_job(name="seed", direction=Direction.UPLOAD,
                             source_root="C:\\d", dest_prefix="")
    repo.add_planned_files(job_id, [PlannedFile("a.bin", "C:\\d\\a.bin", 10, 1)])
    repo.mark_failed(repo.get_files(job_id)[0]["id"],
                     ErrorCategory.FILE_LOCKED, "locked")
    conn.close()
    return job_id


def test_files_and_errors_round_trip(running_host):
    host, config, token = running_host
    job_id = _seed_failed_job(config)
    client = ApiClient(config.base_url, token)

    groups = client.errors(job_id)
    assert groups[0]["category"] == "file_locked"
    rows = client.files(job_id, category="file_locked")
    assert rows[0]["relative_path"] == "a.bin"


def test_retry_and_exclude_error_groups(running_host):
    host, config, token = running_host
    job_id = _seed_failed_job(config)
    client = ApiClient(config.base_url, token)
    assert client.exclude_errors(job_id, "file_locked")["count"] == 1
    assert client.retry_errors(job_id, "file_locked")["count"] == 1
    assert client.files(job_id, state="pending")[0]["relative_path"] == "a.bin"


def test_settings_round_trip(running_host):
    host, config, token = running_host
    client = ApiClient(config.base_url, token)
    assert client.put_settings({"file_workers": 6})["stored"]["file_workers"] == 6
    assert client.get_settings()["restart_required"] is True


def test_submit_job_returns_the_full_response(running_host, tmp_path):
    host, config, token = running_host
    client = ApiClient(config.base_url, token)
    source = tmp_path / "src"; source.mkdir(); (source / "a.txt").write_text("x")
    result = client.submit_job({
        "name": "j", "direction": "upload", "source_root": str(source),
        "dest_prefix": "p", "bucket": "b", "audit_hash": False,
    })
    assert isinstance(result, dict) and result["job_id"] >= 1
    assert "preflight_summary" in result
