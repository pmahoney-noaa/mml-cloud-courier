"""First-class profile methods, including the Plan 3 deferred race fix:
name allocation must be arbitrated by the UNIQUE index, not COUNT(*)."""

import threading

import pytest

from mml_cloud_transfer.core.models import Direction
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository, ProfileInUse


@pytest.fixture
def repo(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    yield JobRepository(conn)
    conn.close()


def test_get_or_create_is_idempotent_per_triple(repo):
    a = repo.get_or_create_profile(bucket="b", auth_type="adc")
    b = repo.get_or_create_profile(bucket="b", auth_type="adc")
    assert a == b


def test_name_collision_with_a_different_credential_gets_a_suffix(repo):
    a = repo.get_or_create_profile(bucket="b", auth_type="key_file", credential_ref="k1.json")
    b = repo.get_or_create_profile(bucket="b", auth_type="key_file", credential_ref="k2.json")
    assert a != b
    names = {repo.get_profile(a)["name"], repo.get_profile(b)["name"]}
    assert names == {"b [key_file]", "b [key_file] (2)"}


def test_concurrent_get_or_create_converges_on_one_row(tmp_path):
    """The Plan 3 race: two connections, same triple, interleaved. The
    COUNT-based name made one crash; now the UNIQUE index arbitrates and
    both get the same row."""
    db = tmp_path / "jobs.db"
    connect(db).close()  # create schema before threads race on it
    results: list[int] = []
    errors: list[Exception] = []

    def worker():
        conn = connect(db)
        try:
            for _ in range(20):
                results.append(
                    JobRepository(conn).get_or_create_profile(bucket="b", auth_type="adc")
                )
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(set(results)) == 1
    check = connect(db)
    try:
        assert check.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 1
    finally:
        check.close()


def test_find_by_name_and_list(repo):
    pid = repo.create_profile(name="lab", bucket="b", auth_type="oauth_user",
                              credential_ref="cred-000000000000.dpapi")
    assert repo.find_profile_by_name("lab")["id"] == pid
    assert repo.find_profile_by_name("nope") is None
    assert [r["name"] for r in repo.list_profiles()] == ["lab"]


def test_set_profile_validated_stamps_a_timestamp(repo):
    pid = repo.create_profile(name="lab", bucket="b", auth_type="adc")
    assert repo.get_profile(pid)["validated_at"] is None
    repo.set_profile_validated(pid)
    assert repo.get_profile(pid)["validated_at"] is not None
    with pytest.raises(LookupError):
        repo.set_profile_validated(999)


def test_delete_profile_refuses_while_jobs_reference_it(repo):
    pid = repo.create_profile(name="lab", bucket="b", auth_type="adc")
    repo.create_job(name="j", direction=Direction.UPLOAD, source_root=r"C:\x",
                    dest_prefix="p", profile_id=pid)
    with pytest.raises(ProfileInUse):
        repo.delete_profile(pid)
    assert repo.get_profile(pid) is not None  # still there


def test_delete_profile_removes_an_unreferenced_row(repo):
    pid = repo.create_profile(name="lab", bucket="b", auth_type="adc")
    repo.delete_profile(pid)
    with pytest.raises(LookupError):
        repo.get_profile(pid)
    with pytest.raises(LookupError):
        repo.delete_profile(pid)
