"""Tests for comparing channel checksums, and for collecting them."""

from __future__ import annotations

import hashlib
import zlib

from optiff.domain import PhotoshopAnalysis
from optiff.optimize_layers import HEADER
from optiff.psd_codec import RAW, RLE, ZIP
from optiff.psd_layers import Layer, LayerChannel, LayerStack
from optiff.psd_links import LinkedFile, LinkedFiles
from optiff.readers import BytesReader
from optiff.verify import ChannelDigest, _collect, _digest, channel_digests, compare

#: The collaborators that would read this are monkeypatched out below, so
#: only its type needs to be right.
NO_ANALYSIS = PhotoshopAnalysis(
    found=False, signature=None, data_size=0, blocks=(), layer_count=None, warnings=()
)


def digest(
    where: str = "tiff",
    layer: str = "0:Background",
    channel: str = "0:R",
    value: str = "aaa",
    source: str = "pixels",
) -> ChannelDigest:
    return ChannelDigest(
        where=where, layer=layer, channel=channel, digest=value, source=source
    )


def test_identical_sets_pass():
    # Arrange
    items = [digest(), digest(channel="1:G", value="bbb")]

    # Act
    result = compare(items, items)

    # Assert
    assert result.ok
    assert result.total == 2
    assert result.problems == ()


def test_changed_digest_is_reported():
    # Arrange
    before = [digest(value="aaa")]
    after = [digest(value="zzz")]

    # Act
    result = compare(before, after)

    # Assert
    assert not result.ok
    assert "aaa" in result.problems[0]
    assert "zzz" in result.problems[0]


def test_missing_channel_is_reported():
    # Act
    result = compare([digest(), digest(channel="1:G")], [digest()])

    # Assert
    assert not result.ok
    assert "channel count: 2 in source, 1 in result" in result.problems


def test_extra_channel_is_reported():
    # Act
    result = compare([digest()], [digest(), digest(channel="1:G")])

    # Assert
    assert not result.ok
    assert "channel count: 1 in source, 2 in result" in result.problems


def test_reordered_channels_are_reported():
    # Arrange - channels must come out in the same order
    before = [digest(channel="0:R"), digest(channel="1:G")]
    after = [digest(channel="1:G"), digest(channel="0:R")]

    # Act
    result = compare(before, after)

    # Assert
    assert not result.ok
    assert any("mismatch" in problem for problem in result.problems)


def test_channel_moved_between_documents_is_reported():
    # Arrange - the same channel, but in a different embedded file
    before = [digest(where="tiff")]
    after = [digest(where="embedded:0:a.psb")]

    # Act
    result = compare(before, after)

    # Assert
    assert not result.ok


def test_empty_sets_are_equal():
    # Act / Assert
    assert compare([], []).ok


def test_every_difference_is_listed():
    # Arrange
    before = [digest(channel=f"{i}:R", value=f"a{i}") for i in range(3)]
    after = [digest(channel=f"{i}:R", value=f"b{i}") for i in range(3)]

    # Act
    result = compare(before, after)

    # Assert - we do not want to report only the first difference
    assert len(result.problems) == 3


# ============================================================================
# _digest
# ============================================================================


def _channel(*, compression, size, data_offset=0) -> LayerChannel:
    return LayerChannel(
        channel_id=0, size=size, compression=compression, data_offset=data_offset
    )


def _layer(*, width, height, channels) -> Layer:
    return Layer(
        index=0,
        name="L",
        top=0,
        left=0,
        bottom=height,
        right=width,
        channels=channels,
        blend_mode="norm",
        opacity=255,
        clipping=0,
        flags=0,
    )


def test_digest_without_a_rectangle_hashes_raw_pixels_for_raw_compression():
    data = b"hello raw bytes"
    channel = _channel(compression=RAW, size=len(data) + HEADER)
    reader = BytesReader(bytes(HEADER) + data)

    digest, source = _digest(
        reader, _layer(width=0, height=0, channels=(channel,)), channel, 2
    )

    assert source == "pixels"
    assert digest == hashlib.sha256(data).hexdigest()


def test_digest_without_a_rectangle_hashes_decompressed_zip():
    plain = b"some pixel bytes" * 4
    packed = zlib.compress(plain)
    channel = _channel(compression=ZIP, size=len(packed) + HEADER)
    reader = BytesReader(bytes(HEADER) + packed)

    digest, source = _digest(
        reader, _layer(width=0, height=0, channels=(channel,)), channel, 2
    )

    assert source == "pixels"
    assert digest == hashlib.sha256(plain).hexdigest()


