"""
Compressing the flattened TIFF pixels with Adobe Deflate.

Deliberately **without a predictor**: the ``Predictor`` tag (317) does not
exist in these files, and adding it would grow the IFD by 12 bytes and shift
every offset in the file. Without it the IFD entry count stays the same, so
the only things that move are those sitting AFTER the image data.

A predictor would give roughly 65% instead of 82%, but at the price of
rebuilding the whole container together with the Exif sub-IFD - a bad trade
for something worth 8% of the total saving.

Conditions outside which we refuse: the image must be uncompressed and stored
w jednym stripie.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field

from tiff_analyzer.document import DATATYPE_ITEMSIZE
from tiff_analyzer.domain import IntOrder

#: Adobe Deflate: what Photoshop writes and all three applications read.
ADOBE_DEFLATE = 8

NO_COMPRESSION = 1

COMPRESSION_TAG = 259
STRIP_OFFSETS_TAG = 273
STRIP_BYTE_COUNTS_TAG = 279
PREDICTOR_TAG = 317

#: An IFD entry: code(2) + type(2) + count(4) + value-or-offset(4).
VALUE_FIELD = 8

#: TIFF requires values to start at an even offset. Since we shorten the
#: image data, everything behind it moves by the same delta - so that delta
#: must be even, otherwise every shifted offset lands on an odd address. We
#: pad to 4, which costs at most 3 bytes.
ALIGNMENT = 4


@dataclass(frozen=True)
class ImagePlan:
    """New pixels together with the patches to apply to the IFD."""

    data: bytes
    before: int
    after: int
    #: How many bytes StripByteCounts declares, excluding padding.
    stored: int = 0
    patches: tuple[tuple[int, bytes], ...] = field(default=())

    @property
    def saved(self) -> int:
        return self.before - self.after

    @property
    def worth_it(self) -> bool:
        return self.after < self.before


class ImageDataError(RuntimeError):
    """The image data cannot be compressed safely."""


def _u32(value: int, order: IntOrder) -> bytes:
    return value.to_bytes(4, order)


def can_compress(page) -> str:
    """
    Returns the reason for refusing, or an empty string when it is safe.

    >>> class Fake:
    ...     compression = 1
    ...     dataoffsets = (100,)
    ...     databytecounts = (50,)
    ...     tags = {}
    >>> can_compress(Fake())
    ''
    """
    compression = int(getattr(page.compression, "value", page.compression or 1))

    if compression != NO_COMPRESSION:
        return f"image data is already compressed (method {compression})"

    if len(page.dataoffsets) != 1:
        return (
            f"the image has {len(page.dataoffsets)} strips; this version supports "
            f"just one"
        )

    if PREDICTOR_TAG in page.tags:
        return (
            "the file already carries a Predictor tag, "
            "which this version does not handle"
        )

    return ""


def plan_image_data(
    reader,
    page,
    *,
    order: IntOrder = "little",
    level: int = 6,
) -> ImagePlan | None:
    """
    Compresses the pixels and prepares the patches for the IFD entries.

    Returns `None` when compression gains nothing. Raises `ImageDataError`
    when the file cannot be touched safely.
    """
    reason = can_compress(page)

    if reason:
        raise ImageDataError(reason)

    start = int(page.dataoffsets[0])
    size = int(page.databytecounts[0])

    raw = reader.read_at(start, size)

    if len(raw) != size:
        raise ImageDataError(f"read {len(raw)} B of pixels, expected {size}")

    packed = zlib.compress(raw, level)

    padding = (-len(packed)) % ALIGNMENT
    region = packed + b"\x00" * padding

    if len(region) >= size:
        return None

    if (size - len(region)) % 2:
        raise ImageDataError(
            f"the delta of {size - len(region)} B is odd; offsets behind "
            f"the image would land on odd addresses"
        )

    patches = [
        # compression method: 1 -> 8
        (
            page.tags[COMPRESSION_TAG].offset + VALUE_FIELD,
            _u32(ADOBE_DEFLATE, order),
        ),
        # the new strip length, without padding, since that is no longer data
        (
            page.tags[STRIP_BYTE_COUNTS_TAG].offset + VALUE_FIELD,
            _u32(len(packed), order),
        ),
    ]

    return ImagePlan(
        data=region,
        before=size,
        after=len(region),
        stored=len(packed),
        patches=tuple(patches),
    )


#: Tags that point at nested IFDs.
SUB_IFD_TAGS = (34665, 34853, 40965)


def _sub_ifd_patches(reader, offset, delta, boundary, order: IntOrder):
    """Patches for sub-IFD entry values sitting past the boundary."""
    count = int.from_bytes(reader.read_at(offset, 2), order)

    if not 0 < count < 4096:
        raise ImageDataError(f"sub-IFD @0x{offset:X} declares {count} entries")

    table = reader.read_at(offset + 2, count * 12)
    patches = []

    for index in range(count):
        entry = table[index * 12 : (index + 1) * 12]

        dtype = int.from_bytes(entry[2:4], order)
        items = int.from_bytes(entry[4:8], order)
        size = DATATYPE_ITEMSIZE.get(dtype, 0) * items

        if size <= 4:
            continue

        value = int.from_bytes(entry[8:12], order)

        if value >= boundary:
            at = offset + 2 + index * 12 + VALUE_FIELD
            patches.append((at, _u32(value - delta, order)))

    return patches


def shift_patches(reader, page, *, delta, boundary, order: IntOrder = "little"):
    """
    Patches that shift by `delta` every offset pointing past `boundary`.

    Covers both the sub-IFD pointers and the value offsets inside them;
    without that, Exif would break once the image data is shortened.
    """
    patches: list[tuple[int, bytes]] = []

    for code, tag in page.tags.items():
        value_at = tag.offset + VALUE_FIELD

        if code in SUB_IFD_TAGS:
            pointer = int.from_bytes(reader.read_at(value_at, 4), order)

            if pointer >= boundary:
                patches.append((value_at, _u32(pointer - delta, order)))
                patches.extend(
                    _sub_ifd_patches(reader, pointer, delta, boundary, order)
                )
            continue

        if tag.valuebytecount > 4 and tag.valueoffset >= boundary:
            patches.append((value_at, _u32(tag.valueoffset - delta, order)))

    return patches
