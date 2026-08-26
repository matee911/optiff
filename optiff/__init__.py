"""Analysis of the physical byte layout of TIFF files."""

from __future__ import annotations

from optiff.cli import __version__, analyze
from optiff.document import TiffDocument

__all__ = ["TiffDocument", "__version__", "analyze"]
