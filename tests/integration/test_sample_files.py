"""
The contract of the sample catalogue.

Every case in `tests/sample_files.py` claims to reproduce one real-world
situation. A generated file that quietly stopped reproducing it would make
every test built on it pass for the wrong reason, so each claim is asserted
here, once, against the production code path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.sample_files import CASES, GEOMETRY, build
from tiff_analyzer.document import TiffDocument
from tiff_analyzer.optimize import OptimizeError, optimize
from tiff_analyzer.psd_analyzer import ImageSourceDataAnalyzer, TiffPhotoshopAnalyzer
from tiff_analyzer.psd_codec import RAW, RLE, ZIP_PREDICTED, decode_channel
from tiff_analyzer.psd_layers import read_layer_stack

#: Cases the optimizer is expected to refuse outright.
REFUSED = {"photoshop-not-last", "no-photoshop"}

#: Cases where every channel is already packed as tightly as we can pack it.
NOTHING_TO_GAIN = {"packed-layers", "rle-layers"}


# ============================================================================
# EVERY CASE
# ============================================================================


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_case_is_a_readable_tiff(name: str, sample_file):
    # Act
    path = sample_file(name)

    # Assert
    with TiffDocument(path) as document:
        assert document.image_info.width > 0
        assert document.file_size == path.stat().st_size


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_case_has_a_summary(name: str):
    # Assert - the catalogue is documentation as much as it is fixtures
    assert CASES[name].summary.endswith(".")
    assert len(CASES[name].summary) > 30


@pytest.mark.parametrize("name", sorted(set(CASES) - {"no-photoshop"}))
def test_photoshop_block_parses_without_warnings(name: str, sample_file):
    # Arrange
    path = sample_file(name)

    # Act
    with TiffDocument(path) as document:
        analysis = TiffPhotoshopAnalyzer(ImageSourceDataAnalyzer()).analyze(document)

    # Assert - a warning here means the generator wrote something we cannot read
    assert analysis.found
    assert analysis.warnings == ()


@pytest.mark.parametrize("name", sorted(set(CASES) - REFUSED - NOTHING_TO_GAIN))
def test_optimizing_verifies(name: str, sample_file, tmp_path: Path):
    # Arrange
    path = sample_file(name)

    # Act
    result = optimize(path, tmp_path / "result.tif", zip_fallback=True)

    # Assert
    assert result.verified, result.comparison
    assert path.read_bytes() == build(name), "the original must not be touched"


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_refused_cases_are_refused(name: str, sample_file, tmp_path: Path):
    # Arrange
    path = sample_file(name)
    output = tmp_path / "result.tif"

    # Act / Assert
    with pytest.raises(OptimizeError):
        optimize(path, output)

    assert not output.exists()


# ============================================================================
# WHAT EACH CASE CLAIMS
# ============================================================================


def _channels(path: Path):
    with TiffDocument(path) as document:
        analysis = TiffPhotoshopAnalyzer(ImageSourceDataAnalyzer()).analyze(document)
        reader = document.photoshop_source_reader()

        assert reader is not None, "the file has no tag 37724"

        try:
            stack = read_layer_stack(analysis, reader)

            assert stack is not None, "the file has no layer section"

            return [
                (channel, reader.read_at(channel.pixel_offset, channel.pixel_bytes))
                for layer in stack.layers
                for channel in layer.channels
            ]
        finally:
            reader.close()


def test_raw_layers_are_stored_raw(sample_file):
    # Act
    channels = _channels(sample_file("raw-layers"))

    # Assert
    assert [channel.compression for channel, _ in channels] == [RAW, RAW, RAW]


def test_rle_layers_decode_to_the_full_row_count(sample_file):
    # Arrange - the encoder lives in the test tree, the decoder in the package,
    # so this is the one place the two are compared
    channels = _channels(sample_file("rle-layers"))

    # Assert
    assert [channel.compression for channel, _ in channels] == [RLE, RLE, RLE]

    for _channel, data in channels:
        plain = decode_channel(data, compression=RLE, geometry=GEOMETRY)

        assert len(plain) == GEOMETRY.pixel_bytes


def test_packed_layers_are_already_predicted(sample_file):
    # Act
    channels = _channels(sample_file("packed-layers"))

    # Assert
    assert {channel.compression for channel, _ in channels} == {ZIP_PREDICTED}


def test_mixed_layers_carry_two_methods(sample_file):
    # Act
    channels = _channels(sample_file("mixed-layers"))

    # Assert
    assert [channel.compression for channel, _ in channels] == [
        ZIP_PREDICTED,
        RAW,
        RAW,
    ]


def test_adjustment_mask_has_no_geometry(sample_file):
    # Act
    channels = _channels(sample_file("adjustment-mask"))

    # Assert - a 0x0 rectangle with a mask channel carrying all the bytes
    ((channel, _data),) = channels

    assert channel.channel_id == -2
    assert channel.pixel_bytes > 1000


def test_adjustment_mask_needs_zip_fallback(sample_file, tmp_path: Path):
    # Arrange
    path = sample_file("adjustment-mask")

    # Act
    plain = optimize(path, tmp_path / "plain.tif")
    fallback = optimize(path, tmp_path / "fallback.tif", zip_fallback=True)

    # Assert
    assert plain.channels_changed == 0
    assert plain.skipped
    assert fallback.channels_changed == 1
    assert fallback.size_after < fallback.size_before


def test_grouped_layers_nest(sample_file):
    # Arrange
    path = sample_file("grouped-layers")

    # Act
    with TiffDocument(path) as document:
        analysis = TiffPhotoshopAnalyzer(ImageSourceDataAnalyzer()).analyze(document)
        reader = document.photoshop_source_reader()

        assert reader is not None, "the file has no tag 37724"

        try:
            stack = read_layer_stack(analysis, reader)
        finally:
            reader.close()

    # Assert
    assert stack is not None
    assert [layer.depth for layer in stack.layers] == [0, 1, 0]
    assert stack.layers[1].name == "Inside the group"


def test_large_document_container_uses_eight_byte_lengths(sample_file):
    # Arrange
    path = sample_file("large-document-container")

    # Act
    with TiffDocument(path) as document:
        analysis = TiffPhotoshopAnalyzer(ImageSourceDataAnalyzer()).analyze(document)

    # Assert - a 16-byte header is what an 8-byte length looks like from outside
    block = next(item for item in analysis.blocks if item.key == "Lr16")

    assert block.header_size == 16


def test_smart_object_holds_a_whole_psb(sample_file, tmp_path: Path):
    # Arrange
    path = sample_file("smart-object")

    # Act
    result = optimize(path, tmp_path / "result.tif")

    # Assert - the channels found are the ones inside the embedded file
    assert result.channels_total == 3
    assert result.channels_changed == 3
    assert result.verified


def test_compressible_image_shrinks_only_with_the_flag(sample_file, tmp_path: Path):
    # Arrange
    path = sample_file("compressible-image")

    # Act
    without = optimize(path, tmp_path / "without.tif")
    with_flag = optimize(path, tmp_path / "with.tif", image_data=True)

    # Assert
    assert without.image_before == 0
    assert with_flag.image_after < with_flag.image_before


def test_compressed_image_is_a_note_not_a_failure(sample_file, tmp_path: Path):
    # Arrange
    path = sample_file("compressed-image")

    # Act
    result = optimize(path, tmp_path / "result.tif", image_data=True)

    # Assert - the layers still get done; only the pixels are skipped
    assert result.channels_changed == 3
    assert result.verified
    assert any("image pixels skipped" in note for note in result.notes)


@pytest.mark.parametrize("name", sorted(NOTHING_TO_GAIN))
def test_nothing_to_gain_writes_no_file(name: str, sample_file, tmp_path: Path):
    # Arrange
    path = sample_file(name)
    output = tmp_path / "result.tif"

    # Act
    result = optimize(path, output)

    # Assert
    assert result.channels_changed == 0
    assert result.skipped
    assert not output.exists()
