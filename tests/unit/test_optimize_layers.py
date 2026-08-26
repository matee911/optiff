"""
Tests for recompressing the layer section.

The point: after a rebuild the section must parse and yield the same pixels.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

from optiff.optimize_layers import (
    HEADER,
    ChannelResult,
    _decode,
    _geometry,
    plan_layer_section,
)
from optiff.psd_codec import (
    RAW,
    RLE,
    ZIP_PREDICTED,
    ChannelGeometry,
    decode_channel,
)
from optiff.psd_layers import Layer, LayerChannel, parse_layers
from optiff.readers import BytesReader
from optiff.segments import materialise, total_size
from tests.unit.builders import layer_record, layer_section

WIDTH, ROWS = 64, 16
PIXEL_BYTES = WIDTH * ROWS * 2


def pixels(seed: int) -> bytes:
    """Smooth 16-bit data, realistic for a photographic layer."""
    rng = np.random.default_rng(seed)
    base = np.cumsum(rng.integers(-3, 4, size=(ROWS, WIDTH)), axis=1)

    return (base % 65536).astype(">u2").tobytes()


def raw_channel(data: bytes) -> bytes:
    return RAW.to_bytes(HEADER, "little") + data


def build_section(*channel_data: bytes, name: str = "Background") -> bytes:
    """A section with one layer and the given RAW channels."""
    channels = tuple(
        (index, len(data) + HEADER) for index, data in enumerate(channel_data)
    )

    return layer_section(
        layer_record(name=name, bounds=(0, 0, ROWS, WIDTH), channels=channels)
    ) + b"".join(raw_channel(data) for data in channel_data)


def rebuild(source: bytes, **kwargs):
    """Parsuje, planuje, materializuje i parsuje ponownie."""
    reader = BytesReader(source)
    stack = parse_layers(reader, 0, len(source))

    plan = plan_layer_section(
        reader, stack, 0, len(source), bpp=2, byte_order="<", **kwargs
    )

    rebuilt = materialise(plan.segments, reader)

    return plan, rebuilt, parse_layers(BytesReader(rebuilt), 0, len(rebuilt))


# ============================================================================
# ROUND-TRIP
# ============================================================================


def test_rebuilt_section_parses_and_shrinks():
    # Arrange
    source = build_section(pixels(1), pixels(2), pixels(3))

    # Act
    _plan, rebuilt, stack = rebuild(source)

    # Assert
    assert len(rebuilt) < len(source)
    assert len(stack.layers) == 1
    assert stack.is_complete


def test_pixels_survive_round_trip():
    # Arrange
    originals = [pixels(1), pixels(2), pixels(3)]
    source = build_section(*originals)

    # Act
    _plan, rebuilt, stack = rebuild(source)

    # Assert
    reader = BytesReader(rebuilt)
    geometry = ChannelGeometry(WIDTH, ROWS, 2)

    for channel, original in zip(stack.layers[0].channels, originals, strict=True):
        assert channel.compression is not None

        plain = decode_channel(
            reader.read_at(channel.data_offset + HEADER, channel.pixel_bytes),
            compression=channel.compression,
            geometry=geometry,
        )

        assert plain == original


def test_channel_sizes_are_patched_in_the_record():
    # Arrange
    source = build_section(pixels(1), pixels(2))

    # Act
    plan, _rebuilt, stack = rebuild(source)

    # Assert - the sizes in the record must match the new data
    assert [c.size for c in stack.layers[0].channels] == [
        item.after for item in plan.results
    ]


def test_compression_code_is_updated():
    # Arrange
    source = build_section(pixels(1))

    # Act
    _plan, _rebuilt, stack = rebuild(source)

    # Assert
    assert stack.layers[0].channels[0].compression == ZIP_PREDICTED


def test_layer_metadata_is_untouched():
    # Arrange
    source = build_section(pixels(1), name="Ünïcode ✓ läyer")

    # Act
    _plan, _rebuilt, stack = rebuild(source)

    # Assert
    layer = stack.layers[0]
    assert layer.name == "Ünïcode ✓ läyer"
    assert (layer.width, layer.height) == (WIDTH, ROWS)
    assert layer.blend_mode == "norm"
    assert layer.opacity == 255


def test_plan_size_matches_materialised_output():
    # Arrange
    source = build_section(pixels(1), pixels(2))

    # Act
    plan, rebuilt, _stack = rebuild(source)

    # Assert - the length computed without reading data must match
    assert total_size(plan.segments) == len(rebuilt)


# ============================================================================
# DECYZJE
# ============================================================================


def test_incompressible_channel_is_left_alone():
    # Arrange - white noise cannot be compressed
    rng = np.random.default_rng(0)
    noise = rng.integers(0, 65536, size=(ROWS, WIDTH)).astype(">u2").tobytes()
    source = build_section(noise)

    # Act
    plan, _rebuilt, stack = rebuild(source)

    # Assert
    assert plan.results[0].changed is False
    assert stack.layers[0].channels[0].compression == RAW


def test_already_predicted_channel_is_not_touched():
    # Arrange
    source = build_section(pixels(1))
    plan_one, once, _ = rebuild(source)

    # Act - a second pass over already compressed material
    reader = BytesReader(once)
    stack = parse_layers(reader, 0, len(once))
    plan_two = plan_layer_section(reader, stack, 0, len(once), bpp=2, byte_order="<")

    # Assert
    assert plan_one.changed is True
    assert plan_two.changed is False


def test_empty_channel_is_copied_verbatim():
    # Arrange - a channel with no data has not even a compression header
    source = layer_section(
        layer_record(name="empty", bounds=(0, 0, 0, 0), channels=((0, 0),))
    )

    # Act
    plan, rebuilt, stack = rebuild(source)

    # Assert
    assert rebuilt == source
    assert plan.results == []
    assert stack.layers[0].channels[0].size == 0


def test_channel_without_geometry_is_copied_verbatim():
    # Arrange - an adjustment layer: 0x0 rectangle, but the channel has data
    payload = b"\x00" * 32
    source = layer_section(
        layer_record(
            name="korekta",
            bounds=(0, 0, 0, 0),
            channels=((0, len(payload) + HEADER),),
        )
    ) + raw_channel(payload)

    # Act
    plan, rebuilt, _stack = rebuild(source)

    # Assert
    assert rebuilt == source
    assert plan.results[0].changed is False
    assert plan.results[0].digest_of == "bytes"


def test_sources_filter_is_respected():
    # Arrange
    source = build_section(pixels(1))

    # Act - nothing may be touched
    reader = BytesReader(source)
    stack = parse_layers(reader, 0, len(source))
    plan = plan_layer_section(
        reader, stack, 0, len(source), bpp=2, byte_order="<", sources=()
    )

    # Assert
    assert plan.changed is False
    assert materialise(plan.segments, reader) == source


# ============================================================================
# REPORTING
# ============================================================================


def test_plan_reports_savings():
    # Arrange
    source = build_section(pixels(1), pixels(2))

    # Act
    plan, _rebuilt, _stack = rebuild(source)

    # Assert
    assert plan.before > plan.after
    assert plan.saved == plan.before - plan.after
    assert plan.changed is True


def test_digests_are_of_decoded_pixels():
    # Arrange
    original = pixels(1)
    source = build_section(original)

    # Act
    plan, _rebuilt, _stack = rebuild(source)

    # Assert
    assert plan.results[0].digest == hashlib.sha256(original).hexdigest()
    assert plan.results[0].digest_of == "pixels"


def test_digest_keys_are_positional():
    # Arrange - two channels sharing a name must not overwrite each other
    source = build_section(pixels(1), pixels(2), pixels(3), pixels(4))

    # Act
    plan, _rebuilt, _stack = rebuild(source)

    # Assert
    assert len(plan.digests()) == len(plan.results)


@pytest.mark.parametrize("byte_order", ["<", ">"])
def test_works_in_both_byte_orders(byte_order):
    # Arrange - build the section in the given byte order
    order = "little" if byte_order == "<" else "big"
    data = pixels(5)

    record = (
        b"".join(v.to_bytes(4, order, signed=True) for v in (0, 0, ROWS, WIDTH))
        + (1).to_bytes(2, order)
        + (0).to_bytes(2, order, signed=True)
        + (len(data) + HEADER).to_bytes(4, order)
        + (b"8BIM"[::-1] if byte_order == "<" else b"8BIM")
        + (b"norm"[::-1] if byte_order == "<" else b"norm")
        + bytes([255, 0, 0, 0])
        + (8).to_bytes(4, order)
        + (0).to_bytes(4, order)
        + (0).to_bytes(4, order)
    )
    source = (
        (1).to_bytes(2, order, signed=True)
        + record
        + RAW.to_bytes(HEADER, order)
        + data
    )

    reader = BytesReader(source)
    stack = parse_layers(reader, 0, len(source), byte_order=byte_order)

    # Act
    plan = plan_layer_section(
        reader, stack, 0, len(source), bpp=2, byte_order=byte_order
    )
    rebuilt = materialise(plan.segments, reader)
    again = parse_layers(BytesReader(rebuilt), 0, len(rebuilt), byte_order=byte_order)

    # Assert
    channel = again.layers[0].channels[0]

    assert channel.compression is not None

    plain = decode_channel(
        BytesReader(rebuilt).read_at(channel.data_offset + HEADER, channel.pixel_bytes),
        compression=channel.compression,
        geometry=ChannelGeometry(WIDTH, ROWS, 2),
    )
    assert plain == data
    assert len(rebuilt) < len(source)


# ============================================================================
# ChannelResult.saved / _geometry / _decode
# ============================================================================


def test_channel_result_saved_is_before_minus_after():
    result = ChannelResult(
        layer="0:Sky",
        channel="0:R",
        before=100,
        after=60,
        compression=RAW,
        digest="abc",
        digest_of="pixels",
    )

    assert result.saved == 40


def _layer(*, width, height) -> Layer:
    return Layer(
        index=0,
        name="L",
        top=0,
        left=0,
        bottom=height,
        right=width,
        channels=(),
        blend_mode="norm",
        opacity=255,
        clipping=0,
        flags=0,
    )


def test_geometry_rejects_raw_data_of_the_wrong_size():
    channel = LayerChannel(
        channel_id=0, size=HEADER + 3, compression=RAW
    )  # 3 B of pixels

    geometry = _geometry(_layer(width=WIDTH, height=ROWS), channel, bpp=2)

    assert geometry is None


def test_decode_returns_none_when_the_channel_cannot_be_decoded():
    # Too short to be a valid RLE row-length table for a 4-row image.
    channel = LayerChannel(channel_id=0, size=HEADER + 2, compression=RLE)
    geometry = ChannelGeometry(4, 4, 2)
    reader = BytesReader(bytes(HEADER) + b"\x00\x00")

    assert _decode(reader, channel, geometry) is None
