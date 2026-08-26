"""The `optimize` subcommand."""

from __future__ import annotations

import argparse
from pathlib import Path

from optiff.optimize import OptimizeResult, optimize


class OptimizeCommand:
    name = "optimize"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("path", type=Path)

        parser.add_argument(
            "--out",
            metavar="FILE",
            type=Path,
            required=True,
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

    def run(self, args: argparse.Namespace) -> OptimizeResult:
        return optimize(
            args.path,
            args.out,
            level=args.level,
            verify=not args.no_verify,
            image_data=args.image_data,
            zip_fallback=args.zip_fallback,
        )
