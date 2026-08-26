"""
A generator for whole sample files, one per real-world case we know about.

Why it exists: the cases worth testing were found in real client files, which
must never enter the repository. Keeping the *recipe* instead of the file gives
the same coverage at a few kilobytes, and every case carries a note saying what
it reproduces.

Each entry in `CASES` builds a complete TIFF in memory. `sample_file` and
`sample_dir` (in `tests/conftest.py`) write them into pytest's `tmp_path`, so
nothing is ever left on disk.

To eyeball them in Photoshop or Affinity:

    python -m tests.sample_files /some/directory
"""

from __future__ import annotations

import argparse
import sys
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import numpy as np

from optiff.psd_codec import RAW, RLE, ZIP, ZIP_PREDICTED, ChannelGeometry
from tests.unit.builders import (
    CONTAINER_V0002,
    build_tiff,
    layer_extra_block,
    layer_record,
    layer_record_be,
    layer_section,
    layer_section_be,
    link_record_with_psb,
    psd_block_header,
    psd_container,
    tiff_header_and_ifd,
)

#: Canvas of the generated files. Small enough that a whole case builds in
#: milliseconds, large enough that compression has something to bite on.
WIDTH, ROWS = 96, 24

#: 16-bit channels, like every file this tool was written for.
BPP = 2

#: The compression-method header at the start of channel data.
HEADER = 2

GEOMETRY = ChannelGeometry(WIDTH, ROWS, BPP)

#: Section divider block: this is how Photoshop marks group boundaries.
SECTION_DIVIDER = "lsct"

GROUP_OPEN, GROUP_END = 1, 3


def _walk_chunk(rng: np.random.Generator, width: int, n_rows: int) -> bytes:
    """
    `n_rows` rows of the random walk, drawn from `rng`.

    Calling this repeatedly with the same `rng` and summing `n_rows` across
    calls gives the exact same bytes as one call with the total row count -
    `axis=1` cumsum resets every row, so rows never depend on each other, only
    on where the shared `rng` stream already is. That is what lets a large
    channel stream to disk in chunks instead of building in one array.

    >>> import numpy as np
    >>> whole = _walk_chunk(np.random.default_rng(1), 8, 6)
    >>> rng = np.random.default_rng(1)
    >>> chunked = _walk_chunk(rng, 8, 2) + _walk_chunk(rng, 8, 4)
    >>> whole == chunked
    True
    """
    walk = np.cumsum(rng.integers(-4, 5, size=(n_rows, width)), axis=1)

    return (walk % 65536).astype(">u2").tobytes()


def smooth(seed: int) -> bytes:
    """
    16-bit pixels with the local continuity real photographic layers have.

    Pure noise would compress to nothing and every case would look like a
    no-op, which is the opposite of what we want to measure.

    >>> len(smooth(1)) == WIDTH * ROWS * BPP
    True
    >>> smooth(1) == smooth(1)
    True
    >>> smooth(1) == smooth(2)
    False
    """
    return _walk_chunk(np.random.default_rng(seed), WIDTH, ROWS)


def packbits(data: bytes) -> bytes:
    """
    The PackBits encoder, so a channel can be stored the way Photoshop does.

    `psd_codec` only decodes RLE - we shrink files with deflate and never
    write RLE - so the encoder lives here, on the test side.

    >>> packbits(b"ABCXXXX")
    b'\\x02ABC\\xfdX'
    >>> packbits(b"")
    b''
    """
    out = bytearray()
    index = 0

    while index < len(data):
        run = 1

        while (
            index + run < len(data) and data[index + run] == data[index] and run < 128
        ):
            run += 1

        if run > 1:
            out += bytes([257 - run, data[index]])
            index += run
            continue

        start = index

        while (
            index < len(data)
            and index - start < 128
            and (index + 1 >= len(data) or data[index + 1] != data[index])
        ):
            index += 1

        out += bytes([index - start - 1]) + data[start:index]

    return bytes(out)


