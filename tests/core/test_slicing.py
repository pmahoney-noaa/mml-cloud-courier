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


def test_size_policy_default_matches_module_constants():
    from mml_cloud_transfer.core.slicing import (
        MAX_COMPONENTS,
        MIN_SLICE_BYTES,
        RESUMABLE_MAX_BYTES,
        SINGLE_SHOT_MAX_BYTES,
        SizePolicy,
    )

    policy = SizePolicy.default()
    assert policy.single_shot_max == SINGLE_SHOT_MAX_BYTES
    assert policy.resumable_max == RESUMABLE_MAX_BYTES
    assert policy.min_slice == MIN_SLICE_BYTES
    assert policy.max_components == MAX_COMPONENTS


def test_tiny_policy_reroutes_methods_and_slices():
    from mml_cloud_transfer.core.slicing import SizePolicy, choose_method, plan_slices

    tiny = SizePolicy(
        single_shot_max=64 * 1024,
        resumable_max=256 * 1024,
        min_slice=256 * 1024,
        max_components=32,
    )
    assert choose_method(64 * 1024, policy=tiny) is TransferMethod.SINGLE_SHOT
    assert choose_method(64 * 1024 + 1, policy=tiny) is TransferMethod.RESUMABLE
    assert choose_method(256 * 1024 + 1, policy=tiny) is TransferMethod.SLICED

    slices = plan_slices(1024 * 1024, policy=tiny)
    assert len(slices) == 4
    assert sum(s.length for s in slices) == 1024 * 1024
    assert all(s.length == 256 * 1024 for s in slices)


def test_omitting_policy_behaves_exactly_as_before():
    from mml_cloud_transfer.core.slicing import choose_method, plan_slices

    assert choose_method(8 * MIB) is TransferMethod.SINGLE_SHOT
    assert plan_slices(2 * GIB)[0].length == GIB


def test_size_policy_parse_round_trips():
    from mml_cloud_transfer.core.slicing import SizePolicy

    policy = SizePolicy.parse("65536,262144,262144")
    assert policy.single_shot_max == 65536
    assert policy.resumable_max == 262144
    assert policy.min_slice == 262144
    assert policy.max_components == 32


def test_size_policy_parse_rejects_garbage():
    from mml_cloud_transfer.core.slicing import SizePolicy

    with pytest.raises(ValueError):
        SizePolicy.parse("1,2")
    with pytest.raises(ValueError):
        SizePolicy.parse("a,b,c")
