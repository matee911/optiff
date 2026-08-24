"""
Testy optymalizacji na kompletnym pliku TIFF.

We build the file ourselves, because the layout decides whether tag 37724
can be shortened without moving offsets.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import tifffile

from tests.unit.builders import (
    build_tiff,
    layer_record,
    layer_record_be,
    layer_section,
    layer_section_be,
    link_record_with_psb,
    psd_container,
)
from tiff_analyzer import optimize as optimize_module
from tiff_analyzer.document import TiffDocument
from tiff_analyzer.optimize import (
    OptimizeError,
    channel_digests_of,
    optimize,
    plan_file,
)
from tiff_analyzer.psd_analyzer import ImageSourceDataAnalyzer, TiffPhotoshopAnalyzer
from tiff_analyzer.psd_codec import RAW
from tiff_analyzer.psd_file import parse_document
from tiff_analyzer.psd_links import parse_links, read_linked_files
from tiff_analyzer.verify import channel_digests

WIDTH, ROWS = 96, 24
HEADER = 2


def smooth(seed: int) -> bytes:
    """Smooth 16-bit data that compresses like a real photographic layer."""
    rng = np.random.default_rng(seed)
    walk = np.cumsum(rng.integers(-4, 5, size=(ROWS, WIDTH)), axis=1)

    return (walk % 65536).astype(">u2").tobytes()


def layers_blob(count: int = 3) -> bytes:
    """A layer section with raw channels plus their data."""
    payloads = [smooth(index + 1) for index in range(count)]

    section = layer_section(
        layer_record(
            name="Tlo",
            bounds=(0, 0, ROWS, WIDTH),
            channels=tuple(
                (index, len(data) + HEADER)
                for index, data in enumerate(payloads)
            ),
        )
    )

    return section + b"".join(
        RAW.to_bytes(HEADER, "little") + data for data in payloads
    )


def layers_blob_be(count: int = 3) -> bytes:
    """A raw PSB layer section: big-endian, RAW channels."""
    payloads = [smooth(index + 1) for index in range(count)]

    section = layer_section_be(
        layer_record_be(
            bounds=(0, 0, ROWS, WIDTH),
            channels=tuple(
                (index, len(data) + HEADER)
                for index, data in enumerate(payloads)
            ),
        )
    )

    return section + b"".join(
        RAW.to_bytes(HEADER, "big") + data for data in payloads
    )


def write_source(directory: Path, *, photoshop_last: bool = True) -> Path:
    path = directory / "source.tif"

    path.write_bytes(
        build_tiff(
            psd_container(("Lr16", layers_blob())),
            width=WIDTH,
            height=ROWS,
            photoshop_last=photoshop_last,
        )
    )

    return path


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return write_source(tmp_path)


# ============================================================================
# HAPPY PATH
# ============================================================================


def test_output_is_smaller(source: Path, tmp_path: Path):
    # Act
    result = optimize(source, tmp_path / "result.tif")

    # Assert
    assert result.wrote_file
    assert result.size_after < result.size_before
    assert result.saved > 0


def test_verification_passes(source: Path, tmp_path: Path):
    # Act
    result = optimize(source, tmp_path / "result.tif")

    # Assert
    assert result.verified
    assert result.comparison.total > 0
    assert result.comparison.problems == ()


def test_original_is_untouched(source: Path, tmp_path: Path):
    # Arrange
    before = source.read_bytes()

    # Act
    optimize(source, tmp_path / "result.tif")

    # Assert
    assert source.read_bytes() == before


def test_flattened_image_is_identical(source: Path, tmp_path: Path):
    # Arrange - this is the layer Capture One and Affinity actually read
    output = tmp_path / "result.tif"

    # Act
    optimize(source, output)

    # Assert
    assert np.array_equal(tifffile.imread(source), tifffile.imread(output))


def test_channel_pixels_are_identical(source: Path, tmp_path: Path):
    # Arrange
    output = tmp_path / "result.tif"

    def digests(path: Path):
        analyzer = ImageSourceDataAnalyzer()

        with TiffDocument(path) as document:
            analysis = TiffPhotoshopAnalyzer(analyzer).analyze(document)
            reader = document.photoshop_source_reader()

            try:
                return [item.digest for item in channel_digests(reader, analysis)]
            finally:
                reader.close()

    # Act
    optimize(source, output)

    # Assert
    assert digests(source) == digests(output)


def test_mtime_is_preserved(source: Path, tmp_path: Path):
    # Arrange
    output = tmp_path / "result.tif"

    # Act
    optimize(source, output, keep_mtime=True)

    # Assert
    assert output.stat().st_mtime == pytest.approx(source.stat().st_mtime, abs=1)


def test_mtime_can_be_left_alone(source: Path, tmp_path: Path):
    # Act
    result = optimize(source, tmp_path / "result.tif", keep_mtime=False)

    # Assert
    assert result.wrote_file


def test_summary_numbers_add_up(source: Path, tmp_path: Path):
    # Act
    result = optimize(source, tmp_path / "result.tif")

    # Assert
    assert result.saved == result.size_before - result.size_after
    assert result.tag_after < result.tag_before
    assert result.channel_bytes_after < result.channel_bytes_before
    assert result.channels_changed <= result.channels_total


def test_result_is_idempotent(source: Path, tmp_path: Path):
    # Arrange
    once = tmp_path / "raz.tif"
    optimize(source, once)

    # Act - the second pass has nothing left to compress
    twice = optimize(once, tmp_path / "dwa.tif")

    # Assert
    assert twice.skipped
    assert not twice.wrote_file


# ============================================================================
# REFUSALS AND ERRORS
# ============================================================================


def test_refuses_when_tag_is_not_last(tmp_path: Path):
    # Arrange - obraz zapisany PO tagu 37724
    path = write_source(tmp_path, photoshop_last=False)

    # Act / Assert
    with pytest.raises(OptimizeError, match="not last in the file"):
        plan_file(path)


def test_refuses_to_overwrite_source(source: Path):
    # Act / Assert
    with pytest.raises(OptimizeError, match="cannot be the source file"):
        optimize(source, source)


def test_refuses_file_without_photoshop_tag(tmp_path: Path):
    # Arrange
    path = tmp_path / "goly.tif"
    tifffile.imwrite(path, np.zeros((8, 8, 3), dtype=np.uint16), photometric="rgb")

    # Act / Assert
    with pytest.raises(OptimizeError, match="no tag 37724"):
        plan_file(path)


def test_creates_missing_output_directory(source: Path, tmp_path: Path):
    # Act
    result = optimize(source, tmp_path / "new" / "katalog" / "result.tif")

    # Assert
    assert result.wrote_file
    assert result.output.exists()


def test_skipping_verification_still_writes(source: Path, tmp_path: Path):
    # Act
    result = optimize(source, tmp_path / "result.tif", verify=False)

    # Assert
    assert result.wrote_file
    assert result.comparison is None
    assert result.verified is False


def test_output_still_parses_as_tiff(source: Path, tmp_path: Path):
    # Arrange
    output = tmp_path / "result.tif"

    # Act
    optimize(source, output)

    # Assert
    with tifffile.TiffFile(output) as handle:
        page = handle.pages[0]

        assert page.imagewidth == WIDTH
        assert page.imagelength == ROWS
        assert 37724 in page.tags


# ============================================================================
# NUMBER CONSISTENCY
# ============================================================================


def test_saving_comes_from_channels(source: Path, tmp_path: Path):
    # Act
    result = optimize(source, tmp_path / "result.tif")

    # Assert - the saving at three levels may differ only by padding
    assert result.channel_saved > 0
    assert abs(result.padding_saved) < 4096
    assert 0 <= result.tail_saved <= 16


def test_levels_are_nested_not_additive(source: Path, tmp_path: Path):
    # Act
    result = optimize(source, tmp_path / "result.tif")

    # Assert - channels fit inside the tag, the tag fits inside the file
    assert result.channel_bytes_before < result.tag_before < result.size_before
    assert result.channel_bytes_after < result.tag_after < result.size_after


def test_saving_decomposition_adds_up(source: Path, tmp_path: Path):
    # Act
    result = optimize(source, tmp_path / "result.tif")

    # Assert
    assert (
        result.channel_saved + result.padding_saved + result.tail_saved
        == result.saved
    )


def test_inconsistent_numbers_are_rejected(source: Path, tmp_path: Path, monkeypatch):
    # Arrange - pretend the channels saved far less than the tag did
    module = optimize_module

    original = module._check_consistency

    def sabotage(result):
        object.__setattr__(result, "channel_bytes_after", 0)
        original(result)

    monkeypatch.setattr(module, "_check_consistency", sabotage)

    # Act / Assert
    with pytest.raises(OptimizeError, match="does not match"):
        optimize(source, tmp_path / "result.tif")


# ============================================================================
# CZAS
# ============================================================================


def test_timings_are_measured(source: Path, tmp_path: Path):
    # Act
    result = optimize(source, tmp_path / "result.tif")

    # Assert
    assert result.seconds_plan > 0
    assert result.seconds_write > 0
    assert result.seconds_verify > 0


def test_total_time_is_sum_of_stages(source: Path, tmp_path: Path):
    # Act
    result = optimize(source, tmp_path / "result.tif")

    # Assert
    assert result.seconds_total == pytest.approx(
        result.seconds_plan + result.seconds_write + result.seconds_verify
    )


def test_verification_time_is_zero_when_skipped(source: Path, tmp_path: Path):
    # Act
    result = optimize(source, tmp_path / "result.tif", verify=False)

    # Assert
    assert result.seconds_verify == pytest.approx(0.0, abs=0.05)


def test_throughput_is_reported(source: Path, tmp_path: Path):
    # Act
    result = optimize(source, tmp_path / "result.tif")

    # Assert
    assert result.throughput > 0


# ============================================================================
# OSADZONY SMART OBJECT
# ============================================================================


def test_linked_record_tail_survives(tmp_path: Path):
    # Arrange - rekord lnk2 niesie za danymi pliku identyfikator, elapsed
    # modyfikacji i blokade; ucinanie ich psuje file dla Photoshopa
    path = tmp_path / "z_linkiem.tif"
    path.write_bytes(
        build_tiff(
            psd_container(("lnk2", link_record_with_psb(layers_blob_be()))),
            width=WIDTH,
            height=ROWS,
        )
    )

    output = tmp_path / "result.tif"

    def record(target: Path):
        analyzer = ImageSourceDataAnalyzer()

        with TiffDocument(target) as document:
            analysis = TiffPhotoshopAnalyzer(analyzer).analyze(document)
            reader = document.photoshop_source_reader()

            try:
                block = next(b for b in analysis.blocks if b.key == "lnk2")
                links = parse_links(
                    reader, block.payload_offset, block.payload_offset + block.size
                )
                item = links.files[0]

                return item, reader.read_at(item.data_end, item.tail_size)
            finally:
                reader.close()

    before, tail_before = record(path)

    assert before.tail_size == 15, "fixture ma miec ogon jak realne files"

    # Act
    result = optimize(path, output)

    # Assert
    assert result.wrote_file

    after, tail_after = record(output)

    assert after.tail_size == before.tail_size
    assert tail_after == tail_before
    assert after.size < before.size


def test_embedded_psb_shrinks_and_still_parses(tmp_path: Path):
    # Arrange
    path = tmp_path / "z_linkiem.tif"
    path.write_bytes(
        build_tiff(
            psd_container(("lnk2", link_record_with_psb(layers_blob_be()))),
            width=WIDTH,
            height=ROWS,
        )
    )
    output = tmp_path / "result.tif"

    # Act
    optimize(path, output)

    # Assert
    analyzer = ImageSourceDataAnalyzer()

    with TiffDocument(output) as document:
        analysis = TiffPhotoshopAnalyzer(analyzer).analyze(document)
        reader = document.photoshop_source_reader()

        try:
            assert analysis.warnings == ()

            linked = read_linked_files(analysis, reader)
            assert linked.is_exact
            assert linked.warnings == ()

            item = linked.files[0]
            embedded = parse_document(reader, item.data_offset, item.size)

            assert embedded.accounted == item.size
            assert embedded.layers is not None
            assert embedded.layers.is_complete
        finally:
            reader.close()


# ============================================================================
# KOMPRESJA PIKSELI OBRAZU
# ============================================================================


def compressible_source(directory: Path) -> Path:
    """TIFF z gladkimi pikselami, ktore da sie skompresowac."""
    path = directory / "gladki.tif"

    path.write_bytes(
        build_tiff(
            psd_container(("Lr16", layers_blob())),
            width=WIDTH,
            height=ROWS,
            image=smooth(99) * 3,
        )
    )

    return path


def test_image_data_is_off_by_default(source: Path, tmp_path: Path):
    # Act
    result = optimize(source, tmp_path / "result.tif")

    # Assert
    assert result.image_before == 0
    assert result.image_saved == 0


def test_image_data_shrinks_when_enabled(tmp_path: Path):
    # Arrange
    path = compressible_source(tmp_path)
    output = tmp_path / "result.tif"

    # Act
    result = optimize(path, output, image_data=True)

    # Assert
    assert result.image_before > 0
    assert result.image_after < result.image_before
    assert result.verified


def test_image_pixels_survive_compression(tmp_path: Path):
    # Arrange
    path = compressible_source(tmp_path)
    output = tmp_path / "result.tif"

    # Act
    optimize(path, output, image_data=True)

    # Assert - tifffile rozpakowuje i musi dostac te same piksele
    assert np.array_equal(tifffile.imread(path), tifffile.imread(output))


def test_compression_tag_is_updated(tmp_path: Path):
    # Arrange
    path = compressible_source(tmp_path)
    output = tmp_path / "result.tif"

    # Act
    optimize(path, output, image_data=True)

    # Assert
    with tifffile.TiffFile(output) as handle:
        page = handle.pages[0]

        assert int(page.compression) == 8
        assert page.databytecounts[0] < page.imagewidth * page.imagelength * 6


def test_photoshop_tag_still_readable_after_image_compression(tmp_path: Path):
    # Arrange - offsety za obrazem musza sie przesunac
    path = compressible_source(tmp_path)
    output = tmp_path / "result.tif"

    # Act
    optimize(path, output, image_data=True)

    # Assert
    analyzer = ImageSourceDataAnalyzer()

    with TiffDocument(output) as document:
        analysis = TiffPhotoshopAnalyzer(analyzer).analyze(document)

        assert analysis.found
        assert analysis.warnings == ()
        assert analysis.blocks[-1].end == analysis.data_size


def test_refuses_already_compressed_image(tmp_path: Path):
    # Arrange
    path = tmp_path / "spakowany.tif"
    tifffile.imwrite(
        path,
        np.zeros((ROWS, WIDTH, 3), dtype=np.uint16),
        photometric="rgb",
        compression="zlib",
        extratags=[(37724, 7, 40, psd_container(("Pat2", b"")), True)],
    )

    # Act / Assert - the file is rejected earlier anyway, 37724 is not last
    with pytest.raises(OptimizeError):
        plan_file(path, image_data=True)


def test_shifted_offsets_stay_even(tmp_path: Path):
    # Arrange - TIFF wymaga parzystych offsetow wartosci; skrocenie obrazu
    # o nieparzysta liczbe bajtow przesunelo by wszystko na nieparzyste
    path = compressible_source(tmp_path)
    output = tmp_path / "result.tif"

    # Act
    result = optimize(path, output, image_data=True)

    # Assert
    assert result.image_saved % 2 == 0

    with tifffile.TiffFile(output) as handle:
        page = handle.pages[0]

        for tag in page.tags.values():
            if tag.valuebytecount > 4:
                assert tag.valueoffset % 2 == 0, f"{tag.name} na nieparzystym"


def test_strip_byte_count_excludes_padding(tmp_path: Path):
    # Arrange
    path = compressible_source(tmp_path)
    output = tmp_path / "result.tif"

    # Act
    result = optimize(path, output, image_data=True)

    # Assert - deklarowana dlugosc to same data; dopelnienie jest poza nia,
    # a mimo to obraz musi sie rozpakowac w calosci
    with tifffile.TiffFile(output) as handle:
        declared = handle.pages[0].databytecounts[0]

        assert declared <= result.image_after
        assert np.array_equal(handle.asarray(), tifffile.imread(path))


def test_second_pass_skips_pixels_instead_of_failing(tmp_path: Path):
    """Przejechanie drugi raz po wlasnym wyniku nie moze wywalic calosci."""
    # Arrange - pierwszy przebieg pakuje i warstwy, i piksele
    path = compressible_source(tmp_path)
    pierwszy = tmp_path / "raz.tif"
    optimize(path, pierwszy, image_data=True)

    # Act - ten sam file jeszcze raz, tym samym przelacznikiem
    result = optimize(pierwszy, tmp_path / "dwa.tif", image_data=True)

    # Assert
    assert result.skipped
    assert any("image pixels skipped" in note for note in result.notes)


def test_compresses_pixels_when_layers_are_already_done(tmp_path: Path):
    """Warstwy spakowane, piksele nie - robimy same piksele."""
    # Arrange - pierwszy przebieg tyka wylacznie warstwy
    path = compressible_source(tmp_path)
    pierwszy = tmp_path / "warstwy.tif"
    optimize(path, pierwszy, image_data=False)

    # Act
    result = optimize(pierwszy, tmp_path / "piksele.tif", image_data=True)

    # Assert
    assert not result.skipped
    assert result.image_after < result.image_before
    assert result.channels_changed == 0, "layers must not be touched twice"
    assert result.verified


def mask_blob() -> bytes:
    """
    Sekcja z warstwa korekcyjna: prostokat 0x0, a mask niesie data.

    This is what real adjustment layers look like: they have no
    pixels of their own, so their rectangle is zero, yet the mask has its
    wlasny i potrafi wazyc dziesiatki megabajtow.
    """
    mask = smooth(7)

    section = layer_section(
        layer_record(
            name="Black & White 1",
            bounds=(0, 0, 0, 0),
            channels=((-2, len(mask) + HEADER),),
        )
    )

    return section + RAW.to_bytes(HEADER, "little") + mask


def mask_source(directory: Path) -> Path:
    path = directory / "mask.tif"
    path.write_bytes(build_tiff(psd_container(("Lr16", mask_blob()))))

    return path


def test_zero_rect_channel_is_left_alone_by_default(tmp_path: Path):
    # Arrange
    path = mask_source(tmp_path)

    # Act
    result = optimize(path, tmp_path / "result.tif")

    # Assert - without the flag a channel with no geometry is left alone
    assert result.channels_changed == 0
    assert result.skipped


def test_zip_fallback_packs_zero_rect_channel(tmp_path: Path):
    # Arrange
    path = mask_source(tmp_path)
    output = tmp_path / "result.tif"

    # Act
    result = optimize(path, output, zip_fallback=True)

    # Assert
    assert not result.skipped
    assert result.channels_changed == 1
    assert result.size_after < result.size_before
    assert result.verified, "SHA256 musi sie zgadzac mimo braku geometrii"


def test_zip_fallback_keeps_pixels_bit_for_bit(tmp_path: Path):
    # Arrange
    path = mask_source(tmp_path)
    output = tmp_path / "result.tif"

    # Act
    optimize(path, output, zip_fallback=True)

    # Assert - channel wynikowy rozpakowany musi dac dokladnie zrodlowe piksele
    before = channel_digests_of(path)
    after = channel_digests_of(output)

    assert [d.digest for d in before] == [d.digest for d in after]
    assert all(d.source == "pixels" for d in after)
