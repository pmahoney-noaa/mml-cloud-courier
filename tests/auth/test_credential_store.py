"""The DPAPI credential store: token-file pattern (create empty -> cut ACL
-> write), grants by process SID, machine-scope encryption at rest."""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI/ACLs are Windows-only")

from mml_cloud_transfer.auth.credential_store import CredentialStore

PAYLOAD = {
    "type": "authorized_user",
    "client_id": "c",
    "client_secret": "s",
    "refresh_token": "1//THE-SECRET",
    "token_uri": "https://oauth2.googleapis.com/token",
}


def test_save_load_round_trip(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    ref = store.save(PAYLOAD)
    assert ref.startswith("cred-") and ref.endswith(".dpapi")
    assert store.load(ref) == PAYLOAD


def test_secret_is_not_on_disk_in_plaintext(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    ref = store.save(PAYLOAD)
    raw = store.path_for(ref).read_bytes()
    assert b"THE-SECRET" not in raw
    assert b"refresh_token" not in raw


def test_delete_is_idempotent(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    ref = store.save(PAYLOAD)
    store.delete(ref)
    store.delete(ref)  # second delete must not raise
    with pytest.raises(FileNotFoundError):
        store.load(ref)


@pytest.mark.parametrize("bad", ["../../etc", "cred-zzz.dpapi/..", "x.dpapi", ""])
def test_refs_that_are_not_ours_are_rejected(tmp_path, bad):
    store = CredentialStore(tmp_path / "credentials")
    with pytest.raises(ValueError):
        store.path_for(bad)


def test_credential_file_acl_drops_inheritance(tmp_path):
    """Same check shape as test_token_file_acl_drops_inheritance: the blob
    file itself carries a cut ACL — no inherited ACEs survive."""
    store = CredentialStore(tmp_path / "credentials")
    ref = store.save(PAYLOAD)
    out = subprocess.run(
        ["icacls", str(store.path_for(ref))],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "(I)" not in out
    assert "SYSTEM" in out or "S-1-5-18" in out


def test_credentials_directory_acl_is_cut_and_inheritable(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    store.save(PAYLOAD)
    out = subprocess.run(
        ["icacls", str(tmp_path / "credentials")],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "(I)" not in out
    assert "(OI)(CI)" in out


def test_sweep_orphans_removes_only_unreferenced_blobs(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    kept = store.save({"type": "service_account", "k": 1})
    orphan = store.save({"type": "service_account", "k": 2})
    (tmp_path / "credentials" / "not-a-blob.txt").write_text("keep me")

    removed = store.sweep_orphans({kept})

    assert removed == [orphan]
    assert store.load(kept)["k"] == 1                       # referenced blob intact
    assert not (tmp_path / "credentials" / orphan).exists()
    assert (tmp_path / "credentials" / "not-a-blob.txt").exists()  # pattern-gated


def test_sweep_orphans_with_no_store_directory_is_a_noop(tmp_path):
    assert CredentialStore(tmp_path / "never-created").sweep_orphans(set()) == []


def test_sweep_orphans_skips_a_held_file_without_raising(tmp_path, monkeypatch):
    """A PermissionError (e.g. AV holding a blob at boot) must not propagate
    through sweep_orphans -> worker.startup_recovery -> ServiceHost.start():
    the held file is skipped and retried at the next startup instead of
    failing service startup over a stray file."""
    store = CredentialStore(tmp_path / "credentials")
    held = store.save({"type": "service_account", "k": 1})
    removable = store.save({"type": "service_account", "k": 2})

    real_unlink = Path.unlink

    def flaky_unlink(self, *args, **kwargs):
        if self.name == held:
            raise PermissionError("file is in use")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    removed = store.sweep_orphans(set())  # neither ref is referenced

    assert removed == [removable]
    assert (tmp_path / "credentials" / held).exists()            # held file remains
    assert not (tmp_path / "credentials" / removable).exists()   # other orphan still removed
