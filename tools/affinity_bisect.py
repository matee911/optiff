"""
Builds variants of one file with controlled sizes of tag 37724.

Why: to find out whether a program (here: Affinity) drops layers because of
the SIZE of the file, the size has to be the only thing that changes. Any
other approach - a different file, fewer layers, different content - varies
several things at once and proves nothing.

How: only the first N channels are packed, the rest copied byte for byte. The
pixels are identical across every variant, as are the layer count and their
dimensions. The one variable is the number of bytes on disk.

The production logic is not duplicated: `_plan_channel` is swapped for a
wrapper that returns a copy segment once the limit is passed. The container
plan, the write, the offset arithmetic and the verification all run unchanged.

Usage:
    python tools/affinity_bisect.py FILE.tif 2.20 2.05 1.95 1.80 1.55
    python tools/affinity_bisect.py FILE.tif 2.05 1.95 --verify
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

import optiff.optimize_layers as ol
from optiff.optimize import channel_digests_of, optimize, plan_file
from optiff.segments import Copy

GB = 1024**3

ORIGINAL = ol._plan_channel
COUNTER = {"n": 0, "limit": 10**9}


def _limited(reader, layer, channel, **kw):
    """Packs only the first `limit` channels; the rest passes through."""
    payload, result = ORIGINAL(reader, layer, channel, **kw)

    COUNTER["n"] += 1

    if COUNTER["n"] <= COUNTER["limit"]:
        return payload, result

    # The checksum stays: it is computed from pixels, and those do not change.
    return (
        Copy(channel.data_offset, channel.size),
        replace(result, after=result.before, compression=channel.compression),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="affinity_bisect",
        description="Variants of one file at given sizes of tag 37724.",
    )
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "targets",
        type=float,
        nargs="+",
        help="target tag sizes in GB, for example 2.05 1.95",
    )
    parser.add_argument("--level", type=int, default=4)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="compare every channel checksum against the source "
        "(a damaged variant would give a false test result)",
    )

    args = parser.parse_args(argv)

    if not args.source.is_file():
        print(f"File not found: {args.source}", file=sys.stderr)
        return 2

    ol._plan_channel = _limited

    # One full planning pass is enough to learn each channel's saving;
    # after that N is chosen by arithmetic alone, with no further passes.
    COUNTER["limit"] = 10**9
    COUNTER["n"] = 0

    _segments, container, _image, _notes = plan_file(args.source, level=args.level)

    savings = [item.before - item.after for item in container.results]
    whole_tag = container.source_size

    print(f"tag in the source:       {whole_tag / GB:.3f} GB")
    print(f"tag fully compressed:    {container.size / GB:.3f} GB")
    print(f"channels: {len(savings)}\n")

    for target in args.targets:
        wanted = target * GB
        total = n = 0

        for index, saving in enumerate(savings, start=1):
            if whole_tag - (total + saving) < wanted:
                break
            total += saving
            n = index

        output = args.source.with_name(f"{args.source.stem} bis {target:.2f}GB.tif")

        COUNTER["limit"] = n
        COUNTER["n"] = 0

        result = optimize(
            args.source, output, level=args.level, verify=False, keep_mtime=False
        )

        print(
            f"{output.name:<34} tag {result.tag_after:>15,} B "
            f"({result.tag_after / GB:.3f} GB)  channels packed: {n}",
            flush=True,
        )

    if args.verify:
        print("\npixel verification:")
        baseline = [(d.key(), d.digest) for d in channel_digests_of(args.source)]

        for target in args.targets:
            output = args.source.with_name(f"{args.source.stem} bis {target:.2f}GB.tif")
            now = [(d.key(), d.digest) for d in channel_digests_of(output)]
            print(
                f"  {output.name:<34} {'IDENTICAL' if now == baseline else 'DIFFERS!'}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
