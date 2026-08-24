"""Adapter over tifffile: the only place that knows the library exists."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import tifffile

from tiff_analyzer.domain import ImageInfo, PhysicalRange
from tiff_analyzer.readers import FileWindowReader

#: Fallback in case tifffile ever stops exposing `TiffTag.valuebytecount`.
#: Keys are the type numbers from TIFF 6.0 / BigTIFF.
DATATYPE_ITEMSIZE = {
    1: 1,  # BYTE
    2: 1,  # ASCII
    3: 2,  # SHORT
    4: 4,  # LONG
    5: 8,  # RATIONAL
    6: 1,  # SBYTE
    7: 1,  # UNDEFINED
    8: 2,  # SSHORT
    9: 4,  # SLONG
    10: 8,  # SRATIONAL
    11: 4,  # FLOAT
    12: 8,  # DOUBLE
    13: 4,  # IFD
    16: 8,  # LONG8
    17: 8,  # SLONG8
    18: 8,  # IFD8
}


class TiffDocument:
    PHOTOSHOP_IMAGE_SOURCE_DATA = 37724
    PHOTOSHOP_IMAGE_RESOURCES = 34377
    XMP = 700
    IPTC = 33723
    ICC = 34675

    #: Tags that point at nested IFDs (Exif, GPS, Interoperability).
    SUB_IFD_TAGS: ClassVar[tuple[int, ...]] = (34665, 34853, 40965)

    def __init__(self, path: Path):
        self.path = path
        self.tiff = tifffile.TiffFile(path)

    def close(self) -> None:
        self.tiff.close()

    def __enter__(self) -> TiffDocument:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def file_size(self) -> int:
        return self.path.stat().st_size

    @property
    def first_page(self) -> tifffile.TiffPage:
        """
        The first page of the file.

        `TiffFile.pages` yields either a `TiffPage` or a `TiffFrame`; a frame
        is a stripped-down page that carries no tags and only ever appears
        from the second image onwards. `pages.first` is always a full page,
        so this is where the distinction stops mattering.
        """
        return self.tiff.pages.first

    @property
    def image_info(self) -> ImageInfo:
        page = self.first_page

        bits = page.bitspersample
        if isinstance(bits, int):
            bits = (bits,)

        compression = page.compression

        if compression is None:
            compression_value = 1
            compression_name = "NONE"
        else:
            compression_value = (
                compression.value if hasattr(compression, "value") else int(compression)
            )
            compression_name = (
                compression.name if hasattr(compression, "name") else str(compression)
            )

        predictor = page.predictor

        if predictor is not None:
            predictor = (
                predictor.value if hasattr(predictor, "value") else int(predictor)
            )

        return ImageInfo(
            width=page.imagewidth,
            height=page.imagelength,
            samples=page.samplesperpixel,
            bits_per_sample=tuple(bits),
            compression=compression_value,
            compression_name=compression_name,
            predictor=predictor,
        )

    def tag(self, number: int):
        return self.first_page.tags.get(number)

    def raw_tag_data(self, number: int) -> bytes | None:
        """
        The raw bytes of a tag value.

        Materialises the whole value in memory, which for tag 37724 can mean
        gigabytes. To walk the structure, use `tag_reader()` instead.
        """
        tag = self.tag(number)

        if tag is None:
            return None

        value = tag.value

        if isinstance(value, bytes):
            return value

        if isinstance(value, bytearray):
            return bytes(value)

        if hasattr(value, "tobytes"):
            return value.tobytes()

        try:
            return bytes(value)
        except (TypeError, ValueError):
            return None

    def tag_reader(self, number: int) -> FileWindowReader | None:
        """A reader over the tag value, reading from the file through `seek`."""
        physical_range = self.tag_data_range(number)

        if physical_range is None:
            return None

        return FileWindowReader(
            self.path,
            physical_range.start,
            physical_range.size,
        )

    def photoshop_source_reader(self) -> FileWindowReader | None:
        return self.tag_reader(self.PHOTOSHOP_IMAGE_SOURCE_DATA)

    def tag_value_size(self, tag) -> int:
        """
        The size of a tag value in bytes.

        `tag.dtype` is a `DATATYPE` enum and has NO `itemsize` attribute;
        tifffile exposes a ready-made `valuebytecount`. An earlier version
        computed `dtype.itemsize * count`, swallowed the `AttributeError`
        and silently returned 0 for every tag.
        """
        try:
            return int(tag.valuebytecount)
        except (AttributeError, TypeError, ValueError):
            pass

        try:
            return int(DATATYPE_ITEMSIZE[int(tag.dtype)]) * int(tag.count)
        except (AttributeError, KeyError, TypeError, ValueError):
            return 0

    def image_data_ranges(self) -> tuple[PhysicalRange, ...]:
        page = self.first_page

        return tuple(
            PhysicalRange(offset, offset + count)
            for offset, count in zip(
                page.dataoffsets,
                page.databytecounts,
                strict=True,
            )
            if count > 0
        )

    def image_data_range(self) -> PhysicalRange | None:
        ranges = self.image_data_ranges()

        if not ranges:
            return None

        return PhysicalRange(
            min(item.start for item in ranges),
            max(item.end for item in ranges),
        )

    def tag_data_range(self, number: int) -> PhysicalRange | None:
        """
        The physical space a tag value occupies.

        The range is exactly `valueoffset .. valueoffset + valuebytecount`.
        An earlier version appended up to 8 trailing zero bytes as "alignment
        padding" - real TIFF padding is at most 1 byte, and the heuristic
        swallowed the start of the next block whenever it began with zeros.
        """
        tag = self.tag(number)

        if tag is None:
            return None

        offset = getattr(tag, "valueoffset", None)

        if offset is None:
            return None

        size = self.tag_value_size(tag)

        if size <= 0:
            return None

        return PhysicalRange(int(offset), int(offset) + size)

    def tiff_structure_ranges(self) -> tuple[PhysicalRange, ...]:
        """
        The physical structure of the TIFF container.

        Covers the TIFF header, the IFD itself and tag values stored outside
        the IFD. Excludes image data and the metadata blocks shown separately.
        """
        page = self.first_page

        if self.tiff.is_bigtiff:
            header_size = 16
            entry_size = 20
            count_size = 8
            next_ifd_size = 8
            inline_limit = 8
        else:
            header_size = 8
            entry_size = 12
            count_size = 2
            next_ifd_size = 4
            inline_limit = 4

        ranges = [PhysicalRange(0, header_size)]

        ifd_offset = int(page.offset)

        ifd_size = count_size + len(page.tags) * entry_size + next_ifd_size

        ranges.append(PhysicalRange(ifd_offset, ifd_offset + ifd_size))

        for tag in page.tags.values():
            size = self.tag_value_size(tag)

            if size <= inline_limit:
                continue

            offset = getattr(tag, "valueoffset", None)

            if offset is None:
                continue

            ranges.append(PhysicalRange(int(offset), int(offset) + size))

        ranges.extend(self._sub_ifd_ranges(count_size, entry_size, next_ifd_size))

        return tuple(ranges)

    def _sub_ifd_ranges(
        self,
        count_size: int,
        entry_size: int,
        next_ifd_size: int,
    ) -> list[PhysicalRange]:
        """
        Zakresy zajmowane przez sub-IFD (Exif, GPS, Interoperability).

        Without this the sub-IFD bytes look like an unrecognised gap: in
        test1.tif to 684 B pod adresem 0x92491AC (462 B samego IFD plus
        222 B of its values stored outside the entries).

        For these tags `tag.value` already returns a parsed dict, so the
        sub-IFD position is taken from `tag.valueoffset`.
        """
        ranges: list[PhysicalRange] = []

        for number in self.SUB_IFD_TAGS:
            tag = self.tag(number)

            if tag is None:
                continue

            offset = getattr(tag, "valueoffset", None)

            if offset is None:
                continue

            ranges.extend(
                self._ifd_ranges(
                    int(offset),
                    count_size,
                    entry_size,
                    next_ifd_size,
                )
            )

        return ranges

    def _ifd_ranges(
        self,
        offset: int,
        count_size: int,
        entry_size: int,
        next_ifd_size: int,
    ) -> list[PhysicalRange]:
        """The IFD at `offset` plus the entry values stored outside it."""
        file_size = self.file_size

        if offset <= 0 or offset + count_size > file_size:
            return []

        order = "little" if self.tiff.byteorder == "<" else "big"

        # An IFD entry: tag(2) | type(2) | count(N) | value-or-offset(N),
        # where N = 4 in classic TIFF and 8 in BigTIFF. This is NOT `count_size`,
        # which is the width of the entry counter at the start of the IFD (2 / 8).
        field_size = 8 if count_size == 8 else 4
        inline_limit = field_size

        with self.path.open("rb") as file:
            file.seek(offset)
            header = file.read(count_size)

            if len(header) != count_size:
                return []

            entries = int.from_bytes(header, order)

            table_size = count_size + entries * entry_size + next_ifd_size

            if entries <= 0 or offset + table_size > file_size:
                return []

            table = file.read(entries * entry_size)

        ranges = [PhysicalRange(offset, offset + table_size)]

        for index in range(entries):
            entry = table[index * entry_size : (index + 1) * entry_size]

            dtype = int.from_bytes(entry[2:4], order)
            count = int.from_bytes(entry[4 : 4 + field_size], order)

            itemsize = DATATYPE_ITEMSIZE.get(dtype)

            if itemsize is None:
                continue

            size = itemsize * count

            if size <= inline_limit:
                continue

            value_offset = int.from_bytes(
                entry[4 + field_size : 4 + 2 * field_size], order
            )

            if value_offset <= 0 or value_offset + size > file_size:
                continue

            ranges.append(PhysicalRange(value_offset, value_offset + size))

        return ranges

    def photoshop_source_data(self) -> bytes | None:
        return self.raw_tag_data(self.PHOTOSHOP_IMAGE_SOURCE_DATA)
