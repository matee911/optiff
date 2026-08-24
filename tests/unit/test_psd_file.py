"""Testy parsera osadzonego pliku PSD/PSB (big-endian, bez byte-swappingu)."""

from __future__ import annotations

from itertools import pairwise

import pytest

from tiff_analyzer.psd_file import (
    HEADER_SIZE,
    DocumentError,
    FileSection,
    parse_document,
)
from tiff_analyzer.readers import BytesReader


def be(value: int, width: int) -> bytes:
    return value.to_bytes(width, "big")


def header(  # noqa: PLR0913  - builder testowy, wariant per parametr
    *,
    version: int = 2,
    channels: int = 3,
    width: int = 800,
    height: int = 600,
    depth: int = 16,
    color_mode: int = 3,
    signature: bytes = b"8BPS",
) -> bytes:
    return (
        signature
        + be(version, 2)
        + bytes(6)
        + be(channels, 2)
        + be(height, 4)
        + be(width, 4)
        + be(depth, 2)
        + be(color_mode, 2)
    )


def layer_record_be(
    name: str, *, channels=((0, 0),), bounds=(0, 0, 10, 20), large=True
):
    """
    A layer record in a raw file: big-endian, codes not reversed.

    In PSB the channel sizes are 8 bytes whatever the bit depth.
    """
    top, left, bottom, right = bounds

    pascal = bytes([len(name)]) + name.encode("latin1")
    pascal += b"\x00" * ((-len(pascal)) % 4)

    extra = be(0, 4) + be(0, 4) + pascal

    return (
        b"".join(value.to_bytes(4, "big", signed=True)
                 for value in (top, left, bottom, right))
        + be(len(channels), 2)
        + b"".join(
            channel_id.to_bytes(2, "big", signed=True) + be(size, 8 if large else 4)
            for channel_id, size in channels
        )
        + b"8BIM"
        + b"norm"
        + bytes([255, 0, 0, 0])
        + be(len(extra), 4)
        + extra
    )


def document(
    *,
    color_mode_data: bytes = b"",
    image_resources: bytes = b"",
    layer_and_mask: bytes = b"",
    image_data: bytes = b"\x00\x01",
    version: int = 2,
    **kwargs,
) -> bytes:
    large = version == 2

    return (
        header(version=version, **kwargs)
        + be(len(color_mode_data), 4)
        + color_mode_data
        + be(len(image_resources), 4)
        + image_resources
        + be(len(layer_and_mask), 8 if large else 4)
        + layer_and_mask
        + image_data
    )


def parse(data: bytes):
    return parse_document(BytesReader(data), 0, len(data))


# ============================================================================
# HEADER
# ============================================================================


def test_reads_header():
    # Act
    doc = parse(document(width=4954, height=6192, depth=16, channels=3))

    # Assert
    assert doc.format_name == "PSB"
    assert (doc.width, doc.height) == (4954, 6192)
    assert doc.channels == 3
    assert doc.depth == 16
    assert doc.color_mode_name == "RGB"


def test_psd_version_one():
    # Act
    doc = parse(document(version=1))

    # Assert
    assert doc.format_name == "PSD"
    assert doc.is_large is False


def test_bad_signature_raises():
    # Act / Assert
    with pytest.raises(DocumentError, match="missing signature"):
        parse(document(signature=b"ZZZZ"))


def test_truncated_file_raises():
    # Act / Assert
    with pytest.raises(DocumentError, match="past the end of the file"):
        parse(document()[:10])


@pytest.mark.parametrize(
    ("code", "expected"),
    [(0, "Bitmap"), (1, "Grayscale"), (3, "RGB"), (4, "CMYK"), (99, "mode 99")],
)
def test_color_mode_names(code, expected):
    assert parse(document(color_mode=code)).color_mode_name == expected


@pytest.mark.parametrize(
    ("marker", "expected"),
    [(b"\x00\x00", "RAW"), (b"\x00\x01", "RLE"), (b"\x00\x63", "method 99")],
)
def test_image_compression(marker, expected):
    assert parse(document(image_data=marker)).compression_name == expected


