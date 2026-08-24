"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import tifffile

from tiff_analyzer.document import TiffDocument
from tiff_analyzer.optimize import OptimizeError, OptimizeResult, optimize
from tiff_analyzer.psd_analyzer import TiffPhotoshopAnalyzer
from tiff_analyzer.report import WIDTH, Reporter
from tiff_analyzer.units import format_size

try:
    __version__ = version("tiff-analyzer")
except PackageNotFoundError:  # pragma: no cover - uruchomienie bez instalacji
    __version__ = "0.1.0+dev"

EXIT_OK = 0
EXIT_BAD_FILE = 2
EXIT_VERIFY_FAILED = 3


def analyze(path: Path) -> None:
    with TiffDocument(path) as document:
        photoshop = TiffPhotoshopAnalyzer().analyze(document)

        Reporter(document, photoshop).print_report()


def _duration(seconds: float) -> str:
    """
    A duration in human readable form.

    >>> _duration(9.4)
    '9.4 s'
    >>> _duration(94.0)
    '1 min 34 s'
    >>> _duration(3725.0)
    '1 h 02 min'
    """
    if seconds < 60:
        return f"{seconds:.1f} s"

    if seconds < 3600:
        return f"{int(seconds) // 60} min {int(seconds) % 60:02d} s"

    return f"{int(seconds) // 3600} h {int(seconds) % 3600 // 60:02d} min"


def print_optimize(result: OptimizeResult) -> int:
    """Prints the optimization summary and returns the exit code."""
    print("=" * WIDTH)
    print("OPTIMIZATION")
    print("=" * WIDTH)
    print()

    print(f"{'File:':<18} {result.source}")

    for note in result.notes:
        print(f"{'Note:':<18} {note}")

    if result.skipped:
        print(f"{'Result:':<18} skipped - {result.skipped}")
        print()
        print("=" * WIDTH)
        return EXIT_OK

    if result.comparison is not None and not result.comparison.ok:
        print(f"{'Result:':<18} VERIFICATION FAILED - output deleted")
        print()

        for problem in result.comparison.problems[:10]:
            print(f"  {problem}", file=sys.stderr)

        print("=" * WIDTH)
        return EXIT_VERIFY_FAILED

    rows = (
        ("channel data", result.channel_bytes_before, result.channel_bytes_after),
        ("tag 37724", result.tag_before, result.tag_after),
        *(
            (("image pixels", result.image_before, result.image_after),)
            if result.image_before
            else ()
        ),
        ("FILE", result.size_before, result.size_after),
    )

    print()
    print(
        f"{'':<16} {'BEFORE':>17} {'AFTER':>17} {'SAVED':>17}  SHARE"
    )
    print("-" * WIDTH)

    for label, before, after in rows:
        share = f"{after / before * 100:5.1f}%" if before else "     -"

        print(
            f"{label:<16} "
            f"{before:>17,} "
            f"{after:>17,} "
            f"{before - after:>17,} "
            f"{share}"
        )

    print("-" * WIDTH)
    print(
        f"Saved:           {format_size(result.saved)} "
        f"({(1 - result.ratio) * 100:.1f}% of the file)"
    )
    print()
    if result.image_before:
        print(
            f"Saved {format_size(result.channel_saved)} from layer channels, "
            f"{format_size(result.image_saved)} from image pixels."
        )
    else:
        print("All of the saving comes from channel data.")

    print("Differences between levels:")
    print(
        f"  {result.padding_saved:+,} B  block padding to 4 bytes"
    )
    print(
        f"  {result.tail_saved:+,} B  padding dropped at the end of the file"
    )
    print()

    print(
        f"{'Channels:':<18} {result.channels_changed} compressed "
        f"of {result.channels_total}"
    )

    if result.comparison is not None:
        print(
            f"{'Verified:':<18} {result.comparison.total} channels, "
            f"pixel SHA256 unchanged"
        )
    else:
        print(f"{'Verified:':<18} SKIPPED (--no-verify)")

    print(f"{'Written:':<18} {result.output}")
    print(f"{'Original:':<18} untouched")
    print()

    print(
        f"{'Time:':<18} {_duration(result.seconds_total)} "
        f"({format_size(int(result.throughput))}/s)"
    )
    print(
        f"{'':<18} compress {_duration(result.seconds_plan)}, "
        f"write {_duration(result.seconds_write)}, "
        f"verify {_duration(result.seconds_verify)}"
    )
    print()
    print("=" * WIDTH)

    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tiff-analyzer",
        description="Analyses the physical byte layout of a TIFF and its metadata.",
    )

    parser.add_argument("path", type=Path)

    parser.add_argument(
        "--optimize",
        metavar="PLIK",
        type=Path,
        help=(
            "write an optimized copy to the given path; "
            "the original is left untouched"
        ),
    )

    parser.add_argument(
        "--level",
        type=int,
        default=4,
        choices=range(1, 10),
        metavar="1-9",
        help="deflate level (default 4)",
    )

    parser.add_argument(
        "--image-data",
        action="store_true",
        help=(
            "also pack the flattened pixels (Adobe Deflate, no predictor); "
            "off by default, because that is the only part Capture One and "
            "Affinity read, and reaching the pixels gets slower"
        ),
    )

    parser.add_argument(
        "--zip-fallback",
        action="store_true",
        help=(
            "also pack channels with no readable geometry using plain ZIP "
            "(adjustment layer masks carry a 0x0 rectangle); off by default, "
            "because writing method 2 is not yet widely verified"
        ),
    )

    parser.add_argument(
        "--no-verify",
        action="store_true",
        help=(
            "skip the per-channel SHA256 comparison after writing "
            "(faster, but with no proof of losslessness)"
        ),
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.path.is_file():
        print(f"File not found: {args.path}", file=sys.stderr)
        return EXIT_BAD_FILE

    try:
        if args.optimize is not None:
            return print_optimize(
                optimize(
                    args.path,
                    args.optimize,
                    level=args.level,
                    verify=not args.no_verify,
                    image_data=args.image_data,
                    zip_fallback=args.zip_fallback,
                )
            )

        analyze(args.path)
    except tifffile.TiffFileError as error:
        print(f"Not a readable TIFF: {args.path} ({error})", file=sys.stderr)
        return EXIT_BAD_FILE
    except OptimizeError as error:
        print(f"Cannot optimize: {error}", file=sys.stderr)
        return EXIT_BAD_FILE

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