def test_digest_falls_back_to_raw_bytes_when_zip_decompression_fails():
    garbage = b"not a zlib stream"
    channel = _channel(compression=ZIP, size=len(garbage) + HEADER)
    reader = BytesReader(bytes(HEADER) + garbage)

    digest, source = _digest(
        reader, _layer(width=0, height=0, channels=(channel,)), channel, 2
    )

    assert source == "bytes"
    assert digest == hashlib.sha256(bytes(HEADER) + garbage).hexdigest()


def test_digest_falls_back_to_raw_bytes_when_the_pixels_cannot_be_decoded():
    # Too short to be a valid RLE row-length table for a 4-row image.
    channel = _channel(compression=RLE, size=HEADER + 2)
    reader = BytesReader(bytes(HEADER) + b"\x00\x00")

    digest, source = _digest(
        reader, _layer(width=4, height=4, channels=(channel,)), channel, 2
    )

    assert source == "bytes"
    assert digest == hashlib.sha256(bytes(HEADER) + b"\x00\x00").hexdigest()


# ============================================================================
# _collect
# ============================================================================


def test_collect_skips_empty_channels():
    skipped = _channel(compression=None, size=0)
    kept = _channel(compression=RAW, size=HEADER + 3)
    layer = _layer(width=0, height=0, channels=(skipped, kept))
    stack = LayerStack(
        layers=(layer,), declared_count=1, has_transparency=False, consumed=0, total=0
    )
    reader = BytesReader(bytes(HEADER) + b"abc")

    found = _collect(reader, stack, bpp=2, where="tiff")

    assert len(found) == 1


# ============================================================================
# channel_digests
# ============================================================================


def _linked_file(*, kind: str, data_offset: int = 0, size: int = 0) -> LinkedFile:
    return LinkedFile(
        index=0,
        kind=kind,
        version=1,
        uid="",
        name="linked",
        file_type="8BPB",
        creator="",
        size=size,
        offset=0,
        record_size=0,
        has_descriptor=False,
        data_offset=data_offset,
    )


def test_channel_digests_skips_a_non_embedded_linked_file(monkeypatch):
    monkeypatch.setattr("optiff.verify.read_layer_stack", lambda analysis, reader: None)
    monkeypatch.setattr(
        "optiff.verify.read_linked_files",
        lambda analysis, reader: LinkedFiles(
            files=(_linked_file(kind="liFE"),), consumed=0, total=0
        ),
    )

    assert channel_digests(BytesReader(b""), analysis=NO_ANALYSIS) == []


def test_channel_digests_skips_an_embedded_file_that_fails_to_parse(monkeypatch):
    monkeypatch.setattr("optiff.verify.read_layer_stack", lambda analysis, reader: None)
    monkeypatch.setattr(
        "optiff.verify.read_linked_files",
        lambda analysis, reader: LinkedFiles(
            files=(_linked_file(kind="liFD", size=10),), consumed=0, total=0
        ),
    )

    assert (
        channel_digests(BytesReader(b"not a psd file..."), analysis=NO_ANALYSIS) == []
    )


def test_channel_digests_skips_an_embedded_file_with_no_layers(monkeypatch):
    # The exact minimal-PSB recipe from psd_file.parse_document's own doctest,
    # with an empty (size 0) Layer and Mask Information section.
    header = (
        b"8BPS"
        + (2).to_bytes(2, "big")
        + bytes(6)
        + (3).to_bytes(2, "big")
        + (600).to_bytes(4, "big")
        + (800).to_bytes(4, "big")
        + (16).to_bytes(2, "big")
        + (3).to_bytes(2, "big")
    )
    body = (
        (0).to_bytes(4, "big")
        + (4).to_bytes(4, "big")
        + b"abcd"
        + (0).to_bytes(8, "big")
        + (1).to_bytes(2, "big")
    )
    data = header + body

    monkeypatch.setattr("optiff.verify.read_layer_stack", lambda analysis, reader: None)
    monkeypatch.setattr(
        "optiff.verify.read_linked_files",
        lambda analysis, reader: LinkedFiles(
            files=(_linked_file(kind="liFD", size=len(data)),), consumed=0, total=0
        ),
    )

    assert channel_digests(BytesReader(data), analysis=NO_ANALYSIS) == []
