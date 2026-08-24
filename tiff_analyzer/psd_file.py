"""
Parser osadzonego pliku PSD / PSB.

Smart objects sit inside the ``lnk2`` block as **raw files**. Unlike the
rest of ImageSourceData they are not byte-swapped but stored normally,
czyli big-endian.

File layout::

    26 B    header: "8BPS", version, channels, dimensions, depth, colour mode
    uint32  Color Mode Data length + data
    uint32  Image Resources length + data
    uint32/uint64  Layer and Mask Information length + data
    ...     Image Data (the flattened composite): the rest of the file

In PSB (version 2) the layer section lengths and channel sizes are 8 bytes.
"""

from __future__ import annotations

from dataclasses import dataclass

from tiff_analyzer.domain import ParseWarning
from tiff_analyzer.psd_blocks import walk
from tiff_analyzer.psd_layers import (
    LAYER_SECTION_KEYS,
    LayerStack,
    parse_layers,
)
from tiff_analyzer.readers import ByteReader

SIGNATURE = b"8BPS"

HEADER_SIZE = 26

#: Wersja pliku -> name formatu.
VERSIONS: dict[int, str] = {1: "PSD", 2: "PSB"}

#: Colour modes from the header.
COLOR_MODES: dict[int, str] = {
    0: "Bitmap",
    1: "Grayscale",
    2: "Indexed",
    3: "RGB",
    4: "CMYK",
    7: "Multichannel",
    8: "Duotone",
    9: "Lab",
}

#: Metody kompresji sekcji Image Data.
COMPRESSIONS: dict[int, str] = {
    0: "RAW",
    1: "RLE",
    2: "ZIP",
    3: "ZIP with prediction",
}


class DocumentError(ValueError):
    """The stream is not a readable PSD/PSB file."""


@dataclass(frozen=True)
class FileSection:
    """
    A named file section together with where it sits.

    `offset` points at the length field (when the section has one) and `size`
    counts the data alone. Thanks to `total_size` the sections tile the file
    exactly.

    >>> section = FileSection("Image Resources", 26, 4, 100)
    >>> section.data_offset, section.total_size, section.end
    (30, 104, 130)
    """

    name: str
    offset: int
    prefix_size: int
    size: int

    @property
    def data_offset(self) -> int:
        return self.offset + self.prefix_size

    @property
    def total_size(self) -> int:
        return self.prefix_size + self.size

    @property
    def end(self) -> int:
        return self.offset + self.total_size


@dataclass(frozen=True)
class EmbeddedDocument:
    version: int
    channels: int
    width: int
    height: int
    depth: int
    color_mode: int
    sections: tuple[FileSection, ...]
    layers: LayerStack | None
    total: int
    image_compression: int | None = None
    warnings: tuple[ParseWarning, ...] = ()

    @property
    def format_name(self) -> str:
        """
        >>> EmbeddedDocument(2, 3, 1, 1, 16, 3, (), None, 0).format_name
        'PSB'
        >>> EmbeddedDocument(9, 3, 1, 1, 16, 3, (), None, 0).format_name
        'version 9'
        """
        return VERSIONS.get(self.version, f"version {self.version}")

    @property
    def color_mode_name(self) -> str:
        """
        >>> EmbeddedDocument(2, 3, 1, 1, 16, 3, (), None, 0).color_mode_name
        'RGB'
        """
        return COLOR_MODES.get(self.color_mode, f"mode {self.color_mode}")

    @property
    def compression_name(self) -> str:
        """
        >>> EmbeddedDocument(2, 3, 1, 1, 16, 3, (), None, 0, 1).compression_name
        'RLE'
        >>> EmbeddedDocument(2, 3, 1, 1, 16, 3, (), None, 0).compression_name
        'unknown'
        """
        if self.image_compression is None:
            return "unknown"

        return COMPRESSIONS.get(
            self.image_compression, f"method {self.image_compression}"
        )

    @property
    def is_large(self) -> bool:
        return self.version == 2

    @property
    def accounted(self) -> int:
        """Sum of the section sizes plus the header."""
        return HEADER_SIZE + sum(
            section.total_size for section in self.sections
        )

    def section(self, name: str) -> FileSection | None:
        return next(
            (item for item in self.sections if item.name == name), None
        )


class _Cursor:
    """A big-endian cursor: a raw PSD/PSB file is not byte-swapped."""

    def __init__(self, reader: ByteReader, offset: int, end: int):
        self.reader = reader
        self.offset = offset
        self.end = end

    def take(self, count: int) -> bytes:
        if count < 0 or self.offset + count > self.end:
            raise DocumentError(
                f"read of {count} B past the end of the file @0x{self.offset:X}"
            )

        chunk = self.reader.read_at(self.offset, count)
        self.offset += count

        return chunk

    def uint16(self) -> int:
        return int.from_bytes(self.take(2), "big")

    def uint32(self) -> int:
        return int.from_bytes(self.take(4), "big")

    def uint64(self) -> int:
        return int.from_bytes(self.take(8), "big")

    def length(self, large: bool) -> int:
        return self.uint64() if large else self.uint32()


