"""Mapping the physical bytes of a file: what is accounted for, what is a gap."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from optiff.document import TiffDocument
from optiff.domain import DataBlock, PhysicalRange
from optiff.units import calculate_entropy


def merge_ranges(ranges: list[PhysicalRange]) -> list[PhysicalRange]:
    """
    Merges overlapping and touching ranges, skipping empty ones.

    >>> merge_ranges([PhysicalRange(0, 10), PhysicalRange(5, 20)])
    [PhysicalRange(start=0, end=20)]
    >>> merge_ranges([PhysicalRange(0, 10), PhysicalRange(10, 20)])
    [PhysicalRange(start=0, end=20)]
    >>> merge_ranges([PhysicalRange(10, 20), PhysicalRange(0, 5)])
    [PhysicalRange(start=0, end=5), PhysicalRange(start=10, end=20)]
    >>> merge_ranges([PhysicalRange(0, 30), PhysicalRange(10, 20)])
    [PhysicalRange(start=0, end=30)]
    >>> merge_ranges([PhysicalRange(5, 5)])
    []
    """
    ordered = sorted(
        (item for item in ranges if not item.is_empty),
        key=lambda item: item.start,
    )

    merged: list[PhysicalRange] = []

    for current in ordered:
        if not merged:
            merged.append(current)
            continue

        previous = merged[-1]

        if current.start <= previous.end:
            merged[-1] = PhysicalRange(
                previous.start,
                max(previous.end, current.end),
            )
        else:
            merged.append(current)

    return merged


def gaps(ranges: list[PhysicalRange], file_size: int) -> list[PhysicalRange]:
    """
    File bytes covered by none of the given ranges.

    Assumes `ranges` is already merged and sorted.

    >>> gaps([PhysicalRange(0, 10)], 20)
    [PhysicalRange(start=10, end=20)]
    >>> gaps([PhysicalRange(10, 20)], 20)
    [PhysicalRange(start=0, end=10)]
    >>> gaps([PhysicalRange(0, 20)], 20)
    []
    >>> gaps([PhysicalRange(0, 5), PhysicalRange(10, 20)], 20)
    [PhysicalRange(start=5, end=10)]
    """
    result: list[PhysicalRange] = []
    cursor = 0

    for item in ranges:
        if item.start > cursor:
            result.append(PhysicalRange(cursor, item.start))

        cursor = max(cursor, item.end)

    if cursor < file_size:
        result.append(PhysicalRange(cursor, file_size))

    return [item for item in result if not item.is_empty]


class PhysicalStorageAnalyzer:
    TAG_NAMES: ClassVar[dict[int, str]] = {
        37724: "Photoshop ImageSourceData",
        700: "XMP",
        34377: "Photoshop Image Resources",
        34675: "ICC Profile",
        33723: "IPTC / Photoshop",
    }

    def __init__(self, document: TiffDocument):
        self.document = document

    def image_ranges(self) -> list[PhysicalRange]:
        """
        Exact strip ranges, with adjacent ones merged.

        Deliberately NOT the hull `min(start)..max(end)`: foreign bytes
        sitting between strips would then count as image data and vanish
        from the report of gaps.
        """
        return merge_ranges(list(self.document.image_data_ranges()))

    def referenced_blocks(self) -> list[DataBlock]:
        """
        The blocks the SIZE TREE deliberately shows.

        TIFF structure bytes are not included here.
        """
        blocks: list[DataBlock] = []

        image = self.image_ranges()

        if image:
            blocks.append(DataBlock(name="IMAGE DATA", tag=None, ranges=tuple(image)))

        for tag_number, name in self.TAG_NAMES.items():
            physical_range = self.document.tag_data_range(tag_number)

            if physical_range:
                blocks.append(
                    DataBlock(
                        name=name,
                        tag=tag_number,
                        ranges=(physical_range,),
                    )
                )

        return blocks

    def accounted_ranges(self) -> list[PhysicalRange]:
        """Everything known to belong to the TIFF file."""
        ranges: list[PhysicalRange] = []

        for block in self.referenced_blocks():
            ranges.extend(block.ranges)

        ranges.extend(self.document.tiff_structure_ranges())

        return merge_ranges(ranges)

    def unaccounted_ranges(self) -> list[PhysicalRange]:
        """Bytes that are neither TIFF structure nor known data."""
        return gaps(self.accounted_ranges(), self.document.file_size)


class PhysicalClassifier:
    """Zgaduje charakter nierozpoznanego obszaru na podstawie entropii."""

    SAMPLE_LIMIT = 1_000_000

    def __init__(self, path: Path):
        self.path = path

    def classify(self, physical_range: PhysicalRange) -> str:
        with self.path.open("rb") as handle:
            handle.seek(physical_range.start)

            data = handle.read(min(physical_range.size, self.SAMPLE_LIMIT))

        return self.classify_bytes(data)

    @staticmethod
    def classify_bytes(data: bytes) -> str:
        """
        >>> PhysicalClassifier.classify_bytes(b"")
        'EMPTY'
        >>> PhysicalClassifier.classify_bytes(bytes(16))
        'ZERO / PADDING'
        >>> PhysicalClassifier.classify_bytes(b"AAAA")
        'LOW ENTROPY / STRUCTURED'
        """
        if not data:
            return "EMPTY"

        if all(byte == 0 for byte in data):
            return "ZERO / PADDING"

        entropy = calculate_entropy(data)

        if entropy >= 7.5:
            return "HIGH ENTROPY / COMPRESSED OR ENCRYPTED"

        if entropy >= 6.0:
            return "LIKELY COMPRESSED / BINARY"

        return "LOW ENTROPY / STRUCTURED"
