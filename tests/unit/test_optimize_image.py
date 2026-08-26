"""Tests for compressing flattened TIFF pixels and patching sub-IFD offsets."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from optiff import optimize_image
from optiff.optimize_image import (
    ImageDataError,
    can_compress,
    plan_image_data,
    shift_patches,
)
from optiff.readers import BytesReader


def _entry(code: int, dtype: int, count: int, value: int) -> bytes:
    return (
        code.to_bytes(2, "little")
        + dtype.to_bytes(2, "little")
        + count.to_bytes(4, "little")
        + value.to_bytes(4, "little")
    )


def _tag(offset: int, valuebytecount: int, valueoffset: int) -> SimpleNamespace:
    return SimpleNamespace(
        offset=offset, valuebytecount=valuebytecount, valueoffset=valueoffset
    )


def _page(*, compression=1, dataoffsets=(100,), databytecounts=(50,), tags=None):
    return SimpleNamespace(
        compression=compression,
        dataoffsets=dataoffsets,
        databytecounts=databytecounts,
        tags=tags or {},
    )


# ============================================================================
# can_compress
# ============================================================================


def test_can_compress_refuses_already_compressed_data():
    page = _page(compression=SimpleNamespace(value=8))

    assert "method 8" in can_compress(page)


def test_can_compress_refuses_multiple_strips():
    page = _page(dataoffsets=(100, 200), databytecounts=(50, 50))

    assert "2 strips" in can_compress(page)


def test_can_compress_refuses_existing_predictor_tag():
    page = _page(tags={optimize_image.PREDICTOR_TAG: _tag(0, 2, 0)})

    assert "Predictor" in can_compress(page)


def test_can_compress_accepts_a_plain_single_strip_page():
    assert can_compress(_page()) == ""


# ============================================================================
# plan_image_data
# ============================================================================


def _compressible_page(size: int) -> tuple[SimpleNamespace, BytesReader]:
    page = _page(
        dataoffsets=(0,),
        databytecounts=(size,),
        tags={
            optimize_image.COMPRESSION_TAG: _tag(0, 2, 1),
            optimize_image.STRIP_BYTE_COUNTS_TAG: _tag(20, 4, size),
        },
    )
    reader = BytesReader(bytes(size))  # all zeros: highly compressible

    return page, reader


def test_plan_image_data_raises_when_page_cannot_be_touched():
    page = _page(tags={optimize_image.PREDICTOR_TAG: _tag(0, 2, 0)})

    with pytest.raises(ImageDataError, match="Predictor"):
        plan_image_data(BytesReader(b""), page)


def test_plan_image_data_raises_on_short_read():
    page, _ = _compressible_page(64)
    short_reader = BytesReader(bytes(10))  # fewer bytes than databytecounts declares

    with pytest.raises(ImageDataError, match="read 10 B"):
        plan_image_data(short_reader, page)


def test_plan_image_data_returns_none_when_compression_does_not_shrink(monkeypatch):
    page, reader = _compressible_page(9)
    monkeypatch.setattr(optimize_image.zlib, "compress", lambda raw, level: raw)

    assert plan_image_data(reader, page) is None


def test_plan_image_data_raises_when_the_shift_would_be_odd(monkeypatch):
    page, reader = _compressible_page(9)
    monkeypatch.setattr(optimize_image.zlib, "compress", lambda raw, level: b"abc")

    with pytest.raises(ImageDataError, match="odd"):
        plan_image_data(reader, page)


def test_plan_image_data_builds_patches_for_a_shrinking_page():
    page, reader = _compressible_page(64)

    plan = plan_image_data(reader, page)

    assert plan is not None
    assert plan.worth_it
    assert plan.before == 64
    assert plan.after == len(plan.data)
    assert dict(plan.patches)[20 + optimize_image.VALUE_FIELD] == plan.stored.to_bytes(
        4, "little"
    )


# ============================================================================
# shift_patches / sub-IFD patches
# ============================================================================


def test_shift_patches_shifts_an_out_of_line_tag_past_the_boundary():
    page = _page(tags={999: _tag(offset=0, valuebytecount=8, valueoffset=200)})

    patches = shift_patches(BytesReader(b""), page, delta=10, boundary=100)

    assert patches == [(0 + optimize_image.VALUE_FIELD, (190).to_bytes(4, "little"))]


def test_shift_patches_skips_tags_before_the_boundary():
    page = _page(tags={999: _tag(offset=0, valuebytecount=8, valueoffset=50)})

    assert shift_patches(BytesReader(b""), page, delta=10, boundary=100) == []


def test_shift_patches_skips_inline_values():
    page = _page(tags={999: _tag(offset=0, valuebytecount=4, valueoffset=500)})

    assert shift_patches(BytesReader(b""), page, delta=10, boundary=100) == []


def test_shift_patches_follows_a_sub_ifd_pointer_past_the_boundary():
    sub_ifd_offset = 300
    tag_offset = 0
    # one SHORT entry (itemsize 2) x1 = 2 B, inline -> no patch of its own
    table = _entry(code=1, dtype=3, count=1, value=0)

    buf = bytearray(400)
    buf[
        tag_offset + optimize_image.VALUE_FIELD : tag_offset
        + optimize_image.VALUE_FIELD
        + 4
    ] = sub_ifd_offset.to_bytes(4, "little")
    buf[sub_ifd_offset : sub_ifd_offset + 2] = (1).to_bytes(2, "little")
    buf[sub_ifd_offset + 2 : sub_ifd_offset + 2 + 12] = table

    page = _page(
        tags={
            optimize_image.SUB_IFD_TAGS[0]: _tag(
                offset=tag_offset, valuebytecount=4, valueoffset=sub_ifd_offset
            )
        }
    )

    patches = shift_patches(BytesReader(bytes(buf)), page, delta=10, boundary=100)

    assert patches == [
        (
            tag_offset + optimize_image.VALUE_FIELD,
            (sub_ifd_offset - 10).to_bytes(4, "little"),
        )
    ]


def test_shift_patches_ignores_a_sub_ifd_pointer_before_the_boundary():
    tag_offset = 0
    pointer = 50
    buf = bytearray(64)
    buf[
        tag_offset + optimize_image.VALUE_FIELD : tag_offset
        + optimize_image.VALUE_FIELD
        + 4
    ] = pointer.to_bytes(4, "little")
    page = _page(
        tags={
            optimize_image.SUB_IFD_TAGS[0]: _tag(
                offset=tag_offset, valuebytecount=4, valueoffset=pointer
            )
        }
    )

    assert shift_patches(BytesReader(bytes(buf)), page, delta=10, boundary=100) == []


def test_shift_patches_includes_out_of_line_sub_ifd_entries():
    sub_ifd_offset = 300
    entry_value_offset = 350
    # one LONG entry (itemsize 4) x2 = 8 B, out-of-line -> patched too
    table = _entry(code=1, dtype=4, count=2, value=entry_value_offset)

    buf = bytearray(500)
    buf[0 + optimize_image.VALUE_FIELD : 0 + optimize_image.VALUE_FIELD + 4] = (
        sub_ifd_offset.to_bytes(4, "little")
    )
    buf[sub_ifd_offset : sub_ifd_offset + 2] = (1).to_bytes(2, "little")
    buf[sub_ifd_offset + 2 : sub_ifd_offset + 2 + 12] = table

    page = _page(
        tags={
            optimize_image.SUB_IFD_TAGS[0]: _tag(
                offset=0, valuebytecount=4, valueoffset=sub_ifd_offset
            )
        }
    )

    patches = shift_patches(BytesReader(bytes(buf)), page, delta=10, boundary=100)

    entry_at = sub_ifd_offset + 2 + 0 * 12 + optimize_image.VALUE_FIELD

    assert (
        0 + optimize_image.VALUE_FIELD,
        (sub_ifd_offset - 10).to_bytes(4, "little"),
    ) in patches
    assert (entry_at, (entry_value_offset - 10).to_bytes(4, "little")) in patches


def test_sub_ifd_patches_rejects_a_zero_entry_count():
    buf = bytearray(64)
    buf[0:2] = (0).to_bytes(2, "little")  # 0 entries

    with pytest.raises(ImageDataError, match="entries"):
        optimize_image._sub_ifd_patches(
            BytesReader(bytes(buf)), offset=0, delta=10, boundary=100, order="little"
        )


def test_sub_ifd_patches_rejects_an_implausibly_large_entry_count():
    buf = bytearray(64)
    buf[0:2] = (4096).to_bytes(2, "little")  # at the rejection threshold

    with pytest.raises(ImageDataError, match="entries"):
        optimize_image._sub_ifd_patches(
            BytesReader(bytes(buf)), offset=0, delta=10, boundary=100, order="little"
        )
