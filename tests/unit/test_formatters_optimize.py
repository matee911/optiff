"""render_optimize() - pure text rendering of an OptimizeResult."""

from __future__ import annotations

from pathlib import Path

from optiff.formatters.optimize import _duration, render_optimize
from optiff.optimize import OptimizeResult
from optiff.verify import Comparison


def result(**overrides: object) -> OptimizeResult:
    base = OptimizeResult(
        source=Path("in.tif"),
        output=Path("out.tif"),
        size_before=1000,
        size_after=800,
        channels_total=2,
        channels_changed=2,
        tag_before=900,
        tag_after=700,
        channel_bytes_before=850,
        channel_bytes_after=650,
        seconds_plan=0.1,
        seconds_write=0.2,
        seconds_verify=0.3,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_duration_formatting():
    assert _duration(0.4) == "0.4 s"
    assert _duration(94.0) == "1 min 34 s"
    assert _duration(3725.0) == "1 h 02 min"


def test_skipped_result_renders_short_summary():
    # Arrange
    skipped = result(skipped="nothing to compress - everything is already packed")

    # Act
    text = render_optimize(skipped)

    # Assert
    assert "skipped - nothing to compress" in text
    assert "BEFORE" not in text  # the full table never renders


def test_verification_failure_renders_without_the_full_table():
    # Arrange - Comparison.ok is `not problems`, per optiff/verify.py
    failed = result(comparison=Comparison(total=1, problems=("channel 0 mismatch",)))

    # Act
    text = render_optimize(failed)

    # Assert
    assert "VERIFICATION FAILED" in text
    assert "BEFORE" not in text


def test_successful_result_renders_the_full_table():
    # Arrange
    ok = result(comparison=Comparison(total=1, problems=()))

    # Act
    text = render_optimize(ok)

    # Assert
    assert "BEFORE" in text
    assert "Saved:" in text
    assert text.endswith("=" * 80 + "\n")


def test_result_with_image_data_reports_the_extra_row():
    # Arrange - image_before defaults to 0 in every other test in this file,
    # so the `if result.image_before:` branch needs its own case
    ok = result(
        comparison=Comparison(total=1, problems=()),
        image_before=500,
        image_after=300,
    )

    # Act
    text = render_optimize(ok)

    # Assert
    assert "image pixels" in text
    assert "from layer channels" in text
    assert "from image pixels" in text
    assert "All of the saving comes from channel data." not in text


def test_result_without_verification_reports_skipped_verify():
    # Arrange - comparison stays at its dataclass default (None), reaching
    # the full success table without ever setting a Comparison (--no-verify)
    ok = result()

    # Act
    text = render_optimize(ok)

    # Assert
    assert "SKIPPED (--no-verify)" in text
    assert "pixel SHA256 unchanged" not in text
