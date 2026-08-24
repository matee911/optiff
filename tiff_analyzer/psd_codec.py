"""
Encoding and decoding of PSD/PSB channel data.

A channel starts with a 2-byte method code, followed by data in one of four
shapes:

- ``RAW`` (0) - pixels unchanged
- ``RLE`` (1) - PackBits per row, preceded by a table of row lengths
- ``ZIP`` (2) - deflate over the raw pixels
- ``ZIP_PREDICTED`` (3) - horizontal differencing, then deflate

The predicted variant was verified against data written by Photoshop itself:
decoding yields exactly ``width x height x bytes_per_sample``, and
`encode_channel(decode_channel(x)) `odtwarza piksele bit w bit.

Pixels are always big-endian, exactly as stored in the PSD/PSB file.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

import numpy as np

RAW = 0
RLE = 1
ZIP = 2
ZIP_PREDICTED = 3

#: Default deflate level. Photoshop uses a faster one; we stay below its
#: output size at 6.
DEFAULT_LEVEL = 6

#: 32-bit channels use a different prediction scheme (byte shuffling) that
#: we have not verified against real data.
SUPPORTED_DEPTHS = (1, 2)


class CodecError(ValueError):
    """Channel data could not be encoded or decoded."""


@dataclass(frozen=True)
class ChannelGeometry:
    """Channel shape; without it prediction cannot be reversed."""

    width: int
    rows: int
    bpp: int

    @property
    def pixel_bytes(self) -> int:
        """
        >>> ChannelGeometry(4954, 6192, 2).pixel_bytes
        61350336
        """
        return self.width * self.rows * self.bpp

    @property
    def dtype(self) -> str:
        if self.bpp not in SUPPORTED_DEPTHS:
            raise CodecError(f"unsupported depth: {self.bpp * 8} bit")

        return ">u2" if self.bpp == 2 else "u1"


def _as_rows(data: bytes, geometry: ChannelGeometry) -> np.ndarray:
    if len(data) != geometry.pixel_bytes:
        raise CodecError(
            f"expected {geometry.pixel_bytes} B of pixels "
            f"({geometry.width}x{geometry.rows}x{geometry.bpp}), got {len(data)}"
        )

    return np.frombuffer(data, dtype=geometry.dtype).reshape(
        geometry.rows, geometry.width
    )


def predict(pixels: np.ndarray) -> np.ndarray:
    """
    Difference against the left neighbour; the first column is untouched.

    >>> import numpy as np
    >>> predict(np.array([[10, 12, 11]], dtype=">u2")).tolist()
    [[10, 2, 65535]]
    """
    result = np.empty_like(pixels)
    result[:, :1] = pixels[:, :1]
    result[:, 1:] = (
        pixels[:, 1:].astype(np.int64) - pixels[:, :-1].astype(np.int64)
    ).astype(pixels.dtype)

    return result


def unpredict(deltas: np.ndarray) -> np.ndarray:
    """
    Inverse of `predict`: a running sum along each row.

    >>> import numpy as np
    >>> unpredict(np.array([[10, 2, 65535]], dtype=">u2")).tolist()
    [[10, 12, 11]]
    >>> data = np.array([[7, 3, 9, 1]], dtype="u1")
    >>> unpredict(predict(data)).tolist() == data.tolist()
    True
    """
    modulus = 1 << (deltas.dtype.itemsize * 8)

    running = np.cumsum(deltas.astype(np.int64), axis=1) % modulus

    return running.astype(deltas.dtype.newbyteorder("="))


def _decode_rle(data: bytes, geometry: ChannelGeometry, large: bool) -> bytes:
    counts_width = 4 if large else 2
    header = geometry.rows * counts_width

    if len(data) < header:
        raise CodecError("the row length table is truncated")

    counts = [
        int.from_bytes(data[i * counts_width : (i + 1) * counts_width], "big")
        for i in range(geometry.rows)
    ]

    out = bytearray()
    cursor = header
    row_bytes = geometry.width * geometry.bpp

    for count in counts:
        chunk = data[cursor : cursor + count]

        if len(chunk) != count:
            raise CodecError("RLE row data is truncated")

        row = _unpackbits(chunk)

        if len(row) != row_bytes:
            raise CodecError(
                f"RLE row data has {len(row)} B, expected {row_bytes}"
            )

        out += row
        cursor += count

    return bytes(out)


def _unpackbits(data: bytes) -> bytes:
    """
    Decodes a PackBits stream.

    >>> _unpackbits(bytes([2, 65, 66, 67, 253, 88]))
    b'ABCXXXX'
    """
    out = bytearray()
    index = 0

    while index < len(data):
        control = data[index]
        index += 1

        if control == 128:
            continue

        if control < 128:
            length = control + 1
            out += data[index : index + length]
            index += length
        else:
            if index >= len(data):
                raise CodecError("PackBits: missing repeat byte")

            out += bytes([data[index]]) * (257 - control)
            index += 1

    return bytes(out)


def decode_channel(
    data: bytes,
    *,
    compression: int,
    geometry: ChannelGeometry,
    large: bool = True,
) -> bytes:
    """
    Returns the raw channel pixels, whatever the compression method.

    >>> import zlib
    >>> shape = ChannelGeometry(3, 1, 2)
    >>> pixels = bytes([0, 10, 0, 12, 0, 11])
    >>> decode_channel(pixels, compression=0, geometry=shape) == pixels
    True
    >>> decode_channel(zlib.compress(pixels), compression=2, geometry=shape) == pixels
    True
    """
    if compression == RAW:
        if len(data) != geometry.pixel_bytes:
            raise CodecError(
                f"RAW channel has {len(data)} B, expected {geometry.pixel_bytes}"
            )

        return data

    if compression == RLE:
        return _decode_rle(data, geometry, large)

    if compression in (ZIP, ZIP_PREDICTED):
        plain = zlib.decompress(data)

        if compression == ZIP:
            return plain

        return (
            unpredict(_as_rows(plain, geometry))
            .astype(geometry.dtype)
            .tobytes()
        )

    raise CodecError(f"unknown compression method: {compression}")


def encode_channel(
    pixels: bytes,
    *,
    compression: int,
    geometry: ChannelGeometry,
    level: int = DEFAULT_LEVEL,
) -> bytes:
    """
    Encodes raw pixels with the chosen method.

    Writing RLE is not supported: we shrink files with deflate,
    a RLE tylko odczytujemy.

    >>> shape = ChannelGeometry(3, 1, 2)
    >>> pixels = bytes([0, 10, 0, 12, 0, 11])
    >>> out = encode_channel(pixels, compression=3, geometry=shape)
    >>> decode_channel(out, compression=3, geometry=shape) == pixels
    True
    >>> encode_channel(pixels, compression=0, geometry=shape) == pixels
    True
    """
    if compression == RAW:
        return pixels

    if compression == ZIP:
        return zlib.compress(pixels, level)

    if compression == ZIP_PREDICTED:
        deltas = predict(_as_rows(pixels, geometry))

        return zlib.compress(deltas.astype(geometry.dtype).tobytes(), level)

    if compression == RLE:
        raise CodecError("writing RLE is not supported")

    raise CodecError(f"unknown compression method: {compression}")


def recompress_channel(  # noqa: PLR0913  - kodek: source, target i geometria
    data: bytes,
    *,
    source: int,
    target: int,
    geometry: ChannelGeometry,
    large: bool = True,
    level: int = DEFAULT_LEVEL,
) -> tuple[bytes, bytes]:
    """
    Recompresses a channel and returns `(new_data, pixels)`.

    The pixels come back with the result so the caller can compute a checksum
    and prove losslessness without decoding a second time.

    >>> shape = ChannelGeometry(4, 1, 2)
    >>> pixels = bytes([0, 5, 0, 9, 0, 9, 0, 1])
    >>> packed, plain = recompress_channel(
    ...     pixels, source=0, target=3, geometry=shape
    ... )
    >>> plain == pixels
    True
    >>> decode_channel(packed, compression=3, geometry=shape) == pixels
    True
    """
    plain = decode_channel(
        data, compression=source, geometry=geometry, large=large
    )

    encoded = encode_channel(
        plain, compression=target, geometry=geometry, level=level
    )

    return encoded, plain
