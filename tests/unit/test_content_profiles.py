"""
The content-profile axis in `tests/sample_files.py`.

`CASES` covers file *structure* thoroughly; these profiles cover pixel
*content* - the axis a compression benchmark actually depends on. Each test
checks the profile behaves like the real-world thing it stands in for, not
just that it runs.
"""

from __future__ import annotations

import zlib

import numpy as np
import pytest

from tests.sample_files import (
    BPP,
    CONTENT_PROFILES,
    ROWS,
    WIDTH,
    banded,
    detail,
    grain,
    smooth,
)

SIZE = {"width": 64, "rows": 64}


@pytest.mark.parametrize("name", sorted(CONTENT_PROFILES))
def test_deterministic_and_seed_sensitive(name):
    # Arrange
    profile = CONTENT_PROFILES[name]

    # Act / Assert
    assert profile(1, **SIZE) == profile(1, **SIZE)
    assert profile(1, **SIZE) != profile(2, **SIZE)


@pytest.mark.parametrize("name", sorted(CONTENT_PROFILES))
def test_default_size_matches_the_module_constants(name):
    # Act
    data = CONTENT_PROFILES[name](1)

    # Assert
    assert len(data) == WIDTH * ROWS * BPP


def _deflated_size(data: bytes) -> int:
    return len(zlib.compress(data, 6))


def test_flat_compresses_smallest():
    # Arrange
    sizes = {
        name: _deflated_size(fn(1, **SIZE)) for name, fn in CONTENT_PROFILES.items()
    }

    # Assert
    assert sizes["flat"] == min(sizes.values())


def test_grain_is_near_incompressible():
    # Arrange
    data = grain(1, **SIZE)

    # Act
    deflated = _deflated_size(data)

    # Assert - deflate cannot shrink independent noise; it may even grow it
    assert deflated >= len(data) * 0.98


def test_detail_sits_between_smooth_and_grain():
    # Arrange
    smooth_size = _deflated_size(smooth(1, **SIZE))
    detail_size = _deflated_size(detail(1, **SIZE))
    grain_size = _deflated_size(grain(1, **SIZE))

    # Assert
    assert smooth_size < detail_size < grain_size


def test_banded_benefits_more_from_horizontal_prediction_than_others():
    # Arrange - the same horizontal-differencing ZIP_PREDICTED uses
    width = SIZE["width"]

    def predicted_size(data: bytes) -> int:
        rows = np.frombuffer(data, dtype=">u2").reshape(-1, width)
        deltas = np.diff(rows.astype("int64"), axis=1, prepend=0) % 65536
        return _deflated_size(deltas.astype(">u2").tobytes())

    def improvement(fn) -> float:
        data = fn(1, **SIZE)
        return _deflated_size(data) / predicted_size(data)

    banded_improvement = improvement(banded)

    # Assert - prediction shrinks banded more, relatively, than smooth/detail
    assert banded_improvement > improvement(smooth)
    assert banded_improvement > improvement(detail)
    assert banded_improvement > 1  # prediction actually helps banded
