"""The AnalyzeReport, rendered as text."""

from __future__ import annotations

import io

from optiff.analysis import AnalyzeReport
from optiff.domain import DataBlock, ImageInfo
from optiff.psd_layers import Layer, LayerStack
from optiff.units import format_size

WIDTH = 80


def render_size_tree(
    blocks: list[DataBlock],
    file_size: int,
    info: ImageInfo | None = None,
) -> list[str]:
    """
    The size tree as a list of lines.

    The last node at each level gets `└──`, the earlier ones `├──`. A node's
    children follow its own line rather than preceding it; an earlier version
    printed `└── TIFF tag` before further `├──` entries, which produced an
    inconsistent tree.

    >>> from optiff.domain import DataBlock, PhysicalRange
    >>> blocks = [
    ...     DataBlock("XMP", 700, (PhysicalRange(0, 60),)),
    ...     DataBlock("ICC Profile", 34675, (PhysicalRange(60, 100),)),
    ... ]
    >>> for line in render_size_tree(blocks, 100):
    ...     print(line)
          100.00 B  TIFF
    ├──     60.00 B  XMP                                     60.00%
    │   └── TIFF tag 700
    └──     40.00 B  ICC Profile                             40.00%
        └── TIFF tag 34675
    """
    lines = [f"{format_size(file_size):>12}  TIFF"]

    ordered = sorted(blocks, key=lambda block: block.size, reverse=True)

    for index, block in enumerate(ordered):
        is_last = index == len(ordered) - 1

        connector = "└──" if is_last else "├──"
        indent = "    " if is_last else "│   "

        percentage = block.size / file_size * 100 if file_size else 0.0

        lines.append(
            f"{connector} "
            f"{format_size(block.size):>12}  "
            f"{block.name:<38} "
            f"{percentage:>6.2f}%"
        )

        lines.extend(f"{indent}{child}" for child in _block_children(block, info))

    return lines


def _block_children(block: DataBlock, info: ImageInfo | None) -> list[str]:
    """The child lines of a node, already carrying their own glyphs."""
    details: list[str] = []

    if block.name == "IMAGE DATA" and info is not None:
        details.append(f"{info.width} × {info.height}")
        details.append(
            f"{info.samples} samples × {', '.join(map(str, info.bits_per_sample))}-bit"
        )
        details.append(f"Compression: {info.compression_name}")
        details.append(f"Predictor: {info.predictor or 'None'}")

        if block.is_fragmented:
            details.append(f"Strips: {len(block.ranges)}")

    if block.tag is not None:
        details.append(f"TIFF tag {block.tag}")

    return [
        f"{'└──' if index == len(details) - 1 else '├──'} {detail}"
        for index, detail in enumerate(details)
    ]


def _layer_line(layer: Layer) -> str:
    """
    One layer description line, indented by its nesting inside groups.

    >>> line = _layer_line(Layer(0, "Sky", 0, 0, 100, 200, (), "mul ", 128, 0, 0))
    >>> line.split()
    ['Sky', '0.00', 'B', '200x100', '-', 'Multiply', '50%']
    """
    name = "  " * layer.depth + (layer.name or "(unnamed)")

    bounds = "-" if layer.is_empty else f"{layer.width}x{layer.height}"

    marks = []

    if layer.is_hidden:
        marks.append("hidden")

    if layer.section != "layer":
        marks.append(layer.section)

    suffix = f"  [{', '.join(marks)}]" if marks else ""

    return (
        f"{name:<40} "
        f"{format_size(layer.data_size):>11}  "
        f"{bounds:>12}  "
        f"{layer.compression_short:<6} "
        f"{layer.blend_mode_name} {layer.opacity_percent}%"
        f"{suffix}"
    )


def _compression_summary(stack: LayerStack) -> str:
    """
    How many channel bytes fall to each compression method.

    >>> from optiff.psd_layers import LayerChannel, LayerStack
    >>> layer = Layer(
    ...     0, "x", 0, 0, 1, 1,
    ...     (LayerChannel(0, 1002, 0), LayerChannel(1, 502, 3)),
    ...     "norm", 255, 0, 0,
    ... )
    >>> _compression_summary(LayerStack((layer,), 1, False, 0, 0))
    'RAW 1000.00 B, ZIP with prediction 500.00 B'
    """
    totals: dict[str, int] = {}

    for layer in stack.layers:
        for channel in layer.channels:
            if channel.pixel_bytes == 0:
                continue

            name = channel.compression_name
            totals[name] = totals.get(name, 0) + channel.pixel_bytes

    if not totals:
        return "no channel data"

    return ", ".join(
        f"{name} {format_size(size)}"
        for name, size in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    )


