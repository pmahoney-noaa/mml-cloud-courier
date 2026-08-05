import json

from mml_cloud_transfer.service.config import (
    DEFAULT_PORT,
    default_data_dir,
    load_config,
)


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("MMLCT_DATA_DIR", str(tmp_path))
    assert default_data_dir() == tmp_path


def test_defaults(tmp_path):
    config = load_config(tmp_path)
    assert config.host == "127.0.0.1"
    assert config.port == DEFAULT_PORT
    assert config.db_path == tmp_path / "jobs.db"
    assert config.reports_dir == tmp_path / "reports"
    assert config.token_path == tmp_path / "api_token"
    assert config.auto_resume_on_startup is True
    assert config.size_policy is None
    assert config.base_url == f"http://127.0.0.1:{DEFAULT_PORT}"


def test_settings_json_overrides(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({
        "port": 5555,
        "auto_resume_on_startup": False,
        "file_workers": 2,
        "size_policy": "1,2,3",
        "poll_interval": 0.1,
    }), encoding="utf-8")
    config = load_config(tmp_path)
    assert config.port == 5555
    assert config.auto_resume_on_startup is False
    assert config.file_workers == 2
    assert config.size_policy.min_slice == 3
    assert config.poll_interval == 0.1


def test_port_argument_beats_settings(tmp_path):
    (tmp_path / "settings.json").write_text('{"port": 5555}', encoding="utf-8")
    assert load_config(tmp_path, port=6666).port == 6666
