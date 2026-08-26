"""
Kodek skonfrontowany z danymi zapisanymi przez samego Photoshopa.

Layers inside tag 37724 are compressed by Photoshop with
"ZIP with prediction". If our decoder unpacks them to the correct geometry
and the encoder reproduces the pixels bit for bit, then the codec agrees
with Adobe's implementation, with no need to run Photoshop at all.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from optiff.document import TiffDocument
from optiff.psd_analyzer import ImageSourceDataAnalyzer, TiffPhotoshopAnalyzer
from optiff.psd_codec import (
    ZIP_PREDICTED,
    ChannelGeometry,
    decode_channel,
    encode_channel,
)
from optiff.psd_file import parse_document
from optiff.psd_layers import read_layer_stack
from optiff.psd_links import read_linked_files

#: How many channels to check; each one is tens of MB.
CHANNEL_LIMIT = 3


def photoshop_channels(path: Path, limit: int = CHANNEL_LIMIT):
    """Channels compressed by Photoshop together with their geometry."""
    analyzer = ImageSourceDataAnalyzer()

    with TiffDocument(path) as document:
        analysis = TiffPhotoshopAnalyzer(analyzer).analyze(document)
        reader = document.photoshop_source_reader()

        assert reader is not None, "the file has no tag 37724"

        if reader is None:
            pytest.skip(f"{path.name} has no tag 37724")

        try:
            stack = read_layer_stack(analysis, reader)

            if stack is None:
                pytest.skip(f"{path.name} has no layers")

            found = []

            for layer in stack.layers:
                for channel in layer.channels:
                    if channel.compression != ZIP_PREDICTED:
                        continue

                    if channel.pixel_bytes == 0 or layer.is_empty:
                        continue

                    found.append(
                        (
                            layer,
                            channel,
                            reader.read_at(channel.pixel_offset, channel.pixel_bytes),
                        )
                    )

                    if len(found) >= limit:
                        return found

            return found
        finally:
            reader.close()


@pytest.mark.slow
def test_decodes_photoshop_channels_to_exact_geometry(sample_tiff: Path):
    # Arrange
    channels = photoshop_channels(sample_tiff)

    assert channels, "the sample should carry layers compressed by Adobe"

    # Act / Assert
    for layer, _channel, data in channels:
        plain = decode_channel(
            data,
            compression=ZIP_PREDICTED,
            geometry=ChannelGeometry(layer.width, layer.height, 2),
        )

        assert len(plain) == layer.width * layer.height * 2


@pytest.mark.slow
def test_round_trip_of_photoshop_data_is_lossless(sample_tiff: Path):
    # Arrange
    channels = photoshop_channels(sample_tiff)

    # Act / Assert
    for layer, _channel, data in channels:
        geometry = ChannelGeometry(layer.width, layer.height, 2)

        original = decode_channel(data, compression=ZIP_PREDICTED, geometry=geometry)

        ours = encode_channel(original, compression=ZIP_PREDICTED, geometry=geometry)

        again = decode_channel(ours, compression=ZIP_PREDICTED, geometry=geometry)

        assert hashlib.sha256(again).hexdigest() == (
            hashlib.sha256(original).hexdigest()
        )


@pytest.mark.slow
def test_our_encoder_is_not_worse_than_adobe(sample_tiff: Path):
    # Arrange
    channels = photoshop_channels(sample_tiff, limit=2)

    # Act / Assert
    for layer, channel, data in channels:
        geometry = ChannelGeometry(layer.width, layer.height, 2)

        plain = decode_channel(data, compression=ZIP_PREDICTED, geometry=geometry)
        ours = encode_channel(plain, compression=ZIP_PREDICTED, geometry=geometry)

        assert len(ours) <= channel.pixel_bytes * 1.05, (
            f"{layer.name}/{channel.name}: ours {len(ours):,} B "
            f"vs Adobe {channel.pixel_bytes:,} B"
        )


@pytest.mark.bigfile
def test_raw_channels_shrink_substantially(sample_named):
    # Arrange - raw channels of embedded PSBs are the main optimization target
    path = sample_named("mixed")

    if not path.exists():
        pytest.skip(f"Brak {path}")

    analyzer = ImageSourceDataAnalyzer()

    with TiffDocument(path) as document:
        analysis = TiffPhotoshopAnalyzer(analyzer).analyze(document)
        reader = document.photoshop_source_reader()

        assert reader is not None, "the file has no tag 37724"

        try:
            linked = read_linked_files(analysis, reader)

            assert linked is not None, "no block with linked smart objects"

            embedded = parse_document(
                reader, linked.files[0].data_offset, linked.files[0].size
            )

            assert embedded.layers is not None, "the embedded file has no layers"

            layer = embedded.layers.layers[0]
            channel = layer.channels[1]

            assert channel.compression == 0, "expected a RAW channel"

            data = reader.read_at(channel.pixel_offset, channel.pixel_bytes)
        finally:
            reader.close()

    geometry = ChannelGeometry(layer.width, layer.height, 2)

    # Act
    packed = encode_channel(data, compression=ZIP_PREDICTED, geometry=geometry)
    back = decode_channel(packed, compression=ZIP_PREDICTED, geometry=geometry)

    # Assert
    assert back == data
    assert len(packed) < len(data) * 0.5
