"""
Where a file came from: Content Credentials, generative AI, Photoshop version.

The data lives in ImageSourceData blocks written as descriptors:

- ``CAI `` - Content Authenticity Initiative (C2PA)
- ``GenI`` - whether generative tools were used
- ``cinf`` - compositor info, including the Photoshop version that wrote the file
"""

from __future__ import annotations

from dataclasses import dataclass

from optiff.domain import PhotoshopAnalysis
from optiff.psd_analyzer import ImageSourceDataAnalyzer
from optiff.psd_descriptor import Descriptor, parse_block
from optiff.readers import ByteReader

#: A signed C2PA manifest is at least a few kB (certificate chain).
#: A `CAI ` block of a few dozen bytes is the marker alone, not a manifest.
MANIFEST_MIN_SIZE = 2048


@dataclass(frozen=True)
class ContentCredentials:
    """Content Credentials state, read from the ``CAI `` block."""

    present: bool
    enabled: bool | None = None
    generational_guid: str = ""
    block_size: int = 0

    @property
    def has_manifest(self) -> bool:
        """
        Whether the block is large enough to hold a signed manifest.

        >>> ContentCredentials(present=True, block_size=77).has_manifest
        False
        >>> ContentCredentials(present=True, block_size=9000).has_manifest
        True
        """
        return self.block_size >= MANIFEST_MIN_SIZE

    def summary(self) -> str:
        """
        >>> ContentCredentials(present=False).summary()
        'NOT FOUND'
        >>> ContentCredentials(present=True, enabled=False, block_size=77).summary()
        'marker only (disabled, 77 B - too small for a signed manifest)'
        """
        if not self.present:
            return "NOT FOUND"

        if self.has_manifest:
            return f"FOUND (signed manifest, {self.block_size:,} B)"

        state = "disabled" if self.enabled is False else "enabled"

        detail = f"{state}, {self.block_size:,} B - too small for a signed manifest"

        if self.generational_guid:
            detail = f"{state}, guid={self.generational_guid}"

        return f"marker only ({detail})"


@dataclass(frozen=True)
class GenerativeInfo:
    """State of the ``GenI`` block: whether generative tools were used."""

    present: bool
    used: bool | None = None
    models: tuple[str, ...] = ()

    def summary(self) -> str:
        """
        >>> GenerativeInfo(present=False).summary()
        'NOT FOUND'
        >>> GenerativeInfo(present=True, used=False).summary()
        'NO (isUsingGenTech=0)'
        >>> GenerativeInfo(present=True, used=True, models=("Firefly",)).summary()
        'YES (Firefly)'
        """
        if not self.present:
            return "NOT FOUND"

        if self.used is None:
            return "UNKNOWN"

        if not self.used:
            return "NO (isUsingGenTech=0)"

        if self.models:
            return f"YES ({', '.join(self.models)})"

        return "YES (model not named)"


@dataclass(frozen=True)
class Provenance:
    content_credentials: ContentCredentials
    generative: GenerativeInfo
    photoshop_version: str | None
    compositor_version: str | None

    def report(self) -> dict[str, str]:
        return {
            "Content Credentials (CAI)": self.content_credentials.summary(),
            "Generative AI (GenI)": self.generative.summary(),
            "Written by Photoshop": self.photoshop_version or "UNKNOWN",
            "Compositor version": self.compositor_version or "UNKNOWN",
        }


def _version(descriptor: Descriptor | None) -> str | None:
    """
    Assembles `major.minor.fix` from the nested version descriptor.

    >>> _version(Descriptor("", "null", {"major": 26, "minor": 8, "fix": 1}))
    '26.8.1'
    >>> _version(None) is None
    True
    """
    if descriptor is None:
        return None

    parts = [descriptor.get(name) for name in ("major", "minor", "fix")]

    if any(part is None for part in parts):
        return None

    return ".".join(str(part) for part in parts)


def _models(value: object) -> tuple[str, ...]:
    """Model names from `externalModelList`, when they can be read."""
    if not isinstance(value, list):
        return ()

    names: list[str] = []

    for item in value:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, Descriptor):
            for key in ("name", "modelName", "title"):
                if isinstance(item.get(key), str) and item.get(key):
                    names.append(item[key])
                    break

    return tuple(names)


def read_provenance(
    analysis: PhotoshopAnalysis,
    reader: ByteReader,
    analyzer: ImageSourceDataAnalyzer | None = None,
) -> Provenance:
    """Reads the provenance blocks from an already parsed analysis."""
    analyzer = analyzer or ImageSourceDataAnalyzer()

    parsed: dict[str, Descriptor] = {}
    sizes: dict[str, int] = {}

    for block in analysis.blocks:
        sizes[block.key] = block.size

        result = parse_block(block.key, analyzer.payload(reader, block))

        if result is not None:
            parsed[block.key] = result.descriptor

    cai = parsed.get("CAI ")
    gen = parsed.get("GenI")
    cinf = parsed.get("cinf")

    credentials = ContentCredentials(
        present="CAI " in sizes,
        enabled=cai.get("enab") if cai else None,
        generational_guid=(cai.get("generationalGuid", "") if cai else ""),
        block_size=sizes.get("CAI ", 0),
    )

    used = gen.get("isUsingGenTech") if gen else None

    generative = GenerativeInfo(
        present="GenI" in sizes,
        used=None if used is None else bool(used),
        models=_models(gen.get("externalModelList")) if gen else (),
    )

    return Provenance(
        content_credentials=credentials,
        generative=generative,
        photoshop_version=_version(cinf.get("psVersion") if cinf else None),
        compositor_version=_version(cinf.get("Vrsn") if cinf else None),
    )
