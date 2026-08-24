"""Tests for the Photoshop block parser: no I/O, builder-made streams."""

from __future__ import annotations

import pytest

from tests.unit.builders import psd_block, psd_stream
from tiff_analyzer.psd_blocks import align4, detect_layout, logical_key, walk
from tiff_analyzer.readers import BytesReader

BYTE_ORDERS = ["<", ">"]


def parse(data: bytes, start: int = 0):
    return walk(BytesReader(data), start, len(data))


# ============================================================================
# LAYOUT
# ============================================================================


def test_detect_layout_covers_all_four_signatures():
    # Arrange / Act / Assert
    assert detect_layout(b"8BIM") == ("8BIM", ">", 4, 12)
    assert detect_layout(b"MIB8") == ("8BIM", "<", 4, 12)
    assert detect_layout(b"8B64") == ("8B64", ">", 8, 16)
    assert detect_layout(b"46B8") == ("8B64", "<", 8, 16)


def test_detect_layout_rejects_unknown_signature():
    assert detect_layout(b"zzzz") is None
    assert detect_layout(b"") is None


@pytest.mark.parametrize(
    ("raw", "byte_order", "expected"),
    [
        ("61rL", "<", "Lr16"),
        ("Lr16", ">", "Lr16"),
        ("2knl", "<", "lnk2"),
        (" IAC", "<", "CAI "),
    ],
)
def test_logical_key_unswaps_only_little_endian(raw, byte_order, expected):
    assert logical_key(raw, byte_order) == expected


@pytest.mark.parametrize(
    ("value", "expected"), [(0, 0), (1, 4), (3, 4), (4, 4), (5, 8)]
)
def test_align4(value, expected):
    assert align4(value) == expected


# ============================================================================
# WALK - HAPPY PATH
# ============================================================================


@pytest.mark.parametrize("byte_order", BYTE_ORDERS)
def test_walk_returns_logical_keys_in_both_byte_orders(byte_order):
    # Arrange
    data = psd_stream(
        ("Lr16", b"warstwy"),
        ("LMsk", b"\x00" * 14),
        ("cinf", b"x"),
        byte_order=byte_order,
    )

    # Act
    blocks, warnings = parse(data)

    # Assert
    assert [block.key for block in blocks] == ["Lr16", "LMsk", "cinf"]
    assert [block.size for block in blocks] == [7, 14, 1]
    assert warnings == ()


@pytest.mark.parametrize("byte_order", BYTE_ORDERS)
def test_walk_consumes_stream_exactly(byte_order):
    # Arrange
    data = psd_stream(
        ("Lr16", b"a" * 33),
        ("Pat2", b""),
        ("cinf", b"bb"),
        byte_order=byte_order,
    )

    # Act
    blocks, warnings = parse(data)

    # Assert - the total padded_size must hit the end of the data exactly
    assert sum(block.padded_size for block in blocks) == len(data)
    assert blocks[-1].end == len(data)
    assert warnings == ()


@pytest.mark.parametrize("byte_order", BYTE_ORDERS)
def test_walk_exposes_raw_and_logical_key(byte_order):
    # Arrange
    data = psd_stream(("Lr16", b"ab"), byte_order=byte_order)

    # Act
    (block,), _ = parse(data)

    # Assert
    assert block.key == "Lr16"
    assert block.raw_key == ("61rL" if byte_order == "<" else "Lr16")
    assert block.signature == "8BIM"
    assert block.raw_signature == (b"MIB8" if byte_order == "<" else b"8BIM")
    assert block.byte_order == byte_order


@pytest.mark.parametrize("byte_order", BYTE_ORDERS)
def test_walk_handles_zero_length_block(byte_order):
    # Arrange - a real case: Pat2 usually has length 0
    data = psd_stream(("Pat2", b""), ("cinf", b"x"), byte_order=byte_order)

    # Act
    blocks, warnings = parse(data)

    # Assert
    assert [block.key for block in blocks] == ["Pat2", "cinf"]
    assert blocks[0].size == 0
    assert blocks[0].padded_size == 12
    assert warnings == ()


@pytest.mark.parametrize("payload_size", [1, 2, 3, 4, 5])
def test_walk_handles_every_padding_width(payload_size):
    # Arrange
    data = psd_stream(("Lr16", b"x" * payload_size), ("cinf", b"y"))

    # Act
    blocks, warnings = parse(data)

    # Assert
    assert len(blocks) == 2
    assert blocks[0].size == payload_size
    assert blocks[0].padded_size == 12 + align4(payload_size)
    assert warnings == ()


def test_walk_offsets_are_relative_to_window_start():
    # Arrange - parse from 36 B, past the container header
    header = b"H" * 36
    data = header + psd_stream(("Lr16", b"ab"), ("cinf", b"c"))

    # Act
    blocks, warnings = parse(data, start=36)

    # Assert
    assert [block.offset for block in blocks] == [36, 52]
    assert blocks[0].payload_offset == 48
    assert warnings == ()