def rle_channel(pixels: bytes, *, large: bool = True) -> bytes:
    """
    A channel packed row by row, preceded by the table of row lengths.

    `large` picks the width of a row-length entry: 4 bytes in the PSB layout,
    2 bytes in the PSD one. The reader defaults to the PSB layout, because
    that is what 16-bit documents use.
    """
    width = 4 if large else 2
    row_bytes = WIDTH * BPP

    rows = [
        packbits(pixels[start : start + row_bytes])
        for start in range(0, len(pixels), row_bytes)
    ]

    table = b"".join(len(row).to_bytes(width, "big") for row in rows)

    return table + b"".join(rows)


def encoded(pixels: bytes, method: int) -> bytes:
    """Channel data for `method`, including its 2-byte method header."""
    if method == RAW:
        body = pixels
    elif method == RLE:
        body = rle_channel(pixels)
    elif method == ZIP:
        body = zlib.compress(pixels, 6)
    elif method == ZIP_PREDICTED:
        rows = np.frombuffer(pixels, dtype=">u2").reshape(ROWS, WIDTH)
        deltas = np.diff(rows.astype("int64"), axis=1, prepend=0) % 65536
        body = zlib.compress(deltas.astype(">u2").tobytes(), 6)
    else:
        raise ValueError(f"unknown compression method: {method}")

    return method.to_bytes(HEADER, "little") + body


def layers(
    *methods: int,
    name: str = "Background",
    extras: bytes = b"",
    large: bool = False,
) -> bytes:
    """A one-layer section whose channels use the given methods, in order."""
    payloads = [
        encoded(smooth(index + 1), method) for index, method in enumerate(methods)
    ]

    section = layer_section(
        layer_record(
            name=name,
            bounds=(0, 0, ROWS, WIDTH),
            channels=tuple(
                (index, len(payload)) for index, payload in enumerate(payloads)
            ),
            extras=extras,
            large=large,
        )
    )

    return section + b"".join(payloads)


def _section_divider(kind: int) -> bytes:
    return layer_extra_block(SECTION_DIVIDER, kind.to_bytes(4, "little"))


# ============================================================================
# THE CASES
# ============================================================================


def _raw_layers() -> bytes:
    return build_tiff(
        psd_container(("Lr16", layers(RAW, RAW, RAW))), width=WIDTH, height=ROWS
    )


def _rle_layers() -> bytes:
    return build_tiff(
        psd_container(("Lr16", layers(RLE, RLE, RLE))), width=WIDTH, height=ROWS
    )


def _packed_layers() -> bytes:
    return build_tiff(
        psd_container(("Lr16", layers(ZIP_PREDICTED, ZIP_PREDICTED, ZIP_PREDICTED))),
        width=WIDTH,
        height=ROWS,
    )


def _mixed_layers() -> bytes:
    return build_tiff(
        psd_container(("Lr16", layers(ZIP_PREDICTED, RAW, RAW))),
        width=WIDTH,
        height=ROWS,
    )


def _adjustment_mask() -> bytes:
    mask = encoded(smooth(7), RAW)

    section = layer_section(
        layer_record(
            name="Black & White 1",
            bounds=(0, 0, 0, 0),
            channels=((-2, len(mask)),),
        )
    )

    return build_tiff(psd_container(("Lr16", section + mask)), width=WIDTH, height=ROWS)


def _grouped_layers() -> bytes:
    payload = encoded(smooth(3), RAW)

    # Photoshop stores layers bottom-up, so the group end comes first and the
    # group header last.
    section = layer_section(
        layer_record(
            name="</Layer group>",
            bounds=(0, 0, 0, 0),
            channels=(),
            extras=_section_divider(GROUP_END),
        ),
        layer_record(
            name="Inside the group",
            bounds=(0, 0, ROWS, WIDTH),
            channels=((0, len(payload)),),
        ),
        layer_record(
            name="Retouch",
            bounds=(0, 0, 0, 0),
            channels=(),
            extras=_section_divider(GROUP_OPEN),
        ),
    )

    return build_tiff(
        psd_container(("Lr16", section + payload)), width=WIDTH, height=ROWS
    )


