"""The interface every CLI subcommand implements."""

from __future__ import annotations

import argparse
from typing import Protocol


class Command(Protocol):
    """One CLI subcommand: how to build its parser, and how to run it."""

    name: str

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Registers this command's arguments on its own subparser."""
        ...

    def run(self, args: argparse.Namespace) -> object:
        """Executes the command and returns its result object."""
        ...
