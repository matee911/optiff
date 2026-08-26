"""The OptimizeResult, rendered as text."""

from __future__ import annotations

import io

from optiff.optimize import OptimizeResult
from optiff.units import format_size

WIDTH = 80


def _duration(seconds: float) -> str:
    """
    A duration in human readable form.

    >>> _duration(9.4)
    '9.4 s'
    >>> _duration(94.0)
    '1 min 34 s'
    >>> _duration(3725.0)
    '1 h 02 min'
    """
    if seconds < 60:
        return f"{seconds:.1f} s"

    if seconds < 3600:
        return f"{int(seconds) // 60} min {int(seconds) % 60:02d} s"

    return f"{int(seconds) // 3600} h {int(seconds) % 3600 // 60:02d} min"


def render_optimize(result: OptimizeResult) -> str:
    """The optimization summary as text. No I/O, no exit-code decisions."""
    buf = io.StringIO()

    def p(*args: object) -> None:
        print(*args, file=buf)

    p("=" * WIDTH)
    p("OPTIMIZATION")
    p("=" * WIDTH)
    p()

    p(f"{'File:':<18} {result.source}")

    for note in result.notes:
        p(f"{'Note:':<18} {note}")

    if result.skipped:
        p(f"{'Result:':<18} skipped - {result.skipped}")
        p()
        p("=" * WIDTH)
        return buf.getvalue()

    if result.comparison is not None and not result.comparison.ok:
        p(f"{'Result:':<18} VERIFICATION FAILED - output deleted")
        p()
        p("=" * WIDTH)
        return buf.getvalue()

    rows = (
        ("channel data", result.channel_bytes_before, result.channel_bytes_after),
        ("tag 37724", result.tag_before, result.tag_after),
        *(
            (("image pixels", result.image_before, result.image_after),)
            if result.image_before
            else ()
        ),
        ("FILE", result.size_before, result.size_after),
    )

    p()
    p(f"{'':<16} {'BEFORE':>17} {'AFTER':>17} {'SAVED':>17}  SHARE")
    p("-" * WIDTH)

    for label, before, after in rows:
        share = f"{after / before * 100:5.1f}%" if before else "     -"

        p(f"{label:<16} {before:>17,} {after:>17,} {before - after:>17,} {share}")

    p("-" * WIDTH)
    p(
        f"Saved:           {format_size(result.saved)} "
        f"({(1 - result.ratio) * 100:.1f}% of the file)"
    )
    p()
    if result.image_before:
        p(
            f"Saved {format_size(result.channel_saved)} from layer channels, "
            f"{format_size(result.image_saved)} from image pixels."
        )
    else:
        p("All of the saving comes from channel data.")

    p("Differences between levels:")
    p(f"  {result.padding_saved:+,} B  block padding to 4 bytes")
    p(f"  {result.tail_saved:+,} B  padding dropped at the end of the file")
    p()

    p(
        f"{'Channels:':<18} {result.channels_changed} compressed "
        f"of {result.channels_total}"
    )

    if result.comparison is not None:
        p(
            f"{'Verified:':<18} {result.comparison.total} channels, "
            f"pixel SHA256 unchanged"
        )
    else:
        p(f"{'Verified:':<18} SKIPPED (--no-verify)")

    p(f"{'Written:':<18} {result.output}")
    p(f"{'Original:':<18} untouched")
    p()

    p(
        f"{'Time:':<18} {_duration(result.seconds_total)} "
        f"({format_size(int(result.throughput))}/s)"
    )
    p(
        f"{'':<18} compress {_duration(result.seconds_plan)}, "
        f"write {_duration(result.seconds_write)}, "
        f"verify {_duration(result.seconds_verify)}"
    )
    p()
    p("=" * WIDTH)

    return buf.getvalue()
