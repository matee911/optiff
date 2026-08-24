"""
Buduje warianty jednego pliku o kontrolowanych rozmiarach tagu 37724.

Po co: zeby sprawdzic, czy jakis program (tu: Affinity) gubi warstwy z powodu
the SIZE of the file, the size has to be the only thing that changes. Any
other approach - a different file, fewer layers, different content - varies
several things at once and proves
dowodzi.

How: only the first N channels are packed, the rest copied byte for byte. The
sa identyczne we wszystkich wariantach, liczba i wymiary warstw tez. Jedyna
zmienna to liczba bajtow na dysku.

Nie dublujemy logiki produkcyjnej: podmieniamy `_plan_channel` na opakowanie,
ktore po przekroczeniu limitu zwraca segment kopiujacy. Plan kontenera, zapis,
przeliczanie offsetow i weryfikacja dzialaja bez zmian.

Uzycie:
    python tools/affinity_bisect.py PLIK.tif 2.20 2.05 1.95 1.80 1.55
    python tools/affinity_bisect.py PLIK.tif 2.05 1.95 --verify
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

import tiff_analyzer.optimize_layers as ol
from tiff_analyzer.optimize import channel_digests_of, optimize, plan_file
from tiff_analyzer.segments import Copy

GB = 1024**3

ORYGINALNY = ol._plan_channel
COUNTER = {"n": 0, "limit": 10**9}


def _ograniczony(reader, layer, channel, **kw):
    """Packs only the first `limit` channels; the rest passes through."""
    payload, result = ORYGINALNY(reader, layer, channel, **kw)

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
        description="Warianty jednego pliku o zadanych rozmiarach tagu 37724.",
    )
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "targets",
        type=float,
        nargs="+",
        help="docelowe sizes tagu w GB, np. 2.05 1.95",
    )
    parser.add_argument("--level", type=int, default=4)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="porownaj sumy kontrolne kazdego channel ze source "
        "(uszkodzony wariant dalby falszywy result testu)",
    )

    args = parser.parse_args(argv)

    if not args.source.is_file():
        print(f"Nie znaleziono pliku: {args.source}", file=sys.stderr)
        return 2

    ol._plan_channel = _ograniczony

    # One full planning pass is enough to learn each channel's saving;
    # after that N is chosen by arithmetic alone, with no further passes.
    COUNTER["limit"] = 10**9
    COUNTER["n"] = 0

    _segmenty, kontener, _obraz, _uwagi = plan_file(args.source, level=args.level)

    zyski = [item.before - item.after for item in kontener.results]
    tag_pelny = kontener.source_size

    print(f"tag w source:            {tag_pelny / GB:.3f} GB")
    print(f"tag po pelnej kompresji: {kontener.size / GB:.3f} GB")
    print(f"channels: {len(zyski)}\n")

    for target in args.targets:
        docelowo = target * GB
        total = n = 0

        for i, z in enumerate(zyski, start=1):
            if tag_pelny - (total + z) < docelowo:
                break
            total += z
            n = i

        output = args.source.with_name(
            f"{args.source.stem} bis {target:.2f}GB.tif"
        )

        COUNTER["limit"] = n
        COUNTER["n"] = 0

        result = optimize(
            args.source, output, level=args.level, verify=False, keep_mtime=False
        )

        print(
            f"{output.name:<34} tag {result.tag_after:>15,} B "
            f"({result.tag_after / GB:.3f} GB)  channels spakowanych: {n}",
            flush=True,
        )

    if args.verify:
        print("\nweryfikacja pikseli:")
        baza = [(d.key(), d.digest) for d in channel_digests_of(args.source)]

        for target in args.targets:
            output = args.source.with_name(
                f"{args.source.stem} bis {target:.2f}GB.tif"
            )
            teraz = [(d.key(), d.digest) for d in channel_digests_of(output)]
            print(
                f"  {output.name:<34} "
                f"{'IDENTYCZNE' if teraz == baza else 'ROZNICA!'}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
