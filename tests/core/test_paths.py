import sys

import pytest

from mml_cloud_courier.core.paths import (
    extended_path,
    is_unc,
    resolve_mapped_drive,
    to_object_name,
    to_relative_path,
)


def fake_resolver(drive: str) -> str | None:
    return {"Z:": r"\\nas01\imaging"}.get(drive.upper())


def test_extended_path_prefixes_a_local_path():
    assert extended_path(r"C:\data\run47") == "\\\\?\\C:\\data\\run47"


def test_extended_path_uses_the_unc_form_for_shares():
    assert extended_path(r"\\nas01\imaging\run47") == "\\\\?\\UNC\\nas01\\imaging\\run47"


def test_extended_path_is_idempotent():
    once = extended_path(r"C:\data")
    assert extended_path(once) == once


def test_extended_path_normalises_forward_slashes():
    assert extended_path("C:/data/run47") == "\\\\?\\C:\\data\\run47"


@pytest.mark.skipif(sys.platform != "win32", reason="drive-letter semantics")
def test_extended_path_makes_relative_paths_absolute(tmp_path, monkeypatch):
    # \\?\ paths are only valid when absolute; a relative input must be
    # resolved against the current directory before the prefix is applied.
    monkeypatch.chdir(tmp_path)
    assert extended_path("src") == "\\\\?\\" + str(tmp_path / "src")


def test_is_unc():
    assert is_unc(r"\\nas01\imaging")
    assert not is_unc(r"C:\data")
    assert not is_unc(r"Z:\data")


def test_resolve_mapped_drive_rewrites_to_unc():
    assert resolve_mapped_drive(r"Z:\run47\a.tif", fake_resolver) == r"\\nas01\imaging\run47\a.tif"


def test_resolve_mapped_drive_leaves_local_drives_alone():
    assert resolve_mapped_drive(r"C:\run47", fake_resolver) == r"C:\run47"


def test_resolve_mapped_drive_leaves_unc_alone():
    assert resolve_mapped_drive(r"\\nas01\imaging\x", fake_resolver) == r"\\nas01\imaging\x"


def test_to_relative_path_uses_forward_slashes():
    assert to_relative_path(r"C:\data", r"C:\data\run47\a.tif") == "run47/a.tif"


def test_to_relative_path_is_case_insensitive_on_the_root():
    assert to_relative_path(r"C:\Data", r"C:\data\a.tif") == "a.tif"


def test_to_relative_path_rejects_a_path_outside_the_root():
    with pytest.raises(ValueError, match="not inside"):
        to_relative_path(r"C:\data", r"C:\other\a.tif")


def test_to_object_name_joins_and_trims_separators():
    assert to_object_name("archive/run47/", "a/b.tif") == "archive/run47/a/b.tif"
    assert to_object_name("", "a/b.tif") == "a/b.tif"
    assert to_object_name("/archive/", "/a.tif") == "archive/a.tif"


def test_display_path_strips_the_extended_prefix():
    from mml_cloud_courier.core.paths import display_path

    assert display_path("\\\\?\\C:\\data\\run47") == "C:\\data\\run47"
    assert display_path("\\\\?\\UNC\\nas01\\imaging") == "\\\\nas01\\imaging"
    assert display_path(r"C:\data\run47") == "C:\\data\\run47"
    assert display_path("archive/run47/a.tif") == "archive/run47/a.tif"


def test_canonical_source_key_equates_the_same_folder_spelled_differently():
    from mml_cloud_courier.core.paths import canonical_source_key

    assert (
        canonical_source_key(r"C:\Data\Run47")
        == canonical_source_key(r"c:/data/run47/")
        == canonical_source_key("\\\\?\\C:\\DATA\\RUN47")
    )
    assert canonical_source_key(r"C:\a") != canonical_source_key(r"C:\b")


def test_canonical_source_key_resolves_mapped_drives():
    from mml_cloud_courier.core.paths import canonical_source_key

    key = canonical_source_key(
        r"Z:\imaging", resolver=lambda drive: r"\\server\share"
    )
    assert key == canonical_source_key(
        r"\\server\share\imaging", resolver=lambda drive: None
    )
