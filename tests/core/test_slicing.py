import pytest

from mml_cloud_transfer.core.models import TransferMethod
from mml_cloud_transfer.core.slicing import (
    MAX_COMPONENTS,
    SliceSpec,
    choose_method,
    plan_slices,
)

MIB = 1024**2
GIB = 1024**3


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, TransferMethod.SINGLE_SHOT),
        (1, TransferMethod.SINGLE_SHOT),
        (8 * MIB, TransferMethod.SINGLE_SHOT),
        (8 * MIB + 1, TransferMethod.RESUMABLE),
        (GIB, TransferMethod.RESUMABLE),
        (GIB + 1, TransferMethod.SLICED),
        (500 * GIB, TransferMethod.SLICED),
    ],
)
def test_choose_method_boundaries(size, expected):
    assert choose_method(size) is expected


def test_small_files_are_a_single_slice():
    assert plan_slices(1234) == [SliceSpec(index=0, offset=0, length=1234)]


def test_two_gib_splits_into_two_one_gib_slices():
    slices = plan_slices(2 * GIB)
    assert slices == [
        SliceSpec(index=0, offset=0, length=GIB),
        SliceSpec(index=1, offset=GIB, length=GIB),
    ]


def test_a_very_large_file_never_exceeds_the_component_cap():
    slices = plan_slices(500 * GIB)
    assert len(slices) == MAX_COMPONENTS
    assert sum(s.length for s in slices) == 500 * GIB


@pytest.mark.parametrize("size", [1, 8 * MIB, GIB + 1, 40 * GIB, 500 * GIB, 3000 * GIB])
def test_slices_are_contiguous_and_complete(size):
    slices = plan_slices(size)
    assert len(slices) <= MAX_COMPONENTS
    assert slices[0].offset == 0
    assert sum(s.length for s in slices) == size
    for previous, current in zip(slices, slices[1:]):
        assert current.offset == previous.offset + previous.length
        assert current.index == previous.index + 1
    assert all(s.length > 0 for s in slices)


def test_zero_byte_file_yields_one_empty_slice():
    assert plan_slices(0) == [SliceSpec(index=0, offset=0, length=0)]


def test_negative_size_is_rejected():
    with pytest.raises(ValueError):
        plan_slices(-1)