def _large_document_container() -> bytes:
    return build_tiff(
        psd_container(
            # In a "V0002" container the channel sizes inside the record are
            # 8 bytes wide as well, not only the block length.
            ("Lr16", layers(RAW, RAW, RAW, large=True)),
            header=CONTAINER_V0002,
            large_length_keys=frozenset({"Lr16"}),
        ),
        width=WIDTH,
        height=ROWS,
    )


def _smart_object() -> bytes:
    payloads = [encoded(smooth(index + 1), RAW) for index in range(3)]

    inner = layer_section_be(
        layer_record_be(
            bounds=(0, 0, ROWS, WIDTH),
            channels=tuple(
                (index, len(payload)) for index, payload in enumerate(payloads)
            ),
        )
    ) + b"".join(RAW.to_bytes(HEADER, "big") + payload[HEADER:] for payload in payloads)

    return build_tiff(
        psd_container(("lnk2", link_record_with_psb(inner))),
        width=WIDTH,
        height=ROWS,
    )


def _compressible_image() -> bytes:
    return build_tiff(
        psd_container(("Lr16", layers(RAW, RAW, RAW))),
        width=WIDTH,
        height=ROWS,
        image=smooth(99) * 3,
    )


def _compressed_image() -> bytes:
    return build_tiff(
        psd_container(("Lr16", layers(RAW, RAW, RAW))),
        width=WIDTH,
        height=ROWS,
        image=zlib.compress(smooth(99) * 3, 6),
        compression=8,
    )


def _photoshop_not_last() -> bytes:
    return build_tiff(
        psd_container(("Lr16", layers(RAW, RAW, RAW))),
        width=WIDTH,
        height=ROWS,
        photoshop_last=False,
    )


def _no_photoshop() -> bytes:
    return build_tiff(b"", width=WIDTH, height=ROWS)


@dataclass(frozen=True)
class Sample:
    """One generated file plus the case it stands for."""

    name: str
    summary: str
    build: Callable[[], bytes]
    #: Whether `write_scaled_raw_layers` can stream this case past RAM - true
    #: only for the RAW-only cases whose channel lengths are known up front.
    scalable: bool = False


CASES: dict[str, Sample] = {
    sample.name: sample
    for sample in (
        Sample(
            "raw-layers",
            "Layers stored uncompressed - the ordinary case and the big win.",
            _raw_layers,
            scalable=True,
        ),
        Sample(
            "rle-layers",
            "Layers stored as RLE, Photoshop's own default. We deliberately "
            "leave those alone, but we still have to read them to verify.",
            _rle_layers,
        ),
        Sample(
            "packed-layers",
            "Layers already ZIP with prediction: nothing left to gain, so the "
            "run has to cost close to nothing and change nothing.",
            _packed_layers,
        ),
        Sample(
            "mixed-layers",
            "One channel already packed, the rest raw - a file half-processed "
            "by an earlier run or by Photoshop itself.",
            _mixed_layers,
        ),
        Sample(
            "adjustment-mask",
            "An adjustment layer: a 0x0 rectangle, with all the bytes in the "
            "mask. Unreachable without --zip-fallback, because there is no "
            "geometry to decode against.",
            _adjustment_mask,
        ),
        Sample(
            "grouped-layers",
            "Layers nested in a group, marked by lsct section dividers.",
            _grouped_layers,
        ),
        Sample(
            "large-document-container",
            'The "V0002" container newer Photoshop versions write, in which '
            "Lr16 carries an 8-byte length.",
            _large_document_container,
        ),
        Sample(
            "smart-object",
            "A smart object: a whole PSB file embedded in an lnk2 record, "
            "big-endian, with the 15-byte tail behind the file data.",
            _smart_object,
        ),
        Sample(
            "compressible-image",
            "The flattened image stored uncompressed, so --image-data has "
            "something to do.",
            _compressible_image,
        ),
        Sample(
            "compressed-image",
            "The flattened image already deflated. --image-data has to say so "
            "in a note and carry on with the layers, not abort the run.",
            _compressed_image,
        ),
        Sample(
            "photoshop-not-last",
            "Tag 37724 sits before the image data. Shortening it would move "
            "every offset behind it, so the file must be refused.",
            _photoshop_not_last,
        ),
        Sample(
            "no-photoshop",
            "A plain TIFF with no Photoshop block at all.",
            _no_photoshop,
        ),
    )
}


