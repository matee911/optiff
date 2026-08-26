"""AnalyzeReport: the data analyze() collects, with no printing involved."""

from __future__ import annotations

from pathlib import Path

from optiff.analysis import analyze
from optiff.document import TiffDocument
from optiff.metadata import MetadataAnalyzer


def test_reports_basic_file_properties(synthetic_tiff: Path):
    # Act
    report = analyze(synthetic_tiff)

    # Assert
    assert report.path == synthetic_tiff
    assert report.file_size == synthetic_tiff.stat().st_size
    assert report.byte_order in ("little-endian", "big-endian")
    assert report.image.width > 0


def test_no_photoshop_tag_reports_no_tag_states(synthetic_tiff: Path):
    # Act
    report = analyze(synthetic_tiff)

    # Assert
    assert report.photoshop.found is False
    assert report.provenance.state in ("no-blocks", "no-tag")
    assert report.layers.state == "no-tag"
    assert report.layers.stack is None
    assert report.linked_files is None


def test_photoshop_tag_reports_layers_and_provenance(synthetic_psd_tiff: Path):
    # Act
    report = analyze(synthetic_psd_tiff)

    # Assert
    assert report.photoshop.found is True
    assert report.layers.state == "found"
    assert report.layers.stack is not None
    assert report.provenance.state == "found"
    assert report.provenance.report is not None


def test_metadata_matches_metadata_analyzer(synthetic_psd_tiff: Path):
    # Arrange
    with TiffDocument(synthetic_psd_tiff) as document:
        expected = MetadataAnalyzer(document).report()

    # Act
    report = analyze(synthetic_psd_tiff)

    # Assert
    assert report.metadata == expected


def test_structure_lists_every_tiff_tag(synthetic_tiff: Path):
    # Arrange
    with TiffDocument(synthetic_tiff) as document:
        expected_count = len(document.first_page.tags)

    # Act
    report = analyze(synthetic_tiff)

    # Assert
    assert len(report.structure) == expected_count
    assert all(item.code > 0 for item in report.structure)
