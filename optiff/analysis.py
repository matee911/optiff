"""What `analyze()` collects about a TIFF, with no printing involved."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import tifffile

from optiff.document import TiffDocument
from optiff.domain import DataBlock, ImageInfo, PhotoshopAnalysis, PhysicalRange
from optiff.metadata import MetadataAnalyzer
from optiff.provenance import read_provenance
from optiff.psd_analyzer import TiffPhotoshopAnalyzer
from optiff.psd_file import DocumentError, EmbeddedDocument, parse_document
from optiff.psd_layers import LayerStack, read_layer_stack
from optiff.psd_links import LinkedFile, LinkedFiles, read_linked_files
from optiff.storage import PhysicalClassifier, PhysicalStorageAnalyzer


@dataclass(frozen=True)
class ProvenanceSection:
    """The three states `Reporter._print_provenance` distinguishes today."""

    state: Literal["no-blocks", "no-tag", "found"]
    report: dict[str, str] | None  # set only when state == "found"


@dataclass(frozen=True)
class LayersSection:
    """
    The two "nothing here" states plus the found one.

    A bare `LayerStack | None` cannot tell "no tag 37724 at all" apart from
    "the tag exists but has no Lr16/Lr32/Layr block" - the two messages
    `Reporter._print_layers` prints are different.
    """

    state: Literal["no-tag", "no-section", "found"]
    stack: LayerStack | None  # set only when state == "found"


@dataclass(frozen=True)
class TagInfo:
    code: int
    name: str
    dtype_name: str
    count: int
    size: int


@dataclass(frozen=True)
class GapClassification:
    gap: PhysicalRange
    classification: str


@dataclass(frozen=True)
class EmbeddedDocumentSection:
    """
    One linked smart object's embedded PSD/PSB, recursively.

    `EmbeddedDocument` already carries width/height/channels/depth/
    color_mode_name/compression_name/sections/layers/warnings as properties -
    wrap it instead of re-declaring those fields here.
    """

    name: str
    error: str | None  # DocumentError message, when parse_document failed
    document: EmbeddedDocument | None  # None exactly when error is set


@dataclass(frozen=True)
class LinkedFilesSection:
    linked: LinkedFiles
    embedded: dict[int, EmbeddedDocumentSection]  # keyed by LinkedFile.index


@dataclass(frozen=True)
class AnalyzeReport:
    path: Path
    file_size: int
    is_bigtiff: bool
    byte_order: Literal["little-endian", "big-endian"]
    image: ImageInfo
    size_tree: list[DataBlock]
    metadata: dict[str, str]
    provenance: ProvenanceSection
    photoshop: PhotoshopAnalysis
    layers: LayersSection
    linked_files: LinkedFilesSection | None
    physical_gaps: list[GapClassification]
    structure: list[TagInfo]


def _collect_provenance(
    document: TiffDocument, photoshop: PhotoshopAnalysis
) -> ProvenanceSection:
    if not photoshop.blocks:
        return ProvenanceSection("no-blocks", None)

    reader = document.photoshop_source_reader()

    if reader is None:
        return ProvenanceSection("no-tag", None)

    try:
        provenance = read_provenance(photoshop, reader)
    finally:
        reader.close()

    return ProvenanceSection("found", provenance.report())


def _collect_layers(
    document: TiffDocument, photoshop: PhotoshopAnalysis
) -> LayersSection:
    reader = document.photoshop_source_reader()

    if reader is None:
        return LayersSection("no-tag", None)

    try:
        stack = read_layer_stack(photoshop, reader)
    finally:
        reader.close()

    if stack is None:
        return LayersSection("no-section", None)

    return LayersSection("found", stack)


def _collect_embedded(reader, item: LinkedFile) -> EmbeddedDocumentSection:
    try:
        document = parse_document(reader, item.data_offset, item.size)
    except DocumentError as error:
        return EmbeddedDocumentSection(name=item.name, error=str(error), document=None)

    return EmbeddedDocumentSection(name=item.name, error=None, document=document)


def _collect_linked_files(
    document: TiffDocument, photoshop: PhotoshopAnalysis
) -> LinkedFilesSection | None:
    reader = document.photoshop_source_reader()

    if reader is None:
        return None

    try:
        linked = read_linked_files(photoshop, reader)
    finally:
        reader.close()

    if linked is None or not linked.files:
        return None

    embedded: dict[int, EmbeddedDocumentSection] = {}

    reader = document.photoshop_source_reader()

    try:
        for item in linked.files:
            if reader is not None and item.is_embedded:
                embedded[item.index] = _collect_embedded(reader, item)
    finally:
        if reader is not None:
            reader.close()

    return LinkedFilesSection(linked, embedded)


def _collect_physical_gaps(
    document: TiffDocument, storage: PhysicalStorageAnalyzer
) -> list[GapClassification]:
    classifier = PhysicalClassifier(document.path)

    ordered = sorted(
        storage.unaccounted_ranges(), key=lambda item: item.size, reverse=True
    )

    return [GapClassification(gap, classifier.classify(gap)) for gap in ordered]


def _collect_structure(document: TiffDocument) -> list[TagInfo]:
    return [
        TagInfo(
            code=tag.code,
            name=tag.name,
            dtype_name=tifffile.DATATYPE(tag.dtype).name,
            count=tag.count,
            size=document.tag_value_size(tag),
        )
        for tag in document.first_page.tags.values()
    ]


def analyze(path: Path) -> AnalyzeReport:
    with TiffDocument(path) as document:
        photoshop = TiffPhotoshopAnalyzer().analyze(document)
        storage = PhysicalStorageAnalyzer(document)

        return AnalyzeReport(
            path=document.path,
            file_size=document.file_size,
            is_bigtiff=document.tiff.is_bigtiff,
            byte_order=(
                "little-endian" if document.tiff.byteorder == "<" else "big-endian"
            ),
            image=document.image_info,
            size_tree=storage.referenced_blocks(),
            metadata=MetadataAnalyzer(document).report(),
            provenance=_collect_provenance(document, photoshop),
            photoshop=photoshop,
            layers=_collect_layers(document, photoshop),
            linked_files=_collect_linked_files(document, photoshop),
            physical_gaps=_collect_physical_gaps(document, storage),
            structure=_collect_structure(document),
        )
