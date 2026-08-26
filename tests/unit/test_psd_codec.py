"""Tests for the PSD/PSB channel codec."""

from __future__ import annotations

import zlib

import numpy as np
import pytest

from optiff.psd_codec import (
    RAW,
    RLE,
    ZIP,
    ZIP_PREDICTED,
    ChannelGeometry,
    CodecError,
    decode_channel,
    encode_channel,
    predict,
    recompress_channel,
    unpredict,
)


def pixels16(values: list[int]) -> bytes:
    return np.array(values, dtype=">u2").tobytes()


def geometry(width: int, rows: int = 1, bpp: int = 2) -> dict:
    return {"geometry": ChannelGeometry(width, rows, bpp)}


# ============================================================================
# PREDYKCJA
# ============================================================================


def test_predict_and_unpredict_are_inverse_16bit():
    # Arrange
    data = np.array([[100, 105, 90, 32000, 65535]], dtype=">u2")

    # Act / Assert
    assert unpredict(predict(data)).tolist() == data.tolist()


def test_predict_and_unpredict_are_inverse_8bit():
    # Arrange
    data = np.array([[7, 3, 250, 9, 0]], dtype="u1")

    # Act / Assert
    assert unpredict(predict(data)).tolist() == data.tolist()


def test_prediction_wraps_around_modulo():
    # Arrange - a negative difference must wrap in the dtype without loss
    data = np.array([[0, 65535, 0]], dtype=">u2")

    # Act / Assert
    assert unpredict(predict(data)).tolist() == data.tolist()


def test_prediction_is_per_row():
    # Arrange - the second row restarts, it does not continue the first
    data = np.array([[10, 11], [200, 201]], dtype=">u2")

    # Act
    deltas = predict(data)

    # Assert
    assert deltas[1, 0] == 200
    assert unpredict(deltas).tolist() == data.tolist()


# ============================================================================
# RUNDY W OBIE STRONY
# ============================================================================


@pytest.mark.parametrize("compression", [RAW, ZIP, ZIP_PREDICTED])
@pytest.mark.parametrize("bpp", [1, 2])
def test_round_trip(compression, bpp):
    # Arrange
    rng = np.random.default_rng(7)
    width, rows = 32, 8
    dtype = ">u2" if bpp == 2 else "u1"
    limit = 65535 if bpp == 2 else 255
    source = rng.integers(0, limit, size=(rows, width)).astype(dtype).tobytes()

    # Act
    packed = encode_channel(
        source, compression=compression, geometry=ChannelGeometry(width, rows, bpp)
    )
    plain = decode_channel(
        packed, compression=compression, geometry=ChannelGeometry(width, rows, bpp)
    )

    # Assert
    assert plain == source


def test_smooth_data_compresses_well_with_prediction():
    # Arrange - a gradient is the best case for prediction
    width, rows = 512, 64
    gradient = np.tile(np.arange(width, dtype=">u2"), (rows, 1)).tobytes()

    # Act
    plain_zip = encode_channel(gradient, compression=ZIP, **geometry(width, rows))
    predicted = encode_channel(
        gradient, compression=ZIP_PREDICTED, **geometry(width, rows)
    )

    # Assert
    assert len(predicted) < len(plain_zip)
    assert len(predicted) < len(gradient) // 100


# ============================================================================
# RLE
# ============================================================================


def test_decode_rle_with_row_table():
    # Arrange - jeden row: literal "ABC" plus 4x "X"
    row = bytes([2, 65, 66, 67, 253, 88])
    data = len(row).to_bytes(4, "big") + row

    # Act
    plain = decode_channel(
        data, compression=RLE, geometry=ChannelGeometry(7, 1, 1), large=True
    )

    # Assert
    assert plain == b"ABCXXXX"


def test_decode_rle_short_row_table_is_rejected():
    # Act / Assert
    with pytest.raises(CodecError, match="row length table"):
        decode_channel(b"\x00", compression=RLE, geometry=ChannelGeometry(4, 2, 1))


def test_decode_rle_wrong_row_length_is_rejected():
    # Arrange - the row unpacks to 3 B while we expect 7
    row = bytes([2, 65, 66, 67])
    data = len(row).to_bytes(4, "big") + row

    # Act / Assert
    with pytest.raises(CodecError, match="RLE row data"):
        decode_channel(data, compression=RLE, geometry=ChannelGeometry(7, 1, 1))


def test_encoding_rle_is_refused():
    # Act / Assert
    with pytest.raises(CodecError, match="writing RLE"):
        encode_channel(b"\x00" * 8, compression=RLE, **geometry(4))


# ============================================================================
# ERRORS
# ============================================================================


def test_raw_length_must_match_geometry():
    # Act / Assert
    with pytest.raises(CodecError, match="RAW channel"):
        decode_channel(b"\x00" * 5, compression=RAW, **geometry(3))


def test_predicted_length_must_match_geometry():
    # Arrange
    packed = zlib.compress(b"\x00" * 10)

    # Act / Assert
    with pytest.raises(CodecError, match="expected"):
        decode_channel(packed, compression=ZIP_PREDICTED, **geometry(3))


def test_unknown_compression_is_rejected():
    # Act / Assert
    with pytest.raises(CodecError, match="unknown compression method"):
        decode_channel(b"", compression=99, **geometry(1))

    with pytest.raises(CodecError, match="unknown compression method"):
        encode_channel(b"\x00\x00", compression=99, **geometry(1))


def test_unsupported_depth_is_rejected():
    # Act / Assert - a 32-bit channel uses a different prediction scheme
    with pytest.raises(CodecError, match="unsupported depth"):
        encode_channel(
            b"\x00" * 16,
            compression=ZIP_PREDICTED,
            geometry=ChannelGeometry(4, 1, 4),
        )


# ============================================================================
# RECOMPRESSION
# ============================================================================


def test_recompress_returns_pixels_for_verification():
    # Arrange
    source = pixels16([5, 9, 9, 1])

    # Act
    packed, plain = recompress_channel(
        source, source=RAW, target=ZIP_PREDICTED, **geometry(4)
    )

    # Assert
    assert plain == source
    assert decode_channel(packed, compression=ZIP_PREDICTED, **geometry(4)) == source


def test_recompress_shrinks_uniform_data():
    # Arrange
    source = b"\x00\x64" * 4096

    # Act
    packed, _ = recompress_channel(
        source, source=RAW, target=ZIP_PREDICTED, **geometry(4096)
    )

    # Assert
    assert len(packed) < len(source) // 50


def test_recompress_from_zip_to_predicted():
    # Arrange
    source = pixels16(list(range(64)))
    packed_zip = encode_channel(source, compression=ZIP, **geometry(64))

    # Act
    packed, plain = recompress_channel(
        packed_zip, source=ZIP, target=ZIP_PREDICTED, **geometry(64)
    )

    # Assert
    assert plain == source
    assert len(packed) < len(packed_zip)
