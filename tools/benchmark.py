"""
Sweeps deflate levels 1-9 across every content profile and charts the trade-off.

Why: the samples come from `tests/sample_files.py`, so this benchmark ships
with the repository instead of depending on files nobody else has. The
question a reader has is "what does the extra time buy me?" - a trade-off,
not a time series - so the chart is time on x, size on y, one point per
level, one line per content profile. Never dual-axis: two scales on one plot
invite exactly the misreading this is meant to prevent.

Repeats separate a real level 3/4 inversion from noise, so a median of
several runs is reported rather than a single timing. CI does not run this:
timings from a shared runner measure the runner, not the code - the chart is
committed, the raw timings are not.

Usage:
    python -m tools.benchmark --out docs/levels.svg
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

from tests.sample_files import CONTENT_PROFILES

#: 8192 x 8192 x 2 bytes = 128 MB per channel raw - a real multi-layer,
#: multi-channel file totalling ~2 GB (AFFINITY_NOTES.md's scale) is many
#: channels this size, not one channel that size. Large enough to reproduce
#: the level 3/4 size inversion and the level 4-5 time jump seen there;
#: neither shows reliably at a few hundred kilobytes.
DEFAULT_WIDTH, DEFAULT_ROWS = 8192, 8192

LEVELS = range(1, 10)

#: The seed every profile is generated with - one content sample per profile,
#: reused across every level so only the level varies.
SEED = 1

#: Colors, in the order CONTENT_PROFILES iterates (dict insertion order).
_PALETTE = {
    "light": {
        "smooth": "#2563eb",
        "grain": "#dc2626",
        "flat": "#16a34a",
        "detail": "#d97706",
        "banded": "#7c3aed",
        "text": "#1f2937",
        "grid": "#d1d5db",
        "background": "#ffffff",
    },
    "dark": {
        "smooth": "#60a5fa",
        "grain": "#f87171",
        "flat": "#4ade80",
        "detail": "#fbbf24",
        "banded": "#a78bfa",
        "text": "#e5e7eb",
        "grid": "#374151",
        "background": "#111827",
    },
}


@dataclass(frozen=True)
class LevelResult:
    """One (profile, level)'s measured trade-off."""

    level: int
    size: int
    seconds: float


def _median_compress(data: bytes, level: int, repeats: int) -> LevelResult:
    """Compresses `data` at `level` `repeats` times; reports the median time."""
    timings = []
    size = None

    for _ in range(repeats):
        start = time.perf_counter()
        compressed = zlib.compress(data, level)
        timings.append(time.perf_counter() - start)
        size = len(compressed)

    assert size is not None

    return LevelResult(level=level, size=size, seconds=statistics.median(timings))


def sweep(*, width: int, rows: int, repeats: int) -> dict[str, list[LevelResult]]:
    """Every content profile, swept across every deflate level."""
    results: dict[str, list[LevelResult]] = {}

    for name, profile in CONTENT_PROFILES.items():
        data = profile(SEED, width=width, rows=rows)
        results[name] = [_median_compress(data, level, repeats) for level in LEVELS]

    return results


def grain_inversion(results: list[LevelResult]) -> str:
    """
    Whether `grain`'s levels 3/4 reproduce the previously-observed inversion:
    level 4 sometimes faster AND smaller than level 3 on compressible data,
    but larger than level 3 on incompressible data - which `grain` is.
    """
    by_level = {r.level: r for r in results}
    three, four = by_level[3], by_level[4]
    verdict = "reproduced" if four.size > three.size else "not reproduced"
    comparison = "is larger than" if four.size > three.size else "is not larger than"

    return (
        f"{verdict}: level 4 ({four.size:,} B) {comparison} "
        f"level 3 ({three.size:,} B) on grain"
    )


def _format_table(results: dict[str, list[LevelResult]]) -> str:
    lines = []

    for name, levels in results.items():
        lines.append(f"\n{name}")
        lines.append(f"{'level':>6}  {'size (B)':>12}  {'time (ms, median)':>18}")

        for r in levels:
            lines.append(f"{r.level:>6}  {r.size:>12,}  {r.seconds * 1000:>18.2f}")

    return "\n".join(lines)


def _scale(value: float, lo: float, hi: float, out_lo: float, out_hi: float) -> float:
    if hi == lo:
        return (out_lo + out_hi) / 2

    return out_lo + (value - lo) / (hi - lo) * (out_hi - out_lo)


def _format_bytes(n: float) -> str:
    for unit, size in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= size:
            return f"{n / size:.1f} {unit}"

    return f"{n:.0f} B"


def _format_seconds(t: float) -> str:
    return f"{t * 1000:.0f} ms" if t < 1 else f"{t:.2f} s"


