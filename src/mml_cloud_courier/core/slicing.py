"""Choose a transfer method per file, and cut large files into slices.

Slice size is deliberately ``max(1 GiB, ceil(size / 32))`` so the component
count never exceeds 32 and a single ``compose`` call always finishes the job.
"""

from __future__ import annotations

from dataclasses import dataclass

from mml_cloud_courier.core.models import TransferMethod

SINGLE_SHOT_MAX_BYTES = 8 * 1024**2
RESUMABLE_MAX_BYTES = 1024**3
MIN_SLICE_BYTES = 1024**3
MAX_COMPONENTS = 32


@dataclass(frozen=True, slots=True)
class SliceSpec:
    index: int
    offset: int
    length: int


@dataclass(frozen=True, slots=True)
class SizePolicy:
    """Size thresholds for method selection and slicing.

    Production always uses ``default()``. Tests inject tiny thresholds so a
    2 MB fixture exercises the sliced path without writing gigabytes.
    """

    single_shot_max: int
    resumable_max: int
    min_slice: int
    max_components: int

    @classmethod
    def default(cls) -> "SizePolicy":
        return cls(
            single_shot_max=SINGLE_SHOT_MAX_BYTES,
            resumable_max=RESUMABLE_MAX_BYTES,
            min_slice=MIN_SLICE_BYTES,
            max_components=MAX_COMPONENTS,
        )

    @classmethod
    def parse(cls, text: str) -> "SizePolicy":
        """Parse 'single_shot_max,resumable_max,min_slice' (bytes, integers)."""
        parts = text.split(",")
        if len(parts) != 3:
            raise ValueError(
                "size policy must be 'single_shot_max,resumable_max,min_slice'"
            )
        single, resumable, min_slice = (int(p) for p in parts)
        return cls(
            single_shot_max=single, resumable_max=resumable,
            min_slice=min_slice, max_components=32,
        )


def choose_method(
    size_bytes: int, *, policy: SizePolicy | None = None
) -> TransferMethod:
    if size_bytes < 0:
        raise ValueError("size_bytes must not be negative")
    p = policy or SizePolicy.default()
    if size_bytes <= p.single_shot_max:
        return TransferMethod.SINGLE_SHOT
    if size_bytes <= p.resumable_max:
        return TransferMethod.RESUMABLE
    return TransferMethod.SLICED


def plan_slices(size_bytes: int, *, policy: SizePolicy | None = None) -> list[SliceSpec]:
    """Cut ``size_bytes`` into at most ``policy.max_components`` contiguous slices."""
    if size_bytes < 0:
        raise ValueError("size_bytes must not be negative")
    if size_bytes == 0:
        return [SliceSpec(index=0, offset=0, length=0)]

    p = policy or SizePolicy.default()
    slice_size = max(p.min_slice, -(-size_bytes // p.max_components))

    slices: list[SliceSpec] = []
    offset = 0
    index = 0
    while offset < size_bytes:
        length = min(slice_size, size_bytes - offset)
        slices.append(SliceSpec(index=index, offset=offset, length=length))
        offset += length
        index += 1
    return slices