def test_walk_payload_offset_points_past_header():
    # Arrange
    data = psd_stream(("Lr16", b"hello"))

    # Act
    (block,), _ = parse(data)

    # Assert
    assert block.payload_offset == 12
    assert data[block.payload_offset : block.payload_offset + block.size] == b"hello"


# ============================================================================
# WALK - 8B64
# ============================================================================


@pytest.mark.parametrize("byte_order", BYTE_ORDERS)
def test_walk_supports_8b64_with_eight_byte_length(byte_order):
    # Arrange
    data = psd_stream(
        ("Lr16", b"duzy"), byte_order=byte_order, signature="8B64"
    )

    # Act
    blocks, warnings = parse(data)

    # Assert
    assert len(blocks) == 1
    assert blocks[0].signature == "8B64"
    assert blocks[0].key == "Lr16"
    assert blocks[0].size == 4
    assert blocks[0].header_size == 16
    assert warnings == ()


# ============================================================================
# WALK - V0002 CONTAINER (PSB rule)
# ============================================================================


@pytest.mark.parametrize("byte_order", BYTE_ORDERS)
def test_large_document_gives_selected_keys_eight_byte_length(byte_order):
    # Arrange - Lr16 is on the PSB list, cinf is not
    data = psd_block(
        "Lr16", b"layers1", byte_order=byte_order, length_size=8
    ) + psd_block("cinf", b"xy", byte_order=byte_order)

    # Act
    blocks, warnings = walk(
        BytesReader(data), 0, len(data), large_document=True
    )

    # Assert
    assert [block.key for block in blocks] == ["Lr16", "cinf"]
    assert [block.header_size for block in blocks] == [16, 12]
    assert blocks[-1].end == len(data)
    assert warnings == ()


def test_large_document_flag_is_off_by_default():
    # Arrange - the same stream without the flag falls apart
    data = psd_block("Lr16", b"layers1", length_size=8) + psd_block("cinf", b"xy")

    # Act
    blocks, warnings = walk(BytesReader(data), 0, len(data))

    # Assert
    assert blocks[0].header_size == 12
    assert warnings != ()


def test_large_document_does_not_affect_8b64_blocks():
    # Arrange - 8B64 carries an 8-byte length whatever the container
    data = psd_stream(("cinf", b"xy"), signature="8B64")

    # Act
    blocks, _ = walk(BytesReader(data), 0, len(data), large_document=True)

    # Assert
    assert blocks[0].header_size == 16
    assert blocks[0].signature == "8B64"


# ============================================================================
# WALK - ERRORS
# ============================================================================


def test_walk_reports_truncated_header():
    # Arrange
    data = psd_stream(("Lr16", b"ab")) + b"MIB8ab"

    # Act
    blocks, warnings = parse(data)

    # Assert
    assert len(blocks) == 1
    assert [warning.code for warning in warnings] == ["trailing-bytes"]


def test_walk_ignores_zero_tail():
    # Arrange - a few zero bytes at the end are not an error
    data = psd_stream(("Lr16", b"ab")) + b"\x00" * 3

    # Act
    blocks, warnings = parse(data)

    # Assert
    assert len(blocks) == 1
    assert warnings == ()


def test_walk_drops_block_whose_length_overruns():
    # Arrange - the block declares 9999 B, the stream holds barely a dozen
    data = psd_block("Lr16", b"ab", declared_length=9999)

    # Act
    blocks, warnings = parse(data)

    # Assert
    assert blocks == ()
    assert [warning.code for warning in warnings] == ["length-overrun"]


def test_walk_stops_on_unknown_signature():
    # Arrange
    data = psd_stream(("Lr16", b"ab")) + b"ZZZZkey\x00" + b"\x00" * 8

    # Act
    blocks, warnings = parse(data)

    # Assert
    assert len(blocks) == 1
    assert [warning.code for warning in warnings] == ["unknown-signature"]


def test_walk_warns_on_mixed_byte_order_but_continues():
    # Arrange
    data = psd_stream(("Lr16", b"ab"), byte_order="<") + psd_stream(
        ("cinf", b"cd"), byte_order=">"
    )

    # Act
    blocks, warnings = parse(data)

    # Assert
    assert [block.key for block in blocks] == ["Lr16", "cinf"]
    assert "mixed-byte-order" in [warning.code for warning in warnings]


def test_walk_on_empty_window_returns_nothing():
    # Arrange / Act
    blocks, warnings = parse(b"")

    # Assert
    assert blocks == ()
    assert warnings == ()


def test_warning_carries_offset():
    # Arrange
    data = psd_stream(("Lr16", b"ab")) + b"ZZZZkey\x00" + b"\x00" * 8

    # Act
    _, (warning,) = parse(data)

    # Assert
    assert warning.offset == 16
    assert "ZZZZ" in warning.detail
