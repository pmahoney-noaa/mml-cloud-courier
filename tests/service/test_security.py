import subprocess
import sys

import pytest

from mml_cloud_transfer.service.security import ensure_token, read_token


def test_ensure_token_is_stable_and_round_trips(tmp_path):
    path = tmp_path / "deep" / "api_token"
    first = ensure_token(path)
    second = ensure_token(path)
    assert first == second == read_token(path)
    assert len(first) >= 32


def test_read_token_rejects_empty_file(tmp_path):
    path = tmp_path / "api_token"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        read_token(path)


@pytest.mark.skipif(sys.platform != "win32", reason="ACLs are Windows-only")
def test_token_file_acl_drops_inheritance(tmp_path):
    path = tmp_path / "api_token"
    ensure_token(path)
    out = subprocess.run(
        ["icacls", str(path)], capture_output=True, text=True, check=True
    ).stdout
    assert "(I)" not in out          # no inherited ACEs survive
    assert "SYSTEM" in out or "S-1-5-18" in out
