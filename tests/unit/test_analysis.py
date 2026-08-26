"""Tests for analysis.py's "nothing here" branches, with tifffile faked out."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import tifffile

from optiff.analysis import _collect_embedded, _collect_layers, _collect_provenance
from optiff.document import TiffDocument
from optiff.domain import PhotoshopAnalysis, PhotoshopBlock
from optiff.psd_links import LinkedFile
from optiff.readers import BytesReader


def _document(path, *, tags) -> TiffDocument:
    document = object.__new__(TiffDocument)
    document.path = path
    document.tiff = cast(
        tifffile.TiffFile,
        SimpleNamespace(
            byteorder="<",
            is_bigtiff=False,
            pages=SimpleNamespace(first=SimpleNamespace(tags=tags, offset=0)),
        ),
    )
    return document


def _block() -> PhotoshopBlock:
    return PhotoshopBlock(
        signature="8BIM",
        key="8BIM",
        offset=0,
        size=10,
        padded_size=12,
        description="",
        payload_offset=8,
        raw_signature=b"",
        raw_key="",
        byte_order="<",
        header_size=12,
    )


def _analysis(*, blocks=()) -> PhotoshopAnalysis:
    return PhotoshopAnalysis(
        found=bool(blocks),
        signature="8BIM" if blocks else None,
        data_size=100,
        blocks=blocks,
        layer_count=None,
        warnings=(),
    )


def test_collect_provenance_is_no_tag_when_the_photoshop_tag_is_absent(tmp_path):
    path = tmp_path / "f.tif"
    path.write_bytes(bytes(200))
    document = _document(path, tags={})  # no tag 37724
    photoshop = _analysis(blocks=(_block(),))  # blocks present, so past "no-blocks"

    result = _collect_provenance(document, photoshop)

    assert result.state == "no-tag"
    assert result.report is None


def test_collect_layers_is_no_section_when_no_layer_block_is_found(tmp_path):
    path = tmp_path / "f.tif"
    path.write_bytes(bytes(200))
    tag = SimpleNamespace(valuebytecount=10, valueoffset=50)
    document = _document(path, tags={37724: tag})
    photoshop = _analysis(blocks=())  # no LAYER_SECTION_KEYS block to find

    result = _collect_layers(document, photoshop)

    assert result.state == "no-section"
    assert result.stack is None


def test_collect_embedded_reports_a_document_error():
    reader = BytesReader(b"not a psd file...")
    item = LinkedFile(
        index=0,
        kind="liFD",
        version=1,
        uid="",
        name="bad.psb",
        file_type="8BPB",
        creator="",
        size=10,
        offset=0,
        record_size=0,
        has_descriptor=False,
        data_offset=0,
    )

    section = _collect_embedded(reader, item)

    assert section.document is None
    assert section.error is not None
    assert section.name == "bad.psb"
