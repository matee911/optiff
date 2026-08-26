"""Tests for the layer record parser."""

from __future__ import annotations

import pytest

from optiff.psd_layers import Layer, parse_layers
from optiff.readers import BytesReader
from tests.unit.builders import (
    layer_extra_block,
    layer_record,
    layer_section,
)


def parse(data: bytes, *, large: bool = False):
    return parse_layers(BytesReader(data), 0, len(data), large=large)


def section_divider(kind: int) -> bytes:
    return layer_extra_block("lsct", kind.to_bytes(4, "little"))


# ============================================================================
# PODSTAWY
# ============================================================================


def test_reads_single_layer():
    # Arrange
    data = layer_section(layer_record(name="Background", bounds=(0, 0, 100, 200)))

    # Act
    stack = parse(data)

    # Assert
    assert len(stack.layers) == 1
    assert stack.layers[0].name == "Background"
    assert stack.layers[0].width == 200
    assert stack.layers[0].height == 100
    assert stack.is_exact


def test_reads_multiple_layers_in_order():
    # Arrange
    data = layer_section(
        layer_record(name="Dol"),
        layer_record(name="Srodek"),
        layer_record(name="Gora"),
    )

    # Act
    stack = parse(data)

    # Assert
    assert [layer.name for layer in stack.layers] == ["Dol", "Srodek", "Gora"]
    assert [layer.index for layer in stack.layers] == [0, 1, 2]


def test_unicode_name_wins_over_pascal():
    # Arrange
    data = layer_section(layer_record(name="Ünïcode ✓ läyer"))

    # Act
    stack = parse(data)

    # Assert
    assert stack.layers[0].name == "Ünïcode ✓ läyer"


def test_pascal_name_is_fallback_without_luni():
    # Arrange
    data = layer_section(layer_record(name="BezUnicode", unicode=False))

    # Act
    stack = parse(data)

    # Assert
    assert stack.layers[0].name == "BezUnicode"


def test_negative_count_means_transparency():
    # Arrange
    data = layer_section(layer_record(), count=-1)

    # Act
    stack = parse(data)

    # Assert
    assert stack.has_transparency is True
    assert stack.declared_count == -1
    assert len(stack.layers) == 1


def test_channel_sizes_are_summed():
    # Arrange
    data = (
        layer_section(layer_record(channels=((0, 100), (1, 200), (2, 300))))
        + b"\x00" * 600
    )

    # Act
    stack = parse(data)

    # Assert
    assert stack.layers[0].data_size == 600
    assert stack.channel_bytes == 600
    assert stack.is_exact


def test_channel_names():
    # Arrange
    data = layer_section(layer_record(channels=((-1, 0), (0, 0), (1, 0))))

    # Act
    channels = parse(data).layers[0].channels

    # Assert
    assert [channel.name for channel in channels] == ["alpha", "R", "G"]


# ============================================================================
# ATRYBUTY
# ============================================================================


@pytest.mark.parametrize(
    ("blend", "expected"),
    [("norm", "Normal"), ("mul ", "Multiply"), ("zzzz", "zzzz")],
)
def test_blend_mode_name(blend, expected):
    # Arrange
    data = layer_section(layer_record(blend=blend))

    # Assert
    assert parse(data).layers[0].blend_mode_name == expected


@pytest.mark.parametrize(
    ("opacity", "percent"), [(255, 100), (128, 50), (26, 10), (0, 0)]
)
def test_opacity_percent(opacity, percent):
    # Arrange
    data = layer_section(layer_record(opacity=opacity))

    # Assert
    assert parse(data).layers[0].opacity_percent == percent


def test_hidden_flag():
    # Arrange
    data = layer_section(
        layer_record(name="visible", flags=0),
        layer_record(name="ukryta", flags=0x02),
    )

    # Act
    layers = parse(data).layers

    # Assert
    assert layers[0].is_hidden is False
    assert layers[1].is_hidden is True


def test_transparency_locked_flag():
    # Arrange
    data = layer_section(layer_record(flags=0x01))

    # Assert
    assert parse(data).layers[0].is_transparency_locked is True


def test_empty_bounds_layer():
    # Arrange - adjustment layers carry a zero rectangle
    data = layer_section(layer_record(bounds=(0, 0, 0, 0)))

    # Assert
    assert parse(data).layers[0].is_empty is True


