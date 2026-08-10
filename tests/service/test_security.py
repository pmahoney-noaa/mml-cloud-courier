import subprocess
import sys

import pytest

from mml_cloud_courier.service import security
from mml_cloud_courier.service.security import (
    _acl_grants,
    _current_sid,
    ensure_token,
    read_token,
)


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


def test_ensure_token_regenerates_a_half_created_empty_file(tmp_path):
    """A start that dies between touch() and the token write leaves an
    empty file; the next start must regenerate it, not crash-loop on
    read_token's ValueError (observed live in the Phase 3 gate)."""
    path = tmp_path / "api_token"
    path.write_text("", encoding="utf-8")
    token = ensure_token(path)
    assert token
    assert read_token(path) == token


def test_acl_grants_use_the_process_sid_never_account_names(monkeypatch):
    """Gate finding: on a workgroup machine LocalSystem presents
    WORKGROUP\\MACHINE$, which icacls cannot map (error 1332) — so grants
    must be raw SIDs, which need no name mapping at all."""
    monkeypatch.setattr(
        security, "_current_sid", lambda: "S-1-5-21-111-222-333-1001"
    )
    grants = _acl_grants()
    assert grants.count("/grant:r") == 3
    assert "*S-1-5-21-111-222-333-1001:(F)" in grants
    assert not any("\\" in g for g in grants)


def test_acl_grants_skip_the_sid_when_running_as_system(monkeypatch):
    monkeypatch.setattr(security, "_current_sid", lambda: "S-1-5-18")
    grants = _acl_grants()
    assert grants.count("/grant:r") == 2


def test_acl_grants_survive_sid_resolution_failure(monkeypatch):
    monkeypatch.setattr(security, "_current_sid", lambda: None)
    grants = _acl_grants()
    assert grants.count("/grant:r") == 2


@pytest.mark.skipif(sys.platform != "win32", reason="whoami is Windows-only")
def test_current_sid_resolves_a_real_sid():
    sid = _current_sid()
    assert sid is not None
    assert sid.startswith("S-1-")


@pytest.mark.skipif(sys.platform != "win32", reason="ACLs are Windows-only")
def test_token_file_acl_drops_inheritance(tmp_path):
    path = tmp_path / "api_token"
    ensure_token(path)
    out = subprocess.run(
        ["icacls", str(path)], capture_output=True, text=True, check=True
    ).stdout
    assert "(I)" not in out          # no inherited ACEs survive
    assert "SYSTEM" in out or "S-1-5-18" in out


def test_acl_grants_can_be_inheritable(monkeypatch):
    """The credentials directory needs (OI)(CI) grants so files created
    inside inherit the restriction instead of ProgramData's default ACL."""
    monkeypatch.setattr(
        security, "_current_sid", lambda: "S-1-5-21-111-222-333-1001"
    )
    grants = _acl_grants(inheritable=True)
    assert "*S-1-5-18:(OI)(CI)(F)" in grants
    assert "*S-1-5-21-111-222-333-1001:(OI)(CI)(F)" in grants
    assert not any(g.endswith(":(F)") for g in grants if g.startswith("*"))


def test_acl_grants_default_stays_non_inheritable(monkeypatch):
    monkeypatch.setattr(security, "_current_sid", lambda: None)
    grants = _acl_grants()
    assert "*S-1-5-18:(F)" in grants


def test_reader_sids_parses_sids_skipping_comments_blanks_and_junk(tmp_path):
    (tmp_path / "gui-users.sids").write_text(
        "# gui users\n\nS-1-5-21-111-222-333-1001\nnot-a-sid\n"
        "s-1-5-21-9-9-9-500\n",
        encoding="utf-8",
    )
    assert security._reader_sids(tmp_path) == [
        "S-1-5-21-111-222-333-1001",
        "s-1-5-21-9-9-9-500",
    ]


def test_reader_sids_missing_file_means_no_readers(tmp_path):
    assert security._reader_sids(tmp_path) == []


@pytest.mark.skipif(sys.platform != "win32", reason="ACLs are Windows-only")
def test_ensure_token_grants_listed_readers(tmp_path, monkeypatch):
    """The installer writes gui-users.sids (Phase 6); every token
    (re)creation must re-apply the read grants, or a regeneration would
    silently lock the GUI user out again."""
    calls = []
    real_run = subprocess.run

    def spy(cmd, **kwargs):
        calls.append(list(cmd))
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(security.subprocess, "run", spy)
    # S-1-5-32-545 = BUILTIN\Users: a well-known SID that always resolves.
    (tmp_path / "gui-users.sids").write_text("S-1-5-32-545\n", encoding="utf-8")
    ensure_token(tmp_path / "api_token")
    grant_calls = [c for c in calls if "/grant" in c]
    assert any("*S-1-5-32-545:(R)" in c for c in grant_calls)


@pytest.mark.skipif(sys.platform != "win32", reason="ACLs are Windows-only")
def test_token_acl_actually_includes_reader_sids(tmp_path):
    (tmp_path / "gui-users.sids").write_text("S-1-5-32-545\n", encoding="utf-8")
    ensure_token(tmp_path / "api_token")
    out = subprocess.run(
        ["icacls", str(tmp_path / "api_token")],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "BUILTIN\\Users:(R)" in out
