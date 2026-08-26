"""Tests for document.py's pure helpers, with no I/O."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from optiff.document import (
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