def test_extra_keys_are_listed():
    # Arrange
    data = layer_section(layer_record(extras=layer_extra_block("lclr", b"\x00" * 8)))

    # Act
    keys = parse(data).layers[0].extra_keys

    # Assert
    assert "lclr" in keys
    assert "luni" in keys


# ============================================================================
# CHANNEL COMPRESSION
# ============================================================================


def channel_data(*codes: int, payload: int = 8) -> bytes:
    """Channel data: a 2-byte compression code plus filler."""
    return b"".join(code.to_bytes(2, "little") + b"\x00" * payload for code in codes)


def test_channel_compression_is_read():
    # Arrange - three channels of 10 B each, every one with its own code
    data = layer_section(
        layer_record(channels=((0, 10), (1, 10), (2, 10)))
    ) + channel_data(1, 1, 1)

    # Act
    layer = parse(data).layers[0]

    # Assert
    assert [c.compression for c in layer.channels] == [1, 1, 1]
    assert layer.compression_name == "RLE"
    assert parse(data).is_exact


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0, "RAW"),
        (1, "RLE"),
        (2, "ZIP"),
        (3, "ZIP with prediction"),
        (99, "method 99"),
    ],
)
def test_compression_names(code, expected):
    # Arrange
    data = layer_section(layer_record(channels=((0, 10),))) + channel_data(code)

    # Assert
    assert parse(data).layers[0].channels[0].compression_name == expected


def test_mixed_compression_is_reported():
    # Arrange
    data = layer_section(layer_record(channels=((0, 10), (1, 10)))) + channel_data(0, 3)

    # Act
    layer = parse(data).layers[0]

    # Assert
    assert layer.compression_name == "mixed (RAW, ZIP with prediction)"


def test_compression_offsets_follow_channel_sizes():
    # Arrange - the second layer must land on ITS OWN data, not somebody else's
    data = (
        layer_section(
            layer_record(name="a", channels=((0, 10),)),
            layer_record(name="b", channels=((0, 10),)),
        )
        + channel_data(1)
        + channel_data(3)
    )

    # Act
    layers = parse(data).layers

    # Assert
    assert layers[0].channels[0].compression == 1
    assert layers[1].channels[0].compression == 3


def test_empty_channel_has_unknown_compression():
    # Arrange - a channel with no data has not even a header
    data = layer_section(layer_record(channels=((0, 0),)))

    # Act
    layer = parse(data).layers[0]

    # Assert
    assert layer.channels[0].compression is None
    assert layer.channels[0].compression_name == "unknown"
    assert layer.compression_name == "none"


def test_pixel_bytes_excludes_compression_header():
    # Arrange
    data = layer_section(layer_record(channels=((0, 10),))) + channel_data(1)

    # Act
    layer = parse(data).layers[0]

    # Assert
    assert layer.channels[0].size == 10
    assert layer.channels[0].pixel_bytes == 8
    assert layer.pixel_bytes == 8


def test_compression_is_read_big_endian_too():
    # Arrange - a raw PSD/PSB: the compression code is big-endian too
    data = (1).to_bytes(2, "big") + _be_record() + (3).to_bytes(2, "big") + b"\x00" * 8

    # Act
    stack = parse_layers(BytesReader(data), 0, len(data), byte_order=">")

    # Assert
    assert stack.layers[0].channels[0].compression == 3


def _be_record() -> bytes:
    """A minimal big-endian layer record with one 10 B channel."""
    pascal = bytes([1]) + b"x" + b"\x00\x00"
    extra = (0).to_bytes(4, "big") + (0).to_bytes(4, "big") + pascal

    return (
        b"".join(v.to_bytes(4, "big", signed=True) for v in (0, 0, 1, 1))
        + (1).to_bytes(2, "big")
        + (0).to_bytes(2, "big", signed=True)
        + (10).to_bytes(4, "big")
        + b"8BIM"
        + b"norm"
        + bytes([255, 0, 0, 0])
        + len(extra).to_bytes(4, "big")
        + extra
    )


# ============================================================================
# GRUPY
# ============================================================================


