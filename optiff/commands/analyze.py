"""The `analyze` subcommand."""

from __future__ import annotations

import argparse
from pathlib import Path

from optiff.analysis import AnalyzeReport, analyze


class AnalyzeCommand:
    name = "analyze"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("path", type=Path)

    def run(self, args: argparse.Namespace) -> AnalyzeReport:
        return analyze(args.path)
