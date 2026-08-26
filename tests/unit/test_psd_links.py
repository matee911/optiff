"""Tests for the linked smart object parser."""

from __future__ import annotations

import pytest

from optiff.psd_links import parse_links
from optiff.readers import BytesReader


def unicode_string(value: str) -> bytes:
    return (len(value) + 1).to_bytes(4, "little") + (value + "\x00").encode("utf-16-le")


def open_descriptor() -> bytes:
    """A minimal opening descriptor: version 16 plus an empty descriptor."""
    return (
        (16).to_bytes(4, "little")  # version
        + (1).to_bytes(4, "little")
        + b"\x00\x00"  # empty Unicode name
        + (0).to_bytes(4, "little")
        + b"llun"  # classID "null"
        + (0).to_bytes(4, "little")  # no fields
    )


def link_record(  # noqa: PLR0913  - a test builder, one knob per variant
    *,
    name: str = "smart.psb",
    kind: str = "liFD",
    uid: str = "abc",
    file_type: str = "8BPB",
    creator: str = "8BIM",
    size: int = 100,
    version: int = 7,
    descriptor: bool = False,
    payload: bytes = b"",
    tail: bytes = b"",
) -> bytes:
    body = (
        kind.encode("latin1")[::-1]
        + version.to_bytes(4, "little")
        + bytes([len(uid)])
        + uid.encode("latin1")
        + unicode_string(name)
        + file_type.encode("latin1")[::-1]
        + creator.encode("latin1")[::-1]
        + size.to_bytes(8, "little")
        + bytes([1 if descriptor else 0])
        + (open_descriptor() if descriptor else b"")
        + payload
        + tail
    )

    padding = (-len(body)) % 4

    return len(body).to_bytes(8, "little") + body + b"\x00" * padding


# ============================================================================
# READING
# ============================================================================


def test_reads_single_embedded_file():
    # Arrange
    data = link_record(name="Portrait Volumes.psb", size=1_800_000)

    # Act
    result = parse_links(BytesReader(data), 0, len(data))

    # Assert
    assert len(result.files) == 1

    item = result.files[0]
    assert item.name == "Portrait Volumes.psb"
    assert item.size == 1_800_000
    assert item.kind_name == "embedded"
    assert item.file_type_name == "PSB"
    assert result.is_exact


def test_reads_multiple_records():
    # Arrange
    data = link_record(name="a.psb", size=10) + link_record(name="b.psb", size=20)

    # Act
    result = parse_links(BytesReader(data), 0, len(data))

    # Assert
    assert [item.name for item in result.files] == ["a.psb", "b.psb"]
    assert [item.index for item in result.files] == [0, 1]
    assert result.is_exact


def test_embedded_bytes_sums_only_embedded():
    # Arrange
    data = link_record(name="a.psb", size=10) + link_record(
        name="b.psb", kind="liFE", size=999
    )

    # Act
    result = parse_links(BytesReader(data), 0, len(data))

    # Assert
    assert result.embedded_bytes == 10


def test_payload_is_skipped_by_record_length():
    # Arrange - file data inside the record must not derail the parser
    data = link_record(name="a.psb", size=64, payload=b"\xab" * 64) + link_record(
        name="b.psb", size=1
    )

    # Act
    result = parse_links(BytesReader(data), 0, len(data))

    # Assert
    assert [item.name for item in result.files] == ["a.psb", "b.psb"]
    assert result.is_exact


def test_record_padding_to_four_bytes():
    # Arrange - a name that gives the record an odd length
    data = link_record(name="x.psb", uid="ab", payload=b"\x01") + link_record(
        name="y.psb"
    )

    # Act
    result = parse_links(BytesReader(data), 0, len(data))

    # Assert
    assert len(result.files) == 2
    assert result.is_exact


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("liFD", "embedded"), ("liFE", "external"), ("liFA", "alias")],
)
def test_kind_names(kind, expected):
    # Arrange
    data = link_record(kind=kind)

    # Assert
    assert parse_links(BytesReader(data), 0, len(data)).files[0].kind_name == (expected)


@pytest.mark.parametrize(
    ("code", "expected"),
    [("8BPS", "PSD"), ("8BPB", "PSB"), ("TIFF", "TIFF"), ("zzzz", "zzzz")],
)
def test_file_type_names(code, expected):
    # Arrange
    data = link_record(file_type=code)

    # Assert
    assert (
        parse_links(BytesReader(data), 0, len(data)).files[0].file_type_name == expected
    )


def test_descriptor_flag_is_read():
    # Arrange
    data = link_record(descriptor=True)

    # Act
    item = parse_links(BytesReader(data), 0, len(data)).files[0]

    # Assert - data_offset must point PAST the descriptor, at the end of
    # the record content, excluding buffer padding
    assert item.has_descriptor
    assert item.data_offset == 8 + item.record_size


