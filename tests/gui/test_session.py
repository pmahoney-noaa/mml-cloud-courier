import pytest

pytest.importorskip("PySide6")  # module convention for every tests/gui file

from mml_cloud_courier.gui.session import discover_session


def test_discover_session_uses_env_overrides(tmp_path, monkeypatch):
    token = tmp_path / "api_token"
    token.write_text("secret-token")
    monkeypatch.setenv("MMLCT_SERVICE_URL", "http://127.0.0.1:5")
    monkeypatch.setenv("MMLCT_TOKEN_FILE", str(token))
    session = discover_session()
    assert session.base_url == "http://127.0.0.1:5"
    assert session.client is not None
    assert session.error is None


def test_discover_session_reports_missing_token_plainly(tmp_path, monkeypatch):
    monkeypatch.setenv("MMLCT_DATA_DIR", str(tmp_path / "empty"))
    monkeypatch.delenv("MMLCT_SERVICE_URL", raising=False)
    monkeypatch.delenv("MMLCT_TOKEN_FILE", raising=False)
    session = discover_session()
    assert session.client is None
    assert "installed" in session.error
