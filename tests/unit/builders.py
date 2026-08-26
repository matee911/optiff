"""
Building synthetic Photoshop block streams.

The same builder feeds the parser unit tests and the integration
fixtures, so the synthetic TIFF runs it through the real tifffile writer.
"""

from __future__ import annotations

CONTAINER_SIGNATURE = b"Adobe Photoshop Document Data Block"

#: The container header including its NUL terminator: 36 bytes.
CONTAINER_HEADER = CONTAINER_SIGNATURE + b"\x00"

#: The variant newer Photoshop versions write, in which selected keys
#: carry an 8-byte length.
CONTAINER_V0002 = b"Adobe Photoshop Document Data V0002\x00"


def _swap(value: bytes, byte_order: str) -> bytes:
    """Photoshop writes the signature and key in the TIFF file byte order."""
    return value[::-1] if byte_order == "<" else value


def psd_block(  # noqa: PLR0913  - test builder, one knob per variant
    key: str,
    payload: bytes,
    *,
    byte_order: str = "<",
    signature: str = "8BIM",
    declared_length: int | None = None,
    length_size: int | None = None,
) -> bytes:
    """
    A single block: signature, key, length, payload, padding to 4 B.

    `declared_length` lets a test lie about the size on purpose (overrun
    test). `length_size` forces the width of the length field, which the
    "V0002" container needs, where selected keys use 8 bytes despite an
    `8BIM` signature.

    >>> psd_block("Lr16", b"ab", byte_order=">")
    b'8BIMLr16\\x00\\x00\\x00\\x02ab\\x00\\x00'
    >>> psd_block("Lr16", b"ab", byte_order="<")
    b'MIB861rL\\x02\\x00\\x00\\x00ab\\x00\\x00'
    >>> len(psd_block("Lr16", b"ab", length_size=8))
    20
    """
    length = len(payload) if declared_length is None else declared_length
    padding = (-len(payload)) % 4

    return (
        psd_block_header(
            key,
            length,
            byte_order=byte_order,
            signature=signature,
            length_size=length_size,
        )
        + payload
        + b"\x00" * padding
    )


def psd_block_header(
    key: str,
    length: int,
    *,
    byte_order: str = "<",
    signature: str = "8BIM",
    length_size: int | None = None,
) -> bytes:
    """
    The signature+key+length prefix of a block, without its payload.

    Split out of `psd_block` so a streaming writer - one whose payload is too
    large to hold in memory - can still get a correct block header from a
    length alone.

    >>> psd_block_header("Lr16", 2, byte_order=">")
    b'8BIMLr16\\x00\\x00\\x00\\x02'
    """
    if len(key) != 4:
        raise ValueError(f"The key must be 4 characters, got {key!r}")

    if length_size is None:
        length_size = 8 if signature == "8B64" else 4
    order = "little" if byte_order == "<" else "big"

    return (
        _swap(signature.encode("latin1"), byte_order)
        + _swap(key.encode("latin1"), byte_order)
        + length.to_bytes(length_size, order)
    )


def psd_stream(
    *blocks: tuple[str, bytes],
    byte_order: str = "<",
    signature: str = "8BIM",
    large_length_keys: frozenset[str] = frozenset(),
) -> bytes:
    """
    A run of blocks with no container header.

    `large_length_keys` reproduces the PSB rule: inside a "V0002" container
    those keys carry an 8-byte length even though the signature stays "8BIM".

    >>> stream = psd_stream(("Lr16", b"ab"), ("Pat2", b""))
    >>> len(stream)                       # 12+2+2 (padding) and 12+0+0
    28
    >>> len(psd_stream(("Lr16", b"ab"), large_length_keys=frozenset({"Lr16"})))
    20
    """
    return b"".join(
        psd_block(
            key,
            payload,
            byte_order=byte_order,
            signature=signature,
            length_size=8 if key in large_length_keys else None,
        )
        for key, payload in blocks
    )


def layer_extra_block(key: str, payload: bytes) -> bytes:
    """An additional block inside a layer record (`MIB8` on disk)."""
    padding = (-len(payload)) % 4

    return (
        b"8BIM"[::-1]
        + key.encode("latin1")[::-1]
        + len(payload).to_bytes(4, "little")
        + payload
        + b"\x00" * padding
    )


def unicode_name(name: str) -> bytes:
    """The `luni` block payload: the UTF-16 unit count plus a terminator."""
    return (len(name) + 1).to_bytes(4, "little") + (name + "\x00").encode("utf-16-le")


def pascal_name(name: str) -> bytes:
    """
    A Pascal name padded to a multiple of 4 bytes.

    The field is single byte, so characters outside latin1 land as "?".
    Photoshop does the same, which is why the full name lives separately in
    the `luni` block.
    """
    encoded = name.encode("latin1", errors="replace")[:255]

    raw = bytes([len(encoded)]) + encoded

    return raw + b"\x00" * ((-len(raw)) % 4)


