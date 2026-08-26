"""
Tests for optimize_psd.py's own branching, with its collaborators
(parse_document, walk, parse_links, _plan_layers_block) replaced by fakes.

Their own correctness is covered elsewhere (psd_file, psd_blocks, psd_links,
optimize_layers); these tests isolate optimize_psd's decisions about when to
give up and copy a block verbatim versus rebuild it.
"""

from __future__ import annotations

from optiff.domain import PhotoshopAnalysis, PhotoshopBlock
from optiff.optimize_layers import DEFAULT_SOURCES, LayerSectionPlan
from optiff.optimize_psd import _plan_embedded, _plan_links_block, plan_container
from optiff.psd_codec import ZIP_PREDICTED
from optiff.psd_file import DocumentError, EmbeddedDocument, FileSection
from optiff.psd_layers import LayerStack
from optiff.psd_links import LinkedFile, LinkedFiles
from optiff.readers import BytesReader
from optiff.segments import Copy

EMPTY_STACK = LayerStack(
    layers=(), declared_count=0, has_transparency=False, consumed=0, total=0
)


def _plan_embedded_kwargs():
    return {"level": 6, "sources": DEFAULT_SOURCES, "target": ZIP_PREDICTED}


# ============================================================================
# _plan_embedded
# ============================================================================


def test_plan_embedded_copies_verbatim_without_a_layer_and_mask_section(monkeypatch):
    document = EmbeddedDocument(
        version=1,
        channels=3,
        width=1,
        height=1,
        depth=8,
        color_mode=3,
        sections=(),  # no "Layer and Mask Information" section at all
        layers=EMPTY_STACK,
        total=200,
    )
    monkeypatch.setattr(
        "optiff.optimize_psd.parse_document", lambda reader, start, size: document
    )

    result = _plan_embedded(BytesReader(b""), 0, 200, **_plan_embedded_kwargs())

    assert result == ([Copy(0, 200)], 200, [])


def test_plan_embedded_copies_verbatim_when_layer_info_size_is_positive(monkeypatch):
    section = FileSection("Layer and Mask Information", 20, 4, 50)  # data_offset 24
    document = EmbeddedDocument(
        version=1,  # is_large False -> 4 B length fields
        channels=3,
        width=1,
        height=1,
        depth=8,
        color_mode=3,
        sections=(section,),
        layers=EMPTY_STACK,
        total=200,
    )
    monkeypatch.setattr(
        "optiff.optimize_psd.parse_document", lambda reader, start, size: document
    )

    data = bytearray(200)
    data[24:28] = (5).to_bytes(4, "big")  # classic Layer Info, not empty
    reader = BytesReader(bytes(data))

    result = _plan_embedded(reader, 0, 200, **_plan_embedded_kwargs())

    assert result == ([Copy(0, 200)], 200, [])


def test_plan_embedded_copies_verbatim_when_no_layer_block_is_found(monkeypatch):
    section = FileSection("Layer and Mask Information", 20, 4, 50)
    document = EmbeddedDocument(
        version=1,
        channels=3,
        width=1,
        height=1,
        depth=8,
        color_mode=3,
        sections=(section,),
        layers=EMPTY_STACK,
        total=200,
    )
    monkeypatch.setattr(
        "optiff.optimize_psd.parse_document", lambda reader, start, size: document
    )
    monkeypatch.setattr(
        "optiff.optimize_psd.walk", lambda reader, offset, end, large_document: ((), ())
    )

    data = bytearray(200)
    data[24:28] = (0).to_bytes(4, "big")  # layers live in the additional Lr16 block
    reader = BytesReader(bytes(data))

    result = _plan_embedded(reader, 0, 200, **_plan_embedded_kwargs())

    assert result == ([Copy(0, 200)], 200, [])


def test_plan_embedded_copies_verbatim_when_the_layer_plan_is_unchanged(monkeypatch):
    section = FileSection("Layer and Mask Information", 20, 4, 50)
    document = EmbeddedDocument(
        version=1,
        channels=3,
        width=1,
        height=1,
        depth=8,
        color_mode=3,
        sections=(section,),
        layers=EMPTY_STACK,
        total=200,
    )
    layer_block = PhotoshopBlock(
        signature="8BIM",
        key="Lr16",
        offset=40,
        size=10,
        padded_size=10,
        description="",
        payload_offset=48,
        raw_signature=b"",
        raw_key="",
        byte_order=">",
        header_size=12,
    )
    monkeypatch.setattr(
        "optiff.optimize_psd.parse_document", lambda reader, start, size: document
    )
    monkeypatch.setattr(
        "optiff.optimize_psd.walk",
        lambda reader, offset, end, large_document: ((layer_block,), ()),
    )
    monkeypatch.setattr(
        "optiff.optimize_psd._plan_layers_block",
        lambda *args, **kwargs: ([], 10, LayerSectionPlan()),  # no results -> unchanged
    )

    data = bytearray(200)
    data[24:28] = (0).to_bytes(4, "big")
    reader = BytesReader(bytes(data))

    result = _plan_embedded(reader, 0, 200, **_plan_embedded_kwargs())

    assert result == ([Copy(0, 200)], 200, [])


# ============================================================================
# _plan_links_block
# ============================================================================


