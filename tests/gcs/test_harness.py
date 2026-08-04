import uuid

import pytest


@pytest.mark.emulator
def test_emulator_round_trip(emulator_client):
    client, bucket_name = emulator_client
    bucket = client.bucket(bucket_name)
    name = f"probe-{uuid.uuid4().hex}.bin"
    bucket.blob(name).upload_from_string(b"hello emulator")

    fetched = bucket.get_blob(name)
    assert fetched is not None
    assert fetched.size == 14
    assert fetched.download_as_bytes() == b"hello emulator"


@pytest.mark.emulator
def test_each_test_gets_a_fresh_bucket(emulator_client):
    client, bucket_name = emulator_client
    assert list(client.list_blobs(bucket_name)) == []
