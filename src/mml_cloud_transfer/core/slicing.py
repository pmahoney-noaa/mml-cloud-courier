"""Choose a transfer method per file, and cut large files into slices.

Slice size is deliberately ``max(1 GiB, ceil(size / 32))`` so the component
count never exceeds 32 and a single ``compose`` call always finishes the job.
"""

from __future__ import annotations

from dataclasses import dataclass

from mml_cloud_transfer.core.models import TransferMethod

SINGLE_SHOT_MAX_BYTES = 8 * 1024**2
RESUMABLE_MAX_BYTES = 1024**3
MIN_SLICE_BYTES = 1024**3
MAX_COMPONENTS = 32


@dataclass(frozen=True, slots=True)
class SliceSpec:
    index: int
    offset: int
    length: int


def choose_method(size_bytes: int) -> TransferMethod:
    if size_bytes < 0:
        raise ValueError("size_bytes must not be negative")
    if size_bytes <= SINGLE_SHOT_MAX_BYTES:
        return TransferMethod.SINGLE_SHOT
    if size_bytes <= RESUMABLE_MAX_BYTES:
        return TransferMethod.RESUMABLE
    return TransferMethod.SLICED


def plan_slices(size_bytes: int) -> list[SliceSpec]:
    """Cut ``size_bytes`` into at most ``MAX_COMPONENTS`` contiguous slices."""
    if size_bytes < 0:
        raise ValueError("size_bytes must not be negative")
    if size_bytes == 0:
        return [SliceSpec(index=0, offset=0, length=0)]

    slice_size = max(MIN_SLICE_BYTES, -(-size_bytes // MAX_COMPONENTS))

    slices: list[SliceSpec] = []
    offset = 0
    index = 0
    while offset < size_bytes:
        length = min(slice_size, size_bytes - offset)
        slices.append(SliceSpec(index=index, offset=offset, length=length))
        offset += length
        index += 1
    return slices