def render_analyze(report: AnalyzeReport) -> str:
    buf = io.StringIO()

    def p(*args: object) -> None:
        print(*args, file=buf)

    p("=" * WIDTH)
    p("TIFF STORAGE ANALYZER")
    p("=" * WIDTH)

    p(f"File:          {report.path}")

    p(f"File size:     {format_size(report.file_size)} ({report.file_size:,} bytes)")

    p(f"Format:        {'BigTIFF' if report.is_bigtiff else 'Classic TIFF'}")
    p(f"Byte order:    {report.byte_order}")
    p(f"Image:         {report.image.width} × {report.image.height}")

    _render_size_tree(p, report)
    _render_metadata(p, report)
    _render_provenance(p, report)
    _render_photoshop(p, report)
    _render_layers(p, report)
    _render_linked_files(p, report)
    _render_physical_gaps(p, report)
    _render_structure(p, report)
    _render_compression(p, report)

    p("=" * WIDTH)
    p("DONE")
    p("=" * WIDTH)

    return buf.getvalue()


def _render_size_tree(p, report: AnalyzeReport) -> None:
    p("\n" + "=" * WIDTH)
    p("SIZE TREE")
    p("=" * WIDTH)
    p()

    lines = render_size_tree(report.size_tree, report.file_size, report.image)

    p("\n".join(lines))


def _render_metadata(p, report: AnalyzeReport) -> None:
    p("\n" + "=" * WIDTH)
    p("EMBEDDED METADATA / CONTENT")
    p("=" * WIDTH)
    p()

    for key, value in report.metadata.items():
        p(f"{key + ':':<32} {value}")


def _render_provenance(p, report: AnalyzeReport) -> None:
    p("\n" + "=" * WIDTH)
    p("PROVENANCE / HISTORY")
    p("=" * WIDTH)
    p()

    provenance = report.provenance

    if provenance.state == "no-blocks":
        p("No ImageSourceData blocks - nothing to read.")
        return

    if provenance.state == "no-tag":
        p("No tag 37724.")
        return

    assert provenance.report is not None
    for key, value in provenance.report.items():
        p(f"{key + ':':<32} {value}")


def _render_photoshop(p, report: AnalyzeReport) -> None:
    p("\n" + "=" * WIDTH)
    p("PHOTOSHOP IMAGESOURCEDATA")
    p("=" * WIDTH)
    p()

    photoshop = report.photoshop

    if not photoshop.found:
        p("Photoshop ImageSourceData: NOT DETECTED")
        return

    p(f"Signature:       {photoshop.signature}")
    p(f"Total size:      {format_size(photoshop.data_size)}")
    p(f"Parsed blocks:   {len(photoshop.blocks)}")

    if photoshop.layer_count is not None:
        p(f"Layer count:     {photoshop.layer_count}")

    p()
    p("BLOCKS")
    p("-" * WIDTH)

    if not photoshop.blocks:
        p("No blocks detected after the Photoshop header.")
        return

    for index, block in enumerate(photoshop.blocks, start=1):
        p(
            f"{index:>3}. "
            f"{block.key:<8} "
            f"{format_size(block.size):>12}  "
            f"offset=0x{block.offset:X}  "
            f"{block.description}"
        )