def render_svg(results: dict[str, list[LevelResult]], *, theme: str) -> str:
    """
    One SVG: time on x, size on y (log scale), one polyline per profile.

    Content profiles span orders of magnitude in compressed size (a `flat`
    channel and a `grain` channel of the same pixel count can differ by
    100x), so a linear size axis flattens every profile but the largest
    into an invisible line hugging the bottom. Log scale is still one axis,
    one unit - not the dual-axis trade-off this chart deliberately avoids -
    it just lets each profile's own level-to-level movement be seen
    regardless of its absolute size.
    """
    colors = _PALETTE[theme]
    width, height = 720, 440
    margin = {"left": 90, "right": 24, "top": 24, "bottom": 56}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]

    all_points = [(r.seconds, r.size) for levels in results.values() for r in levels]
    times = [t for t, _ in all_points]
    log_sizes = [math.log10(s) for _, s in all_points]
    t_lo, t_hi = min(times), max(times)
    s_lo, s_hi = min(log_sizes), max(log_sizes)

    def x(t: float) -> float:
        return margin["left"] + _scale(t, t_lo, t_hi, 0, plot_w)

    def y(s: float) -> float:
        return margin["top"] + _scale(math.log10(s), s_lo, s_hi, plot_h, 0)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'font-family="sans-serif" font-size="11">',
        f'<rect width="{width}" height="{height}" fill="{colors["background"]}"/>',
    ]

    for fraction in (0, 0.25, 0.5, 0.75, 1):
        gy = margin["top"] + fraction * plot_h
        tick_size = 10 ** _scale(fraction, 0, 1, s_hi, s_lo)
        parts.append(
            f'<text x="{margin["left"] - 8}" y="{gy + 3:.1f}" fill="{colors["text"]}" '
            f'text-anchor="end">{_format_bytes(tick_size)}</text>'
        )

    for fraction in (0, 0.25, 0.5, 0.75, 1):
        gx = margin["left"] + fraction * plot_w
        tick_time = _scale(fraction, 0, 1, t_lo, t_hi)
        parts.append(
            f'<text x="{gx:.1f}" y="{margin["top"] + plot_h + 14}" '
            f'fill="{colors["text"]}" text-anchor="middle">'
            f"{_format_seconds(tick_time)}</text>"
        )

    for fraction in (0, 0.25, 0.5, 0.75, 1):
        gy = margin["top"] + fraction * plot_h
        parts.append(
            f'<line x1="{margin["left"]}" y1="{gy:.1f}" '
            f'x2="{width - margin["right"]}" y2="{gy:.1f}" '
            f'stroke="{colors["grid"]}" stroke-width="1"/>'
        )

    parts.append(
        f'<text x="{margin["left"] + plot_w / 2:.1f}" y="{height - 12}" '
        f'fill="{colors["text"]}" text-anchor="middle">time (median, seconds)</text>'
    )
    parts.append(
        f'<text x="14" y="{margin["top"] + plot_h / 2:.1f}" fill="{colors["text"]}" '
        f'text-anchor="middle" '
        f'transform="rotate(-90 14 {margin["top"] + plot_h / 2:.1f})">'
        "compressed size (bytes)</text>"
    )

    for name, levels in results.items():
        color = colors[name]
        points = " ".join(f"{x(r.seconds):.1f},{y(r.size):.1f}" for r in levels)
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="{color}" '
            f'stroke-width="2"/>'
        )

        for r in levels:
            px, py = x(r.seconds), y(r.size)
            parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3" fill="{color}"/>')
            parts.append(
                f'<text x="{px + 4:.1f}" y="{py - 4:.1f}" fill="{colors["text"]}" '
                f'font-size="9">{r.level}</text>'
            )

    legend_x = width - margin["right"]
    # Sized to `results`, not a constant that only happened to fit 5 rows.
    row_height = min(16, plot_h / len(results))

    for i, name in enumerate(results):
        legend_y = margin["top"] + i * row_height
        parts.append(
            f'<circle cx="{legend_x - 96}" cy="{legend_y + 4:.1f}" r="4" '
            f'fill="{colors[name]}"/>'
        )
        parts.append(
            f'<text x="{legend_x - 86}" y="{legend_y + 8:.1f}" '
            f'fill="{colors["text"]}">{name}</text>'
        )

    parts.append("</svg>")

    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="benchmark",
        description="Sweeps deflate levels across content profiles and charts it.",
    )
    parser.add_argument("--out", type=Path, default=Path("docs/levels.svg"))
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument("--repeats", type=int, default=3)

    args = parser.parse_args(argv)

    results = sweep(width=args.width, rows=args.rows, repeats=args.repeats)

    print(_format_table(results))
    print(f"\ngrain level 3/4 inversion: {grain_inversion(results['grain'])}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_svg(results, theme="light"))

    dark_out = args.out.with_name(f"{args.out.stem}-dark{args.out.suffix}")
    dark_out.write_text(render_svg(results, theme="dark"))

    print(f"\nwrote {args.out} and {dark_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