def layer_record(  # noqa: PLR0913  - a test builder, one knob per variant
    *,
    name: str = "Layer",
    bounds: tuple[int, int, int, int] = (0, 0, 10, 20),
    channels: tuple[tuple[int, int], ...] = ((0, 0),),
    blend: str = "norm",
    opacity: int = 255,
    flags: int = 0,
    extras: bytes = b"",
    unicode: bool = True,
    large: bool = False,
) -> bytes:
    """
    A single layer record in byte-swapped form.

    `large` picks the width of a channel size field: 8 bytes inside a "V0002"
    container, 4 bytes inside the classic one. Getting it wrong does not fail
    loudly - the record just parses into nonsense - so it has to follow
    whichever container the record is going into.
    """
    top, left, bottom, right = bounds

    blocks = extras

    if unicode:
        blocks += layer_extra_block("luni", unicode_name(name))

    extra = (
        (0).to_bytes(4, "little")  # no layer mask
        + (0).to_bytes(4, "little")  # no blending ranges
        + pascal_name(name)
        + blocks
    )

    return (
        b"".join(
            value.to_bytes(4, "little", signed=True)
            for value in (top, left, bottom, right)
        )
        + len(channels).to_bytes(2, "little")
        + b"".join(
            channel_id.to_bytes(2, "little", signed=True)
            + size.to_bytes(8 if large else 4, "little")
            for channel_id, size in channels
        )
        + b"8BIM"[::-1]
        + blend.encode("latin1")[::-1]
        + bytes([opacity, 0, flags, 0])
        + len(extra).to_bytes(4, "little")
        + extra
    )


def layer_section(*records: bytes, count: int | None = None) -> bytes:
    """A layer section: the counter plus records, with no channel data."""
    declared = len(records) if count is None else count

    return declared.to_bytes(2, "little", signed=True) + b"".join(records)


def psd_container(
    *blocks: tuple[str, bytes],
    byte_order: str = "<",
    signature: str = "8BIM",
    header: bytes = CONTAINER_HEADER,
    large_length_keys: frozenset[str] = frozenset(),
) -> bytes:
    """
    The full content of tag 37724: container header plus blocks.

    >>> blob = psd_container(("Lr16", b"ab"))
    >>> blob.startswith(CONTAINER_HEADER)
    True
    >>> len(blob) - len(CONTAINER_HEADER)
    16
    """
    return header + psd_stream(
        *blocks,
        byte_order=byte_order,
        signature=signature,
        large_length_keys=large_length_keys,
    )


# ============================================================================
# A MINIMAL TIFF
# ============================================================================

#: An IFD entry: code(2) + type(2) + count(4) + value-or-offset(4).
IFD_ENTRY = 12

SHORT, LONG, UNDEFINED = 3, 4, 7


def _entry(code: int, dtype: int, count: int, value: int) -> bytes:
    return (
        code.to_bytes(2, "little")
        + dtype.to_bytes(2, "little")
        + count.to_bytes(4, "little")
        + value.to_bytes(4, "little")
    )


def tiff_header_and_ifd(  # noqa: PLR0913  - a test builder, one knob per variant
    *,
    width: int,
    height: int,
    photoshop_last: bool,
    image_len: int,
    photoshop_len: int,
    compression: int,
) -> bytes:
    """
    Everything in a classic little-endian TIFF before the image/photoshop tail.

    Split out of `build_tiff` so a streaming writer - one that cannot
    materialise the (possibly gigabytes-large) tail in memory - can still get
    a correct header and IFD from lengths alone.

    >>> tiff_header_and_ifd(
    ...     width=8, height=4, photoshop_last=True,
    ...     image_len=192, photoshop_len=16, compression=1,
    ... )[:4]
    b'II*\\x00'
    """
    samples, bits = 3, 16

    codes = [256, 257, 258, 259, 262, 273, 277, 278, 279, 37724]

    ifd_at = 8
    ifd_size = 2 + len(codes) * IFD_ENTRY + 4
    values_at = ifd_at + ifd_size

    bits_at = values_at
    after_bits = bits_at + samples * 2

    if photoshop_last:
        image_at = after_bits
        photoshop_at = image_at + image_len
    else:
        photoshop_at = after_bits
        image_at = photoshop_at + photoshop_len

    entries = b"".join(
        [
            _entry(256, SHORT, 1, width),
            _entry(257, SHORT, 1, height),
            _entry(258, SHORT, samples, bits_at),
            _entry(259, SHORT, 1, compression),
            _entry(262, SHORT, 1, 2),  # RGB
            _entry(273, LONG, 1, image_at),
            _entry(277, SHORT, 1, samples),
            _entry(278, LONG, 1, height),
            _entry(279, LONG, 1, image_len),
            _entry(37724, UNDEFINED, photoshop_len, photoshop_at),
        ]
    )

    ifd = len(codes).to_bytes(2, "little") + entries + (0).to_bytes(4, "little")

    header = b"II" + (42).to_bytes(2, "little") + ifd_at.to_bytes(4, "little")

    body = b"".join(bits.to_bytes(2, "little") for _ in range(samples))

    return header + ifd + body


