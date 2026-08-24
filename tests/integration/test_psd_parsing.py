"""Parsowanie ImageSourceData na realnych i syntetycznych TIFF-ach."""

from __future__ import annotations

from pathlib import Path

import pytest

from tiff_analyzer.document import TiffDocument
from tiff_analyzer.psd_analyzer import ImageSourceDataAnalyzer, TiffPhotoshopAnalyzer

#: Verified block layout of the small sample file.
TEST1_KEYS = ["Lr16", "LMsk", "Pat2", "CAI ", "GenI", "FMsk", "cinf"]
TEST1_OFFSETS = [0x24, 0x5F1E1CC, 0x5F1E1E8, 0x5F1E1F4, 0x5F1E250, 0x5F1E2B0, 0x5F1E2C8]
TEST1_END = 0x5F1E46C

#: Verified layout of the largest production file.
PROBKA_A_KEYS = [
    "Lr16",
    "LMsk",
    "Pat2",
    "CAI ",
    "GenI",
    "lnk2",
    "lnkE",
    "FEid",
    "FMsk",
    "cinf",
]
PROBKA_A_END = 0xA66A5240


# ============================================================================
# SYNTETYK
# ============================================================================


def test_analyzes_synthetic_tiff_through_tifffile(synthetic_psd_tiff: Path):
    # Arrange
    with TiffDocument(synthetic_psd_tiff) as document:
        # Act
        result = TiffPhotoshopAnalyzer().analyze(document)

    # Assert
    assert result.found is True
    assert [block.key for block in result.blocks] == [
        "Lr16",
        "LMsk",
        "Pat2",
        "CAI ",
        "cinf",
    ]
    assert result.warnings == ()


def test_synthetic_walk_terminates_exactly(synthetic_psd_tiff: Path):
    # Arrange
    with TiffDocument(synthetic_psd_tiff) as document:
        # Act
        result = TiffPhotoshopAnalyzer().analyze(document)

    # Assert
    assert result.blocks[-1].end == result.data_size


def test_tiff_without_photoshop_tag_is_not_found(synthetic_tiff: Path):
    # Arrange
    with TiffDocument(synthetic_tiff) as document:
        # Act
        result = TiffPhotoshopAnalyzer().analyze(document)

    # Assert
    assert result.found is False
    assert result.blocks == ()


def test_reader_path_matches_bytes_path(synthetic_psd_tiff: Path):
    # Arrange
    analyzer = ImageSourceDataAnalyzer()

    with TiffDocument(synthetic_psd_tiff) as document:
        data = document.photoshop_source_data()

        assert data is not None, "the file has no tag 37724"

        # Act
        via_reader = TiffPhotoshopAnalyzer(analyzer).analyze(document)
        via_bytes = analyzer.analyze(data)

    # Assert
    assert via_reader == via_bytes


# ============================================================================
# REAL FILES
# ============================================================================


@pytest.mark.slow
def test_test1_block_layout(sample_tiff: Path):
    # Arrange
    with TiffDocument(sample_tiff) as document:
        # Act
        result = TiffPhotoshopAnalyzer().analyze(document)

    # Assert
    assert [block.key for block in result.blocks] == TEST1_KEYS
    assert [block.offset for block in result.blocks] == TEST1_OFFSETS
    assert result.blocks[-1].end == TEST1_END == result.data_size
    assert result.warnings == ()


@pytest.mark.slow
def test_test1_descriptions_are_resolved(sample_tiff: Path):
    # Arrange
    with TiffDocument(sample_tiff) as document:
        # Act
        result = TiffPhotoshopAnalyzer().analyze(document)

    # Assert - Bug 4: every block used to get a generic label
    described = {block.key: block.description for block in result.blocks}
    assert described["Lr16"] == "Layers (16-bit)"
    assert described["CAI "] == "Content Authenticity (C2PA)"
    assert "Photoshop resource" not in described.values()


