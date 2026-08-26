"""
Parser for the layer records inside an ``Lr16`` / ``Lr32`` / ``Layr`` block.

Section layout (everything byte-swapped like the rest of ImageSourceData:
little-endian numbers, reversed 4-character codes)::

    int16   layer count (negative = the first channel is transparency)
    N x     layer record
    ...     channel data, concatenated in the same order

Layer record::

    4 x int32   rectangle: top, left, bottom, right
    int16       channel count
    K x         int16 channel id + int32/int64 data size
    "8BIM"      blend mode signature (on disk "MIB8")
    4 chars     blend mode ("norm", "mul ", ...)
    uint8       opacity 0-255
    uint8       clipping
    uint8       flags
    uint8       filler
    int32       length of the extra section, holding:
                  the layer mask, the blending ranges,
                  the Pascal name (padded to 4 B),
                  extra 8BIM blocks (among them "luni", the Unicode name)

The records themselves are small; channel data (tens or hundreds of MB) is
merely skipped over while its size is counted.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from optiff.domain import (
    ByteOrder,
    IntOrder,
    ParseWarning,
    PhotoshopAnalysis,
    int_order,
)
from optiff.readers import ByteReader

#: Block keys carrying the layer name in Unicode.
UNICODE_NAME_KEY = "luni"

#: The block that divides layers into groups.
SECTION_DIVIDER_KEY = "lsct"

#: Human readable blend mode names.
BLEND_MODES: dict[str, str] = {
    "pass": "Pass Through",
    "norm": "Normal",
    "diss": "Dissolve",
    "dark": "Darken",
    "mul ": "Multiply",
    "idiv": "Color Burn",
    "lbrn": "Linear Burn",
    "dkCl": "Darker Color",
    "lite": "Lighten",
    "scrn": "Screen",
    "div ": "Color Dodge",
    "lddg": "Linear Dodge",
    "lgCl": "Lighter Color",
    "over": "Overlay",
    "sLit": "Soft Light",
    "hLit": "Hard Light",
    "vLit": "Vivid Light",
    "lLit": "Linear Light",
    "pLit": "Pin Light",
    "hMix": "Hard Mix",
    "diff": "Difference",
    "smud": "Exclusion",
    "fsub": "Subtract",
    "fdiv": "Divide",
    "hue ": "Hue",
    "sat ": "Saturation",
    "colr": "Color",
    "lum ": "Luminosity",
}

#: What the channel identifiers mean.
CHANNEL_NAMES: dict[int, str] = {
    0: "R",
    1: "G",
    2: "B",
    -1: "alpha",
    -2: "mask",
    -3: "real mask",
}

#: Rodzaje wpisu `lsct`.
SECTION_KINDS: dict[int, str] = {
    0: "layer",
    1: "group open",
    2: "group closed",
    3: "group end",
}

#: Channel data compression methods. The code sits in the first 2 bytes of
#: each channel's data, ahead of the pixels themselves.
COMPRESSIONS: dict[int, str] = {
    0: "RAW",
    1: "RLE",
    2: "ZIP",
    3: "ZIP with prediction",
}

#: Short forms for the report table.
COMPRESSION_SHORT: dict[str, str] = {
    "RAW": "RAW",
    "RLE": "RLE",
    "ZIP": "ZIP",
    "ZIP with prediction": "ZIPp",
    "unknown": "?",
}

COMPRESSION_HEADER = 2

_FLAG_TRANSPARENCY_LOCKED = 0x01
_FLAG_HIDDEN = 0x02
_FLAG_IRRELEVANT = 0x10


@dataclass(frozen=True)
class LayerChannel:
    channel_id: int
    size: int
    #: Compression code read from the first 2 bytes of the channel data.
    #: `None` when the channel is empty or its data lies outside the section.
    compression: int | None = None
    #: Where in the reader this channel's compression header begins.
    data_offset: int = 0
    #: Where in the layer record this channel's size field sits. Needed to
    #: write the new length after recompression.
    size_offset: int = 0
    #: Width of the size field: 4 B, or 8 B in the PSB variant.
    size_width: int = 4

    @property
    def pixel_offset(self) -> int:
        """Offset of the pixels themselves, past the compression header."""
        return self.data_offset + COMPRESSION_HEADER

    @property
    def name(self) -> str:
        """
        >>> LayerChannel(0, 10).name
        'R'
        >>> LayerChannel(-1, 10).name
        'alpha'
        >>> LayerChannel(9, 10).name
        'channel 9'
        """
        return CHANNEL_NAMES.get(self.channel_id, f"channel {self.channel_id}")

    @property
    def compression_name(self) -> str:
        """
        >>> LayerChannel(0, 10, compression=1).compression_name
        'RLE'
        >>> LayerChannel(0, 10, compression=99).compression_name
        'method 99'
        >>> LayerChannel(0, 0).compression_name
        'unknown'
        """
        if self.compression is None:
            return "unknown"

        return COMPRESSIONS.get(self.compression, f"method {self.compression}")

    @property
    def pixel_bytes(self) -> int:
        """
        Data bytes excluding the 2-byte compression header.

        >>> LayerChannel(0, 1002, compression=1).pixel_bytes
        1000
        >>> LayerChannel(0, 0).pixel_bytes
        0
        """
        return max(0, self.size - COMPRESSION_HEADER) if self.size else 0


@dataclass(frozen=True)
class Layer:
    index: int
    name: str
    top: int
    left: int
    bottom: int
    right: int
    channels: tuple[LayerChannel, ...]
    blend_mode: str
    opacity: int
    clipping: int
    flags: int
    extra_keys: tuple[str, ...] = ()
    section: str = "layer"
    depth: int = 0
    #: The name exactly as stored. For a group-end marker `name` shows the
    #: name of the group being closed, while this keeps the literal
    #: "</Layer group>".
    raw_name: str = ""

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def data_size(self) -> int:
        """Total size of this layer's channel data."""
        return sum(channel.size for channel in self.channels)

    @property
    def is_hidden(self) -> bool:
        return bool(self.flags & _FLAG_HIDDEN)

    @property
    def is_transparency_locked(self) -> bool:
        return bool(self.flags & _FLAG_TRANSPARENCY_LOCKED)

    @property
    def is_pixel_irrelevant(self) -> bool:
        """A layer whose pixels do not affect the result (group, adjustment)."""
        return bool(self.flags & _FLAG_IRRELEVANT)

    @property
    def is_empty(self) -> bool:
        """
        >>> base = dict(
        ...     index=0, name="x", top=0, left=0, channels=(),
        ...     blend_mode="norm", opacity=255, clipping=0, flags=0,
        ... )
        >>> Layer(bottom=0, right=0, **base).is_empty
        True
        >>> Layer(bottom=10, right=10, **base).is_empty
        False
        """
        return self.width == 0 or self.height == 0

    @property
    def blend_mode_name(self) -> str:
        """
        >>> Layer(
        ...     0, "x", 0, 0, 1, 1, (), "mul ", 255, 0, 0
        ... ).blend_mode_name
        'Multiply'
        """
        return BLEND_MODES.get(self.blend_mode, self.blend_mode)

    @property
    def opacity_percent(self) -> int:
        """
        >>> Layer(0, "x", 0, 0, 1, 1, (), "norm", 255, 0, 0).opacity_percent
        100
        >>> Layer(0, "x", 0, 0, 1, 1, (), "norm", 128, 0, 0).opacity_percent
        50
        """
        return round(self.opacity / 255 * 100)

    @property
    def compression_name(self) -> str:
        """
        Layer compression: a single name, or "mixed" when channels differ.

        Empty channels (no data) do not count towards the verdict.

        >>> def layer(*codes):
        ...     channels = tuple(
        ...         LayerChannel(index, 10, code)
        ...         for index, code in enumerate(codes)
        ...     )
        ...     return Layer(0, "x", 0, 0, 1, 1, channels, "norm", 255, 0, 0)
        >>> layer(1, 1, 1).compression_name
        'RLE'
        >>> layer(1, 0).compression_name
        'mixed (RAW, RLE)'
        >>> Layer(0, "x", 0, 0, 1, 1, (), "norm", 255, 0, 0).compression_name
        'none'
        """
        codes = {
            channel.compression
            for channel in self.channels
            if channel.compression is not None
        }

        if not codes:
            return "none"

        if len(codes) == 1:
            return COMPRESSIONS.get(next(iter(codes)), f"method {next(iter(codes))}")

        names = sorted(COMPRESSIONS.get(code, f"method {code}") for code in codes)

        return f"mixed ({', '.join(names)})"

    @property
    def compression_short(self) -> str:
        """
        Short compression form for the table.

        >>> def layer(*codes):
        ...     channels = tuple(
        ...         LayerChannel(index, 10, code)
        ...         for index, code in enumerate(codes)
        ...     )
        ...     return Layer(0, "x", 0, 0, 1, 1, channels, "norm", 255, 0, 0)
        >>> layer(3).compression_short
        'ZIPp'
        >>> layer(0, 3).compression_short
        'mixed'
        >>> Layer(0, "x", 0, 0, 1, 1, (), "norm", 255, 0, 0).compression_short
        '-'
        """
        name = self.compression_name

        if name == "none":
            return "-"

        if name.startswith("mixed"):
            return "mixed"

        return COMPRESSION_SHORT.get(name, name)

    @property
    def pixel_bytes(self) -> int:
        """Pixel bytes excluding the compression headers."""
        return sum(channel.pixel_bytes for channel in self.channels)


