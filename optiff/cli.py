"""Command line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import tifffile

from optiff.document import TiffDocument
from optiff.formatters.optimize import render_optimize
from optiff.optimize import OptimizeError, OptimizeResult
from optiff.psd_analyzer import TiffPhotoshopAnalyzer
from optiff.report import WIDTH, Reporter

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
        sys.stdout.write(render_optimize(result))

        if result.comparison is not None and not result.comparison.ok:
            for problem in result.comparison.problems[:10]:
                print(f"  {problem}", file=sys.stderr)
            return EXIT_VERIFY_FAILED

        return EXIT_OK

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
