"""
`tests/realistic_file.py` - a synthetic file modeling a real production TIFF.

Small scale throughout (a few dozen pixels per channel) - the point is
proving the structure (bimodal channel sizes, unreadable geometry, an
embedded smart object, an oversized layer), not building a realistic file
on every test run.
"""

from __future__ import annotations

import pytest

from optiff.document import TiffDocument
from optiff.psd_analyzer import TiffPhotoshopAnalyzer
from tests.realistic_file import (
    build,
    build_manifest,
    detail16,
    estimated_size,
    measured_ratio,
    photographic,
    write,
)

SIZE = {"width": 32, "rows": 32}


def test_photographic_and_detail16_are_deterministic():
    assert photographic(1, **SIZE) == photographic(1, **SIZE)
    assert photographic(1, **SIZE) != photographic(2, **SIZE)
    assert detail16(1, **SIZE) == detail16(1, **SIZE)
    assert detail16(1, **SIZE) != detail16(2, **SIZE)


def test_manifest_has_both_readable_and_unreadable_geometry():
    # Act
    manifest = build_manifest(**SIZE)

    # Assert
    readable = [layer for layer in manifest if layer.bounds != (0, 0, 0, 0)]
    unreadable = [layer for layer in manifest if layer.bounds == (0, 0, 0, 0)]
    assert readable
    assert unreadable
    # unreadable layers vastly outnumber readable ones, matching the survey
    assert len(unreadable) > len(readable)


def test_manifest_has_an_oversized_layer():
    # Act
    manifest = build_manifest(**SIZE)

    # Assert - at least one layer's rectangle exceeds the canvas
    width, rows = SIZE["width"], SIZE["rows"]
    assert any(
        layer.bounds != (0, 0, 0, 0)
        and (layer.bounds[2] > rows or layer.bounds[3] > width)
        for layer in manifest
    )


def test_built_file_parses_with_zero_warnings(tmp_path):
    # Act
    path = write(tmp_path / "realistic.tif", **SIZE)

    with TiffDocument(path) as document:
        result = TiffPhotoshopAnalyzer().analyze(document)

    # Assert
    assert set(result.warnings) == set()
    assert {"Lr16", "lnk2"} <= {block.key for block in result.blocks}


def test_estimated_size_is_in_the_right_ballpark(tmp_path):
    # Arrange
    path = write(tmp_path / "realistic.tif", **SIZE)

    # Act
    estimate = estimated_size(**SIZE)
    actual = path.stat().st_size

    # Assert - a rough guide, not exact (headers aren't fully accounted for)
    assert estimate == pytest.approx(actual, rel=0.1)


def test_build_is_deterministic():
    assert build(**SIZE) == build(**SIZE)


def test_zip_fallback_compresses_more_of_the_payload():
    # Assert - the whole point of the unreadable-geometry channels: they're
    # only reachable with --zip-fallback, so that run always compresses more
    with_fallback = measured_ratio(with_zip_fallback=True, **SIZE)
    without_fallback = measured_ratio(with_zip_fallback=False, **SIZE)
    assert with_fallback < without_fallback
