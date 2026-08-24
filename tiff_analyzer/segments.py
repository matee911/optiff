"""
Describing the output file as a list of segments.

With multi-gigabyte files the output cannot be built in memory. Instead we
describe it with a plan: "copy this range from the source" or "insert these
bytes". The total length is computed without touching the data, and writing is
strumieniowy.

Plans nest: a rebuilt layer section is simply a list of segments spliced
into the segment list of the enclosing block.
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import BinaryIO, Union

from tiff_analyzer.readers import ByteReader

#: How many bytes to copy at a time while streaming.
CHUNK = 4 * 1024 * 1024


@dataclass(frozen=True)
class Copy:
    """A range copied from the source unchanged."""

    offset: int
    size: int

    def __post_init__(self) -> None:
        if self.offset < 0 or self.size < 0:
            raise ValueError(
                f"niepoprawny zakres: offset={self.offset}, size={self.size}"
            )


@dataclass(frozen=True)
class Literal:
    """New bytes inserted in place of the original."""

    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)


Segment = Copy | Literal

#: A plan may contain nested lists; they are flattened on write.
Plan = Iterable[Union[Segment, "Plan"]]


def flatten(plan: Plan) -> Iterator[Segment]:
    """
    Expands nested segment lists.

    >>> list(flatten([Copy(0, 4), [Literal(b"ab"), [Copy(8, 2)]]]))
    [Copy(offset=0, size=4), Literal(data=b'ab'), Copy(offset=8, size=2)]
    """
    for item in plan:
        if isinstance(item, (Copy, Literal)):
            yield item
        else:
            yield from flatten(item)


def rebase(plan: Plan, delta: int) -> list[Segment]:
    """
    Shifts every source read by `delta`.

    A plan built over a window (a tag value, say) has to be shifted before it
    can run against the whole file.

    >>> rebase([Copy(0, 4), Literal(b"ab"), Copy(10, 2)], 100)
    [Copy(offset=100, size=4), Literal(data=b'ab'), Copy(offset=110, size=2)]
    """
    return [
        Copy(segment.offset + delta, segment.size)
        if isinstance(segment, Copy)
        else segment
        for segment in flatten(plan)
    ]


def apply_patches(
    start: int,
    end: int,
    patches: Iterable[tuple[int, bytes]],
) -> list[Segment]:
    """
    Copies a range from the source, replacing the given bytes.

    Patches are given as `(offset, new_bytes)` relative to the source. The
    output size equals the input size, so offsets outside the patched range
    do not move.

    >>> apply_patches(0, 10, [(4, b"XY")])
    [Copy(offset=0, size=4), Literal(data=b'XY'), Copy(offset=6, size=4)]
    >>> apply_patches(0, 6, [])
    [Copy(offset=0, size=6)]
    """
    segments: list[Segment] = []
    cursor = start

    for offset, data in sorted(patches):
        if offset < cursor:
            raise ValueError(f"patches overlap @0x{offset:X}")

        if offset + len(data) > end:
            raise ValueError(f"patch falls outside the range @0x{offset:X}")

        if offset > cursor:
            segments.append(Copy(cursor, offset - cursor))

        segments.append(Literal(data))
        cursor = offset + len(data)

    if cursor < end:
        segments.append(Copy(cursor, end - cursor))

    return segments


def total_size(plan: Plan) -> int:
    """
    The output size without reading a single byte of data.

    >>> total_size([Copy(0, 100), Literal(b"abc"), Copy(500, 20)])
    123
    >>> total_size([])
    0
    """
    return sum(segment.size for segment in flatten(plan))


def write_plan(plan: Plan, reader: ByteReader, output: BinaryIO) -> int:
    """
    Writes the plan to a stream and returns the number of bytes written.

    >>> import io
    >>> from tiff_analyzer.readers import BytesReader
    >>> source = BytesReader(b"0123456789")
    >>> out = io.BytesIO()
    >>> write_plan([Copy(0, 3), Literal(b"XY"), Copy(8, 2)], source, out)
    7
    >>> out.getvalue()
    b'012XY89'
    """
    written = 0

    for segment in flatten(plan):
        if isinstance(segment, Literal):
            output.write(segment.data)
            written += segment.size
            continue

        remaining = segment.size
        cursor = segment.offset

        while remaining > 0:
            chunk = reader.read_at(cursor, min(CHUNK, remaining))

            if not chunk:
                raise ValueError(f"source ended mid-copy @0x{cursor:X}")

            output.write(chunk)
            written += len(chunk)
            cursor += len(chunk)
            remaining -= len(chunk)

    return written


def materialise(plan: Plan, reader: ByteReader) -> bytes:
    """
    Returns the plan as bytes. Small structures and tests only.

    >>> from tiff_analyzer.readers import BytesReader
    >>> materialise([Copy(2, 3), Literal(b"!")], BytesReader(b"abcdef"))
    b'cde!'
    """
    buffer = io.BytesIO()
    write_plan(plan, reader, buffer)

    return buffer.getvalue()
