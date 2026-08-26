"""
Parser for the ``lnk2`` / ``lnkD`` / ``lnkE`` block: linked smart objects.

The block is a run of records, each describing one embedded or external file::

    int64       record length (excluding this field)
    4 chars     type: "liFD" embedded, "liFE" external, "liFA" alias
    uint32      record version
    pascal      identifier (UUID)
    unicode     original file name
    4 chars     file type ("8BPS" = PSD, "8BPB" = PSB)
    4 chars     creator ("8BIM")
    int64       file data length
    uint8       whether an opening descriptor follows
    [uint32 version + descriptor]
    ...         the raw file bytes (for "liFD")

The next record sits at ``start + 8 + align4(length)``, verified
empirically on production files.

We never read the file data; we only care what is linked and how big it is.
"""

from __future__ import annotations

from dataclasses import dataclass

from optiff.domain import ParseWarning, PhotoshopAnalysis
from optiff.psd_descriptor import DescriptorError
from optiff.psd_descriptor import parse as parse_descriptor
from optiff.readers import ByteReader

#: Blocks that carry the list of linked objects.
LINK_BLOCK_KEYS = ("lnk2", "lnkD", "lnk3", "lnkE")

#: Record types.
LINK_KINDS: dict[str, str] = {
    "liFD": "embedded",
    "liFE": "external",
    "liFA": "alias",
}

#: File type codes seen in smart objects.
FILE_TYPES: dict[str, str] = {
    "8BPS": "PSD",
    "8BPB": "PSB",
    "TIFF": "TIFF",
    "JPEG": "JPEG",
    "PNGf": "PNG",
    "RAW ": "RAW",
    "AIfl": "Illustrator",
    "PDF ": "PDF",
}

_HEADER_MIN = 32


def _align4(value: int) -> int:
    return (value + 3) & ~3


@dataclass(frozen=True)
class LinkedFile:
    index: int
    kind: str
    version: int
    uid: str
    name: str
    file_type: str
    creator: str
    size: int
    offset: int
    record_size: int
    has_descriptor: bool
    #: Where in the reader the raw bytes of the embedded file begin.
    data_offset: int = 0
    #: Where the 8-byte file data length field sits. Needed to write the
    #: new size after recompression.
    size_offset: int = 0

    @property
    def data_end(self) -> int:
        """The first byte past the embedded file data."""
        return self.data_offset + self.size

    @property
    def record_end(self) -> int:
        """End of the record content, excluding padding."""
        return self.offset + 8 + self.record_size

    @property
    def tail_size(self) -> int:
        """
        The fields written BEHIND the file data.

        From version 5 the record also carries a document identifier
        from 6 a modification time (double), from 7 a lock state (byte):
        15 bytes altogether in Photoshop files. They must be carried over
        rebuild, or the record comes out incomplete.
        """
        return max(0, self.record_end - self.data_end)

    @property
    def kind_name(self) -> str:
        """
        >>> LinkedFile(0, "liFD", 7, "", "", "", "", 0, 0, 0, False).kind_name
        'embedded'
        >>> LinkedFile(0, "zzzz", 7, "", "", "", "", 0, 0, 0, False).kind_name
        'zzzz'
        """
        return LINK_KINDS.get(self.kind, self.kind)

    @property
    def file_type_name(self) -> str:
        """
        >>> LinkedFile(0, "liFD", 7, "", "", "8BPB", "", 0, 0, 0, False).file_type_name
        'PSB'
        >>> LinkedFile(0, "liFD", 7, "", "", "zzzz", "", 0, 0, 0, False).file_type_name
        'zzzz'
        """
        return FILE_TYPES.get(self.file_type, self.file_type.strip() or "?")

    @property
    def is_embedded(self) -> bool:
        return self.kind == "liFD"


@dataclass(frozen=True)
class LinkedFiles:
    files: tuple[LinkedFile, ...]
    consumed: int
    total: int
    warnings: tuple[ParseWarning, ...] = ()

    @property
    def is_exact(self) -> bool:
        return self.consumed == self.total

    @property
    def embedded_bytes(self) -> int:
        return sum(item.size for item in self.files if item.is_embedded)


class _Cursor:
    def __init__(self, reader: ByteReader, offset: int, end: int):
        self.reader = reader
        self.offset = offset
        self.end = end

    def take(self, count: int) -> bytes:
        if count < 0 or self.offset + count > self.end:
            raise ValueError(f"read of {count} B past the block @0x{self.offset:X}")

        chunk = self.reader.read_at(self.offset, count)
        self.offset += count

        return chunk

    def uint8(self) -> int:
        return self.take(1)[0]

    def uint32(self) -> int:
        return int.from_bytes(self.take(4), "little")

    def int64(self) -> int:
        return int.from_bytes(self.take(8), "little", signed=True)

    def code(self) -> str:
        return self.take(4)[::-1].decode("latin1")

    def pascal_string(self) -> str:
        return self.take(self.uint8()).decode("latin1", errors="replace")

    def unicode_string(self) -> str:
        units = self.uint32()

        return self.take(units * 2).decode("utf-16-le").rstrip("\x00")


