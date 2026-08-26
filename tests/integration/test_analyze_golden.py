"""
Permanent regression test: render_analyze(analyze(path)) against frozen
golden output, captured while Reporter still existed (see git history for
tests/integration/test_analyze_parity.py, which proved the two matched).
"""

from __future__ import annotations

from pathlib import Path

from optiff.analysis import analyze
from optiff.formatters.analyze import render_analyze

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden"


def _normalized(path: Path) -> str:
    return render_analyze(analyze(path)).replace(
        f"File:          {path}", "File:          <PATH>"
    )


def test_matches_golden_output_for_plain_tiff(synthetic_tiff: Path):
    expected = (GOLDEN_DIR / "analyze_synthetic_tiff.txt").read_text(encoding="utf-8")

    assert _normalized(synthetic_tiff) == expected


def test_matches_golden_output_for_psd_tiff(synthetic_psd_tiff: Path):
    expected = (GOLDEN_DIR / "analyze_synthetic_psd_tiff.txt").read_text(
        encoding="utf-8"
    )

    assert _normalized(synthetic_psd_tiff) == expected


def test_matches_golden_output_for_psd_tiff_with_linked_file(
    synthetic_psd_tiff_with_linked_file: Path,
):
    expected = (
        GOLDEN_DIR / "analyze_synthetic_psd_tiff_with_linked_file.txt"
    ).read_text(encoding="utf-8")

    assert _normalized(synthetic_psd_tiff_with_linked_file) == expected
