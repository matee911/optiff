"""Analysis of the physical byte layout of TIFF files."""

from __future__ import annotations

from optiff.analysis import analyze
from optiff.cli import __version__
from optiff.document import TiffDocument

__all__ = ["TiffDocument", "__version__", "analyze"]
