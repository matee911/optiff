"""
The pure logic in `tools/benchmark.py` - everything except real wall-clock
timing, which by definition can't be asserted on deterministically. The
sweep itself is exercised at a tiny size so the suite stays fast.
"""

from __future__ import annotations

import re

import pytest

from tools.benchmark import (
    BENCHMARK_PROFILES,
    LevelResult,
    _format_bytes,
    _format_seconds,
    _scale,
    grain_inversion,
    render_svg,
    sweep,
    whole_file_curiosity,
)

SIZE = {"width": 32, "rows": 32}


def test_sweep_covers_every_profile_and_level():
    # Act
    results = sweep(**SIZE, repeats=1)

    # Assert
    assert set(results) == set(BENCHMARK_PROFILES)
    assert all(len(levels) == 9 for levels in results.values())
    assert all(
        [r.level for r in levels] == list(range(1, 10)) for levels in results.values()
    )


def test_sweep_is_deterministic_in_size():
    # Arrange - the same content, deflated at the same level, always
    # produces the same size (only wall-clock time can vary between runs).
    first = sweep(**SIZE, repeats=1)
    second = sweep(**SIZE, repeats=1)

    # Assert
    for name in first:
        assert [r.size for r in first[name]] == [r.size for r in second[name]]


@pytest.mark.parametrize(
    ("three_size", "four_size", "expect_reproduced"),
    [(1000, 1200, True), (1000, 900, False), (1000, 1000, False)],
)
def test_grain_inversion_detects_level_4_larger_than_3(
    three_size, four_size, expect_reproduced
):
    # Arrange
    results = [
        LevelResult(
            level=level, size=three_size if level == 3 else four_size, seconds=0.0
        )
        for level in range(1, 10)
    ]

    # Act
    message = grain_inversion(results)

    # Assert
    assert message.startswith("reproduced") is expect_reproduced


def test_scale_maps_the_range_linearly():
    assert _scale(5, 0, 10, 0, 100) == pytest.approx(50)
    assert _scale(0, 0, 10, 0, 100) == pytest.approx(0)
    assert _scale(10, 0, 10, 0, 100) == pytest.approx(100)


def test_scale_handles_a_degenerate_range():
    # A single data point (hi == lo) must not divide by zero.
    assert _scale(5, 5, 5, 0, 100) == pytest.approx(50)


def test_render_svg_is_well_formed_and_theme_specific():
    # Arrange
    results = sweep(**SIZE, repeats=1)

    # Act
    light = render_svg(results, theme="light")
    dark = render_svg(results, theme="dark")

    # Assert
    for svg in (light, dark):
        assert svg.startswith("<svg")
        assert svg.rstrip().endswith("</svg>")
        # one polyline per content profile
        assert len(re.findall(r"<polyline", svg)) == len(results)
        # one labelled point per (profile, level)
        expected_points = sum(len(levels) for levels in results.values())
        assert len(re.findall(r"<circle", svg)) == expected_points + len(results)
        # 5 y-axis size ticks + 5 x-axis time ticks
        assert (
            svg.count(" KB</text>")
            + svg.count(" B</text>")
            + svg.count(" MB</text>")
            + svg.count(" GB</text>")
            >= 1
        )
        assert svg.count(" ms</text>") + svg.count(" s</text>") >= 1

    assert light != dark


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (500, "500 B"),
        (1_500, "1.5 KB"),
        (2_500_000, "2.5 MB"),
        (3_200_000_000, "3.2 GB"),
    ],
)
def test_format_bytes(size, expected):
    assert _format_bytes(size) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"), [(0.0123, "12 ms"), (1.5, "1.50 s"), (0.0, "0 ms")]
)
def test_format_seconds(seconds, expected):
    assert _format_seconds(seconds) == expected


def test_whole_file_curiosity_reports_raw_and_zlib_and_lzma():
    # Arrange - repeated bytes so every compressor has something to do
    data = (b"abcdefgh" * 1000) + bytes(500)

    # Act
    report = whole_file_curiosity(data)

    # Assert - always present, regardless of whether zstd is installed
    assert "raw" in report
    assert str(len(data)) in report.replace(",", "")
    assert "zlib" in report
    assert "lzma" in report
