import os
import sys

import pytest

from mml_cloud_transfer.core.errors import ErrorCategory, ScanError
from mml_cloud_transfer.core.models import PlannedFile
from mml_cloud_transfer.core.scanner import iter_source, summarise


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "run47").mkdir()
    (tmp_path / "run47" / "nested").mkdir()
    (tmp_path / "empty_dir").mkdir()
    (tmp_path / "top.txt").write_bytes(b"a" * 10)
    (tmp_path / "run47" / "a.tif").write_bytes(b"b" * 20)
    (tmp_path / "run47" / "nested" / "b.tif").write_bytes(b"c" * 30)
    return tmp_path


def collect(root):
    files, errors, totals = summarise(iter_source(str(root), follow_extended=False))
    return files, errors, totals


def test_finds_every_file_with_forward_slash_relative_paths(tree):
    files, _, _ = collect(tree)
    assert sorted(f.relative_path for f in files) == [
        "run47/a.tif",
        "run47/nested/b.tif",
        "top.txt",
    ]


def test_records_size_and_mtime(tree):
    files, _, _ = collect(tree)
    by_path = {f.relative_path: f for f in files}
    assert by_path["run47/a.tif"].size_bytes == 20
    assert by_path["run47/a.tif"].mtime_ns > 0


def test_totals_are_accurate(tree):
    _, _, totals = collect(tree)
    assert totals.file_count == 3
    assert totals.byte_count == 60
    assert totals.error_count == 0


def test_empty_directories_produce_no_entries(tree):
    files, errors, _ = collect(tree)
    assert not any("empty_dir" in f.relative_path for f in files)
    assert errors == []


def test_yields_planned_files_not_lists(tree):
    first = next(iter_source(str(tree), follow_extended=False))
    assert isinstance(first, (PlannedFile, ScanError))


def test_missing_root_is_reported_as_an_error_not_raised(tmp_path):
    files, errors, totals = collect(tmp_path / "does-not-exist")
    assert files == []
    assert len(errors) == 1
    assert errors[0].category is ErrorCategory.NOT_FOUND
    assert totals.error_count == 1


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unsupported")
def test_symlinks_are_skipped_and_reported(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "file.txt").write_bytes(b"data")
    try:
        os.symlink(real, tmp_path / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    files, errors, _ = collect(tmp_path)
    assert sorted(f.relative_path for f in files) == ["real/file.txt"]
    assert [e.category for e in errors] == [ErrorCategory.UNKNOWN]
    assert "link" in errors[0].path


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_junctions_are_skipped_and_reported(tmp_path):
    import _winapi

    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "file.txt").write_bytes(b"data")
    junction_path = tmp_path / "junction"

    try:
        _winapi.CreateJunction(str(real_dir), str(junction_path))
    except (OSError, NotImplementedError):
        pytest.skip("junction creation not permitted in this environment")

    files, errors, _ = collect(tmp_path)
    assert sorted(f.relative_path for f in files) == ["real/file.txt"]
    assert [e.category for e in errors] == [ErrorCategory.UNKNOWN]
    assert "junction" in errors[0].path


@pytest.mark.skipif(sys.platform != "win32", reason="extended paths are Windows-only")
def test_follow_extended_true_with_extended_path_form(tmp_path):
    # Test that follow_extended=True (the production default) works correctly
    # with extended path prefixes and produces the same relative paths
    (tmp_path / "subdir").mkdir()
    (tmp_path / "file.txt").write_bytes(b"content")
    (tmp_path / "subdir" / "nested.txt").write_bytes(b"more")

    # Scan with follow_extended=True (default production behavior)
    files, errors, totals = summarise(iter_source(str(tmp_path), follow_extended=True))

    # Verify same relative paths come out with forward slashes
    assert sorted(f.relative_path for f in files) == [
        "file.txt",
        "subdir/nested.txt",
    ]
    assert errors == []
    assert totals.file_count == 2
    assert totals.error_count == 0