def build(name: str) -> bytes:
    """
    The bytes of one case.

    >>> build("raw-layers")[:4]
    b'II*\\x00'
    >>> build("nope")
    Traceback (most recent call last):
    ...
    KeyError: "unknown case 'nope'"
    """
    if name not in CASES:
        raise KeyError(f"unknown case {name!r}")

    return CASES[name].build()


# ============================================================================
# STREAMING A LARGE FILE (--scale)
# ============================================================================

#: Cases whose channels are RAW - no RLE table, no zlib - so every byte
#: length is known before a single pixel is generated. Only these can stream
#: to disk instead of building fully in memory; scaling any other case would
#: need seek-based patching this file doesn't implement. Derived from each
#: `Sample`'s own `scalable` flag, not maintained as a separate list.
SCALABLE_CASES = frozenset(name for name, sample in CASES.items() if sample.scalable)

#: Refuse to write a file past this many bytes without --yes.
SIZE_WARN_BYTES = 500_000_000

#: Rows generated per write() call to the file, bounding peak memory use.
CHUNK_ROWS = 8_192


@dataclass(frozen=True)
class _ScaledLayout:
    """Every length and header byte needed to stream a scaled raw-layers file."""

    rows: int
    image_len: int
    section_bytes: bytes
    block_header: bytes
    padding: int
    photoshop_len: int
    head: bytes

    @property
    def total(self) -> int:
        return len(self.head) + self.image_len + self.photoshop_len