@dataclass(frozen=True)
class LayerStack:
    layers: tuple[Layer, ...]
    declared_count: int
    has_transparency: bool
    consumed: int
    total: int
    warnings: tuple[ParseWarning, ...] = ()
    #: Zero padding bytes at the end of the section (the length is
    #: sometimes rounded up to 4 B).
    padding: int = 0

    @property
    def is_exact(self) -> bool:
        """Records plus channels hit the end of the section exactly."""
        return self.consumed == self.total

    @property
    def is_complete(self) -> bool:
        """
        Like `is_exact`, but tolerates recognised padding.

        >>> LayerStack((), 0, False, 97, 100, padding=3).is_exact
        False
        >>> LayerStack((), 0, False, 97, 100, padding=3).is_complete
        True
        """
        return self.consumed + self.padding == self.total

    @property
    def channel_bytes(self) -> int:
        return sum(layer.data_size for layer in self.layers)

    def by_size(self) -> tuple[Layer, ...]:
        return tuple(
            sorted(self.layers, key=lambda layer: layer.data_size, reverse=True)
        )


class _Cursor:
    """
    Reader for the layer section.

    `byte_order` "<" is the variant embedded in a little-endian TIFF:
    little-endian numbers, reversed 4-character codes. ">" is a raw PSD/PSB
    file: big-endian numbers, codes the right way round.
    """

    def __init__(
        self,
        reader: ByteReader,
        offset: int,
        end: int,
        byte_order: ByteOrder = "<",
    ):
        self.reader = reader
        self.offset = offset
        self.end = end
        self.byte_order = byte_order
        self.order: IntOrder = int_order(byte_order)

    def take(self, count: int) -> bytes:
        if count < 0 or self.offset + count > self.end:
            raise ValueError(
                f"read of {count} B past the layer section @0x{self.offset:X}"
            )

        chunk = self.reader.read_at(self.offset, count)
        self.offset += count

        return chunk

    def skip(self, count: int) -> None:
        if count < 0 or self.offset + count > self.end:
            raise ValueError(f"skip of {count} B past the section @0x{self.offset:X}")

        self.offset += count

    def int16(self) -> int:
        return int.from_bytes(self.take(2), self.order, signed=True)

    def int32(self) -> int:
        return int.from_bytes(self.take(4), self.order, signed=True)

    def uint32(self) -> int:
        return int.from_bytes(self.take(4), self.order)

    def int64(self) -> int:
        return int.from_bytes(self.take(8), self.order, signed=True)

    def uint8(self) -> int:
        return self.take(1)[0]

    def code(self) -> str:
        raw = self.take(4)

        return (raw[::-1] if self.byte_order == "<" else raw).decode("latin1")

    def pascal_string(self, alignment: int = 4) -> str:
        length = self.uint8()
        raw = self.take(length)

        padding = (-(length + 1)) % alignment
        self.skip(padding)

        return raw.decode("latin1", errors="replace")

    def unicode_string(self) -> str:
        """A UTF-16 string in the same byte order as the rest of the stream."""
        units = self.uint32()

        encoding = "utf-16-le" if self.byte_order == "<" else "utf-16-be"

        return self.take(units * 2).decode(encoding).rstrip("\x00")


