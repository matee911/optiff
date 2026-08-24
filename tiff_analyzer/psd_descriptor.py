"""
Parser for Photoshop Action Descriptors.

The format is recursive: a descriptor holds named fields, and a field may be
another descriptor or a list of descriptors.

Inside ImageSourceData embedded in a little-endian TIFF, a byte-swapped
byte-swapped, zweryfikowany na blokach `cinf` i `GenI`:

- numbers (`uint32`, `uint64`, `double`) are **little-endian**
- 4-character codes (OSType, classID, length-less keys) are **reversed**
  bajtowo: `gnol` to `long`, `cjbO` to `Objc`, `sLlV` to `VlLs`
- length-prefixed strings stay in **normal** order

Photoshop writes an empty Unicode string as length 1 plus one NUL, hence
the `01 00 00 00 00 00` at the start of a typical descriptor.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

#: Header size before the descriptor, per block key. Verified by exact
#: payload consumption: `cinf` and `GenI` end on the byte.
DESCRIPTOR_OFFSETS: dict[str, int] = {
    "cinf": 4,  # uint32 wersja (16)
    "GenI": 4,  # uint32 wersja (16)
    "CAI ": 8,  # uint32 wersja (3) + uint32 wersja deskryptora (16)
}


class DescriptorError(ValueError):
    """The stream cannot be read as a descriptor."""


@dataclass
class Descriptor:
    """Deskryptor: name, klasa i nazwane pola."""

    name: str
    classid: str
    items: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.items.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.items[key]

    def __contains__(self, key: str) -> bool:
        return key in self.items

    def flat(self, prefix: str = "") -> dict[str, Any]:
        """
        Fields flattened into `a.b.c` paths.

        >>> inner = Descriptor("", "null", {"major": 1, "minor": 3})
        >>> outer = Descriptor("", "null", {"Vrsn": inner, "on": True})
        >>> outer.flat() == {"Vrsn.major": 1, "Vrsn.minor": 3, "on": True}
        True
        """
        result: dict[str, Any] = {}

        for key, value in self.items.items():
            path = f"{prefix}{key}"

            if isinstance(value, Descriptor):
                result.update(value.flat(f"{path}."))
            else:
                result[path] = value

        return result


@dataclass(frozen=True)
class ParsedDescriptor:
    """The parse result together with how many bytes were left over."""

    descriptor: Descriptor
    consumed: int
    total: int

    @property
    def trailing(self) -> int:
        return self.total - self.consumed

    @property
    def is_exact(self) -> bool:
        return self.trailing == 0


class _Cursor:
    """Czytnik strumienia byte-swapped."""

    def __init__(self, data: bytes, offset: int = 0):
        self.data = data
        self.offset = offset

    def _take(self, count: int) -> bytes:
        if count < 0 or self.offset + count > len(self.data):
            raise DescriptorError(
                f"read of {count} B past the end of the stream @0x{self.offset:X}"
            )

        chunk = self.data[self.offset : self.offset + count]
        self.offset += count

        return chunk

    def uint32(self) -> int:
        return int.from_bytes(self._take(4), "little")

    def uint64(self) -> int:
        return int.from_bytes(self._take(8), "little")

    def double(self) -> float:
        return struct.unpack("<d", self._take(8))[0]

    def boolean(self) -> bool:
        return self._take(1)[0] != 0

    def blob(self) -> bytes:
        """Raw data with a length prefix."""
        return self._take(self.uint32())

    def code(self) -> str:
        """
        A four-byte OSType, reversed on disk.

        Codes are always printable ASCII (`long`, `Objc`, `#Ang`), so
        anything else means we are not reading a descriptor. Without this
        check a stream of pure zeros parses as an empty descriptor.
        """
        offset = self.offset
        raw = self._take(4)[::-1]

        if not all(32 <= byte < 127 for byte in raw):
            raise DescriptorError(f"code {raw!r} is not ASCII @0x{offset:X}")

        return raw.decode("latin1")

    def key(self) -> str:
        """A field key: length 0 means a four-byte code."""
        length = self.uint32()

        if length == 0:
            return self.code()

        offset = self.offset
        raw = self._take(length)

        if not all(32 <= byte < 127 for byte in raw):
            raise DescriptorError(f"key {raw[:16]!r} is not ASCII @0x{offset:X}")

        return raw.decode("latin1")

    def unicode_string(self) -> str:
        """Length in UTF-16 units, then the characters; a trailing NUL is dropped."""
        units = self.uint32()

        return self._take(units * 2).decode("utf-16-le").rstrip("\x00")


def _read_list(cursor: _Cursor, depth: int) -> list[Any]:
    count = cursor.uint32()

    return [_read_value(cursor, cursor.code(), depth + 1) for _ in range(count)]


def _read_reference(cursor: _Cursor, _depth: int) -> list[str]:
    count = cursor.uint32()

    return [cursor.code() for _ in range(count)]


#: OSType -> the function that reads the value. Codes are already reversed
#: into logical form, so they match the names in the PSD specification.
_READERS: dict[str, Any] = {
    "Objc": lambda cursor, depth: _read_descriptor(cursor, depth + 1),
    "GlbO": lambda cursor, depth: _read_descriptor(cursor, depth + 1),
    "VlLs": _read_list,
    "long": lambda cursor, _depth: cursor.uint32(),
    "comp": lambda cursor, _depth: cursor.uint64(),
    "doub": lambda cursor, _depth: cursor.double(),
    "bool": lambda cursor, _depth: cursor.boolean(),
    "TEXT": lambda cursor, _depth: cursor.unicode_string(),
    "enum": lambda cursor, _depth: (cursor.key(), cursor.key()),
    "type": lambda cursor, _depth: cursor.key(),
    "GlbC": lambda cursor, _depth: cursor.key(),
    "UntF": lambda cursor, _depth: (cursor.code(), cursor.double()),
    "tdta": lambda cursor, _depth: cursor.blob(),
    "alis": lambda cursor, _depth: cursor.blob(),
    "obj ": _read_reference,
}

MAX_DEPTH = 32


def _read_value(cursor: _Cursor, ostype: str, depth: int) -> Any:
    if depth > MAX_DEPTH:
        raise DescriptorError(
            f"descriptor nesting exceeded {MAX_DEPTH} levels"
        )

    reader = _READERS.get(ostype)

    if reader is None:
        raise DescriptorError(f"unknown OSType {ostype!r} @0x{cursor.offset:X}")

    return reader(cursor, depth)


def _read_descriptor(cursor: _Cursor, depth: int = 0) -> Descriptor:
    name = cursor.unicode_string()
    classid = cursor.key()
    count = cursor.uint32()

    items: dict[str, Any] = {}

    for _ in range(count):
        key = cursor.key()
        items[key] = _read_value(cursor, cursor.code(), depth)

    return Descriptor(name=name, classid=classid, items=items)


def parse(data: bytes, offset: int = 0) -> ParsedDescriptor:
    """
    Reads the descriptor starting at `offset`.

    >>> stream = (
    ...     (1).to_bytes(4, "little") + b"\\x00\\x00"      # pusta name
    ...     + (0).to_bytes(4, "little") + b"llun"          # classID "null"
    ...     + (1).to_bytes(4, "little")                    # jedno pole
    ...     + (5).to_bytes(4, "little") + b"major"         # klucz
    ...     + b"gnol" + (3).to_bytes(4, "little")          # long = 3
    ... )
    >>> result = parse(stream)
    >>> result.descriptor.classid
    'null'
    >>> result.descriptor["major"]
    3
    >>> result.is_exact
    True
    """
    cursor = _Cursor(data, offset)

    descriptor = _read_descriptor(cursor)

    return ParsedDescriptor(
        descriptor=descriptor,
        consumed=cursor.offset,
        total=len(data),
    )


def parse_block(key: str, payload: bytes) -> ParsedDescriptor | None:
    """
    Parses the payload of a block whose header size is known.

    Returns `None` when the offset for that key is unknown or the stream
    cannot be read; we deliberately do not guess.

    >>> parse_block("Lr16", b"cokolwiek") is None
    True
    """
    offset = DESCRIPTOR_OFFSETS.get(key)

    if offset is None or len(payload) <= offset:
        return None

    try:
        return parse(payload, offset)
    except DescriptorError:
        return None