# ============================================================================
# SEKCJE
# ============================================================================


def test_sections_tile_the_file_exactly():
    # Arrange
    data = document(
        color_mode_data=b"abcd",
        image_resources=b"x" * 100,
        layer_and_mask=b"y" * 40,
    )

    # Act
    doc = parse(data)

    # Assert
    assert doc.accounted == len(data)
    assert [item.name for item in doc.sections] == [
        "Color Mode Data",
        "Image Resources",
        "Layer and Mask Information",
        "Image Data",
    ]


def test_section_sizes():
    # Act
    doc = parse(
        document(color_mode_data=b"abcd", image_resources=b"x" * 100)
    )

    # Assert
    assert doc.section("Color Mode Data").size == 4
    assert doc.section("Image Resources").size == 100
    assert doc.section("Color Mode Data").total_size == 8


def test_sections_are_contiguous():
    # Arrange
    doc = parse(document(image_resources=b"x" * 16, layer_and_mask=b"y" * 8))

    # Act
    offsets = [(item.offset, item.end) for item in doc.sections]

    # Assert
    assert offsets[0][0] == HEADER_SIZE

    for (_, end), (start, _) in pairwise(offsets):
        assert end == start


def test_overrunning_section_is_reported():
    # Arrange - Image Resources declares more than the file has left
    data = bytearray(document(image_resources=b"x" * 8))
    data[HEADER_SIZE + 4 : HEADER_SIZE + 8] = be(10_000, 4)

    # Act
    doc = parse(bytes(data))

    # Assert
    assert [w.code for w in doc.warnings] == ["embedded-section-overrun"]


def test_unknown_section_lookup_returns_none():
    assert parse(document()).section("Nie ma takiej") is None


# ============================================================================
# WARSTWY
# ============================================================================


def test_classic_layer_info_is_read():
    # Arrange - dokument 8-bitowy trzyma warstwy w Layer Info
    records = layer_record_be("Tlo") + layer_record_be("Gora")
    layer_info = be(2, 2) + records

    data = document(
        depth=8,
        layer_and_mask=be(len(layer_info), 8) + layer_info + be(0, 4),
    )

    # Act
    doc = parse(data)

    # Assert
    assert doc.layers is not None
    assert [layer.name for layer in doc.layers.layers] == ["Tlo", "Gora"]


def test_layers_in_lr16_additional_block():
    # Arrange - in a 16-bit document Layer Info has length 0 and the
    # layers live in an 8BIM/Lr16 block with an 8-byte length
    layer_info = be(1, 2) + layer_record_be("W Lr16")

    # The block payload is padded to a multiple of 4 bytes.
    block = (
        b"8BIM"
        + b"Lr16"
        + be(len(layer_info), 8)
        + layer_info
        + b"\x00" * ((-len(layer_info)) % 4)
    )

    layer_and_mask = be(0, 8) + be(0, 4) + block

    # Act
    doc = parse(document(layer_and_mask=layer_and_mask))

    # Assert
    assert doc.layers is not None
    assert [layer.name for layer in doc.layers.layers] == ["W Lr16"]


def test_no_layers_when_section_empty():
    assert parse(document()).layers is None


def test_channel_sizes_are_big_endian():
    # Arrange
    layer_info = be(1, 2) + layer_record_be(
        "Z kanalami", channels=((0, 10), (1, 20))
    )
    data = document(
        depth=8,
        layer_and_mask=be(len(layer_info), 8) + layer_info + b"\x00" * 30,
    )

    # Act
    doc = parse(data)

    # Assert
    assert doc.layers.layers[0].data_size == 30


def test_file_section_geometry():
    # Arrange
    section = FileSection("X", 100, 4, 50)

    # Assert
    assert section.data_offset == 104
    assert section.total_size == 54
    assert section.end == 154