@dataclass
class _Extra:
    """What came out of the extra section of a layer record."""

    unicode_name: str = ""
    pascal_name: str = ""
    section: int | None = None
    keys: list[str] = field(default_factory=list)


def _read_extra(cursor: _Cursor, length: int, large: bool) -> _Extra:
    """Reads the mask, blending ranges, Pascal name and extra blocks."""
    stop = cursor.offset + length
    extra = _Extra()

    cursor.skip(cursor.uint32())  # layer mask data
    cursor.skip(cursor.uint32())  # blending ranges

    if cursor.offset < stop:
        extra.pascal_name = cursor.pascal_string()

    while cursor.offset + 12 <= stop:
        signature = cursor.code()

        if signature not in ("8BIM", "8B64"):
            break

        key = cursor.code()
        extra.keys.append(key)

        size = cursor.int64() if large and key in _LARGE_EXTRA_KEYS else cursor.uint32()

        payload_end = cursor.offset + size

        if payload_end > stop:
            break

        if key == UNICODE_NAME_KEY:
            extra.unicode_name = cursor.unicode_string()
        elif key == SECTION_DIVIDER_KEY and size >= 4:
            extra.section = cursor.uint32()

        cursor.offset = payload_end + (-size % 4 if size % 4 else 0)

    cursor.offset = stop

    return extra