def _render_layers(p, report: AnalyzeReport) -> None:
    p("\n" + "=" * WIDTH)
    p("LAYERS")
    p("=" * WIDTH)
    p()

    layers = report.layers

    if layers.state == "no-tag":
        p("No tag 37724.")
        return

    if layers.state == "no-section":
        p("No layer section (Lr16 / Lr32 / Layr).")
        return

    stack = layers.stack
    assert stack is not None

    p(f"Layer count:     {abs(stack.declared_count)}")
    p(f"Transparency:    {'yes' if stack.has_transparency else 'no'}")
    p(f"Channel data:    {format_size(stack.channel_bytes)}")
    p(f"Compression:     {_compression_summary(stack)}")

    if not stack.is_complete:
        p(
            f"WARNING: records plus channels give {stack.consumed:,} B, "
            f"the section holds {stack.total:,} B"
        )

    for warning in stack.warnings:
        p(f"UWAGA: {warning.code} @0x{warning.offset:X} {warning.detail}")

    p()
    p(f"{'':>3}  {'NAME':<40} {'SIZE':>11}  {'BOUNDS':>12}  {'COMPR':<6} MODE")
    p("-" * WIDTH)

    for layer in stack.layers:
        p(f"{layer.index:>3}. {_layer_line(layer)}")


def _render_linked_files(p, report: AnalyzeReport) -> None:
    linked_files = report.linked_files

    if linked_files is None:
        return

    linked = linked_files.linked

    p("\n" + "=" * WIDTH)
    p("LINKED SMART OBJECTS")
    p("=" * WIDTH)
    p()

    p(f"Files:           {len(linked.files)}")
    p(f"Embedded data:   {format_size(linked.embedded_bytes)}")

    for warning in linked.warnings:
        p(f"UWAGA: {warning.code} @0x{warning.offset:X} {warning.detail}")

    p()

    for item in linked.files:
        p(
            f"{item.index + 1:>3}. "
            f"{item.name:<40} "
            f"{format_size(item.size):>11}  "
            f"{item.file_type_name} / {item.kind_name}"
        )
        p(f"     uid={item.uid}")

        if item.is_embedded and item.index in linked_files.embedded:
            _render_embedded(p, linked_files.embedded[item.index])

        p()


def _render_embedded(p, embedded) -> None:
    """Breaks an embedded PSD/PSB file down into sections and layers."""
    if embedded.error is not None:
        p(f"     not readable as PSD/PSB: {embedded.error}")
        return

    document = embedded.document
    assert document is not None

    p(
        f"     {document.format_name} "
        f"{document.width}x{document.height} "
        f"{document.channels}ch {document.depth}-bit "
        f"{document.color_mode_name}, "
        f"Image Data: {document.compression_name}"
    )

    for section in document.sections:
        share = section.total_size / document.total * 100 if document.total else 0.0

        p(
            f"       {section.name:<30} "
            f"{format_size(section.total_size):>11}  "
            f"{share:>6.2f}%"
        )

    for warning in document.warnings:
        p(f"       UWAGA: {warning.code} {warning.detail}")

    stack = document.layers

    if stack is None:
        return

    p(f"       layers: {len(stack.layers)}")
    p(f"       compression: {_compression_summary(stack)}")

    for warning in stack.warnings:
        p(f"       UWAGA: {warning.code} {warning.detail}")

    for layer in stack.layers:
        p(f"       {layer.index:>3}. {_layer_line(layer)}")


def _render_physical_gaps(p, report: AnalyzeReport) -> None:
    p("\n" + "=" * WIDTH)
    p("UNACCOUNTED PHYSICAL AREAS")
    p("=" * WIDTH)
    p()

    if not report.physical_gaps:
        p("None.")
        return

    for index, item in enumerate(report.physical_gaps, start=1):
        p(
            f"{index:>2}. "
            f"{format_size(item.gap.size):>12}  "
            f"offset=0x{item.gap.start:X} "
            f"end=0x{item.gap.end:X}"
        )
        p(f"    Type:       {item.classification}")


def _render_structure(p, report: AnalyzeReport) -> None:
    p("\n" + "=" * WIDTH)
    p("TIFF STRUCTURE / ENTRIES")
    p("=" * WIDTH)
    p()

    for tag in report.structure:
        p(
            f"{tag.code:7d}  "
            f"{tag.name:<35} "
            f"{tag.dtype_name:<10} "
            f"count={tag.count:<10} "
            f"size={format_size(tag.size):>10}"
        )


def _render_compression(p, report: AnalyzeReport) -> None:
    p("\n" + "=" * WIDTH)
    p("COMPRESSION / IMAGE DATA")
    p("=" * WIDTH)
    p()

    info = report.image

    p(f"Compression: {info.compression_name}")
    p(f"Predictor:   {info.predictor if info.predictor is not None else 'None'}")
    p(f"Bits/sample: {list(info.bits_per_sample)}")
