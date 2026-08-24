"""
Rebuilding the whole of ImageSourceData while recompressing channels.

A change in channel size has to propagate through seven nested length
fields::

    channel -> layer section -> Lr16 block -> Layer and Mask -> PSB file
          -> lnk2 record -> lnk2 block -> container 37724

Each level rewrites only its own length field; the remaining bytes are
copied from the source. The functions return `(segments, new_size)`, so
the parent size comes out of a sum rather than out of a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tiff_analyzer.domain import ByteOrder, PhotoshopAnalysis, PhotoshopBlock
from tiff_analyzer.optimize_layers import (
    DEFAULT_SOURCES,
    ChannelResult,
    LayerSectionPlan,
    plan_layer_section,
)
from tiff_analyzer.psd_analyzer import CONTAINER_HEADER_SIZE
from tiff_analyzer.psd_blocks import align4, walk
from tiff_analyzer.psd_codec import ZIP_PREDICTED
from tiff_analyzer.psd_file import DocumentError, parse_document
from tiff_analyzer.psd_layers import LAYER_SECTION_KEYS, parse_layers
from tiff_analyzer.psd_links import LINK_BLOCK_KEYS, parse_links
from tiff_analyzer.readers import ByteReader
from tiff_analyzer.segments import Copy, Literal, Segment, total_size


@dataclass
class ContainerPlan:
    """A rebuild plan for tag 37724 plus the material to verify it."""

    segments: list[Segment] = field(default_factory=list)
    results: list[ChannelResult] = field(default_factory=list)
    #: Container size after the rebuild.
    size: int = 0
    #: Container size in the source, for the "before / after" summary.
    source_size: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def before(self) -> int:
        return sum(item.before for item in self.results)

    @property
    def after(self) -> int:
        return sum(item.after for item in self.results)

    @property
    def changed(self) -> bool:
        return any(item.changed for item in self.results)


def _pad(size: int) -> Segment | None:
    """Padding a payload up to a multiple of 4 bytes."""
    padding = align4(size) - size

    return Literal(b"\x00" * padding) if padding else None


def _block(
    block: PhotoshopBlock,
    payload: list[Segment],
    payload_size: int,
) -> tuple[list[Segment], int]:
    """
    Assembles a block with a new payload: signature, key, length, data, padding.

    The length field width comes from the source block, so the variant
    (12 or 16 byte header) never changes.
    """
    length_size = block.header_size - 8
    order = "little" if block.byte_order == "<" else "big"

    segments: list[Segment] = [
        Copy(block.offset, 8),
        Literal(payload_size.to_bytes(length_size, order)),
        *payload,
    ]

    filler = _pad(payload_size)

    if filler:
        segments.append(filler)

    return segments, block.header_size + align4(payload_size)


def _plan_layers_block(  # noqa: PLR0913  - geometry, variant and tuning
    reader: ByteReader,
    block: PhotoshopBlock,
    *,
    bpp: int,
    byte_order: ByteOrder,
    large: bool,
    level: int,
    sources: tuple[int, ...],
    target: int,
    blind: bool = False,
) -> tuple[list[Segment], int, LayerSectionPlan]:
    """An Lr16/Lr32/Layr block with its layer section rebuilt."""
    start = block.payload_offset
    end = start + block.size

    stack = parse_layers(reader, start, end, large=large, byte_order=byte_order)

    plan = plan_layer_section(
        reader,
        stack,
        start,
        end,
        bpp=bpp,
        byte_order=byte_order,
        level=level,
        sources=sources,
        blind=blind,
        target=target,
    )

    payload_size = total_size(plan.segments)

    segments, size = _block(block, plan.segments, payload_size)

    return segments, size, plan


def _plan_embedded(  # noqa: PLR0913  - zakres, strojenie i wybor methods
    reader: ByteReader,
    start: int,
    size: int,
    *,
    level: int,
    sources: tuple[int, ...],
    target: int,
    blind: bool = False,
) -> tuple[list[Segment], int, list[ChannelResult]]:
    """
    An embedded PSD/PSB file with its Layer and Mask section rebuilt.

    A raw file is big-endian and is not byte-swapped.
    """
    document = parse_document(reader, start, size)

    section = document.section("Layer and Mask Information")

    if section is None or section.size == 0 or document.layers is None:
        return [Copy(start, size)], size, []

    bpp = document.depth // 8
    large = document.is_large
    length_size = 8 if large else 4

    # 16/32-bit layers live in the additional Lr16 block, not in Layer Info.
    inner_start = section.data_offset
    layer_info_size = int.from_bytes(reader.read_at(inner_start, length_size), "big")

    if layer_info_size > 0:
        return [Copy(start, size)], size, []

    blocks_start = (
        inner_start
        + length_size
        + 4
        + int.from_bytes(reader.read_at(inner_start + length_size, 4), "big")
    )

    blocks, _warnings = walk(reader, blocks_start, section.end, large_document=large)

    layer_block = next(
        (item for item in blocks if item.key in LAYER_SECTION_KEYS), None
    )

    if layer_block is None:
        return [Copy(start, size)], size, []

    rebuilt, _block_size, layer_plan = _plan_layers_block(
        reader,
        layer_block,
        bpp=bpp,
        byte_order=">",
        large=large,
        level=level,
        sources=sources,
        blind=blind,
        target=target,
    )

    if not layer_plan.changed:
        return [Copy(start, size)], size, layer_plan.results

    # Layer and Mask section: everything up to the layer block, the new
    # block, then the rest.
    tail_start = layer_block.offset + layer_block.padded_size

    inner: list[Segment] = [
        Copy(inner_start, layer_block.offset - inner_start),
        *rebuilt,
        Copy(tail_start, section.end - tail_start),
    ]

    inner_size = total_size(inner)

    segments: list[Segment] = [
        Copy(start, section.offset - start),
        Literal(inner_size.to_bytes(length_size, "big")),
        *inner,
        Copy(section.end, start + size - section.end),
    ]

    return segments, total_size(segments), layer_plan.results


def _plan_links_block(  # noqa: PLR0913  - zakres, strojenie i wybor methods
    reader: ByteReader,
    block: PhotoshopBlock,
    *,
    level: int,
    sources: tuple[int, ...],
    target: int,
    blind: bool = False,
) -> tuple[list[Segment], int, list[ChannelResult]]:
    """The lnk2 block with its embedded files rebuilt."""
    start = block.payload_offset
    end = start + block.size

    linked = parse_links(reader, start, end)

    if linked.warnings or not linked.files:
        return [Copy(block.offset, block.padded_size)], block.padded_size, []

    payload: list[Segment] = []
    results: list[ChannelResult] = []
    cursor = start

    for item in linked.files:
        if not item.is_embedded:
            continue

        try:
            rebuilt, new_size, item_results = _plan_embedded(
                reader,
                item.data_offset,
                item.size,
                level=level,
                sources=sources,
                blind=blind,
                target=target,
            )
        except DocumentError:
            continue

        results.extend(item_results)

        if new_size == item.size:
            continue

        head = item.data_offset - item.offset - 8
        record_size = head + new_size + item.tail_size

        payload.append(Copy(cursor, item.offset - cursor))
        # the new record length
        payload.append(Literal(record_size.to_bytes(8, "little")))
        # the record header up to the file size field
        payload.append(Copy(item.offset + 8, item.size_offset - (item.offset + 8)))
        payload.append(Literal(new_size.to_bytes(8, "little")))
        # the flag and the opening descriptor stay as they are
        payload.append(
            Copy(item.size_offset + 8, item.data_offset - (item.size_offset + 8))
        )
        payload.extend(rebuilt)

        # Fields behind the file data: identifier, modification time, lock.
        if item.tail_size:
            payload.append(Copy(item.data_end, item.tail_size))

        filler = _pad(record_size)

        if filler:
            payload.append(filler)

        cursor = item.offset + 8 + align4(item.record_size)

    if not payload:
        return [Copy(block.offset, block.padded_size)], block.padded_size, results

    payload.append(Copy(cursor, end - cursor))

    segments, size = _block(block, payload, total_size(payload))

    return segments, size, results


#: Sample depth implied by the layer section key.
SECTION_DEPTH: dict[str, int] = {"Lr16": 2, "Lr32": 4, "Layr": 1}


def plan_container(  # noqa: PLR0913  - strojenie i wybor methods
    reader: ByteReader,
    analysis: PhotoshopAnalysis,
    *,
    level: int = 6,
    sources: tuple[int, ...] = DEFAULT_SOURCES,
    target: int = ZIP_PREDICTED,
    blind: bool = False,
) -> ContainerPlan:
    """Builds a rebuild plan for the entire content of tag 37724."""
    plan = ContainerPlan()

    segments: list[Segment] = [Copy(0, CONTAINER_HEADER_SIZE)]

    for block in analysis.blocks:
        if block.key in LAYER_SECTION_KEYS:
            rebuilt, _size, layer_plan = _plan_layers_block(
                reader,
                block,
                bpp=SECTION_DEPTH.get(block.key, 2),
                byte_order=block.byte_order,
                large=block.header_size == 16,
                level=level,
                sources=sources,
                blind=blind,
                target=target,
            )
            plan.results.extend(layer_plan.results)

            segments.extend(rebuilt)
            continue

        if block.key in LINK_BLOCK_KEYS and block.size:
            rebuilt, _size, results = _plan_links_block(
                reader,
                block,
                level=level,
                sources=sources,
                target=target,
                blind=blind,
            )
            plan.results.extend(results)
            segments.extend(rebuilt)
            continue

        segments.append(Copy(block.offset, block.padded_size))

    plan.segments = segments
    plan.size = total_size(segments)
    plan.source_size = analysis.data_size

    return plan