#: Keys that carry an 8-byte length in the PSB variant, also inside
#: the extra section of a layer.
_LARGE_EXTRA_KEYS = frozenset({"LMsk", "Lr16", "Lr32", "Layr", "lnk2", "FEid"})


def _read_layer(cursor: _Cursor, index: int, large: bool) -> tuple[Layer, _Extra]:
    top = cursor.int32()
    left = cursor.int32()
    bottom = cursor.int32()
    right = cursor.int32()

    count = cursor.int16()

    if not 0 <= count <= 64:
        raise ValueError(f"implausible channel count: {count}")

    channels: list[LayerChannel] = []

    for _ in range(count):
        channel_id = cursor.int16()
        size_offset = cursor.offset

        channels.append(
            LayerChannel(
                channel_id=channel_id,
                size=cursor.int64() if large else cursor.uint32(),
                size_offset=size_offset,
                size_width=8 if large else 4,
            )
        )

    frozen = tuple(channels)

    signature = cursor.code()

    if signature not in ("8BIM", "8B64"):
        raise ValueError(f"bad blend mode signature: {signature!r}")

    blend_mode = cursor.code()
    opacity = cursor.uint8()
    clipping = cursor.uint8()
    flags = cursor.uint8()
    cursor.uint8()  # filler

    extra = _read_extra(cursor, cursor.uint32(), large)

    layer = Layer(
        index=index,
        name=extra.unicode_name or extra.pascal_name,
        raw_name=extra.unicode_name or extra.pascal_name,
        top=top,
        left=left,
        bottom=bottom,
        right=right,
        channels=frozen,
        blend_mode=blend_mode,
        opacity=opacity,
        clipping=clipping,
        flags=flags,
        extra_keys=tuple(extra.keys),
        section=SECTION_KINDS.get(extra.section or 0, "layer"),
    )

    return layer, extra


def _read_channel_compression(
    reader: ByteReader,
    layers: list[Layer],
    start: int,
    end: int,
    order: IntOrder,
) -> list[Layer]:
    """
    Attaches the compression code to every channel.

    Channel data sits concatenated AFTER all layer records, in the same order
    as the layers and their channels. Knowing the sizes, we jump to the start
    of each channel and read 2 bytes, never touching the pixels.
    """
    offset = start
    result: list[Layer] = []

    for layer in layers:
        channels: list[LayerChannel] = []

        for channel in layer.channels:
            compression = None

            if channel.size >= COMPRESSION_HEADER and offset + 2 <= end:
                compression = int.from_bytes(reader.read_at(offset, 2), order)

            channels.append(
                replace(channel, compression=compression, data_offset=offset)
            )

            offset += channel.size

        result.append(replace(layer, channels=tuple(channels)))

    return result


