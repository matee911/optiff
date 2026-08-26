"""
Proof of losslessness: checksums over the pixels of every channel.

We compare pixels rather than bytes, because the bytes are exactly what is
about to change. A channel we cannot decode is copied unchanged, so for that
one we hash the raw bytes instead; that is an equally valid proof, as long as
po obu stronach liczymy tak samo.
"""

from __future__ import annotations

import hashlib
import zlib
from dataclasses import dataclass

from optiff.domain import PhotoshopAnalysis
from optiff.optimize_layers import HEADER
from optiff.psd_codec import (
    RAW,
    ZIP,
    ChannelGeometry,
    CodecError,
    decode_channel,
)
from optiff.psd_file import DocumentError, parse_document
from optiff.psd_layers import LayerStack, read_layer_stack
from optiff.psd_links import read_linked_files
from optiff.readers import ByteReader


@dataclass(frozen=True)
class ChannelDigest:
    """One channel checksum together with what it was computed from."""

    where: str
    layer: str
    channel: str
    digest: str
    source: str

    def key(self) -> tuple[str, str, str]:
        return (self.where, self.layer, self.channel)


def _digest(reader: ByteReader, layer, channel, bpp: int) -> tuple[str, str]:
    """Hash of the pixels, or of the raw channel bytes when that is impossible."""
    if layer.width > 0 and layer.height > 0:
        data = reader.read_at(channel.data_offset + HEADER, channel.pixel_bytes)

        try:
            plain = decode_channel(
                data,
                compression=channel.compression,
                geometry=ChannelGeometry(layer.width, layer.height, bpp),
            )

            return hashlib.sha256(plain).hexdigest(), "pixels"
        except (CodecError, ValueError, zlib.error):
            pass

    # Without a usable rectangle the pixels are still reachable, provided
    # the method does not need one: RAW is pixels already, and ZIP without
    # prediction is a single zlib.decompress. That gives a channel packed by
    # --zip-fallback EXACTLY the same checksum as its raw original; otherwise
    # verification would report an error for a change that is in fact
    # lossless.
    if channel.compression in (RAW, ZIP):
        data = reader.read_at(channel.data_offset + HEADER, channel.pixel_bytes)

        try:
            plain = data if channel.compression == RAW else zlib.decompress(data)

            return hashlib.sha256(plain).hexdigest(), "pixels"
        except zlib.error:
            pass

    raw = reader.read_at(channel.data_offset, channel.size)

    return hashlib.sha256(raw).hexdigest(), "bytes"


def _collect(
    reader: ByteReader,
    stack: LayerStack,
    bpp: int,
    where: str,
) -> list[ChannelDigest]:
    found: list[ChannelDigest] = []

    for layer in stack.layers:
        for index, channel in enumerate(layer.channels):
            if channel.compression is None or channel.pixel_bytes == 0:
                continue

            digest, source = _digest(reader, layer, channel, bpp)

            found.append(
                ChannelDigest(
                    where=where,
                    layer=f"{layer.index}:{layer.name}",
                    channel=f"{index}:{channel.name}",
                    digest=digest,
                    source=source,
                )
            )

    return found


def channel_digests(
    reader: ByteReader,
    analysis: PhotoshopAnalysis,
) -> list[ChannelDigest]:
    """
    Checksums for every channel: TIFF-level layers and embedded PSB alike.

    The order is stable, so the lists can be compared directly.
    """
    found: list[ChannelDigest] = []

    stack = read_layer_stack(analysis, reader)

    if stack is not None:
        found.extend(_collect(reader, stack, 2, "tiff"))

    linked = read_linked_files(analysis, reader)

    if linked is not None:
        for item in linked.files:
            if not item.is_embedded:
                continue

            try:
                document = parse_document(reader, item.data_offset, item.size)
            except DocumentError:
                continue

            if document.layers is None:
                continue

            found.extend(
                _collect(
                    reader,
                    document.layers,
                    document.depth // 8,
                    f"embedded:{item.index}:{item.name}",
                )
            )

    return found


@dataclass(frozen=True)
class Comparison:
    """Result of comparing two sets of checksums."""

    total: int
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


def compare(
    before: list[ChannelDigest],
    after: list[ChannelDigest],
) -> Comparison:
    """
    Compares the checksums of the source and the result.

    >>> a = ChannelDigest("tiff", "0:Sky", "0:R", "abc", "pixels")
    >>> compare([a], [a]).ok
    True
    >>> b = ChannelDigest("tiff", "0:Sky", "0:R", "zzz", "pixels")
    >>> compare([a], [b]).ok
    False
    >>> compare([a], []).problems
    ('channel count: 1 in source, 0 in result',)
    """
    problems: list[str] = []

    if len(before) != len(after):
        problems.append(
            f"channel count: {len(before)} in source, {len(after)} in result"
        )

    for source, result in zip(before, after, strict=False):
        if source.key() != result.key():
            problems.append(f"channel mismatch: {source.key()} vs {result.key()}")
            continue

        if source.digest != result.digest:
            problems.append(
                f"{source.where}/{source.layer}/{source.channel}: "
                f"{source.digest[:16]} != {result.digest[:16]}"
            )

    return Comparison(total=len(before), problems=tuple(problems))
