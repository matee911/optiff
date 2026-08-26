"""The `analyze` subcommand."""

from __future__ import annotations

import argparse
from pathlib import Path

from optiff.cli import analyze as _print_analysis


class AnalyzeCommand:
    name = "analyze"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("path", type=Path)

    def run(self, args: argparse.Namespace) -> None:
        # Task 7 replaces this with `optiff.analysis.analyze(args.path)`,
        # returning an `AnalyzeReport` instead of printing directly.
        _print_analysis(args.path)