def _resolve_groups(layers: list[Layer]) -> tuple[Layer, ...]:
    """
    Works out nesting and gives every group-end marker its group's name.

    Photoshop writes layers bottom-up, so in storage order the "group end"
    comes first, then the contents, and the group header last. We therefore
    count from the top of the stack, that is from the end of the list, and
    keep a stack of open names so that instead of a literal `</Layer group>`
    we can show the name of the group being closed.

    >>> def stub(index, name, section):
    ...     return Layer(index, name, 0, 0, 1, 1, (), "norm", 255, 0, 0,
    ...                  section=section, raw_name=name)
    >>> stack = [stub(0, "Background", "layer"),
    ...          stub(1, "</Layer group>", "group end"),
    ...          stub(2, "In the group", "layer"),
    ...          stub(3, "Color Grading", "group open")]
    >>> [(layer.name, layer.depth) for layer in _resolve_groups(stack)]
    [('Background', 0), ('Color Grading', 0), ('In the group', 1), ('Color Grading', 0)]
    >>> _resolve_groups(stack)[1].raw_name
    '</Layer group>'
    """
    depth = 0
    open_names: list[str] = []
    result: list[Layer] = []

    for layer in reversed(layers):
        name = layer.name

        if layer.section == "group end":
            depth = max(0, depth - 1)
            current = depth
            name = open_names.pop() if open_names else layer.name
        elif layer.section.startswith("group"):
            current = depth
            depth += 1
            open_names.append(layer.name)
        else:
            current = depth

        result.append(replace(layer, depth=current, name=name))

    return tuple(reversed(result))


#: Blocks that carry a layer section, in order of preference.
LAYER_SECTION_KEYS = ("Lr16", "Lr32", "Layr")


def read_layer_stack(
    analysis: PhotoshopAnalysis,
    reader: ByteReader,
) -> LayerStack | None:
    """Finds the layer section in an analysis and reads it."""
    block = next(
        (item for item in analysis.blocks if item.key in LAYER_SECTION_KEYS),
        None,
    )

    if block is None:
        return None

    return parse_layers(
        reader,
        block.payload_offset,
        block.payload_offset + block.size,
        large=block.header_size == 16,
    )


def parse_layers(
    reader: ByteReader,
    start: int,
    end: int,
    *,
    large: bool = False,
    byte_order: ByteOrder = "<",
) -> LayerStack:
    """
    Reads the layer stack out of an ``Lr16`` / ``Lr32`` / ``Layr`` section.

    >>> from optiff.readers import BytesReader
    >>> record = (
    ...     (0).to_bytes(4, "little") + (0).to_bytes(4, "little")
    ...     + (10).to_bytes(4, "little") + (20).to_bytes(4, "little")
    ...     + (1).to_bytes(2, "little")
    ...     + (0).to_bytes(2, "little", signed=True) + (7).to_bytes(4, "little")
    ...     + b"MIB8" + b"mron" + bytes([255, 0, 0, 0])
    ...     + (8).to_bytes(4, "little")
    ...     + (0).to_bytes(4, "little") + (0).to_bytes(4, "little")
    ... )
    >>> data = (1).to_bytes(2, "little") + record + b"\\x00" * 7
    >>> stack = parse_layers(BytesReader(data), 0, len(data))
    >>> len(stack.layers), stack.layers[0].blend_mode_name
    (1, 'Normal')
    >>> stack.layers[0].width, stack.layers[0].height
    (20, 10)
    >>> stack.is_exact
    True
    """
    cursor = _Cursor(reader, start, end, byte_order)
    warnings: list[ParseWarning] = []

    try:
        declared = cursor.int16()
    except ValueError as error:
        return LayerStack(
            layers=(),
            declared_count=0,
            has_transparency=False,
            consumed=cursor.offset - start,
            total=end - start,
            warnings=(ParseWarning("layers-truncated", start, str(error)),),
        )

    count = abs(declared)

    layers: list[Layer] = []

    for index in range(count):
        try:
            layer, _ = _read_layer(cursor, index, large)
        except ValueError as error:
            warnings.append(
                ParseWarning("layer-record-failed", cursor.offset, str(error))
            )
            break

        layers.append(layer)

    layers = _read_channel_compression(reader, layers, cursor.offset, end, cursor.order)

    consumed = cursor.offset - start + sum(layer.data_size for layer in layers)

    # The section length is sometimes rounded up to 4 B; a few zero bytes
    # at the end are padding, not corruption.
    padding = end - start - consumed
    aligned = 0 < padding < 4 and not any(reader.read_at(start + consumed, padding))

    if layers and padding != 0 and not aligned:
        warnings.append(
            ParseWarning(
                "layers-size-mismatch",
                start,
                f"records plus channels give {consumed} B, "
                f"the section has {end - start} B",
            )
        )

    return LayerStack(
        layers=_resolve_groups(layers),
        declared_count=declared,
        has_transparency=declared < 0,
        consumed=consumed,
        total=end - start,
        warnings=tuple(warnings),
        padding=padding if aligned else 0,
    )
