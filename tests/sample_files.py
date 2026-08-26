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
    psd_container,
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
    rng = np.random.default_rng(seed)
    walk = np.cumsum(rng.integers(-4, 5, size=(ROWS, WIDTH)), axis=1)

    return (walk % 65536).astype(">u2").tobytes()


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


CASES: dict[str, Sample] = {
    sample.name: sample
    for sample in (
        Sample(
            "raw-layers",
            "Layers stored uncompressed - the ordinary case and the big win.",
            _raw_layers,
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


def write(name: str, directory: Path) -> Path:
    """Writes one case into `directory` and returns its path."""
    path = directory / f"{name}.tif"
    path.write_bytes(build(name))

    return path


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

    args = parser.parse_args(argv)
    args.directory.mkdir(parents=True, exist_ok=True)

    for name in args.only or sorted(CASES):
        path = write(name, args.directory)

        print(f"{path.name:<30}{path.stat().st_size:>10,} B  {CASES[name].summary}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
