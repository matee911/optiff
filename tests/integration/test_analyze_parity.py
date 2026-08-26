"""
Byte-for-byte parity between the old Reporter and the new render_analyze().

Temporary: deleted once report.py/Reporter are removed. Its job is to prove
the refactor changed no output before the old code path is retired, and to
generate the golden fixtures that replace it as the permanent regression test.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

from optiff.analysis import analyze
from optiff.document import TiffDocument
from optiff.formatters.analyze import render_analyze
from optiff.psd_analyzer import TiffPhotoshopAnalyzer
from optiff.report import Reporter

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden"


def _reporter_output(path: Path) -> str:
    buf = io.StringIO()

    with TiffDocument(path) as document:
        photoshop = TiffPhotoshopAnalyzer().analyze(document)

        with contextlib.redirect_stdout(buf):
            Reporter(document, photoshop).print_report()

    return buf.getvalue()


def test_synthetic_tiff_output_is_byte_identical(synthetic_tiff: Path):
    # Act
    old = _reporter_output(synthetic_tiff)
    new = render_analyze(analyze(synthetic_tiff))

    # Assert
    assert new == old


def test_synthetic_psd_tiff_output_is_byte_identical(synthetic_psd_tiff: Path):
    # Act
    old = _reporter_output(synthetic_psd_tiff)
    new = render_analyze(analyze(synthetic_psd_tiff))

    # Assert
    assert new == old


def test_linked_file_output_is_byte_identical(
    synthetic_psd_tiff_with_linked_file: Path,
):
    # Arrange - the fixture from Step 4a; this is the only test in the plan
    # that exercises LINKED SMART OBJECTS / _render_embedded at all
    path = synthetic_psd_tiff_with_linked_file

    # Act
    old = _reporter_output(path)
    new = render_analyze(analyze(path))

    # Assert
    assert new == old
    assert "LINKED SMART OBJECTS" in old  # confirms the section actually ran
