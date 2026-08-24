"""Analysis of the physical byte layout of TIFF files."""

from __future__ import annotations

from tiff_analyzer.cli import __version__, analyze
from tiff_analyzer.document import TiffDocument

__all__ = ["TiffDocument", "__version__", "analyze"]