def test_data_offset_without_descriptor():
    # Arrange
    data = link_record(descriptor=False, payload=b"XYZ\x00")

    # Act
    item = parse_links(BytesReader(data), 0, len(data)).files[0]

    # Assert
    assert data[item.data_offset : item.data_offset + 3] == b"XYZ"


def test_broken_descriptor_is_reported():
    # Arrange - the flag claims a descriptor that is not there
    body = (
        b"DFil"
        + (7).to_bytes(4, "little")
        + bytes([1])
        + b"a"
        + unicode_string("x.psb")
        + b"BPB8"
        + b"MIB8"
        + (0).to_bytes(8, "little")
        + bytes([1])
    )
    data = len(body).to_bytes(8, "little") + body

    # Act
    result = parse_links(BytesReader(data), 0, len(data))

    # Assert
    assert result.files == ()
    assert result.warnings[0].code == "link-record-failed"
    assert "descriptor" in result.warnings[0].detail


def test_uid_is_read():
    # Arrange
    uid = "47668f3f-190b-a440-87a7-2a0091093c6c"
    data = link_record(uid=uid)

    # Assert
    assert parse_links(BytesReader(data), 0, len(data)).files[0].uid == uid


# ============================================================================
# ERRORS
# ============================================================================


def test_empty_block():
    # Act
    result = parse_links(BytesReader(b""), 0, 0)

    # Assert
    assert result.files == ()
    assert result.is_exact


def test_unknown_record_type_is_reported():
    # Arrange
    data = bytearray(link_record())
    data[8:12] = b"ZZZZ"

    # Act
    result = parse_links(BytesReader(bytes(data)), 0, len(data))

    # Assert
    assert result.files == ()
    assert [w.code for w in result.warnings] == ["link-record-failed"]


def test_record_overrunning_block_is_reported():
    # Arrange - declare a record longer than the block
    data = bytearray(link_record())
    data[0:8] = (10_000).to_bytes(8, "little")

    # Act
    result = parse_links(BytesReader(bytes(data)), 0, len(data))

    # Assert
    assert [w.code for w in result.warnings] == ["link-record-overrun"]


def test_zero_length_record_is_reported():
    # Arrange
    data = bytearray(link_record())
    data[0:8] = (0).to_bytes(8, "little")

    # Act
    result = parse_links(BytesReader(bytes(data)), 0, len(data))

    # Assert
    assert [w.code for w in result.warnings] == ["link-record-failed"]


def test_zero_tail_is_not_a_warning():
    # Arrange
    data = link_record() + b"\x00" * 40

    # Act
    result = parse_links(BytesReader(data), 0, len(data))

    # Assert
    assert result.warnings == ()


def test_nonzero_tail_is_reported():
    # Arrange
    data = link_record() + b"\xff" * 40

    # Act
    result = parse_links(BytesReader(data), 0, len(data))

    # Assert - both warnings are true: the read failed and bytes remained
    # nierozliczone data_bytes
    assert [w.code for w in result.warnings] == [
        "link-record-failed",
        "link-trailing-bytes",
    ]
    assert result.is_exact is False


# ============================================================================
# THE RECORD TAIL
# ============================================================================


def record_tail() -> bytes:
    """The version 5-7 fields: identifier, modification time, lock."""
    return (
        (1).to_bytes(4, "little")
        + b"\x00\x00"  # empty Unicode string
        + b"\x00" * 8  # elapsed modyfikacji
        + b"\x00"  # lock state
    )


def test_tail_after_file_data_is_measured():
    # Arrange - real Photoshop records carry 15 B behind the file data
    data = link_record(size=32, payload=b"\xab" * 32, tail=record_tail())

    # Act
    item = parse_links(BytesReader(data), 0, len(data)).files[0]

    # Assert
    assert item.tail_size == 15
    assert item.data_end == item.data_offset + 32
    assert item.record_end == item.data_end + 15


def test_record_without_tail_reports_zero():
    # Arrange
    data = link_record(size=8, payload=b"\x01" * 8)

    # Act
    item = parse_links(BytesReader(data), 0, len(data)).files[0]

    # Assert
    assert item.tail_size == 0


def test_tail_does_not_confuse_next_record():
    # Arrange
    data = link_record(
        name="a.psb", size=16, payload=b"\x01" * 16, tail=record_tail()
    ) + link_record(name="b.psb", size=4, payload=b"\x02" * 4, tail=record_tail())

    # Act
    result = parse_links(BytesReader(data), 0, len(data))

    # Assert
    assert [item.name for item in result.files] == ["a.psb", "b.psb"]
    assert all(item.tail_size == 15 for item in result.files)
    assert result.is_exact
