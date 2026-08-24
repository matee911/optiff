"""Analysis of the Photoshop ImageSourceData block (TIFF tag 37724)."""

from __future__ import annotations

from collections import Counter, defaultdict

from tiff_analyzer.document import TiffDocument
from tiff_analyzer.domain import (
    ParseWarning,
    PhotoshopAnalysis,
    PhotoshopBlock,
)
from tiff_analyzer.psd_blocks import walk
from tiff_analyzer.readers import ByteReader, BytesReader
from tiff_analyzer.units import calculate_entropy

#: Container signatures Photoshop puts in front of the block stream.
#: Both are 35 bytes long.
CONTAINER_SIGNATURE = b"Adobe Photoshop Document Data Block"

#: Newer Photoshop versions write the "V0002" variant, in which selected
#: keys carry an 8-byte length (the PSB rule; see LARGE_LENGTH_KEYS).
CONTAINER_V0002 = b"Adobe Photoshop Document Data V0002"

CONTAINER_SIGNATURES = (CONTAINER_SIGNATURE, CONTAINER_V0002)

SIGNATURE_SIZE = len(CONTAINER_SIGNATURE)

#: The signature is NUL-terminated.
CONTAINER_HEADER_SIZE = SIGNATURE_SIZE + 1

#: How many payload bytes are enough to judge entropy.
STATS_SAMPLE_LIMIT = 1_000_000


def _empty(data_size: int, *, found: bool, warnings=()) -> PhotoshopAnalysis:
    return PhotoshopAnalysis(
        found=found,
        signature=None,
        data_size=data_size,
        blocks=(),
        layer_count=None,
        warnings=tuple(warnings),
    )


class ImageSourceDataAnalyzer:
    """Breaks the content of tag 37724 down into blocks."""

    SIGNATURES = CONTAINER_SIGNATURES

    def analyze(self, data: bytes) -> PhotoshopAnalysis:
        """
        Convenience entry point for `bytes`.

        >>> analyzer = ImageSourceDataAnalyzer()
        >>> analyzer.analyze(b"").found
        False
        >>> analyzer.analyze(b"cokolwiek").warnings[0].code
        'unknown-container'
        """
        return self.analyze_reader(BytesReader(data))

    def analyze_reader(self, reader: ByteReader) -> PhotoshopAnalysis:
        """Streaming entry point; never materialises payloads in memory."""
        data_size = reader.size

        if data_size == 0:
            return _empty(0, found=False)

        header = reader.read_at(0, SIGNATURE_SIZE)

        if header not in self.SIGNATURES:
            return _empty(
                data_size,
                found=True,
                warnings=(
                    ParseWarning(
                        "unknown-container",
                        0,
                        f"unknown container signature {header[:40]!r}",
                    ),
                ),
            )

        terminator = reader.read_at(SIGNATURE_SIZE, 1)

        if terminator != b"\x00":
            return _empty(
                data_size,
                found=True,
                warnings=(
                    ParseWarning(
                        "unterminated-signature",
                        SIGNATURE_SIZE,
                        f"expected NUL, found {terminator!r}",
                    ),
                ),
            )

        blocks, warnings = walk(
            reader,
            CONTAINER_HEADER_SIZE,
            data_size,
            large_document=header == CONTAINER_V0002,
        )

        return PhotoshopAnalysis(
            found=True,
            signature=header.decode("ascii"),
            data_size=data_size,
            blocks=blocks,
            layer_count=None,
            warnings=warnings,
        )

    def summary(
        self,
        blocks: tuple[PhotoshopBlock, ...],
    ) -> tuple[tuple[str, int, int], ...]:
        """
        Block statistics grouped by logical key.

        Each entry is `(key, count, total payload size)`.

        >>> from tiff_analyzer.readers import BytesReader
        >>> from tiff_analyzer.psd_blocks import walk
        >>> def block(key, size):
        ...     return (
        ...         b"MIB8" + key[::-1].encode()
        ...         + size.to_bytes(4, "little") + b"\\x00" * size
        ...     )
        >>> data = block("Lr16", 4) + block("Lr16", 8) + block("cinf", 4)
        >>> blocks, _ = walk(BytesReader(data), 0, len(data))
        >>> ImageSourceDataAnalyzer().summary(blocks)
        (('Lr16', 2, 12), ('cinf', 1, 4))
        """
        grouped: dict[str, list[PhotoshopBlock]] = defaultdict(list)

        for block in blocks:
            grouped[block.key].append(block)

        return tuple(
            (key, len(items), sum(item.size for item in items))
            for key, items in sorted(grouped.items())
        )

    def payload(
        self,
        reader: ByteReader,
        block: PhotoshopBlock,
        *,
        limit: int | None = None,
    ) -> bytes:
        """
        The block payload: a plain slice, with no length re-parsing.

        `limit` guards against gigabyte allocations: the `lnk2` block in
        produkcyjnych ma ponad 2 GB.

        >>> from tiff_analyzer.readers import BytesReader
        >>> from tiff_analyzer.psd_blocks import walk
        >>> data = (
        ...     b"MIB8" + b"61rL" + (5).to_bytes(4, "little")
        ...     + b"hello" + b"\\x00" * 3
        ... )
        >>> reader = BytesReader(data)
        >>> blocks, _ = walk(reader, 0, len(data))
        >>> ImageSourceDataAnalyzer().payload(reader, blocks[0])
        b'hello'
        >>> ImageSourceDataAnalyzer().payload(reader, blocks[0], limit=2)
        b'he'
        """
        length = block.size if limit is None else min(block.size, limit)

        if length <= 0:
            return b""

        return reader.read_at(block.payload_offset, length)

    def payload_stats(self, payload: bytes) -> dict[str, float | int]:
        """
        Podstawowe statystyki binarne payloadu.

        >>> analyzer = ImageSourceDataAnalyzer()
        >>> stats = analyzer.payload_stats(b"AAAA")
        >>> stats["size"], stats["unique_bytes"], stats["zero_ratio"]
        (4, 1, 0.0)
        >>> stats["ascii_ratio"], stats["entropy"]
        (1.0, 0.0)
        >>> analyzer.payload_stats(b"")["size"]
        0
        >>> round(analyzer.payload_stats(bytes(range(256)))["entropy"], 3)
        8.0
        """
        size = len(payload)

        if not payload:
            return {
                "size": 0,
                "unique_bytes": 0,
                "zero_ratio": 0.0,
                "ascii_ratio": 0.0,
                "entropy": 0.0,
            }

        return {
            "size": size,
            "unique_bytes": len(Counter(payload)),
            "zero_ratio": payload.count(0) / size,
            "ascii_ratio": sum(32 <= byte < 127 for byte in payload) / size,
            "entropy": calculate_entropy(payload),
        }


class TiffPhotoshopAnalyzer:
    """Joins `TiffDocument` to the block analyser, reading through `seek`."""

    def __init__(self, analyzer: ImageSourceDataAnalyzer | None = None):
        self.analyzer = analyzer or ImageSourceDataAnalyzer()

    def analyze(self, document: TiffDocument) -> PhotoshopAnalysis:
        reader = document.photoshop_source_reader()

        if reader is None:
            return _empty(0, found=False)

        try:
            return self.analyzer.analyze_reader(reader)
        finally:
            reader.close()
