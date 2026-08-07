"""Profile row -> GcsContext dispatch, including the DPAPI-backed types."""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")

from mml_cloud_transfer.auth.context import context_for_profile
from mml_cloud_transfer.auth.credential_store import CredentialStore

PAYLOAD = {
    "type": "authorized_user",
    "client_id": "c", "client_secret": "s", "refresh_token": "rt",
    "token_uri": "https://oauth2.googleapis.com/token",
}


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, bucket, **kwargs):
        self.calls.append((bucket, kwargs))
        return "CTX"


def _profile(**overrides):
    profile = {
        "bucket": "bkt", "auth_type": "adc", "credential_ref": None,
        "project_id": "",
    }
    profile.update(overrides)
    return profile


def test_oauth_user_profiles_load_the_stored_payload(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    ref = store.save(PAYLOAD)
    recorder = Recorder()
    ctx = context_for_profile(
        _profile(auth_type="oauth_user", credential_ref=ref, project_id="proj"),
        store, make_context_fn=recorder,
    )
    assert ctx == "CTX"
    assert recorder.calls == [("bkt", {"credentials_info": PAYLOAD, "project": "proj"})]


def test_service_account_key_profiles_load_the_stored_payload(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    key = {"type": "service_account", "project_id": "p"}
    ref = store.save(key)
    recorder = Recorder()
    context_for_profile(
        _profile(auth_type="service_account_key", credential_ref=ref),
        store, make_context_fn=recorder,
    )
    assert recorder.calls[0][1]["credentials_info"] == key


def test_legacy_auth_types_dispatch_exactly_as_the_worker_did(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    recorder = Recorder()
    context_for_profile(
        _profile(auth_type="emulator", credential_ref="http://127.0.0.1:9"),
        store, make_context_fn=recorder,
    )
    context_for_profile(
        _profile(auth_type="key_file", credential_ref=r"C:\k.json"),
        store, make_context_fn=recorder,
    )
    context_for_profile(_profile(auth_type="adc"), store, make_context_fn=recorder)
    assert recorder.calls == [
        ("bkt", {"emulator_endpoint": "http://127.0.0.1:9"}),
        ("bkt", {"credentials_path": r"C:\k.json"}),
        ("bkt", {}),
    ]


def test_unknown_auth_type_fails_closed():
    calls = []

    def fake_make_context(bucket, **kwargs):
        calls.append((bucket, kwargs))

    profile = {"auth_type": "mystery", "bucket": "b", "name": "p",
               "credential_ref": None, "project_id": ""}
    with pytest.raises(ValueError, match="mystery"):
        context_for_profile(profile, object(), make_context_fn=fake_make_context)
    assert calls == []  # fail closed: no context was built at all


def test_adc_is_still_an_explicit_type():
    calls = []

    def fake_make_context(bucket, **kwargs):
        calls.append((bucket, kwargs))
        return "ctx"

    profile = {"auth_type": "adc", "bucket": "b", "name": "p",
               "credential_ref": None, "project_id": ""}
    assert context_for_profile(profile, object(), make_context_fn=fake_make_context) == "ctx"
    assert calls == [("b", {})]
