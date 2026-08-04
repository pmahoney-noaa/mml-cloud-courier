import pytest

from mml_cloud_transfer.core.models import (
    TERMINAL_SUCCESS_STATES,
    Direction,
    FileState,
    JobStatus,
    PlannedFile,
    SliceState,
    TransferMethod,
)


def test_file_state_values_are_stable():
    # These strings are persisted in SQLite. Changing one is a data migration.
    assert [s.value for s in FileState] == [
        "pending",
        "transferring",
        "transferred",
        "verified",
        "failed",
        "skipped",
        "changed",
        "quarantined",
    ]


def test_job_status_values_are_stable():
    assert [s.value for s in JobStatus] == [
        "pending",
        "scanning",
        "running",
        "paused",
        "stalled",
        "complete",
        "incomplete",
        "cancelled",
    ]


def test_direction_and_method_values_are_stable():
    assert [d.value for d in Direction] == ["upload", "download"]
    assert [m.value for m in TransferMethod] == ["single_shot", "resumable", "sliced"]
    assert [s.value for s in SliceState] == ["pending", "uploading", "uploaded", "failed"]


def test_only_verified_and_skipped_count_as_success():
    assert TERMINAL_SUCCESS_STATES == frozenset({FileState.VERIFIED, FileState.SKIPPED})


def test_planned_file_is_immutable():
    pf = PlannedFile(
        relative_path="run47/stack_0001.tiff",
        source_path=r"\\?\UNC\nas01\imaging\run47\stack_0001.tiff",
        size_bytes=1024,
        mtime_ns=1_700_000_000_000_000_000,
    )
    assert pf.relative_path == "run47/stack_0001.tiff"
    with pytest.raises(AttributeError):
        pf.size_bytes = 2048  # type: ignore[misc]
