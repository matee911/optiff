"""
Parser for Photoshop ImageSourceData blocks.

Blocks are written in the byte order of the TIFF file, so in a little-endian
TIFF the signature `8BIM` sits on disk as `MIB8` and the key `Lr16` as `61rL`.
The byte order is recognised from the signature itself rather than from the
TIFF header, which lets the parser run on a bare stream with no TIFF around
it.

Layout of a single block::

    signature (4 B) | key (4 B) | length (4 or 8 B) | payload | pad to 4 B

We walk headers, not payloads: the cost is linear in the number of blocks,
not in the tag size (which can reach 2.7 GB).
"""

from __future__ import annotations

from optiff.domain import ByteOrder, ParseWarning, PhotoshopBlock
from optiff.readers import ByteReader

#: on-disk signature -> (logical signature, byte order, length size, header size)
LAYOUTS: dict[bytes, tuple[str, ByteOrder, int, int]] = {
    b"8BIM": ("8BIM", ">", 4, 12),
    b"MIB8": ("8BIM", "<", 4, 12),
    b"8B64": ("8B64", ">", 8, 16),
    b"46B8": ("8B64", "<", 8, 16),
}

MIN_HEADER = 12

#: Keys that carry an 8-byte length instead of a 4-byte one inside a
#: "V0002" container (the PSB large-document rule). Verified against 10
#: files: with this list each of them is consumed to the byte.
LARGE_LENGTH_KEYS = frozenset(
    {
        "Alph",
        "FEid",
        "FMsk",
        "FXid",
        "LMsk",
        "Layr",
        "Lr16",
        "Lr32",
        "Mt16",
        "Mt32",
        "Mtrn",
        "PxSD",
        "lnk2",
    }
)

KEY_DESCRIPTIONS: dict[str, str] = {
    "Layr": "Layer information",
    "Lr16": "Layers (16-bit)",
    "Lr32": "Layers (32-bit)",
    "LMsk": "Layer mask",
    "FMsk": "Filter mask",
    "Patt": "Patterns",
    "Pat2": "Patterns (version 2)",
    "Pat3": "Patterns (version 3)",
    "lnk2": "Linked smart objects",
    "lnk3": "Linked smart objects (version 3)",
    "lnkD": "Linked smart objects (data)",
    "lnkE": "Linked smart objects (external)",
    "FEid": "Filter effects",
    "cinf": "Compositor info",
    "GenI": "Generator settings",
    "CAI ": "Content Authenticity (C2PA)",
    "Alph": "Alpha channels",
    "Anno": "Annotations",
    "XMP ": "XMP metadata",
    "vmsk": "Vector mask",
    "vsms": "Vector mask (shape)",
    "Txt2": "Text engine data",
    "PxSD": "Pixel source data",
    "artb": "Artboard data",
    "artd": "Artboard data",
}


def align4(value: int) -> int:
    """
    Size rounded up to a multiple of 4.

    >>> [align4(n) for n in (0, 1, 3, 4, 5)]
    [0, 4, 4, 4, 8]
    """
    return (value + 3) & ~3


def detect_layout(signature: bytes) -> tuple[str, ByteOrder, int, int] | None:
    """
    Recognises the block layout from the raw signature.

    Returns `(logical signature, byte order, length size, header size)`
    or `None` when the signature is unknown.

    >>> detect_layout(b"MIB8")
    ('8BIM', '<', 4, 12)
    >>> detect_layout(b"8BIM")
    ('8BIM', '>', 4, 12)
    >>> detect_layout(b"46B8")
    ('8B64', '<', 8, 16)
    >>> detect_layout(b"zzzz") is None
    True
    """
    return LAYOUTS.get(signature)


def logical_key(raw_key: str, byte_order: ByteOrder) -> str:
    """
    The key in logical form, reversed when the stream is little-endian.

    >>> logical_key("61rL", "<")
    'Lr16'
    >>> logical_key("Lr16", ">")
    'Lr16'
    >>> logical_key(" IAC", "<")
    'CAI '
    """
    return raw_key[::-1] if byte_order == "<" else raw_key