def _links_block() -> PhotoshopBlock:
    return PhotoshopBlock(
        signature="8BIM",
        key="lnk2",
        offset=0,
        size=100,
        padded_size=100,
        description="",
        payload_offset=8,
        raw_signature=b"",
        raw_key="",
        byte_order="<",
        header_size=12,
    )


def _plan_links_kwargs():
    return {"level": 6, "sources": DEFAULT_SOURCES, "target": ZIP_PREDICTED}


def test_plan_links_block_copies_verbatim_when_there_are_no_files(monkeypatch):
    block = _links_block()
    monkeypatch.setattr(
        "optiff.optimize_psd.parse_links",
        lambda reader, start, end: LinkedFiles(files=(), consumed=0, total=0),
    )

    result = _plan_links_block(BytesReader(b""), block, **_plan_links_kwargs())

    assert result == ([Copy(0, 100)], 100, [])


def test_plan_links_block_skips_a_non_embedded_file(monkeypatch):
    block = _links_block()
    external = LinkedFile(
        index=0,
        kind="liFE",  # not "liFD" -> not embedded
        version=1,
        uid="",
        name="ext",
        file_type="",
        creator="",
        size=0,
        offset=10,
        record_size=0,
        has_descriptor=False,
    )
    monkeypatch.setattr(
        "optiff.optimize_psd.parse_links",
        lambda reader, start, end: LinkedFiles(files=(external,), consumed=0, total=0),
    )

    result = _plan_links_block(BytesReader(b""), block, **_plan_links_kwargs())

    # Skipped -> no payload was ever built -> falls back to a verbatim copy.
    assert result == ([Copy(0, 100)], 100, [])


def test_plan_links_block_skips_a_file_that_fails_to_parse(monkeypatch):
    block = _links_block()
    embedded = LinkedFile(
        index=0,
        kind="liFD",
        version=1,
        uid="",
        name="x.psb",
        file_type="8BPB",
        creator="",
        size=50,
        offset=10,
        record_size=62,
        has_descriptor=False,
        data_offset=30,
        size_offset=20,
    )
    monkeypatch.setattr(
        "optiff.optimize_psd.parse_links",
        lambda reader, start, end: LinkedFiles(files=(embedded,), consumed=0, total=0),
    )

    def _raise(*args, **kwargs):
        raise DocumentError("bad embedded file")

    monkeypatch.setattr("optiff.optimize_psd._plan_embedded", _raise)

    result = _plan_links_block(BytesReader(b""), block, **_plan_links_kwargs())

    assert result == ([Copy(0, 100)], 100, [])


def test_plan_links_block_skips_a_file_whose_size_did_not_change(monkeypatch):
    block = _links_block()
    embedded = LinkedFile(
        index=0,
        kind="liFD",
        version=1,
        uid="",
        name="x.psb",
        file_type="8BPB",
        creator="",
        size=50,
        offset=10,
        record_size=62,
        has_descriptor=False,
        data_offset=30,
        size_offset=20,
    )
    monkeypatch.setattr(
        "optiff.optimize_psd.parse_links",
        lambda reader, start, end: LinkedFiles(files=(embedded,), consumed=0, total=0),
    )
    monkeypatch.setattr(
        "optiff.optimize_psd._plan_embedded",
        lambda *args, **kwargs: ([], 50, []),  # new_size == item.size
    )

    result = _plan_links_block(BytesReader(b""), block, **_plan_links_kwargs())

    assert result == ([Copy(0, 100)], 100, [])


def test_plan_links_block_rebuilds_a_file_whose_size_shrank(monkeypatch):
    block = _links_block()
    # offset+8+record_size(62) == data_offset(30)+size(50) -> tail_size 0.
    embedded = LinkedFile(
        index=0,
        kind="liFD",
        version=1,
        uid="",
        name="x.psb",
        file_type="8BPB",
        creator="",
        size=50,
        offset=10,
        record_size=62,
        has_descriptor=False,
        data_offset=30,
        size_offset=20,
    )
    monkeypatch.setattr(
        "optiff.optimize_psd.parse_links",
        lambda reader, start, end: LinkedFiles(files=(embedded,), consumed=0, total=0),
    )
    monkeypatch.setattr(
        "optiff.optimize_psd._plan_embedded",
        # 41 B, chosen so record_size(12+41+0=53) is not a multiple of 4 ->
        # exercises the padding branch too.
        lambda *args, **kwargs: ([Copy(30, 41)], 41, []),
    )

    segments, size, results = _plan_links_block(
        BytesReader(b""), block, **_plan_links_kwargs()
    )

    assert results == []
    assert size > 0
    assert segments  # the block was actually rebuilt, not copied verbatim


# ============================================================================
# plan_container
# ============================================================================


def test_plan_container_copies_an_unrelated_block_verbatim():
    # A block whose key is neither a layer section nor a links block.
    block = PhotoshopBlock(
        signature="8BIM",
        key="XMP ",
        offset=26,
        size=10,
        padded_size=12,
        description="",
        payload_offset=34,
        raw_signature=b"",
        raw_key="",
        byte_order="<",
        header_size=12,
    )
    analysis = PhotoshopAnalysis(
        found=True,
        signature="8BIM",
        data_size=100,
        blocks=(block,),
        layer_count=None,
        warnings=(),
    )

    plan = plan_container(BytesReader(bytes(100)), analysis)

    assert Copy(block.offset, block.padded_size) in plan.segments
