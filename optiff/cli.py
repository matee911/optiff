"""Command line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import tifffile

from optiff.document import TiffDocument
from optiff.optimize import OptimizeError, OptimizeResult
from optiff.psd_analyzer import TiffPhotoshopAnalyzer
from optiff.report import WIDTH, Reporter
from optiff.units import format_size

try:
    __version__ = version("optiff")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
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
    print(f"{'':<16} {'BEFORE':>17} {'AFTER':>17} {'SAVED':>17}  SHARE")
    print("-" * WIDTH)

    for label, before, after in rows:
        share = f"{after / before * 100:5.1f}%" if before else "     -"

        print(f"{label:<16} {before:>17,} {after:>17,} {before - after:>17,} {share}")

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
    print(f"  {result.padding_saved:+,} B  block padding to 4 bytes")
    print(f"  {result.tail_saved:+,} B  padding dropped at the end of the file")
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


from optiff.commands.analyze import (  # noqa: E402  - after analyze(), which it imports back
    AnalyzeCommand,
)
from optiff.commands.optimize import OptimizeCommand  # noqa: E402

COMMANDS: tuple[object, ...] = (AnalyzeCommand(), OptimizeCommand())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="optiff",
        description="Analyses the physical byte layout of a TIFF and its metadata.",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command_name", required=True)

    for command in COMMANDS:
        subparser = subparsers.add_parser(command.name)
        command.add_arguments(subparser)
        subparser.set_defaults(command=command)

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except BrokenPipeError:
        # Python still holds buffered stdout; redirect it to devnull so the
        # interpreter's exit-time flush doesn't raise a second BrokenPipeError.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)
        return EXIT_OK


def _main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.path.is_file():
        print(f"File not found: {args.path}", file=sys.stderr)
        return EXIT_BAD_FILE

    try:
        result = args.command.run(args)
    except tifffile.TiffFileError as error:
        print(f"Not a readable TIFF: {args.path} ({error})", file=sys.stderr)
        return EXIT_BAD_FILE
    except OptimizeError as error:
        print(f"Cannot optimize: {error}", file=sys.stderr)
        return EXIT_BAD_FILE

    if isinstance(result, OptimizeResult):
        return print_optimize(result)

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
