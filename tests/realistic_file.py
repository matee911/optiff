"""
A synthetic file modeling the structure and content of real production TIFFs.

Why: `tests/sample_files.py`'s cases are minimal, one-concern-at-a-time
fixtures - useful for unit tests, useless for measuring what optimization
actually buys on a file shaped like the real thing. This module builds one
composite file instead, modeled on a structural and content survey of nine
real 2.2-2.7 GB layered TIFFs (measured 2026-08-27, parsed with optiff
itself - see the design doc for the full recipe).

The two facts that matter most:

- **Channel size is bimodal.** ~48% of channels are near-empty masks
  contributing ~0% of bytes; ~34% are full-canvas and carry ~93% of bytes.
  A file built from N equally-sized channels does not resemble this.
- **~29% of payload has no readable geometry** - full-canvas-sized channels
  whose owning layer's bounds don't match the channel's actual rectangle
  (adjustment layers, masks). Skipped without --zip-fallback. A benchmark
  file without these cannot measure what --zip-fallback buys.

Usage:
    python -m tests.realistic_file DIR/file.tif --scale 1.0 --yes
"""

from __future__ import annotations

import argparse
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from optiff.psd_codec import RAW, ZIP_PREDICTED
from tests.sample_files import HEADER, flat
from tests.unit.builders import (
    build_tiff,
    layer_record,
    layer_record_be,
    layer_section,
    layer_section_be,
    link_record_with_psb,
    psd_container,
)

#: The canvas of the real file this is modeled on (sample-05 of the survey).
#: `--scale` multiplies both dimensions from here; scale=1.0 reproduces it.
REFERENCE_WIDTH, REFERENCE_ROWS = 4954, 6192

BPP = 2

#: Refuse to write a file past this many bytes without --yes.
SIZE_WARN_BYTES = 500_000_000


def photographic(seed: int, *, width: int, rows: int) -> bytes:
    """
    8-bit tonal content shifted into a 16-bit container - real photographic
    channels are 8-bit data stored this way, not true 16-bit noise. A local
    random walk in 8-bit space, wrapped rather than clipped so it keeps
    moving instead of saturating at the ends.

    >>> photographic(1, width=8, rows=8) == photographic(1, width=8, rows=8)
    True
    """
    rng = np.random.default_rng(seed)
    walk8 = np.cumsum(rng.integers(-15, 16, size=(rows, width)), axis=1) % 256

    return (walk8.astype(">u2") << 8).tobytes()


def detail16(seed: int, *, width: int, rows: int) -> bytes:
    """
    True 16-bit content with real per-pixel variation - hair, texture. A
    coarser random walk than `photographic`'s, calibrated (against the real
    file this module models) to compress and predict similarly: prediction
    should help noticeably here, unlike `photographic` or `flat`.

    >>> detail16(1, width=8, rows=8) == detail16(1, width=8, rows=8)
    True
    """
    rng = np.random.default_rng(seed)
    walk = np.cumsum(rng.integers(-30, 31, size=(rows, width)), axis=1)
    texture = rng.integers(-5, 6, size=(rows, width))

    return ((walk + texture) % 65536).astype(">u2").tobytes()


def _encode(pixels: bytes, method: int, *, width: int) -> bytes:
    """
    Channel data for `method`, including its 2-byte method header.

    A local encoder, not `sample_files.encoded()` - that one hardcodes the
    module's own WIDTH/ROWS for `ZIP_PREDICTED`'s row reshape, which this
    module's channels don't share (they're every size in the manifest).
    """
    if method == RAW:
        body = pixels
    elif method == ZIP_PREDICTED:
        rows = np.frombuffer(pixels, dtype=">u2").reshape(-1, width)
        deltas = np.diff(rows.astype("int64"), axis=1, prepend=0) % 65536
        body = zlib.compress(deltas.astype(">u2").tobytes(), 6)
    else:
        raise ValueError(f"unknown compression method: {method}")

    return method.to_bytes(HEADER, "little") + body


@dataclass(frozen=True)
class _Channel:
    """One channel's content and how it's stored."""

    channel_id: int
    data: bytes
    width: int
    method: int = RAW

    @property
    def encoded(self) -> bytes:
        return _encode(self.data, self.method, width=self.width)