def _scaled_layout(scale: int) -> _ScaledLayout:
    """
    The layout of a "raw-layers" file whose row count is `ROWS * scale`.

    Always framed as a "V0002" container with 8-byte channel/block lengths
    (the layout `large-document-container` already exercises), so nothing
    here needs to branch once a channel crosses a 4-byte length field.
    """
    rows = ROWS * scale
    channel_len = HEADER + WIDTH * rows * BPP
    image_len = WIDTH * rows * 3 * (16 // 8)

    record = layer_record(
        name="Background",
        bounds=(0, 0, rows, WIDTH),
        channels=((0, channel_len), (1, channel_len), (2, channel_len)),
        large=True,
    )
    section_bytes = layer_section(record)
    content_len = len(section_bytes) + 3 * channel_len
    padding = (-content_len) % 4
    block_header = psd_block_header("Lr16", content_len, length_size=8)
    photoshop_len = len(CONTAINER_V0002) + len(block_header) + content_len + padding

    head = tiff_header_and_ifd(
        width=WIDTH,
        height=rows,
        photoshop_last=True,
        image_len=image_len,
        photoshop_len=photoshop_len,
        compression=1,
    )

    return _ScaledLayout(
        rows=rows,
        image_len=image_len,
        section_bytes=section_bytes,
        block_header=block_header,
        padding=padding,
        photoshop_len=photoshop_len,
        head=head,
    )


def estimated_scaled_size(scale: int) -> int:
    """
    The exact byte count a `--scale scale` "raw-layers" file will have.

    >>> estimated_scaled_size(2) > estimated_scaled_size(1)
    True
    """
    return _scaled_layout(scale).total


def scale_to_cross(tag_37724_bytes: int) -> int:
    """
    The smallest `--scale` whose tag 37724 (the "Lr16" PSD blob) exceeds
    `tag_37724_bytes` - the quantity AFFINITY_NOTES.md measures against 2^31.

    >>> layout = _scaled_layout(scale_to_cross(2**31))
    >>> layout.photoshop_len > 2**31
    True
    >>> _scaled_layout(scale_to_cross(2**31) - 1).photoshop_len <= 2**31
    True
    """
    scale = 1

    while _scaled_layout(scale).photoshop_len <= tag_37724_bytes:
        scale *= 2

    lo, hi = scale // 2, scale

    while lo < hi:
        mid = (lo + hi) // 2

        if _scaled_layout(mid).photoshop_len <= tag_37724_bytes:
            lo = mid + 1
        else:
            hi = mid

    return lo


def _write_zeros(handle: BinaryIO, n: int, progress: Callable[[int], None]) -> None:
    """Streams `n` zero bytes - the base TIFF image strip nobody reads."""
    chunk_size = CHUNK_ROWS * WIDTH * BPP * 3
    zeros = bytes(chunk_size)
    remaining = n

    while remaining > 0:
        block = zeros if remaining >= chunk_size else zeros[:remaining]
        handle.write(block)
        remaining -= len(block)
        progress(handle.tell())


def _write_channel(
    handle: BinaryIO,
    seed: int,
    rows: int,
    progress: Callable[[int], None],
) -> None:
    """Streams one RAW channel's pixels, `CHUNK_ROWS` rows at a time."""
    rng = np.random.default_rng(seed)
    remaining = rows

    while remaining > 0:
        n = min(CHUNK_ROWS, remaining)
        handle.write(_walk_chunk(rng, WIDTH, n))
        remaining -= n
        progress(handle.tell())


def _progress_reporter(total: int) -> Callable[[int], None]:
    """A `progress(written)` callback, printed once per whole percent."""
    last_percent = -1

    def progress(written: int) -> None:
        nonlocal last_percent

        if total <= SIZE_WARN_BYTES:
            return

        percent = written * 100 // total

        if percent == last_percent:
            return

        last_percent = percent
        end = "\n" if written >= total else ""
        print(f"\r{percent:3d}% ({written:,} / {total:,} B)", end=end, file=sys.stderr)

    return progress


def write_scaled_raw_layers(
    path: Path, *, scale: int, layout: _ScaledLayout | None = None
) -> Path:
    """Streams a scaled "raw-layers" case to `path` without holding it in RAM."""
    layout = layout if layout is not None else _scaled_layout(scale)
    progress = _progress_reporter(layout.total)

    with path.open("wb") as handle:
        handle.write(layout.head)
        _write_zeros(handle, layout.image_len, progress)

        handle.write(CONTAINER_V0002)
        handle.write(layout.block_header)
        handle.write(layout.section_bytes)

        for seed in (1, 2, 3):
            handle.write(RAW.to_bytes(HEADER, "little"))
            _write_channel(handle, seed, layout.rows, progress)

        handle.write(b"\x00" * layout.padding)

    return path


def write(
    name: str,
    directory: Path,
    *,
    scale: int = 1,
    layout: _ScaledLayout | None = None,
) -> Path:
    """Writes one case into `directory` and returns its path."""
    path = directory / f"{name}.tif"

    if scale == 1:
        path.write_bytes(build(name))
        return path

    if name not in SCALABLE_CASES:
        raise ValueError(
            f"{name!r} cannot be scaled; only {sorted(SCALABLE_CASES)} can"
        )

    return write_scaled_raw_layers(path, scale=scale, layout=layout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sample_files",
        description="Writes every sample case into a directory.",
    )
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--only",
        action="append",
        choices=sorted(CASES),
        help="write only the named case; may be repeated",
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=1,
        help=(
            "multiply raw-layers' row count by this factor and stream it to "
            f"disk instead of building in memory; only {sorted(SCALABLE_CASES)} "
            "support this"
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help=f"required to write an estimated size past {SIZE_WARN_BYTES:,} B",
    )

    args = parser.parse_args(argv)
    args.directory.mkdir(parents=True, exist_ok=True)

    if args.scale < 1:
        parser.error(f"--scale must be a positive integer, got {args.scale}")

    layout = None

    if args.scale != 1:
        names = args.only or []

        if not names or any(name not in SCALABLE_CASES for name in names):
            parser.error(
                f"--scale requires --only naming one of {sorted(SCALABLE_CASES)}"
            )

        layout = _scaled_layout(args.scale)
        print(f"estimated size: {layout.total:,} B")

        if layout.total > SIZE_WARN_BYTES and not args.yes:
            parser.error(
                f"refusing to write {layout.total:,} B without --yes "
                f"(threshold {SIZE_WARN_BYTES:,} B)"
            )

    for name in args.only or sorted(CASES):
        path = write(name, args.directory, scale=args.scale, layout=layout)

        print(f"{path.name:<30}{path.stat().st_size:>10,} B  {CASES[name].summary}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
