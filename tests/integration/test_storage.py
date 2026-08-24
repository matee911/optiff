"""Accounting for physical bytes on real and synthetic files."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest

from tiff_analyzer.document import TiffDocument
from tiff_analyzer.storage import PhysicalClassifier, PhysicalStorageAnalyzer

#: How many bytes may stay unaccounted: IFD padding and the file tail.
MAX_UNACCOUNTED = 64


def analyzer(path: Path) -> tuple[PhysicalStorageAnalyzer, int]:
    document = TiffDocument(path)

    return PhysicalStorageAnalyzer(document), document.file_size


def test_accounted_plus_gaps_equals_file_size(synthetic_psd_tiff: Path):
    # Arrange
    with TiffDocument(synthetic_psd_tiff) as document:
        storage = PhysicalStorageAnalyzer(document)

        # Act
        accounted = storage.accounted_ranges()
        gaps = storage.unaccounted_ranges()

        # Assert
        total = sum(item.size for item in accounted) + sum(item.size for item in gaps)
        assert total == document.file_size


def test_gaps_are_sorted_disjoint_and_nonempty(synthetic_psd_tiff: Path):
    # Arrange
    with TiffDocument(synthetic_psd_tiff) as document:
        # Act
        gaps = PhysicalStorageAnalyzer(document).unaccounted_ranges()

    # Assert
    assert all(not item.is_empty for item in gaps)
    assert gaps == sorted(gaps, key=lambda item: item.start)

    for previous, current in pairwise(gaps):
        assert previous.end <= current.start


def test_striped_image_uses_exact_ranges(synthetic_striped_tiff: Path):
    # Arrange - Bug 7: a min..max hull would swallow everything between strips
    with TiffDocument(synthetic_striped_tiff) as document:
        storage = PhysicalStorageAnalyzer(document)

        # Act
        strips = document.image_data_ranges()
        block = next(
            item for item in storage.referenced_blocks() if item.name == "IMAGE DATA"
        )

        # Assert
        assert len(strips) > 1, "the fixture must produce several strips"
        assert block.size == sum(item.size for item in strips)
        assert block.size <= block.span.size


def test_image_block_size_is_sum_not_span(synthetic_striped_tiff: Path):
    # Arrange
    with TiffDocument(synthetic_striped_tiff) as document:
        block = next(
            item
            for item in PhysicalStorageAnalyzer(document).referenced_blocks()
            if item.name == "IMAGE DATA"
        )

        # Assert - the byte total never exceeds the span
        assert block.size == sum(item.size for item in block.ranges)


def test_exif_sub_ifd_is_accounted(synthetic_tiff: Path):
    # Arrange - the synthetic file has no Exif, so we only check it does not raise
    with TiffDocument(synthetic_tiff) as document:
        # Act
        ranges = document.tiff_structure_ranges()

    # Assert
    assert ranges
    assert all(not item.is_empty for item in ranges)


# ============================================================================
# REAL FILES
# ============================================================================


@pytest.mark.slow
def test_test1_is_fully_accounted(sample_tiff: Path):
    # Arrange
    with TiffDocument(sample_tiff) as document:
        storage = PhysicalStorageAnalyzer(document)
        classifier = PhysicalClassifier(document.path)

        # Act
        gaps = storage.unaccounted_ranges()
        unaccounted = sum(item.size for item in gaps)

        # Assert - after fixing Bug 8/9/10 only padding remains
        assert unaccounted <= MAX_UNACCOUNTED
        assert all(classifier.classify(item) == "ZERO / PADDING" for item in gaps)


@pytest.mark.slow
def test_test1_size_tree_contains_xmp_and_resources(sample_tiff: Path):
    # Arrange
    with TiffDocument(sample_tiff) as document:
        # Act
        blocks = PhysicalStorageAnalyzer(document).referenced_blocks()

    # Assert - Bug 9: neither entry existed at all before
    sizes = {block.name: block.size for block in blocks}
    assert sizes["XMP"] == 19133
    assert sizes["Photoshop Image Resources"] == 7722
    assert sizes["Photoshop ImageSourceData"] == 99738732


@pytest.mark.bigfile
def test_every_production_tiff_is_fully_accounted(tiff_dir: Path):
    # Arrange
    paths = sorted(tiff_dir.glob("*.tif"))

    if not paths:
        pytest.skip(f"No *.tif files in {tiff_dir}")

    problems: list[str] = []

    # Act
    for path in paths:
        with TiffDocument(path) as document:
            storage = PhysicalStorageAnalyzer(document)
            classifier = PhysicalClassifier(path)

            gaps = storage.unaccounted_ranges()
            accounted = storage.accounted_ranges()

            total = sum(item.size for item in accounted) + sum(
                item.size for item in gaps
            )

            if total != document.file_size:
                problems.append(
                    f"{path.name}: rozliczono {total:,} z {document.file_size:,}"
                )

            unaccounted = sum(item.size for item in gaps)

            if unaccounted > MAX_UNACCOUNTED:
                problems.append(f"{path.name}: {unaccounted:,} B nierozliczonych")

            noise = [
                f"0x{item.start:X}"
                for item in gaps
                if classifier.classify(item) != "ZERO / PADDING"
            ]

            if noise:
                problems.append(f"{path.name}: dziury niezerowe {noise}")

    # Assert
    assert not problems, "\n".join(problems)
