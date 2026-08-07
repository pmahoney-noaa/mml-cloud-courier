import pytest

from mml_cloud_courier.gcs.client import GcsContext, make_context


@pytest.mark.emulator
def test_emulator_context_is_anonymous_and_usable(emulator, emulator_client):
    _, bucket_name = emulator_client
    ctx = make_context(bucket_name, emulator_endpoint=emulator.endpoint)

    assert isinstance(ctx, GcsContext)
    assert ctx.endpoint == emulator.endpoint
    assert ctx.bucket == bucket_name
    # The storage client works against the emulator.
    ctx.client.bucket(bucket_name).blob("t.bin").upload_from_string(b"x")
    # The raw session reaches the same API.
    resp = ctx.session.get(f"{ctx.endpoint}/storage/v1/b/{bucket_name}/o")
    assert resp.status_code == 200
    assert resp.json()["items"][0]["name"] == "t.bin"


def test_missing_key_file_raises_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        make_context("bucket", credentials_path=str(tmp_path / "nope.json"))


def test_endpoint_never_has_a_trailing_slash():
    # Pure string behavior — building the context makes no network calls.
    ctx = make_context("b", emulator_endpoint="http://127.0.0.1:1/")
    assert ctx.endpoint == "http://127.0.0.1:1"


def test_make_context_from_service_account_info(sa_key_json):
    from google.oauth2 import service_account

    ctx = make_context("bkt", credentials_info=sa_key_json)
    assert isinstance(ctx.client._credentials, service_account.Credentials)
    assert ctx.client.project == "mmlct-test"
    assert ctx.bucket == "bkt"


def test_make_context_from_authorized_user_info():
    from google.oauth2.credentials import Credentials as UserCredentials

    info = {
        "type": "authorized_user",
        "client_id": "c", "client_secret": "s",
        "refresh_token": "rt",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    ctx = make_context("bkt", credentials_info=info, project="real-project")
    assert isinstance(ctx.client._credentials, UserCredentials)
    assert ctx.client.project == "real-project"


def test_make_context_authorized_user_without_project_uses_placeholder():
    info = {
        "type": "authorized_user",
        "client_id": "c", "client_secret": "s", "refresh_token": "rt",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    ctx = make_context("bkt", credentials_info=info)
    assert ctx.client.project == "mmlct"


def test_make_context_rejects_an_unknown_credential_type():
    with pytest.raises(ValueError, match="unsupported credential type"):
        make_context("bkt", credentials_info={"type": "mystery"})