def _read_record(cursor: _Cursor, index: int) -> LinkedFile:
    start = cursor.offset

    record_size = cursor.int64()

    if record_size <= 0:
        raise ValueError(f"implausible record length: {record_size}")

    kind = cursor.code()

    if kind not in LINK_KINDS:
        raise ValueError(f"unknown record type {kind!r}")

    version = cursor.uint32()
    uid = cursor.pascal_string()
    name = cursor.unicode_string()
    file_type = cursor.code()
    creator = cursor.code()

    size_offset = cursor.offset
    size = cursor.int64()

    has_descriptor = cursor.uint8() != 0

    if has_descriptor:
        _skip_open_descriptor(cursor)

    return LinkedFile(
        index=index,
        kind=kind,
        version=version,
        uid=uid,
        name=name,
        file_type=file_type,
        creator=creator,
        size=size,
        offset=start,
        record_size=record_size,
        has_descriptor=has_descriptor,
        data_offset=cursor.offset,
        size_offset=size_offset,
    )


#: The open descriptor can be complex, but always fits in a few kB.
_DESCRIPTOR_WINDOW = 1 << 16


def _skip_open_descriptor(cursor: _Cursor) -> None:
    """
    Moves the cursor past the file's opening descriptor.

    The descriptor is preceded by a 4-byte version, and is itself
    byte-swapped, exactly like the rest of ImageSourceData.
    """
    window = cursor.reader.read_at(
        cursor.offset,
        min(_DESCRIPTOR_WINDOW, cursor.end - cursor.offset),
    )

    try:
        result = parse_descriptor(window, 4)
    except DescriptorError as error:
        raise ValueError(f"opening descriptor: {error}") from error

    cursor.offset += result.consumed


def parse_links(reader: ByteReader, start: int, end: int) -> LinkedFiles:
    """
    Reads the list of linked files.

    >>> from optiff.readers import BytesReader
    >>> body = (
    ...     b"DFil" + (7).to_bytes(4, "little")
    ...     + bytes([3]) + b"abc"
    ...     + (4).to_bytes(4, "little") + "a.psb\\x00"[:4].encode("utf-16-le")
    ...     + b"BPB8" + b"MIB8"
    ...     + (100).to_bytes(8, "little") + bytes([0])
    ... )
    >>> data = len(body).to_bytes(8, "little") + body
    >>> result = parse_links(BytesReader(data), 0, len(data))
    >>> result.files[0].kind_name, result.files[0].file_type_name
    ('embedded', 'PSB')
    >>> result.files[0].size
    100
    >>> result.is_exact
    True
    """
    cursor = _Cursor(reader, start, end)
    files: list[LinkedFile] = []
    warnings: list[ParseWarning] = []

    index = 0

    while cursor.offset + _HEADER_MIN <= end:
        record_start = cursor.offset

        # Padding alone at the end of the block is not an error.
        if not any(reader.read_at(record_start, min(64, end - record_start))):
            cursor.offset = end
            break

        try:
            record = _read_record(cursor, index)
        except ValueError as error:
            warnings.append(
                ParseWarning("link-record-failed", record_start, str(error))
            )
            break

        files.append(record)
        index += 1

        cursor.offset = record_start + 8 + _align4(record.record_size)

        if cursor.offset > end:
            warnings.append(
                ParseWarning(
                    "link-record-overrun",
                    record_start,
                    f"the record reaches {cursor.offset - start} B "
                    f"in a block of {end - start} B",
                )
            )
            cursor.offset = end
            break

    consumed = cursor.offset - start

    if files and consumed != end - start:
        remainder = reader.read_at(cursor.offset, min(64, end - cursor.offset))

        if any(remainder):
            warnings.append(
                ParseWarning(
                    "link-trailing-bytes",
                    cursor.offset,
                    f"{end - cursor.offset} B outside the records",
                )
            )

    return LinkedFiles(
        files=tuple(files),
        consumed=consumed,
        total=end - start,
        warnings=tuple(warnings),
    )


def read_linked_files(
    analysis: PhotoshopAnalysis,
    reader: ByteReader,
) -> LinkedFiles | None:
    """Finds the block with the linked objects and reads it."""
    block = next(
        (
            item
            for item in analysis.blocks
            if item.key in LINK_BLOCK_KEYS and item.size > 0
        ),
        None,
    )

    if block is None:
        return None

    return parse_links(
        reader,
        block.payload_offset,
        block.payload_offset + block.size,
    )
