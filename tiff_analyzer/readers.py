"""
Byte access through the narrow `ByteReader` interface.

The Photoshop block parser walks headers (12 B per block), not payloads.
Reading through `seek` instead of materialising the whole tag costs
O(number of blocks) instead of O(tag size) - which matters, because tag
37724 can reach 2.7 GB.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ByteReader(Protocol):
    """A byte window addressed by offsets relative to its own start."""

    @property
    def size(self) -> int: ...

    def read_at(self, offset: int, length: int) -> bytes: ...


class BytesReader:
    """
    A reader over plain `bytes`, used by unit tests.

    >>> reader = BytesReader(b"abcdef")
    >>> reader.size
    6
    >>> reader.read_at(2, 3)
    b'cde'
    >>> reader.read_at(4, 99)
    b'ef'
    >>> reader.read_at(99, 1)
    b''
    """

    def __init__(self, data: bytes):
        self._data = data

    @property
    def size(self) -> int:
        return len(self._data)

    def read_at(self, offset: int, length: int) -> bytes:
        if offset < 0 or length <= 0:
            return b""

        return self._data[offset : offset + length]


class FileWindowReader:
    """
    A reader over a slice of a file, addressed from the slice start.

    Holds an open handle; close it with `close()` or use it as a context
    manager.
    """

    def __init__(self, path: Path, start: int, size: int):
        self._path = path
        self._start = start
        self._size = size
        self._handle = path.open("rb")

    @property
    def size(self) -> int:
        return self._size

    def read_at(self, offset: int, length: int) -> bytes:
        if offset < 0 or length <= 0 or offset >= self._size:
            return b""

        length = min(length, self._size - offset)

        self._handle.seek(self._start + offset)

        return self._handle.read(length)

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> FileWindowReader:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
