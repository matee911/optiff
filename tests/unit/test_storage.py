"""Tests for range merging and gap detection, with no I/O."""

from __future__ import annotations

import os

import pytest

from tiff_analyzer.domain import PhysicalRange
from tiff_analyzer.storage import PhysicalClassifier, gaps, merge_ranges


def ranges(*pairs: tuple[int, int]) -> list[PhysicalRange]:
    return [PhysicalRange(start, end) for start, end in pairs]


def pairs(items: list[PhysicalRange]) -> list[tuple[int, int]]:
    return [(item.start, item.end) for item in items]


# ============================================================================
# MERGE
# ============================================================================


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ([(0, 10), (20, 30)], [(0, 10), (20, 30)]),  # disjoint
        ([(0, 10), (10, 20)], [(0, 20)]),  # touching
        ([(0, 15), (10, 20)], [(0, 20)]),  # overlapping
        ([(0, 30), (10, 20)], [(0, 30)]),  # zawarty
        ([(20, 30), (0, 10)], [(0, 10), (20, 30)]),  # nieposortowane
        ([(0, 10), (5, 5), (20, 30)], [(0, 10), (20, 30)]),  # empty pomijany
        ([], []),
        ([(5, 5)], []),
    ],
)
def test_merge_ranges(given, expected):
    # Act
    result = merge_ranges(ranges(*given))

    # Assert
    assert pairs(result) == expected


def test_merge_ranges_does_not_mutate_input():
    # Arrange
    given = ranges((10, 20), (0, 5))
    snapshot = pairs(given)

    # Act
    merge_ranges(given)

    # Assert
    assert pairs(given) == snapshot


# ============================================================================
# GAPS
# ============================================================================


@pytest.mark.parametrize(
    ("given", "file_size", "expected"),
    [
        ([(0, 10)], 20, [(10, 20)]),  # gap at the end
        ([(10, 20)], 20, [(0, 10)]),  # gap at the start
        ([(0, 20)], 20, []),  # full coverage
        ([(0, 5), (10, 20)], 20, [(5, 10)]),  # gap in the middle
        ([], 20, [(0, 20)]),  # nic nieznane
        ([(0, 5), (10, 15)], 20, [(5, 10), (15, 20)]),  # dwie dziury
    ],
)
def test_gaps(given, file_size, expected):
    # Act
    result = gaps(ranges(*given), file_size)

    # Assert
    assert pairs(result) == expected


def test_gaps_are_sorted_disjoint_and_nonempty():
    # Arrange
    accounted = merge_ranges(ranges((100, 200), (0, 50), (300, 400)))

    # Act
    found = gaps(accounted, 500)

    # Assert
    assert pairs(found) == [(50, 100), (200, 300), (400, 500)]
    assert all(not item.is_empty for item in found)
    assert found == sorted(found, key=lambda item: item.start)


def test_foreign_bytes_between_strips_are_reported():
    # Arrange - Bug 7: a min..max hull would swallow a foreign block between strips
    strips = ranges((1000, 1100), (1200, 1300))
    hull = ranges((1000, 1300))

    # Act
    exact_gaps = gaps(merge_ranges([*strips, PhysicalRange(0, 1000)]), 1300)
    hull_gaps = gaps(merge_ranges([*hull, PhysicalRange(0, 1000)]), 1300)

    # Assert
    assert pairs(exact_gaps) == [(1100, 1200)]
    assert hull_gaps == [], "a hull hides foreign bytes, which is why we do not use one"


def test_accounted_plus_gaps_covers_whole_file():
    # Arrange
    accounted = merge_ranges(ranges((0, 8), (100, 250), (900, 1000)))
    file_size = 1000

    # Act
    found = gaps(accounted, file_size)

    # Assert
    total = sum(item.size for item in accounted) + sum(item.size for item in found)
    assert total == file_size


# ============================================================================
# KLASYFIKACJA
# ============================================================================


def test_classify_empty():
    assert PhysicalClassifier.classify_bytes(b"") == "EMPTY"


def test_classify_zeros():
    assert PhysicalClassifier.classify_bytes(bytes(4096)) == "ZERO / PADDING"


def test_classify_structured_text():
    # Arrange - repetitive text has low entropy
    data = b"<?xml version='1.0'?><rdf:RDF>" * 100

    # Act / Assert
    assert PhysicalClassifier.classify_bytes(data) == "LOW ENTROPY / STRUCTURED"


def test_classify_random_is_high_entropy():
    # Arrange
    data = os.urandom(65536)

    # Act / Assert
    assert (
        PhysicalClassifier.classify_bytes(data)
        == "HIGH ENTROPY / COMPRESSED OR ENCRYPTED"
    )
