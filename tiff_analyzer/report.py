"""The analysis, rendered as text."""

from __future__ import annotations

import tifffile

from tiff_analyzer.document import TiffDocument
from tiff_analyzer.domain import DataBlock, ImageInfo, PhotoshopAnalysis
from tiff_analyzer.metadata import MetadataAnalyzer
from tiff_analyzer.provenance import read_provenance
from tiff_analyzer.psd_file import DocumentError, parse_document
from tiff_analyzer.psd_layers import Layer, read_layer_stack
from tiff_analyzer.psd_links import read_linked_files
from tiff_analyzer.storage import PhysicalClassifier, PhysicalStorageAnalyzer
from tiff_analyzer.units import format_size

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

    >>> from tiff_analyzer.domain import DataBlock, PhysicalRange
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


def _compression_summary(stack) -> str:
    """
    How many channel bytes fall to each compression method.

    >>> from tiff_analyzer.psd_layers import LayerChannel, LayerStack
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


class Reporter:
    def __init__(self, document: TiffDocument, photoshop: PhotoshopAnalysis):
        self.document = document
        self.photoshop = photoshop
        self.storage = PhysicalStorageAnalyzer(document)

    def print_report(self) -> None:
        info = self.document.image_info

        print("=" * WIDTH)
        print("TIFF STORAGE ANALYZER")
        print("=" * WIDTH)

        print(f"File:          {self.document.path}")

        print(
            f"File size:     "
            f"{format_size(self.document.file_size)} "
            f"({self.document.file_size:,} bytes)"
        )

        print(
            f"Format:        "
            f"{'BigTIFF' if self.document.tiff.is_bigtiff else 'Classic TIFF'}"
        )

        byte_order = (
            "little-endian" if self.document.tiff.byteorder == "<" else "big-endian"
        )
        print(f"Byte order:    {byte_order}")

        print(f"Image:         {info.width} × {info.height}")

        self._print_size_tree()
        self._print_metadata()
        self._print_provenance()
        self._print_photoshop()
        self._print_layers()
        self._print_linked_files()
        self._print_physical_gaps()
        self._print_structure()
        self._print_compression()

        print("=" * WIDTH)
        print("DONE")
        print("=" * WIDTH)

    def _print_size_tree(self) -> None:
        print("\n" + "=" * WIDTH)
        print("SIZE TREE")
        print("=" * WIDTH)
        print()

        lines = render_size_tree(
            self.storage.referenced_blocks(),
            self.document.file_size,
            self.document.image_info,
        )

        print("\n".join(lines))

    def _print_metadata(self) -> None:
        print("\n" + "=" * WIDTH)
        print("EMBEDDED METADATA / CONTENT")
        print("=" * WIDTH)
        print()

        for key, value in MetadataAnalyzer(self.document).report().items():
            print(f"{key + ':':<32} {value}")

    def _print_provenance(self) -> None:
        print("\n" + "=" * WIDTH)
        print("PROVENANCE / HISTORY")
        print("=" * WIDTH)
        print()

        if not self.photoshop.blocks:
            print("No ImageSourceData blocks - nothing to read.")
            return

        reader = self.document.photoshop_source_reader()

        if reader is None:
            print("No tag 37724.")
            return

        try:
            provenance = read_provenance(self.photoshop, reader)
        finally:
            reader.close()

        for key, value in provenance.report().items():
            print(f"{key + ':':<32} {value}")

    def _print_photoshop(self) -> None:
        print("\n" + "=" * WIDTH)
        print("PHOTOSHOP IMAGESOURCEDATA")
        print("=" * WIDTH)
        print()

        if not self.photoshop.found:
            print("Photoshop ImageSourceData: NOT DETECTED")
            return

        print(f"Signature:       {self.photoshop.signature}")

        print(f"Total size:      {format_size(self.photoshop.data_size)}")

        print(f"Parsed blocks:   {len(self.photoshop.blocks)}")

        if self.photoshop.layer_count is not None:
            print(f"Layer count:     {self.photoshop.layer_count}")

        print()
        print("BLOCKS")
        print("-" * WIDTH)

        if not self.photoshop.blocks:
            print("No blocks detected after the Photoshop header.")
            return

        for index, block in enumerate(self.photoshop.blocks, start=1):
            print(
                f"{index:>3}. "
                f"{block.key:<8} "
                f"{format_size(block.size):>12}  "
                f"offset=0x{block.offset:X}  "
                f"{block.description}"
            )

    def _print_layers(self) -> None:
        print("\n" + "=" * WIDTH)
        print("LAYERS")
        print("=" * WIDTH)
        print()

        reader = self.document.photoshop_source_reader()

        if reader is None:
            print("No tag 37724.")
            return

        try:
            stack = read_layer_stack(self.photoshop, reader)
        finally:
            reader.close()

        if stack is None:
            print("No layer section (Lr16 / Lr32 / Layr).")
            return

        print(f"Layer count:     {abs(stack.declared_count)}")

        print(f"Transparency:    {'yes' if stack.has_transparency else 'no'}")

        print(f"Channel data:    {format_size(stack.channel_bytes)}")

        print(f"Compression:     {_compression_summary(stack)}")

        if not stack.is_complete:
            print(
                f"WARNING: records plus channels give {stack.consumed:,} B, "
                f"the section holds {stack.total:,} B"
            )

        for warning in stack.warnings:
            print(f"UWAGA: {warning.code} @0x{warning.offset:X} {warning.detail}")

        print()
        print(f"{'':>3}  {'NAME':<40} {'SIZE':>11}  {'BOUNDS':>12}  {'COMPR':<6} MODE")
        print("-" * WIDTH)

        for layer in stack.layers:
            print(f"{layer.index:>3}. {_layer_line(layer)}")

    def _print_linked_files(self) -> None:
        reader = self.document.photoshop_source_reader()

        if reader is None:
            return

        try:
            linked = read_linked_files(self.photoshop, reader)
        finally:
            reader.close()

        if linked is None or not linked.files:
            return

        print("\n" + "=" * WIDTH)
        print("LINKED SMART OBJECTS")
        print("=" * WIDTH)
        print()

        print(f"Files:           {len(linked.files)}")
        print(f"Embedded data:   {format_size(linked.embedded_bytes)}")

        for warning in linked.warnings:
            print(f"UWAGA: {warning.code} @0x{warning.offset:X} {warning.detail}")

        print()

        reader = self.document.photoshop_source_reader()

        try:
            for item in linked.files:
                print(
                    f"{item.index + 1:>3}. "
                    f"{item.name:<40} "
                    f"{format_size(item.size):>11}  "
                    f"{item.file_type_name} / {item.kind_name}"
                )
                print(f"     uid={item.uid}")

                if reader is not None and item.is_embedded:
                    self._print_embedded(reader, item)

                print()
        finally:
            if reader is not None:
                reader.close()

    def _print_embedded(self, reader, item) -> None:
        """Breaks an embedded PSD/PSB file down into sections and layers."""
        try:
            document = parse_document(reader, item.data_offset, item.size)
        except DocumentError as error:
            print(f"     not readable as PSD/PSB: {error}")
            return

        print(
            f"     {document.format_name} "
            f"{document.width}x{document.height} "
            f"{document.channels}ch {document.depth}-bit "
            f"{document.color_mode_name}, "
            f"Image Data: {document.compression_name}"
        )

        for section in document.sections:
            share = section.total_size / item.size * 100 if item.size else 0.0

            print(
                f"       {section.name:<30} "
                f"{format_size(section.total_size):>11}  "
                f"{share:>6.2f}%"
            )

        for warning in document.warnings:
            print(f"       UWAGA: {warning.code} {warning.detail}")

        stack = document.layers

        if stack is None:
            return

        print(f"       layers: {len(stack.layers)}")
        print(f"       compression: {_compression_summary(stack)}")

        for warning in stack.warnings:
            print(f"       UWAGA: {warning.code} {warning.detail}")

        for layer in stack.layers:
            print(f"       {layer.index:>3}. {_layer_line(layer)}")

    def _print_physical_gaps(self) -> None:
        found_gaps = self.storage.unaccounted_ranges()

        print("\n" + "=" * WIDTH)
        print("UNACCOUNTED PHYSICAL AREAS")
        print("=" * WIDTH)
        print()

        if not found_gaps:
            print("None.")
            return

        classifier = PhysicalClassifier(self.document.path)

        ordered = sorted(found_gaps, key=lambda item: item.size, reverse=True)

        for index, gap in enumerate(ordered, start=1):
            classification = classifier.classify(gap)

            print(
                f"{index:>2}. "
                f"{format_size(gap.size):>12}  "
                f"offset=0x{gap.start:X} "
                f"end=0x{gap.end:X}"
            )

            print(f"    Type:       {classification}")

    def _print_structure(self) -> None:
        print("\n" + "=" * WIDTH)
        print("TIFF STRUCTURE / ENTRIES")
        print("=" * WIDTH)
        print()

        page = self.document.first_page

        for tag in page.tags.values():
            size = self.document.tag_value_size(tag)

            print(
                f"{tag.code:7d}  "
                f"{tag.name:<35} "
                f"{tifffile.DATATYPE(tag.dtype).name:<10} "
                f"count={tag.count:<10} "
                f"size={format_size(size):>10}"
            )

    def _print_compression(self) -> None:
        info = self.document.image_info

        print("\n" + "=" * WIDTH)
        print("COMPRESSION / IMAGE DATA")
        print("=" * WIDTH)
        print()

        print(f"Compression: {info.compression_name}")

        predictor = info.predictor if info.predictor is not None else "None"
        print(f"Predictor:   {predictor}")

        print(f"Bits/sample: {list(info.bits_per_sample)}")