def build_tiff(  # noqa: PLR0913  - a test builder, one knob per variant
    photoshop: bytes,
    *,
    width: int = 8,
    height: int = 4,
    photoshop_last: bool = True,
    image: bytes | None = None,
    compression: int = 1,
) -> bytes:
    """
    A classic little-endian TIFF carrying tag 37724.

    We choose the layout ourselves, because it decides whether the optimizer
    can shorten the file without moving offsets. `photoshop_last=False`
    yields a file it has to refuse.

    >>> data = build_tiff(b"x" * 16)
    >>> data[:4]
    b'II*\\x00'
    """
    samples, bits = 3, 16
    expected = width * height * samples * (bits // 8)

    if image is None:
        image = bytes(expected)

    # With compression 1 the strip holds pixels, so its length is fixed. With
    # anything else it holds an already-encoded payload of any length, and the
    # caller is the one who knows what it decodes to.
    if compression == 1 and len(image) != expected:
        raise ValueError(f"the image has {len(image)} B, expected {expected}")

    head = tiff_header_and_ifd(
        width=width,
        height=height,
        photoshop_last=photoshop_last,
        image_len=len(image),
        photoshop_len=len(photoshop),
        compression=compression,
    )

    tail = image + photoshop if photoshop_last else photoshop + image

    return head + tail


def psb_file(layers: bytes, *, width: int = 8, height: int = 4) -> bytes:
    """
    A minimal PSB file (big-endian) with its layers in the extra Lr16 block.

    That is exactly how Photoshop writes 16-bit documents: the classic Layer
    Info section has length 0, and the layers live in Lr16.
    """
    block_payload = layers + b"\x00" * ((-len(layers)) % 4)
    block = b"8BIM" + b"Lr16" + len(layers).to_bytes(8, "big") + block_payload

    mask = (0).to_bytes(8, "big") + (0).to_bytes(4, "big") + block

    header = (
        b"8BPS"
        + (2).to_bytes(2, "big")
        + bytes(6)
        + (3).to_bytes(2, "big")
        + height.to_bytes(4, "big")
        + width.to_bytes(4, "big")
        + (16).to_bytes(2, "big")
        + (3).to_bytes(2, "big")
    )

    return (
        header
        + (0).to_bytes(4, "big")
        + (0).to_bytes(4, "big")
        + len(mask).to_bytes(8, "big")
        + mask
        + (1).to_bytes(2, "big")
    )


#: Fields written BEHIND the file data in an lnk2 record (versions 5-7).
LINK_RECORD_TAIL = (1).to_bytes(4, "little") + b"\x00\x00" + b"\x00" * 8 + b"\x00"


def link_record_with_psb(layers: bytes, *, name: str = "smart.psb") -> bytes:
    """An lnk2 record with an embedded PSB and a complete tail."""
    data = psb_file(layers)

    body = (
        b"liFD"[::-1]
        + (7).to_bytes(4, "little")
        + bytes([3])
        + b"abc"
        + (len(name) + 1).to_bytes(4, "little")
        + (name + "\x00").encode("utf-16-le")
        + b"8BPB"[::-1]
        + b"8BIM"[::-1]
        + len(data).to_bytes(8, "little")
        + bytes([0])
        + data
        + LINK_RECORD_TAIL
    )

    return len(body).to_bytes(8, "little") + body + b"\x00" * ((-len(body)) % 4)


def layer_record_be(
    *,
    name: str = "Background",
    bounds: tuple[int, int, int, int] = (0, 0, 10, 20),
    channels: tuple[tuple[int, int], ...] = ((0, 0),),
) -> bytes:
    """
    A layer record in a raw PSB file: big-endian, codes the right way
    round, 8-byte channel sizes.
    """
    top, left, bottom, right = bounds

    pascal = bytes([len(name)]) + name.encode("latin1")
    pascal += b"\x00" * ((-len(pascal)) % 4)

    extra = (0).to_bytes(4, "big") + (0).to_bytes(4, "big") + pascal

    return (
        b"".join(v.to_bytes(4, "big", signed=True) for v in (top, left, bottom, right))
        + len(channels).to_bytes(2, "big")
        + b"".join(
            cid.to_bytes(2, "big", signed=True) + size.to_bytes(8, "big")
            for cid, size in channels
        )
        + b"8BIM"
        + b"norm"
        + bytes([255, 0, 0, 0])
        + len(extra).to_bytes(4, "big")
        + extra
    )


def layer_section_be(*records: bytes, count: int | None = None) -> bytes:
    """The layer section of a raw PSB."""
    declared = len(records) if count is None else count

    return declared.to_bytes(2, "big", signed=True) + b"".join(records)