def _read_layer_and_mask(
    reader: ByteReader,
    section: FileSection,
    large: bool,
) -> tuple[LayerStack | None, list[ParseWarning]]:
    """
    Extracts the layer section from Layer and Mask Information.

    8-bit documents keep their layers in the classic Layer Info section. In
    16- and 32-bit ones that section has length 0 and the layers live in the
    additional ``Lr16`` / ``Lr32`` block, verified against an embedded PSB.
    """
    warnings: list[ParseWarning] = []

    if section.size == 0:
        return None, warnings

    inner = _Cursor(reader, section.data_offset, section.end)

    try:
        layer_info_size = inner.length(large)

        if layer_info_size > 0:
            end = min(inner.offset + layer_info_size, section.end)

            return (
                parse_layers(
                    reader, inner.offset, end, large=large, byte_order=">"
                ),
                warnings,
            )

        # Careful: `inner.offset += inner.uint32()` would be a bug. Python
        # reads the left-hand side before calling the method, so the cursor
        # advance done by `uint32()` itself would be overwritten.
        global_mask_size = inner.uint32()
        inner.offset = inner.offset + global_mask_size
    except DocumentError as error:
        warnings.append(
            ParseWarning("embedded-layer-info", section.offset, str(error))
        )
        return None, warnings

    blocks, block_warnings = walk(
        reader, inner.offset, section.end, large_document=large
    )

    warnings.extend(block_warnings)

    block = next(
        (item for item in blocks if item.key in LAYER_SECTION_KEYS), None
    )

    if block is None:
        return None, warnings

    return (
        parse_layers(
            reader,
            block.payload_offset,
            block.payload_offset + block.size,
            large=large,
            byte_order=">",
        ),
        warnings,
    )


def parse_document(
    reader: ByteReader,
    start: int,
    size: int,
) -> EmbeddedDocument:
    """
    Reads the structure of an embedded PSD/PSB file.

    >>> from tiff_analyzer.readers import BytesReader
    >>> header = (
    ...     b"8BPS" + (2).to_bytes(2, "big") + bytes(6)
    ...     + (3).to_bytes(2, "big")
    ...     + (600).to_bytes(4, "big") + (800).to_bytes(4, "big")
    ...     + (16).to_bytes(2, "big") + (3).to_bytes(2, "big")
    ... )
    >>> body = (
    ...     (0).to_bytes(4, "big")          # Color Mode Data
    ...     + (4).to_bytes(4, "big") + b"abcd"   # Image Resources
    ...     + (0).to_bytes(8, "big")        # Layer and Mask Information
    ...     + (1).to_bytes(2, "big")        # kompresja Image Data
    ... )
    >>> data = header + body
    >>> doc = parse_document(BytesReader(data), 0, len(data))
    >>> doc.format_name, doc.color_mode_name, doc.compression_name
    ('PSB', 'RGB', 'RLE')
    >>> doc.width, doc.height, doc.channels, doc.depth
    (800, 600, 3, 16)
    >>> [(item.name, item.size) for item in doc.sections]
    [('Color Mode Data', 0), ('Image Resources', 4),
     ('Layer and Mask Information', 0), ('Image Data', 2)]
    >>> [item.total_size for item in doc.sections]
    [4, 8, 8, 2]
    >>> doc.accounted == len(data)
    True
    """
    end = start + size
    cursor = _Cursor(reader, start, end)

    if cursor.take(4) != SIGNATURE:
        raise DocumentError(f"missing signature {SIGNATURE!r} @0x{start:X}")

    version = cursor.uint16()

    cursor.take(6)  # zarezerwowane

    channels = cursor.uint16()
    height = cursor.uint32()
    width = cursor.uint32()
    depth = cursor.uint16()
    color_mode = cursor.uint16()

    large = version == 2

    sections: list[FileSection] = []
    warnings: list[ParseWarning] = []

    for name, uses_large in (
        ("Color Mode Data", False),
        ("Image Resources", False),
        ("Layer and Mask Information", large),
    ):
        section_start = cursor.offset
        length = cursor.length(uses_large)
        prefix = cursor.offset - section_start

        sections.append(FileSection(name, section_start, prefix, length))

        if cursor.offset + length > end:
            warnings.append(
                ParseWarning(
                    "embedded-section-overrun",
                    cursor.offset,
                    f"{name} declares {length} B, {end - cursor.offset} B remain",
                )
            )
            cursor.offset = end
            break

        cursor.offset += length

    compression: int | None = None

    if cursor.offset + 2 <= end:
        compression = int.from_bytes(reader.read_at(cursor.offset, 2), "big")

    sections.append(
        FileSection("Image Data", cursor.offset, 0, end - cursor.offset)
    )

    layers = None

    mask_section = next(
        (
            item
            for item in sections
            if item.name == "Layer and Mask Information"
        ),
        None,
    )

    if mask_section is not None:
        layers, layer_warnings = _read_layer_and_mask(
            reader, mask_section, large
        )
        warnings.extend(layer_warnings)

    return EmbeddedDocument(
        version=version,
        channels=channels,
        width=width,
        height=height,
        depth=depth,
        color_mode=color_mode,
        sections=tuple(sections),
        layers=layers,
        total=size,
        image_compression=compression,
        warnings=tuple(warnings),
    )