@pytest.mark.slow
def test_test1_layer_payload_is_compressed(sample_tiff: Path):
    # Arrange
    analyzer = ImageSourceDataAnalyzer()

    with TiffDocument(sample_tiff) as document:
        result = TiffPhotoshopAnalyzer(analyzer).analyze(document)
        reader = document.photoshop_source_reader()

        assert reader is not None, "the file has no tag 37724"

        try:
            layers = next(b for b in result.blocks if b.key == "Lr16")

            # Act - only the first MB, not the whole payload
            stats = analyzer.payload_stats(
                analyzer.payload(reader, layers, limit=1_000_000)
            )
        finally:
            reader.close()

    # Assert
    assert stats["size"] == 1_000_000
    assert stats["entropy"] > 6.0


@pytest.mark.bigfile
def test_probka_a_block_layout(sample_named):
    # Arrange
    path = sample_named("big-psb")

    if not path.exists():
        pytest.skip(f"Brak {path}")

    with TiffDocument(path) as document:
        # Act
        result = TiffPhotoshopAnalyzer().analyze(document)

    # Assert
    assert [block.key for block in result.blocks] == PROBKA_A_KEYS
    assert result.blocks[-1].end == PROBKA_A_END == result.data_size

    linked = next(block for block in result.blocks if block.key == "lnk2")
    assert linked.size == 2_033_731_564
    assert result.warnings == ()


@pytest.mark.bigfile
def test_v0002_container_uses_psb_lengths(sample_named):
    # Arrange - newer Photoshop writes a "V0002" signature, in which
    # selected keys carry an 8-byte length instead of a 4-byte one.
    path = sample_named("big-raw")

    if not path.exists():
        pytest.skip(f"Brak {path}")

    with TiffDocument(path) as document:
        # Act
        result = TiffPhotoshopAnalyzer().analyze(document)

    # Assert
    assert result.signature == "Adobe Photoshop Document Data V0002"
    assert [block.key for block in result.blocks] == [
        "Lr16",
        "LMsk",
        "Pat2",
        "CAI ",
        "GenI",
        "lnk2",
        "lnkE",
        "FMsk",
        "cinf",
    ]

    by_key = {block.key: block for block in result.blocks}

    # Keys on the PSB list get an 8-byte length despite the 8BIM signature.
    assert by_key["Lr16"].signature == "8BIM"
    assert by_key["Lr16"].header_size == 16
    assert by_key["LMsk"].header_size == 16
    assert by_key["lnk2"].header_size == 16

    # Keys outside the list stay at 4 bytes.
    assert by_key["Pat2"].signature == "8BIM"
    assert by_key["Pat2"].header_size == 12
    assert by_key["CAI "].header_size == 12
    assert by_key["GenI"].header_size == 12

    # The stream tail uses an explicit 8B64 signature ("46B8" on disk).
    assert by_key["cinf"].signature == "8B64"
    assert by_key["cinf"].raw_signature == b"46B8"
    assert by_key["cinf"].header_size == 16

    assert result.blocks[-1].end == result.data_size
    assert result.warnings == ()


@pytest.mark.bigfile
def test_every_production_tiff_walks_cleanly(tiff_dir: Path):
    # Arrange
    paths = sorted(tiff_dir.glob("*.tif"))

    if not paths:
        pytest.skip(f"No *.tif files in {tiff_dir}")

    problems: list[str] = []

    # Act
    for path in paths:
        with TiffDocument(path) as document:
            result = TiffPhotoshopAnalyzer().analyze(document)

        if not result.found:
            continue

        if result.warnings:
            codes = [warning.code for warning in result.warnings]
            problems.append(f"{path.name}: warnings {codes}")
            continue

        if result.blocks and result.blocks[-1].end != result.data_size:
            problems.append(
                f"{path.name}: koniec {result.blocks[-1].end} "
                f"!= size {result.data_size}"
            )

    # Assert
    assert not problems, "\n".join(problems)
    assert len(paths) >= 2
