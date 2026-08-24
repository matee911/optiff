"""Integration tests for `TiffDocument`, on synthetic and real files."""

from __future__ import annotations

from pathlib import Path

import pytest

from tiff_analyzer.document import TiffDocument

PHOTOSHOP_TAG = 37724
XMP_TAG = 700


def test_tag_value_size_is_nonzero_for_every_tag(synthetic_tiff: Path):
    # Arrange
    with TiffDocument(synthetic_tiff) as document:
        tags = list(document.first_page.tags.values())

        # Act
        sizes = {tag.code: document.tag_value_size(tag) for tag in tags}

    # Assert
    assert tags, "The synthetic TIFF has no tags."
    zero = [code for code, size in sizes.items() if size == 0]
    assert not zero, f"tag_value_size() returned 0 for tags: {zero}"


def test_tag_value_size_matches_valuebytecount(synthetic_tiff: Path):
    # Arrange
    with TiffDocument(synthetic_tiff) as document:
        # Act
        actual = {
            tag.code: document.tag_value_size(tag)
            for tag in document.first_page.tags.values()
        }
        expected = {
            tag.code: tag.valuebytecount
            for tag in document.first_page.tags.values()
        }

    # Assert
    assert actual == expected


def test_xmp_tag_value_size_matches_payload(synthetic_tiff: Path):
    # Arrange
    with TiffDocument(synthetic_tiff) as document:
        tag = document.tag(XMP_TAG)

        # Act
        size = document.tag_value_size(tag)
        data = document.raw_tag_data(XMP_TAG)

    # Assert
    assert data is not None
    assert size == len(data)


def test_tag_data_range_has_no_padding_fudge(synthetic_tiff: Path):
    # Arrange
    with TiffDocument(synthetic_tiff) as document:
        tag = document.tag(XMP_TAG)

        # Act
        physical_range = document.tag_data_range(XMP_TAG)

        # Assert
        assert physical_range is not None
        assert physical_range.start == tag.valueoffset
        assert physical_range.end == tag.valueoffset + tag.valuebytecount


def test_tiff_structure_includes_out_of_line_tag_values(synthetic_tiff: Path):
    # Arrange
    with TiffDocument(synthetic_tiff) as document:
        xmp = document.tag_data_range(XMP_TAG)

        # Act
        ranges = document.tiff_structure_ranges()

    # Assert - XMP (a few hundred bytes) does not fit inline, so it must be here
    assert xmp is not None
    assert any(
        item.start == xmp.start and item.end >= xmp.end for item in ranges
    ), f"Brak zakresu XMP {xmp} w tiff_structure_ranges(): {ranges}"


def test_context_manager_closes_document(synthetic_tiff: Path):
    # Arrange / Act
    with TiffDocument(synthetic_tiff) as document:
        assert document.image_info.width > 0

    # Assert
    assert document.tiff.filehandle.closed


# ============================================================================
# REALNE PLIKI
# ============================================================================


@pytest.mark.slow
def test_photoshop_tag_exists(sample_tiff: Path):
    # Arrange
    with TiffDocument(sample_tiff) as document:
        # Act
        tag = document.tag(PHOTOSHOP_TAG)

        # Assert
        assert tag is not None
        assert tag.code == PHOTOSHOP_TAG
        assert tag.count == 99738732


@pytest.mark.slow
def test_tag_data_range_for_photoshop_image_source_data(sample_tiff: Path):
    # Arrange
    with TiffDocument(sample_tiff) as document:
        # Act
        result = document.tag_data_range(PHOTOSHOP_TAG)

        # Assert - 4 zero bytes behind the value are a real gap at the end of
        # the file, not tag alignment padding.
        assert result is not None
        assert result.start == 0x9249458
        assert result.end == 0xF1678C4
        assert result.size == 99738732


@pytest.mark.slow
def test_raw_tag_data_returns_photoshop_container(sample_tiff: Path):
    # Arrange
    with TiffDocument(sample_tiff) as document:
        # Act
        data = document.raw_tag_data(PHOTOSHOP_TAG)

        # Assert
        assert data is not None
        assert len(data) == 99738732
        assert data.startswith(b"Adobe Photoshop Document Data Block\x00")


@pytest.mark.slow
def test_xmp_and_image_resources_are_visible(sample_tiff: Path):
    # Arrange
    with TiffDocument(sample_tiff) as document:
        # Act
        xmp = document.tag_value_size(document.tag(XMP_TAG))
        resources = document.tag_value_size(document.tag(34377))

        # Assert
        assert xmp == 19133
        assert resources == 7722
