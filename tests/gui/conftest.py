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


@pytest.fixture(autouse=True)
def _isolate_wizard_qsettings(tmp_path, monkeypatch):
    """wizard.py persists the last-used connection through the same
    QSettings("MML", "Cloud Courier") org/app as theme.py's setting. Every
    theme.py test that touches its persisted value opts into a tmp-file
    QSettings per-test; wizard.py's constructor *reads* last_connection on
    every construction and _submit_done *writes* it on every successful
    submit, so leaving it opt-in here would mean most wizard tests quietly
    read from and write to the real Windows registry
    (HKCU\\Software\\MML\\Cloud Courier). Applying the same isolation
    autouse, suite-wide for tests/gui, closes that gap; it's a no-op for
    tests that never touch wizard.py's settings."""
    from PySide6.QtCore import QSettings
    from mml_cloud_courier.gui import wizard as wizard_module
    monkeypatch.setattr(
        wizard_module, "_qsettings",
        lambda: QSettings(str(tmp_path / "wizard-qsettings.ini"), QSettings.Format.IniFormat))