def describe_key(key: str) -> str:
    """
    The description of a resource, by its logical key.

    >>> describe_key("Lr16")
    'Layers (16-bit)'
    >>> describe_key("CAI ")
    'Content Authenticity (C2PA)'
    >>> describe_key("zzzz")
    'Photoshop resource'
    """
    return KEY_DESCRIPTIONS.get(key, "Photoshop resource")


def walk(
    reader: ByteReader,
    start: int,
    end: int,
    *,
    large_document: bool = False,
) -> tuple[tuple[PhotoshopBlock, ...], tuple[ParseWarning, ...]]:
    """
    Walks the block stream from `start` to `end`.

    `large_document` enables the PSB rule: keys in `LARGE_LENGTH_KEYS` then
    carry an 8-byte length. It is set from the container signature ("V0002"),
    never guessed from the content.

    Returns `(blocks, warnings)`. A well-formed stream ends exactly at `end`
    and emits no warning at all - a property that doubles as a free
    consistency check and is asserted against production files.

    >>> from optiff.readers import BytesReader
    >>> data = (
    ...     b"MIB8" + b"61rL" + (2).to_bytes(4, "little") + b"ab" + b"\\x00\\x00"
    ...     + b"MIB8" + b"fnic" + (1).to_bytes(4, "little") + b"x" + b"\\x00" * 3
    ... )
    >>> blocks, warnings = walk(BytesReader(data), 0, len(data))
    >>> [block.key for block in blocks]
    ['Lr16', 'cinf']
    >>> blocks[-1].end == len(data)
    True
    >>> warnings
    ()
    """
    blocks: list[PhotoshopBlock] = []
    warnings: list[ParseWarning] = []

    cursor = start
    stream_order: ByteOrder | None = None

    while cursor < end:
        remaining = end - cursor

        if remaining < MIN_HEADER:
            if any(reader.read_at(cursor, remaining)):
                warnings.append(
                    ParseWarning(
                        "trailing-bytes",
                        cursor,
                        f"{remaining} B outside any block",
                    )
                )
            break

        raw_signature = reader.read_at(cursor, 4)
        layout = detect_layout(raw_signature)

        if layout is None:
            warnings.append(
                ParseWarning("unknown-signature", cursor, repr(raw_signature))
            )
            break

        signature, byte_order, length_size, header_size = layout

        raw_key = reader.read_at(cursor + 4, 4).decode("latin1", errors="replace")
        key = logical_key(raw_key, byte_order)

        # PSB rule: inside a "V0002" container selected keys carry an
        # 8-byte length despite the ordinary 8BIM signature.
        if large_document and length_size == 4 and key in LARGE_LENGTH_KEYS:
            length_size = 8
            header_size = 16

        if stream_order is None:
            stream_order = byte_order
        elif byte_order != stream_order:
            warnings.append(
                ParseWarning(
                    "mixed-byte-order",
                    cursor,
                    f"block {byte_order}, stream {stream_order}",
                )
            )

        if remaining < header_size:
            warnings.append(
                ParseWarning(
                    "trailing-bytes",
                    cursor,
                    f"header {signature} truncated ({remaining} B)",
                )
            )
            break

        size = int.from_bytes(
            reader.read_at(cursor + 8, length_size),
            "little" if byte_order == "<" else "big",
        )

        padded_size = header_size + align4(size)

        if cursor + padded_size > end:
            warnings.append(
                ParseWarning(
                    "length-overrun",
                    cursor,
                    f"length {size} exceeds the available {remaining - header_size} B",
                )
            )
            break

        blocks.append(
            PhotoshopBlock(
                signature=signature,
                key=key,
                offset=cursor,
                size=size,
                padded_size=padded_size,
                description=describe_key(key),
                payload_offset=cursor + header_size,
                raw_signature=raw_signature,
                raw_key=raw_key,
                byte_order=byte_order,
                header_size=header_size,
            )
        )

        cursor += padded_size

    return tuple(blocks), tuple(warnings)
