"""
Recompressing layer channels while keeping the rest of the structure
bit for bit.

Exactly two things change:

1. the channel data, to ``ZIP with prediction``
2. that channel's size field inside the layer record

Everything else in the record (rectangle, blend mode, masks, names, extra
blocks) is copied from the source untouched - you cannot corrupt a structure
you never rewrite.

A channel is left alone when: there is no trustworthy geometry (adjustment
layers have a 0x0 rectangle, so prediction cannot be reversed), the codec
does not understand it, or compression would not gain anything.
"""

from __future__ import annotations

import hashlib
import zlib
from dataclasses import dataclass, field

from tiff_analyzer.domain import ByteOrder, IntOrder
from tiff_analyzer.psd_codec import (
    RAW,
    ZIP,
    ZIP_PREDICTED,
    ChannelGeometry,
    CodecError,
    decode_channel,
    encode_channel,
)
from tiff_analyzer.psd_layers import Layer, LayerChannel, LayerStack
from tiff_analyzer.readers import ByteReader
from tiff_analyzer.segments import Copy, Literal, Segment

#: The compression-method header at the start of the channel data.
HEADER = 2

#: Methods we are allowed to recompress. Channels already packed with
#: prediction are left alone; Photoshop already did that work.
DEFAULT_SOURCES = (RAW, ZIP)


@dataclass(frozen=True)
class ChannelResult:
    """What happened to one channel and what verifies it."""

    layer: str
    channel: str
    before: int
    after: int
    #: `None` when the channel is too short to carry a method header at all.
    compression: int | None
    digest: str
    #: "pixels" when taken from decoded pixels, "bytes" when from the raw
    #: channel bytes (a channel we cannot decode is copied verbatim).
    digest_of: str

    @property
    def saved(self) -> int:
        return self.before - self.after

    @property
    def changed(self) -> bool:
        return self.after != self.before


@dataclass
class LayerSectionPlan:
    """A rebuild plan for the layer section plus the material to verify it."""

    segments: list[Segment] = field(default_factory=list)
    results: list[ChannelResult] = field(default_factory=list)

    @property
    def before(self) -> int:
        return sum(item.before for item in self.results)

    @property
    def after(self) -> int:
        return sum(item.after for item in self.results)

    @property
    def saved(self) -> int:
        return self.before - self.after

    @property
    def changed(self) -> bool:
        return any(item.changed for item in self.results)

    def digests(self) -> dict[str, str]:
        """
        Checksums keyed by channel position.

        The key is positional rather than by name: layer names repeat
        (a group and a layer can share one).
        """
        return {
            f"{index}:{item.layer}/{item.channel}": item.digest
            for index, item in enumerate(self.results)
        }


def _geometry(layer: Layer, channel: LayerChannel, bpp: int) -> ChannelGeometry | None:
    """Channel geometry, provided it matches the decoded byte count."""
    if layer.width <= 0 or layer.height <= 0:
        return None

    geometry = ChannelGeometry(layer.width, layer.height, bpp)

    if channel.compression == RAW and geometry.pixel_bytes != channel.pixel_bytes:
        return None

    return geometry


def _decode(
    reader: ByteReader,
    channel: LayerChannel,
    geometry: ChannelGeometry | None,
) -> bytes | None:
    """Channel pixels, or `None` when they cannot be read."""
    if geometry is None or channel.compression is None:
        return None

    data = reader.read_at(channel.data_offset + HEADER, channel.pixel_bytes)

    try:
        return decode_channel(data, compression=channel.compression, geometry=geometry)
    except (CodecError, ValueError, zlib.error):
        return None


def _plan_channel(  # noqa: PLR0913  - kontekst channel wymaga tych parametrow
    reader: ByteReader,
    layer: Layer,
    channel: LayerChannel,
    *,
    bpp: int,
    order: IntOrder,
    level: int,
    sources: tuple[int, ...],
    target: int,
    blind: bool = False,
) -> tuple[Segment, ChannelResult]:
    """The segment holding channel data plus its verification entry."""
    geometry = _geometry(layer, channel, bpp)
    plain = _decode(reader, channel, geometry)

    replacement: bytes | None = None

    if plain is not None and geometry is not None and channel.compression in sources:
        packed = encode_channel(
            plain, compression=target, geometry=geometry, level=level
        )

        if len(packed) + HEADER < channel.size:
            replacement = target.to_bytes(HEADER, order) + packed

    method = target

    if plain is None and blind and channel.compression == RAW:
        # Plain ZIP needs no geometry, neither to write nor to read. A RAW
        # channel is bare pixels, so it can be packed with no knowledge of
        # the rectangle. This is how we recover adjustment-layer masks, which
        # carry a 0x0 rectangle yet hold tens of megabytes.
        plain = reader.read_at(channel.data_offset + HEADER, channel.pixel_bytes)
        packed = zlib.compress(plain, level)

        if len(packed) + HEADER < channel.size:
            method = ZIP
            replacement = ZIP.to_bytes(HEADER, order) + packed

    if plain is not None:
        digest = hashlib.sha256(plain).hexdigest()
        digest_of = "pixels"
    else:
        raw = reader.read_at(channel.data_offset, channel.size)
        digest = hashlib.sha256(raw).hexdigest()
        digest_of = "bytes"

    payload: Segment = (
        Copy(channel.data_offset, channel.size)
        if replacement is None
        else Literal(replacement)
    )

    result = ChannelResult(
        layer=layer.name or f"#{layer.index}",
        channel=channel.name,
        before=channel.size,
        after=channel.size if replacement is None else len(replacement),
        compression=(channel.compression if replacement is None else method),
        digest=digest,
        digest_of=digest_of,
    )

    return payload, result


def plan_layer_section(  # noqa: PLR0913  - range, geometry and tuning
    reader: ByteReader,
    stack: LayerStack,
    start: int,
    end: int,
    *,
    bpp: int,
    byte_order: ByteOrder,
    level: int = 6,
    sources: tuple[int, ...] = DEFAULT_SOURCES,
    target: int = ZIP_PREDICTED,
    blind: bool = False,
) -> LayerSectionPlan:
    """
    Builds the rebuild plan for the layer section.

    Records are copied piecewise, with new size fields inserted exactly
    where the old ones sat. Channel data follows the records, in the same
    order as in the source.
    """
    order = "little" if byte_order == "<" else "big"

    plan = LayerSectionPlan()

    channels = [
        (layer, channel) for layer in stack.layers for channel in layer.channels
    ]

    records_end = min(
        (channel.data_offset for _layer, channel in channels),
        default=end,
    )

    cursor = start
    payloads: list[Segment] = []

    for layer, channel in channels:
        if channel.compression is None or channel.pixel_bytes == 0:
            payloads.append(Copy(channel.data_offset, channel.size))
            continue

        payload, result = _plan_channel(
            reader,
            layer,
            channel,
            bpp=bpp,
            order=order,
            level=level,
            sources=sources,
            target=target,
            blind=blind,
        )

        plan.segments.append(Copy(cursor, channel.size_offset - cursor))
        plan.segments.append(Literal(result.after.to_bytes(channel.size_width, order)))
        cursor = channel.size_offset + channel.size_width

        payloads.append(payload)
        plan.results.append(result)

    plan.segments.append(Copy(cursor, records_end - cursor))
    plan.segments.extend(payloads)

    return plan
