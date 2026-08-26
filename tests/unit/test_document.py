"""Tests for document.py's pure helpers, with no I/O."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import tifffile

from optiff.document import (
    TiffDocument,
    _coerce_bytes,
    _compression_info,
    _entry_value_ranges,
    _parse_ifd_entries,
)
from optiff.domain import PhysicalRange

# ============================================================================
# _compression_info
# ============================================================================


def test_compression_info_none_means_uncompressed():
    assert _compression_info(None) == (1, "NONE")


def test_compression_info_reads_enum_like_value():
    compression = SimpleNamespace(value=5, name="LZW")

    assert _compression_info(compression) == (5, "LZW")


def test_compression_info_falls_back_to_int_and_str():
    assert _compression_info(7) == (7, "7")


# ============================================================================
# _coerce_bytes
# ============================================================================


def test_coerce_bytes_passes_bytes_through():
    assert _coerce_bytes(b"abc") == b"abc"


def test_coerce_bytes_converts_bytearray():
    assert _coerce_bytes(bytearray(b"abc")) == b"abc"


def test_coerce_bytes_uses_tobytes_when_available():
    value = SimpleNamespace(tobytes=lambda: b"xyz")

    assert _coerce_bytes(value) == b"xyz"


def test_coerce_bytes_falls_back_to_bytes_constructor():
    assert _coerce_bytes([1, 2, 3]) == b"\x01\x02\x03"


def test_coerce_bytes_returns_none_when_unconvertible():
    assert _coerce_bytes(object()) is None


# ============================================================================
# _parse_ifd_entries
# ============================================================================


def _entry(dtype: int, count: int, value_offset: int) -> bytes:
    return (
        (0).to_bytes(2, "little")
        + dtype.to_bytes(2, "little")
        + count.to_bytes(4, "little")
        + value_offset.to_bytes(4, "little")
    )


def test_parse_ifd_entries_decodes_dtype_count_and_offset():
    table = _entry(dtype=3, count=1, value_offset=100)

    assert _parse_ifd_entries(
        table, entries=1, entry_size=12, field_size=4, order="little"
    ) == [(3, 1, 100)]


def test_parse_ifd_entries_reads_multiple_entries_in_order():
    table = _entry(dtype=3, count=1, value_offset=100) + _entry(
        dtype=4, count=2, value_offset=200
    )

    assert _parse_ifd_entries(
        table, entries=2, entry_size=12, field_size=4, order="little"
    ) == [(3, 1, 100), (4, 2, 200)]


# ============================================================================
# _entry_value_ranges
# ============================================================================


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        # SHORT (itemsize 2) x1 = 2 bytes, inline (<= inline_limit) -> dropped
        ([(3, 1, 100)], []),
        # LONG (itemsize 4) x2 = 8 bytes, out-of-line, within file -> kept
        ([(4, 2, 100)], [(100, 108)]),
        # unknown dtype -> dropped
        ([(999, 1, 100)], []),
        # zero/negative offset -> dropped
        ([(4, 2, 0)], []),
        # extends past file_size -> dropped
        ([(4, 2, 995)], []),
    ],
)
def test_entry_value_ranges_filters_by_inline_and_bounds(entries, expected):
    result = _entry_value_ranges(entries, inline_limit=4, file_size=1000)

    assert result == [PhysicalRange(start, end) for start, end in expected]


# ============================================================================
# TiffDocument methods, with tifffile substituted by SimpleNamespace fakes
# ============================================================================


def _page(*, tags=None, offset=0, **extra) -> SimpleNamespace:
    return SimpleNamespace(tags=tags or {}, offset=offset, **extra)


def _document(path, *, page=None, is_bigtiff=False, byteorder="<") -> TiffDocument:
    """A `TiffDocument` with `tifffile.TiffFile` swapped for fakes."""
    document = object.__new__(TiffDocument)
    document.path = path
    document.tiff = cast(
        tifffile.TiffFile,
        SimpleNamespace(
            byteorder=byteorder,
            is_bigtiff=is_bigtiff,
            pages=SimpleNamespace(first=page or _page()),
        ),
    )
    return document


def test_raw_tag_data_returns_none_for_a_missing_tag(tmp_path):
    document = _document(tmp_path / "f.tif")

    assert document.raw_tag_data(999) is None


class TestTagValueSize:
    def test_prefers_valuebytecount(self, tmp_path):
        document = _document(tmp_path / "f.tif")
        tag = SimpleNamespace(valuebytecount=42)

        assert document.tag_value_size(tag) == 42

    def test_falls_back_to_dtype_and_count(self, tmp_path):
        document = _document(tmp_path / "f.tif")
        tag = SimpleNamespace(dtype=3, count=2)  # SHORT x2 = 4 B

        assert document.tag_value_size(tag) == 4

    def test_returns_zero_when_nothing_is_usable(self, tmp_path):
        document = _document(tmp_path / "f.tif")
        tag = SimpleNamespace()

        assert document.tag_value_size(tag) == 0


class TestImageDataRange:
    def test_none_when_there_is_no_image_data(self, tmp_path):
        page = _page(dataoffsets=(), databytecounts=())
        document = _document(tmp_path / "f.tif", page=page)

        assert document.image_data_range() is None

    def test_spans_every_strip(self, tmp_path):
        page = _page(dataoffsets=(100, 300), databytecounts=(50, 20))
        document = _document(tmp_path / "f.tif", page=page)

        assert document.image_data_range() == PhysicalRange(100, 320)


def test_tag_data_range_is_none_without_a_value_offset(tmp_path):
    page = _page(tags={5: SimpleNamespace(valuebytecount=10)})  # no valueoffset
    document = _document(tmp_path / "f.tif", page=page)

    assert document.tag_data_range(5) is None


def test_tiff_structure_ranges_uses_bigtiff_field_widths(tmp_path):
    page = _page(tags={}, offset=16)
    document = _document(tmp_path / "f.tif", page=page, is_bigtiff=True)

    ranges = document.tiff_structure_ranges()

    assert ranges[0] == PhysicalRange(0, 16)  # BigTIFF header


def test_tiff_structure_ranges_skips_a_tag_without_a_value_offset(tmp_path):
    # valuebytecount=10 > inline_limit(4), but there is no valueoffset to place it at.
    page = _page(tags={5: SimpleNamespace(valuebytecount=10)}, offset=8)
    document = _document(tmp_path / "f.tif", page=page)

    # Would raise/append a bogus range if the missing-offset guard were removed.
    assert document.tiff_structure_ranges()[0] == PhysicalRange(0, 8)


class TestIfdRanges:
    def _write(self, tmp_path: Path, size: int) -> Path:
        path = tmp_path / "f.tif"
        path.write_bytes(bytes(size))
        return path

    def test_rejects_a_non_positive_offset(self, tmp_path):
        document = _document(self._write(tmp_path, 200))

        assert document._ifd_ranges(0, 2, 12, 4) == []

    def test_rejects_when_the_header_would_run_past_the_file(self, tmp_path):
        document = _document(self._write(tmp_path, 200))

        assert document._ifd_ranges(199, 2, 12, 4) == []

    def test_rejects_a_zero_entry_count(self, tmp_path):
        path = self._write(tmp_path, 200)
        data = bytearray(path.read_bytes())
        data[10:12] = (0).to_bytes(2, "little")
        path.write_bytes(bytes(data))

        assert _document(path)._ifd_ranges(10, 2, 12, 4) == []

    def test_rejects_when_the_table_would_run_past_the_file(self, tmp_path):
        path = self._write(tmp_path, 200)
        data = bytearray(path.read_bytes())
        data[10:12] = (100).to_bytes(2, "little")  # 100 entries won't fit
        path.write_bytes(bytes(data))

        assert _document(path)._ifd_ranges(10, 2, 12, 4) == []

    def test_combines_the_table_range_with_out_of_line_entry_ranges(self, tmp_path):
        path = self._write(tmp_path, 200)
        data = bytearray(path.read_bytes())
        table = _entry(dtype=3, count=1, value_offset=0) + _entry(
            dtype=4, count=2, value_offset=100
        )
        data[50:52] = (2).to_bytes(2, "little")  # 2 entries
        data[52 : 52 + len(table)] = table
        path.write_bytes(bytes(data))

        ranges = _document(path)._ifd_ranges(50, 2, 12, 4)

        # table: count(2) + 2*entry_size(12) + next_ifd(4) = 30 B, starting at 50.
        assert ranges == [PhysicalRange(50, 80), PhysicalRange(100, 108)]


def test_sub_ifd_ranges_skips_a_tag_without_a_value_offset(tmp_path):
    page = _page(tags={34665: SimpleNamespace(valuebytecount=4)})  # no valueoffset
    document = _document(tmp_path / "f.tif", page=page)

    assert document._sub_ifd_ranges(count_size=2, entry_size=12, next_ifd_size=4) == []


class _RacyPath:
    """A `stat().st_size` that no longer matches what `open()` can read.

    Mimics the file shrinking between the size check and the read - the
    only way to reach the defensive short-read guard in `_ifd_ranges`.
    """

    def __init__(self, claimed_size: int, actual_data: bytes):
        self._claimed_size = claimed_size
        self._actual_data = actual_data

    def stat(self):
        return SimpleNamespace(st_size=self._claimed_size)

    def open(self, mode):
        return io.BytesIO(self._actual_data)


def test_ifd_ranges_rejects_a_header_shorter_than_declared():
    document = _document(_RacyPath(claimed_size=1000, actual_data=b"\x00"))

    assert document._ifd_ranges(10, 2, 12, 4) == []


def test_sub_ifd_ranges_reads_the_ifd_at_the_tags_value_offset(tmp_path):
    path = tmp_path / "f.tif"
    data = bytearray(200)
    table = _entry(dtype=4, count=2, value_offset=100)
    data[50:52] = (1).to_bytes(2, "little")
    data[52 : 52 + len(table)] = table
    path.write_bytes(bytes(data))

    page = _page(tags={34665: SimpleNamespace(valuebytecount=4, valueoffset=50)})
    document = _document(path, page=page)

    ranges = document._sub_ifd_ranges(count_size=2, entry_size=12, next_ifd_size=4)

    # table: count(2) + 1*entry_size(12) + next_ifd(4) = 18 B, starting at 50.
    assert ranges == [PhysicalRange(50, 68), PhysicalRange(100, 108)]
