"""The Command protocol and the two concrete commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from optiff.commands.analyze import AnalyzeCommand
from optiff.commands.optimize import OptimizeCommand
from optiff.optimize import OptimizeResult


def test_analyze_command_has_a_name():
    assert AnalyzeCommand().name == "analyze"


def test_optimize_command_has_a_name():
    assert OptimizeCommand().name == "optimize"


def test_optimize_command_add_arguments_registers_path_and_out():
    # Arrange
    parser = argparse.ArgumentParser()

    # Act
    OptimizeCommand().add_arguments(parser)
    args = parser.parse_args(["in.tif", "--out", "out.tif"])

    # Assert
    assert args.path == Path("in.tif")
    assert args.out == Path("out.tif")
    assert args.level == 4
    assert args.image_data is False
    assert args.zip_fallback is False
    assert args.no_verify is False


def test_optimize_command_run_returns_optimize_result(tmp_path: Path, sample_file):
    # Arrange
    parser = argparse.ArgumentParser()
    OptimizeCommand().add_arguments(parser)
    source = sample_file("raw-layers")
    args = parser.parse_args([str(source), "--out", str(tmp_path / "out.tif")])

    # Act
    result = OptimizeCommand().run(args)

    # Assert
    assert isinstance(result, OptimizeResult)


def test_analyze_command_add_arguments_registers_path():
    # Arrange
    parser = argparse.ArgumentParser()

    # Act
    AnalyzeCommand().add_arguments(parser)
    args = parser.parse_args(["in.tif"])

    # Assert
    assert args.path == Path("in.tif")