def test_group_nesting_depth():
    # Arrange - storage runs bottom-up: group end, contents, group header
    data = layer_section(
        layer_record(name="Background"),
        layer_record(name="</Layer group>", extras=section_divider(3)),
        layer_record(name="W grupie"),
        layer_record(name="Grupa", extras=section_divider(1)),
    )

    # Act
    layers = parse(data).layers

    # Assert
    assert [layer.section for layer in layers] == [
        "layer",
        "group end",
        "layer",
        "group open",
    ]
    assert [layer.depth for layer in layers] == [0, 0, 1, 0]


def test_group_end_takes_name_of_its_group():
    # Arrange - in the file the end marker is literally "</Layer group>"
    data = layer_section(
        layer_record(name="</Layer group>", extras=section_divider(3)),
        layer_record(name="W grupie"),
        layer_record(name="Color Grading", extras=section_divider(1)),
    )

    # Act
    layers = parse(data).layers

    # Assert
    assert layers[0].name == "Color Grading"
    assert layers[0].raw_name == "</Layer group>"
    assert layers[2].name == "Color Grading"


def test_nested_groups_pair_correctly():
    # Arrange - grupa w grupie
    data = layer_section(
        layer_record(name="</Layer group>", extras=section_divider(3)),
        layer_record(name="</Layer group>", extras=section_divider(3)),
        layer_record(name="Deeper"),
        layer_record(name="Wewnetrzna", extras=section_divider(1)),
        layer_record(name="Zewnetrzna", extras=section_divider(1)),
    )

    # Act
    layers = parse(data).layers

    # Assert
    assert [layer.name for layer in layers] == [
        "Zewnetrzna",
        "Wewnetrzna",
        "Deeper",
        "Wewnetrzna",
        "Zewnetrzna",
    ]
    assert [layer.depth for layer in layers] == [0, 1, 2, 1, 0]


def test_unmatched_group_end_keeps_literal_name():
    # Arrange - a group end with no header
    data = layer_section(layer_record(name="</Layer group>", extras=section_divider(3)))

    # Act
    layers = parse(data).layers

    # Assert
    assert layers[0].name == "</Layer group>"


def test_closed_group_is_recognised():
    # Arrange
    data = layer_section(layer_record(extras=section_divider(2)))

    # Assert
    assert parse(data).layers[0].section == "group closed"


# ============================================================================
# ERRORS
# ============================================================================


def test_truncated_section_reports_warning():
    # Arrange
    data = layer_section(layer_record())[:-10]

    # Act
    stack = parse(data)

    # Assert
    assert stack.warnings
    assert stack.warnings[0].code in (
        "layer-record-failed",
        "layers-size-mismatch",
    )


def test_empty_section_is_handled():
    # Act
    stack = parse(b"")

    # Assert
    assert stack.layers == ()
    assert stack.warnings[0].code == "layers-truncated"


def test_size_mismatch_is_reported():
    # Arrange - declare a channel but never append its data
    data = layer_section(layer_record(channels=((0, 999),)))

    # Act
    stack = parse(data)

    # Assert
    assert stack.is_exact is False
    assert [w.code for w in stack.warnings] == ["layers-size-mismatch"]


def test_bad_blend_signature_is_reported():
    # Arrange
    record = bytearray(layer_record())
    signature = record.index(b"MIB8")
    record[signature : signature + 4] = b"ZZZZ"

    # Act
    stack = parse(layer_section(bytes(record)))

    # Assert
    assert [w.code for w in stack.warnings] == ["layer-record-failed"]


def test_zero_layers():
    # Act
    stack = parse(layer_section())

    # Assert
    assert stack.layers == ()
    assert stack.declared_count == 0


def test_by_size_orders_descending():
    # Arrange
    data = (
        layer_section(
            layer_record(name="mala", channels=((0, 10),)),
            layer_record(name="duza", channels=((0, 900),)),
        )
        + b"\x00" * 910
    )

    # Act
    ordered = parse(data).by_size()

    # Assert
    assert [layer.name for layer in ordered] == ["duza", "mala"]


def test_layer_is_frozen():
    # Arrange
    layer = Layer(0, "x", 0, 0, 1, 1, (), "norm", 255, 0, 0)

    # Assert
    with pytest.raises(AttributeError):
        # The assignment is the subject of the test, so the type checker is
        # right to object and has to be told to stand down here. Writing it
        # as `setattr` instead only moves the argument to ruff, which rewrites
        # a constant `setattr` back into this line.
        layer.name = "other"  # pyrefly: ignore[read-only]
