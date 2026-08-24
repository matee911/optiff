"""Domain types, with no dependency on tifffile or on I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# ============================================================================
# ZAKRESY FIZYCZNE
# ============================================================================


@dataclass(frozen=True)
class PhysicalRange:
    start: int
    end: int

    @property
    def size(self) -> int:
        return max(0, self.end - self.start)

    @property
    def is_empty(self) -> bool:
        return self.size == 0


@dataclass(frozen=True)
class DataBlock:
    """
    A named region of the file, possibly non-contiguous (image data in strips).

    `size` is the sum of the parts, not the span. Foreign bytes sitting
    between fragments are therefore never counted towards the block.
    """

    name: str
    tag: int | None
    ranges: tuple[PhysicalRange, ...]

    @property
    def size(self) -> int:
        """
        >>> block = DataBlock(
        ...     "IMAGE DATA", None,
        ...     (PhysicalRange(0, 100), PhysicalRange(200, 300)),
        ... )
        >>> block.size
        200
        >>> block.span
        PhysicalRange(start=0, end=300)
        """
        return sum(item.size for item in self.ranges)

    @property
    def span(self) -> PhysicalRange:
        """Span from the first to the last byte of the block."""
        if not self.ranges:
            return PhysicalRange(0, 0)

        return PhysicalRange(
            min(item.start for item in self.ranges),
            max(item.end for item in self.ranges),
        )

    @property
    def is_fragmented(self) -> bool:
        return len(self.ranges) > 1


@dataclass(frozen=True)
class ImageInfo:
    width: int
    height: int
    samples: int
    bits_per_sample: tuple[int, ...]
    compression: int
    compression_name: str
    predictor: int | None


# ============================================================================
# PHOTOSHOP IMAGESOURCEDATA
# ============================================================================


@dataclass(frozen=True)
class ParseWarning:
    """Strukturalny problem napotkany przy chodzeniu po blokach."""

    code: str
    offset: int
    detail: str = ""


@dataclass(frozen=True)
class PhotoshopBlock:
    """
    Pojedynczy blok ImageSourceData.

    Separates the on-disk form from the logical one: in a little-endian
    TIFF, Photoshop writes the signature and the key byte-reversed
    (`MIB8` / `61rL`), while logically they mean `8BIM` / `Lr16`.

    `offset` is relative to the start of the window handed to the parser.
    """

    signature: str
    key: str
    offset: int
    size: int
    padded_size: int
    description: str
    payload_offset: int
    raw_signature: bytes
    raw_key: str
    byte_order: str
    header_size: int

    @property
    def end(self) -> int:
        """
        Offset pierwszego bajtu za blokiem (razem z paddingiem).

        >>> block = PhotoshopBlock(
        ...     "8BIM", "Lr16", 36, 7, 20, "Layers (16-bit)",
        ...     48, b"MIB8", "61rL", "<", 12,
        ... )
        >>> block.end
        56
        """
        return self.offset + self.padded_size


@dataclass(frozen=True)
class PhotoshopAnalysis:
    found: bool
    signature: str | None
    data_size: int
    blocks: tuple[PhotoshopBlock, ...]
    layer_count: int | None
    warnings: tuple[ParseWarning, ...]


class PhotoshopAnalyzer(Protocol):
    def analyze(self, data: bytes) -> PhotoshopAnalysis: ...
