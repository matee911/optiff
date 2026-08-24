"""Tests for the Photoshop descriptor parser."""

from __future__ import annotations

import struct

import pytest

from tiff_analyzer.psd_descriptor import (
    Descriptor,
    DescriptorError,
    parse,
    parse_block,
)


def u32(value: int) -> bytes:
    return value.to_bytes(4, "little")


def code(value: str) -> bytes:
    """An OSType exactly as it sits on disk: reversed."""
    return value.encode("latin1")[::-1]


def text(value: str) -> bytes:
    """A Unicode string: length in UTF-16 units plus a terminator."""
    encoded = (value + "\x00").encode("utf-16-le")

    return u32(len(value) + 1) + encoded


def named_key(name: str) -> bytes:
    return u32(len(name)) + name.encode("latin1")


def descriptor_bytes(*items: bytes, classid: str = "null", name: str = "") -> bytes:
    return (
        text(name)
        + u32(0)
        + code(classid)
        + u32(len(items))
        + b"".join(items)
    )


def field(key: str, ostype: str, payload: bytes) -> bytes:
    return named_key(key) + code(ostype) + payload


# ============================================================================
# TYPY PROSTE
# ============================================================================


def test_long_field():
    # Arrange
    stream = descriptor_bytes(field("major", "long", u32(3)))

    # Act
    result = parse(stream)

    # Assert
    assert result.descriptor["major"] == 3
    assert result.is_exact


def test_bool_field():
    # Arrange
    stream = descriptor_bytes(
        field("on", "bool", b"\x01"), field("off", "bool", b"\x00")
    )

    # Act
    result = parse(stream)

    # Assert
    assert result.descriptor["on"] is True
    assert result.descriptor["off"] is False


def test_double_field():
    # Arrange
    stream = descriptor_bytes(field("val", "doub", struct.pack("<d", 1.5)))

    # Act / Assert
    assert parse(stream).descriptor["val"] == 1.5


def test_text_field():
    # Arrange
    stream = descriptor_bytes(field("name", "TEXT", text("Warstwa 1")))

    # Act / Assert
    assert parse(stream).descriptor["name"] == "Warstwa 1"


def test_empty_text_is_stripped_of_terminator():
    # Arrange - Photoshop writes an empty string as length 1 plus one NUL
    stream = descriptor_bytes(field("empty", "TEXT", u32(1) + b"\x00\x00"))

    # Act / Assert
    assert parse(stream).descriptor["empty"] == ""


def test_enum_field():
    # Arrange
    stream = descriptor_bytes(
        field("Engn", "enum", named_key("Engn") + named_key("compCore"))
    )

    # Act / Assert
    assert parse(stream).descriptor["Engn"] == ("Engn", "compCore")


def test_comp_field_is_64_bit():
    # Arrange
    big = 2**40 + 7
    stream = descriptor_bytes(field("duzy", "comp", big.to_bytes(8, "little")))

    # Act / Assert
    assert parse(stream).descriptor["duzy"] == big


def test_unit_float_field():
    # Arrange
    stream = descriptor_bytes(
        field("kat", "UntF", code("#Ang") + struct.pack("<d", 30.0))
    )

    # Act / Assert
    assert parse(stream).descriptor["kat"] == ("#Ang", 30.0)


def test_raw_data_field():
    # Arrange
    stream = descriptor_bytes(field("data", "tdta", u32(3) + b"abc"))

    # Act / Assert
    assert parse(stream).descriptor["data"] == b"abc"


# ============================================================================
# STRUKTURA
# ============================================================================


def test_four_char_key_when_length_is_zero():
    # Arrange - a length-less key stored as a reversed code
    stream = descriptor_bytes(u32(0) + code("Vrsn") + code("long") + u32(1))

    # Act / Assert
    assert parse(stream).descriptor["Vrsn"] == 1


def test_nested_descriptor():
    # Arrange
    inner = descriptor_bytes(field("major", "long", u32(1)))
    stream = descriptor_bytes(field("Vrsn", "Objc", inner))

    # Act
    result = parse(stream)

    # Assert
    assert isinstance(result.descriptor["Vrsn"], Descriptor)
    assert result.descriptor["Vrsn"]["major"] == 1
    assert result.is_exact


def test_list_field():
    # Arrange
    stream = descriptor_bytes(
        field(
            "lista",
            "VlLs",
            u32(2) + code("long") + u32(7) + code("long") + u32(9),
        )
    )

    # Act / Assert
    assert parse(stream).descriptor["lista"] == [7, 9]


def test_empty_list():
    # Arrange
    stream = descriptor_bytes(field("brak", "VlLs", u32(0)))

    # Act / Assert
    assert parse(stream).descriptor["brak"] == []


def test_classid_and_name_are_read():
    # Arrange
    stream = descriptor_bytes(classid="genI", name="Tytul")

    # Act
    result = parse(stream).descriptor

    # Assert
    assert result.name == "Tytul"
    assert result.classid == "genI"


def test_flat_joins_nested_paths():
    # Arrange
    inner = descriptor_bytes(field("major", "long", u32(2)))
    stream = descriptor_bytes(
        field("Vrsn", "Objc", inner), field("on", "bool", b"\x01")
    )

    # Act
    flat = parse(stream).descriptor.flat()

    # Assert
    assert flat == {"Vrsn.major": 2, "on": True}


def test_offset_skips_block_header():
    # Arrange
    stream = u32(16) + descriptor_bytes(field("major", "long", u32(5)))

    # Act
    result = parse(stream, offset=4)

    # Assert
    assert result.descriptor["major"] == 5
    assert result.is_exact


# ============================================================================
# ERRORS AND TRAILING DATA
# ============================================================================


def test_trailing_bytes_are_reported_not_hidden():
    # Arrange
    stream = descriptor_bytes(field("major", "long", u32(1))) + b"\x00" * 8

    # Act
    result = parse(stream)

    # Assert
    assert result.is_exact is False
    assert result.trailing == 8


def test_unknown_ostype_raises():
    # Arrange
    stream = descriptor_bytes(field("x", "ZZZZ", b""))

    # Act / Assert
    with pytest.raises(DescriptorError, match="unknown OSType"):
        parse(stream)


def test_truncated_stream_raises():
    # Arrange
    stream = descriptor_bytes(field("major", "long", u32(1)))[:-2]

    # Act / Assert
    with pytest.raises(DescriptorError, match="past the end"):
        parse(stream)


def test_deep_nesting_is_rejected():
    # Arrange - a descriptor nested inside itself 40 times
    stream = descriptor_bytes(field("x", "long", u32(1)))

    for _ in range(40):
        stream = descriptor_bytes(field("x", "Objc", stream))

    # Act / Assert
    with pytest.raises(DescriptorError, match="nesting"):
        parse(stream)


def test_parse_block_returns_none_for_unknown_key():
    assert parse_block("Lr16", b"x" * 40) is None


def test_parse_block_returns_none_on_garbage():
    assert parse_block("cinf", b"\x00" * 40) is None


def test_parse_block_uses_known_offset():
    # Arrange - cinf carries a 4-byte version header before the descriptor
    payload = u32(16) + descriptor_bytes(field("major", "long", u32(1)))

    # Act
    result = parse_block("cinf", payload)

    # Assert
    assert result is not None
    assert result.descriptor["major"] == 1
    assert result.is_exact