@dataclass(frozen=True)
class _Layer:
    """One PSD layer: a name, its bounds, and its channels."""

    name: str
    bounds: tuple[int, int, int, int]
    channels: list[_Channel]


def _rgb_layer(  # noqa: PLR0913  - a test builder, one knob per variant
    name: str,
    profile,
    seed: int,
    *,
    width: int,
    rows: int,
    bounds: tuple[int, int, int, int] | None = None,
    methods: tuple[int, ...] = (RAW, RAW, RAW),
) -> _Layer:
    """A normal 3-channel (RGB) layer whose bounds match its content."""
    channels = [
        _Channel(i, profile(seed + i, width=width, rows=rows), width, method)
        for i, method in enumerate(methods)
    ]

    return _Layer(name, bounds or (0, 0, rows, width), channels)


def _masked_layer(name: str, profile, seed: int, *, width: int, rows: int) -> _Layer:
    """
    A single-channel mask layer whose bounds (0,0,0,0) don't match the
    channel's real byte count - unreadable geometry, the `--zip-fallback`
    case. `width`/`rows` control the mask's own size, independent of canvas.
    """
    data = profile(seed, width=width, rows=rows)

    return _Layer(name, (0, 0, 0, 0), [_Channel(-2, data, width)])


def build_manifest(width: int, rows: int) -> list[_Layer]:
    """
    The layer/channel plan, scaled from the reference recipe:

    ~38 full-canvas channels (93% of bytes), ~6 part-canvas (6.5%), ~70
    near-empty masks (~0%, but half the channel count) - and, of the
    full-canvas ones, roughly 29% of total payload has no readable geometry
    (adjustment-layer masks sized like real content, not like real masks).
    """
    layers: list[_Layer] = []

    # Full-canvas, readable geometry: photographic (target ~16 channels)
    for i in range(5):
        methods = (ZIP_PREDICTED, RAW, RAW) if i == 0 else (RAW, RAW, RAW)
        bounds = None
        if i == 4:
            # At least one layer rectangle larger than the canvas.
            bounds = (0, 0, rows + rows // 5, width + width // 5)
        layers.append(
            _rgb_layer(
                f"Photo {i}",
                photographic,
                10 + i * 3,
                width=width,
                rows=rows,
                bounds=bounds,
                methods=methods,
            )
        )

    # Full-canvas, readable geometry: detail16 (target ~12 channels)
    for i in range(4):
        methods = (ZIP_PREDICTED, RAW, RAW) if i == 0 else (RAW, RAW, RAW)
        layers.append(
            _rgb_layer(
                f"Detail {i}",
                detail16,
                40 + i * 3,
                width=width,
                rows=rows,
                methods=methods,
            )
        )

    # Full-canvas, readable geometry: flat/constant (part of the 44%)
    for i in range(4):
        layers.append(_rgb_layer(f"Flat {i}", flat, 70 + i * 3, width=width, rows=rows))

    # Full-canvas, UNREADABLE geometry: the rest of flat/constant's payload,
    # delivered as mask-shaped channels - the ~29%-of-payload finding.
    for i in range(5):
        layers.append(
            _masked_layer(f"Adjustment {i}", flat, 100 + i, width=width, rows=rows)
        )

    # Low-variation / small flat: reduced-canvas layers
    small_width, small_rows = max(width // 5, 1), max(rows // 5, 1)
    layers.append(
        _Layer(
            "Low variation",
            (0, 0, small_rows, small_width),
            [_Channel(0, flat(130, width=small_width, rows=small_rows), small_width)],
        )
    )

    for i in range(2):
        layers.append(
            _rgb_layer(
                f"Small flat {i}",
                flat,
                140 + i * 3,
                width=small_width,
                rows=small_rows,
                methods=(RAW, RAW),
            )
        )

    # Near-empty masks: ~half the channel count, ~none of the bytes.
    mask_width, mask_rows = 8, 8
    for i in range(90):
        layers.append(
            _masked_layer(
                f"Empty mask {i}", flat, 200 + i, width=mask_width, rows=mask_rows
            )
        )

    return layers


def _embedded_smart_object(seed: int) -> bytes:
    """A small PSB with a few nested layers, embedded via an lnk2 record."""
    width, rows = 64, 48
    profiles = (photographic, detail16, flat, photographic, detail16)

    payloads = [
        _encode(profile(seed + i, width=width, rows=rows), RAW, width=width)
        for i, profile in enumerate(profiles)
    ]

    inner = layer_section_be(
        *[
            layer_record_be(
                name=f"Embedded {i}",
                bounds=(0, 0, rows, width),
                channels=((0, len(payload)),),
            )
            for i, payload in enumerate(payloads)
        ]
    ) + b"".join(RAW.to_bytes(HEADER, "big") + payload[HEADER:] for payload in payloads)

    return link_record_with_psb(inner)


def estimated_size(width: int, rows: int) -> int:
    """The approximate byte count `build` will produce for `width`/`rows`."""
    manifest = build_manifest(width, rows)
    payload = sum(
        len(channel.encoded) for layer in manifest for channel in layer.channels
    )
    smart_object = len(_embedded_smart_object(1))
    image = width * rows * 3 * BPP

    return payload + smart_object + image + 4096  # +headers, roughly


def build(width: int, rows: int) -> bytes:
    """The whole file, in memory - see the module docstring for the shape."""
    manifest = build_manifest(width, rows)

    records = [
        layer_record(
            name=layer.name,
            bounds=layer.bounds,
            channels=tuple((c.channel_id, len(c.encoded)) for c in layer.channels),
        )
        for layer in manifest
    ]
    section = layer_section(*records) + b"".join(
        c.encoded for layer in manifest for c in layer.channels
    )

    return build_tiff(
        psd_container(("Lr16", section), ("lnk2", _embedded_smart_object(1))),
        width=width,
        height=rows,
    )


def write(path: Path, *, width: int, rows: int) -> Path:
    path.write_bytes(build(width, rows))

    return path


def measured_ratio(
    *, with_zip_fallback: bool, width: int = REFERENCE_WIDTH, rows: int = REFERENCE_ROWS
) -> float:
    """
    The compression ratio this manifest's own bytes would achieve at deflate
    level 4, computed directly (not by invoking the full optimizer) - the
    sanity check the design doc asks for: ~0.57 without --zip-fallback,
    ~0.28 with it (at the reference canvas size; the shape holds at any
    scale, but the exact ratio is what was measured at full size).
    """
    manifest = build_manifest(width, rows)
    before = after = 0

    for layer in manifest:
        geometry_ok = layer.bounds != (0, 0, 0, 0)

        for channel in layer.channels:
            before += len(channel.encoded)

            if channel.method != RAW:
                after += len(channel.encoded)  # already packed, nothing to gain
            elif geometry_ok or with_zip_fallback:
                after += len(zlib.compress(channel.data, 4)) + HEADER
            else:
                after += len(channel.encoded)  # skipped: unreadable geometry

    return after / before


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="realistic_file",
        description="Writes one synthetic file shaped like a real production TIFF.",
    )
    parser.add_argument("path", type=Path)
    parser.add_argument(
        "--scale",
        type=float,
        default=0.05,
        help=(
            "multiplies the reference canvas "
            f"({REFERENCE_WIDTH}x{REFERENCE_ROWS}); 1.0 reproduces it (~2.3 GB)"
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help=f"required to write an estimated size past {SIZE_WARN_BYTES:,} B",
    )

    args = parser.parse_args(argv)
    width = max(round(REFERENCE_WIDTH * args.scale), 8)
    rows = max(round(REFERENCE_ROWS * args.scale), 8)

    size = estimated_size(width, rows)
    print(f"estimated size: {size:,} B")

    if size > SIZE_WARN_BYTES and not args.yes:
        parser.error(
            f"refusing to write {size:,} B without --yes "
            f"(threshold {SIZE_WARN_BYTES:,} B)"
        )

    args.path.parent.mkdir(parents=True, exist_ok=True)
    write(args.path, width=width, rows=rows)
    print(f"wrote {args.path} ({args.path.stat().st_size:,} B)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
