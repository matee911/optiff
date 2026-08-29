"""
Benchmark compression settings across one or more TIFF files.

For every file the script runs *planning only* (no output written) and reports:
  - compressed size and elapsed time for each deflate level 1-9
  - extra gain from --image-data   (flattened pixels packed with Adobe Deflate)
  - extra gain from --zip-fallback  (channels with 0x0 geometry packed with ZIP)

Only planning is done - nothing is written to disk - so each run is safe and
fast even on multi-gigabyte files.

Usage
-----
    python tools/benchmark.py FILE [FILE ...]
    python tools/benchmark.py *.tif
    python tools/benchmark.py big.tif --level-range 4 6
    python tools/benchmark.py big.tif --csv results.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

import matplotlib
import matplotlib.axes
import matplotlib.figure
import matplotlib.pyplot as plt

matplotlib.use("Agg")  # non-interactive, safe for scripts

from optiff.optimize import OptimizeError, plan_file
from optiff.segments import total_size

MB = 1024**2
GB = 1024**3


# ---------------------------------------------------------------------------
# Hardware info
# ---------------------------------------------------------------------------


def _cpu_brand() -> str:
    """Best-effort CPU model string, works on macOS, Linux and Windows."""
    system = platform.system()
    if system == "Darwin":
        try:
            out = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            return out.strip()
        except Exception:
            pass
    if system == "Linux":
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except Exception:
            pass
    if system == "Windows":
        try:
            out = subprocess.check_output(
                ["wmic", "cpu", "get", "Name", "/value"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for line in out.splitlines():
                if line.startswith("Name="):
                    return line.split("=", 1)[1].strip()
        except Exception:
            pass
    return platform.processor() or "unknown"


def _ram_gb() -> float:
    """Physical RAM in GiB, best-effort."""
    system = platform.system()
    if system == "Darwin":
        try:
            out = subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            return int(out.strip()) / GB
        except Exception:
            pass
    if system == "Linux":
        try:
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return kb / (1024**2)
        except Exception:
            pass
    if system == "Windows":
        try:
            out = subprocess.check_output(
                ["wmic", "ComputerSystem", "get", "TotalPhysicalMemory", "/value"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for line in out.splitlines():
                if line.startswith("TotalPhysicalMemory="):
                    return int(line.split("=", 1)[1].strip()) / GB
        except Exception:
            pass
    return 0.0


def collect_hw_info() -> dict[str, str]:
    """Return a dict of human-readable hardware / runtime fields."""
    cpu_count_physical = os.cpu_count()  # logical; good enough as a proxy
    ram = _ram_gb()
    return {
        "cpu": _cpu_brand(),
        "cpu_cores": str(cpu_count_physical or "?"),
        "ram_gb": f"{ram:.1f}" if ram else "?",
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
    }


def render_hw_info(hw: dict[str, str], out: TextIO = sys.stdout) -> None:
    sep = "=" * 72
    print(sep, file=out)
    print("  Hardware / runtime", file=out)
    print(sep, file=out)
    print(f"  CPU    : {hw['cpu']}", file=out)
    print(f"  Cores  : {hw['cpu_cores']} logical", file=out)
    print(f"  RAM    : {hw['ram_gb']} GiB", file=out)
    print(f"  OS     : {hw['os']}", file=out)
    print(f"  Python : {hw['python']}", file=out)
    print(sep, file=out)
    print(file=out)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class LevelResult:
    level: int
    size_after: int  # bytes that would be written
    tag_after: int  # tag 37724 after compression
    image_after: int  # flattened pixels after compression (0 = not packed)
    seconds: float
    image_data: bool = False
    zip_fallback: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class FileReport:
    path: Path
    size_before: int
    tag_before: int  # tag 37724 raw
    image_before: int  # flattened pixels raw
    results: list[LevelResult] = field(default_factory=list)

    @property
    def baseline(self) -> LevelResult | None:
        """Plain run at level 6 (default), no flags - used as reference."""
        for r in self.results:
            if r.level == 6 and not r.image_data and not r.zip_fallback:
                return r
        return self.results[0] if self.results else None


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


def _measure(
    path: Path,
    level: int,
    *,
    image_data: bool = False,
    zip_fallback: bool = False,
) -> LevelResult:
    t0 = time.monotonic()
    try:
        segments, container, image, _notes = plan_file(
            path,
            level=level,
            image_data=image_data,
            zip_fallback=zip_fallback,
        )
        elapsed = time.monotonic() - t0
        size_after = total_size(segments)
        return LevelResult(
            level=level,
            size_after=size_after,
            tag_after=container.size,
            image_after=image.after if image and image.worth_it else 0,
            seconds=elapsed,
            image_data=image_data,
            zip_fallback=zip_fallback,
        )
    except OptimizeError as exc:
        elapsed = time.monotonic() - t0
        return LevelResult(
            level=level,
            size_after=0,
            tag_after=0,
            image_after=0,
            seconds=elapsed,
            image_data=image_data,
            zip_fallback=zip_fallback,
            error=str(exc),
        )


def _run_variant(  # noqa: PLR0913
    report: FileReport,
    path: Path,
    level: int,
    *,
    image_data: bool = False,
    zip_fallback: bool = False,
    label: str = "",
    verbose: bool = False,
) -> None:
    """Measure one variant and append it to *report*."""
    print(verbose)
    if verbose:
        tag = label or f"level {level}"
        print(f"  {tag}...", end="", flush=True)
    result = _measure(path, level, image_data=image_data, zip_fallback=zip_fallback)
    report.results.append(result)
    if verbose:
        _print_inline(result, report.size_before)


def benchmark_file(
    path: Path,
    *,
    levels: range,
    image_data: bool = True,
    zip_fallback: bool = True,
    verbose: bool = False,
) -> FileReport:
    print("benchmark_file")
    size_before = path.stat().st_size

    # We need tag_before / image_before - do a single dry run at level 1
    # (fastest) just to read those numbers; cost is negligible.
    try:
        _segments, container0, image0, _notes = plan_file(path, level=1)
        tag_before = container0.source_size
        image_before = image0.before if image0 else 0
    except OptimizeError as exc:
        # If even level-1 planning fails, bail out early.
        report = FileReport(
            path=path, size_before=size_before, tag_before=0, image_before=0
        )
        report.results.append(
            LevelResult(
                level=1,
                size_after=0,
                tag_after=0,
                image_after=0,
                seconds=0.0,
                error=str(exc),
            )
        )
        return report

    report = FileReport(
        path=path,
        size_before=size_before,
        tag_before=tag_before,
        image_before=image_before,
    )

    # --- Levels 1..9, plain ---
    for lvl in levels:
        _run_variant(report, path, lvl, label=f"level {lvl}", verbose=verbose)

    # --- Level 4 + --image-data ---
    if image_data:
        _run_variant(
            report,
            path,
            4,
            image_data=True,
            label="level 4 + --image-data",
            verbose=verbose,
        )

    # --- Level 4 + --zip-fallback ---
    if zip_fallback:
        _run_variant(
            report,
            path,
            4,
            zip_fallback=True,
            label="level 4 + --zip-fallback",
            verbose=verbose,
        )

    # --- Level 4 + both ---
    if image_data and zip_fallback:
        _run_variant(
            report,
            path,
            4,
            image_data=True,
            zip_fallback=True,
            label="level 4 + --image-data + --zip-fallback",
            verbose=verbose,
        )

    return report


def _print_inline(result: LevelResult, size_before: int) -> None:
    if result.ok:
        pct = (1 - result.size_after / size_before) * 100
        mb = result.size_after / MB
        print(f" {mb:8.1f} MB  ({pct:5.1f}% saved)  {result.seconds:.1f}s")
    else:
        print(f" ERROR: {result.error}")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt_size(b: int) -> str:
    if b >= GB:
        return f"{b / GB:.3f} GB"
    return f"{b / MB:.1f} MB"


def _fmt_pct(after: int, before: int) -> str:
    if before == 0:
        return "  n/a"
    return f"{(1 - after / before) * 100:+.1f}%"


def _label(r: LevelResult) -> str:
    base = f"level {r.level}"
    flags = []
    if r.image_data:
        flags.append("--image-data")
    if r.zip_fallback:
        flags.append("--zip-fallback")
    return base + ("  " + " ".join(flags) if flags else "")


def render_report(report: FileReport, out: TextIO = sys.stdout) -> None:
    sep = "-" * 72
    name = report.path.name
    print(sep, file=out)
    print(f"  {name}", file=out)
    print(f"  original size : {_fmt_size(report.size_before)}", file=out)
    print(f"  tag 37724     : {_fmt_size(report.tag_before)}", file=out)
    if report.image_before:
        print(f"  image pixels  : {_fmt_size(report.image_before)}", file=out)
    print(sep, file=out)

    # Header
    print(
        f"  {'label':<34} {'size':>10}  {'saved':>10}  {'time':>6}",
        file=out,
    )
    print(f"  {'-' * 34} {'-' * 10}  {'-' * 10}  {'-' * 6}", file=out)

    base_ref = report.baseline

    for r in report.results:
        if not r.ok:
            print(f"  {_label(r):<34}  ERROR: {r.error}", file=out)
            continue

        size_str = _fmt_size(r.size_after)
        saved_b = report.size_before - r.size_after
        saved_str = _fmt_size(saved_b) if saved_b else "  0 B"
        pct_str = _fmt_pct(r.size_after, report.size_before)
        time_str = f"{r.seconds:.1f}s"

        # Extra column: gain vs baseline (level 6, plain)
        extra = ""
        is_flag_variant = r.level == 6 and (r.image_data or r.zip_fallback)
        if base_ref and base_ref is not r and is_flag_variant:
            delta = base_ref.size_after - r.size_after
            if delta != 0:
                sign = "+" if delta > 0 else ""
                pct_delta = delta / base_ref.size_after * 100
                extra = f"   vs base: {sign}{_fmt_size(abs(delta))} ({pct_delta:+.1f}%)"

        row = (
            f"  {_label(r):<34} {size_str:>10}"
            f"  {saved_str:>10} ({pct_str})  {time_str:>6}{extra}"
        )
        print(row, file=out)

    print(sep, file=out)
    print(file=out)


def render_csv(
    reports: list[FileReport],
    csv_path: Path,
    *,
    hw: dict[str, str] | None = None,
) -> None:
    """Write all results to a CSV file for further analysis."""
    hw = hw or {}
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "file",
                "size_before_b",
                "tag_before_b",
                "level",
                "image_data",
                "zip_fallback",
                "size_after_b",
                "tag_after_b",
                "image_after_b",
                "saved_b",
                "pct_saved",
                "seconds",
                "cpu",
                "cpu_cores",
                "ram_gb",
                "os",
                "python",
            ]
        )
        for report in reports:
            for r in report.results:
                if not r.ok:
                    continue
                saved = report.size_before - r.size_after
                pct = (saved / report.size_before * 100) if report.size_before else 0.0
                writer.writerow(
                    [
                        report.path.name,
                        report.size_before,
                        report.tag_before,
                        r.level,
                        int(r.image_data),
                        int(r.zip_fallback),
                        r.size_after,
                        r.tag_after,
                        r.image_after,
                        saved,
                        f"{pct:.2f}",
                        f"{r.seconds:.3f}",
                        hw.get("cpu", ""),
                        hw.get("cpu_cores", ""),
                        hw.get("ram_gb", ""),
                        hw.get("os", ""),
                        hw.get("python", ""),
                    ]
                )


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

_COLORS = {
    "size": "#4C9BE8",
    "time": "#F28C38",
    "base": "#6B8E6B",
    "image_data": "#5B8DD9",
    "zip_fallback": "#D97B5B",
    "both": "#9B59B6",
}


def _make_figure(title: str, hw: dict[str, str]) -> matplotlib.figure.Figure:
    """Create a figure with a shared hardware subtitle."""
    fig = plt.figure(figsize=(13, 9))
    hw_line = (
        f"{hw.get('cpu', '?')}  •  "
        f"{hw.get('cpu_cores', '?')} cores  •  "
        f"{hw.get('ram_gb', '?')} GiB RAM  •  "
        f"{hw.get('os', '?')}  •  "
        f"Python {hw.get('python', '?')}"
    )
    fig.suptitle(title, fontsize=14, fontweight="bold", y=0.98)
    fig.text(
        0.5,
        0.955,
        hw_line,
        ha="center",
        va="top",
        fontsize=8.5,
        color="#666666",
        style="italic",
    )
    return fig


def _plot_levels(
    ax_size: matplotlib.axes.Axes,
    ax_time: matplotlib.axes.Axes,
    report: FileReport,
) -> None:
    """Line plot: size (left axis) and time (right axis) vs deflate level."""
    plain = [
        r for r in report.results if not r.image_data and not r.zip_fallback and r.ok
    ]
    if not plain:
        return

    levels = [r.level for r in plain]
    sizes_mb = [r.size_after / MB for r in plain]
    times_s = [r.seconds for r in plain]
    baseline_mb = report.size_before / MB

    # --- size line ---
    ax_size.axhline(
        baseline_mb,
        color="#AAAAAA",
        linewidth=1,
        linestyle="--",
        label="original",
    )
    ax_size.plot(
        levels,
        sizes_mb,
        color=_COLORS["size"],
        linewidth=2.2,
        marker="o",
        markersize=6,
        label="compressed size",
    )
    ax_size.fill_between(
        levels,
        sizes_mb,
        baseline_mb,
        alpha=0.12,
        color=_COLORS["size"],
    )

    ax_size.set_xlabel("Deflate level", fontsize=10)
    ax_size.set_ylabel("Size after (MB)", color=_COLORS["size"], fontsize=10)
    ax_size.tick_params(axis="y", labelcolor=_COLORS["size"])
    ax_size.set_xticks(levels)

    # annotate each point with saving %
    for lvl, sz in zip(levels, sizes_mb, strict=False):
        pct = (1 - sz / baseline_mb) * 100
        ax_size.annotate(
            f"{pct:.1f}%",
            xy=(lvl, sz),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            fontsize=7.5,
            color=_COLORS["size"],
        )

    # --- time line ---
    ax_time.plot(
        levels,
        times_s,
        color=_COLORS["time"],
        linewidth=2.2,
        marker="s",
        markersize=5,
        linestyle="--",
        label="time (s)",
    )
    ax_time.set_ylabel("Planning time (s)", color=_COLORS["time"], fontsize=10)
    ax_time.tick_params(axis="y", labelcolor=_COLORS["time"])

    # combined legend
    lines_a, labels_a = ax_size.get_legend_handles_labels()
    lines_b, labels_b = ax_time.get_legend_handles_labels()
    ax_size.legend(
        lines_a + lines_b,
        labels_a + labels_b,
        loc="upper right",
        fontsize=8.5,
        framealpha=0.85,
    )
    ax_size.set_title("Size & time vs deflate level", fontsize=11, pad=8)


def _plot_flags(
    ax: matplotlib.axes.Axes,
    report: FileReport,
) -> None:
    """Horizontal bar chart: flag variants vs plain level-6 baseline."""
    base = report.baseline
    if base is None or not base.ok:
        ax.set_visible(False)
        return

    variants = [
        ("base (level 6)", base, _COLORS["base"]),
    ]
    for r in report.results:
        if not r.ok or r.level != 6:
            continue
        if r.image_data and r.zip_fallback:
            variants.append(("--image-data + --zip-fallback", r, _COLORS["both"]))
        elif r.image_data:
            variants.append(("--image-data", r, _COLORS["image_data"]))
        elif r.zip_fallback:
            variants.append(("--zip-fallback", r, _COLORS["zip_fallback"]))

    if len(variants) <= 1:
        ax.set_visible(False)
        return

    labels = [v[0] for v in variants]
    sizes_mb = [v[1].size_after / MB for v in variants]
    colors = [v[2] for v in variants]
    baseline_mb = report.size_before / MB

    y = range(len(labels))
    bars = ax.barh(
        y,
        sizes_mb,
        color=colors,
        height=0.5,
        edgecolor="white",
        linewidth=0.8,
    )

    # vertical line at original size
    ax.axvline(
        baseline_mb,
        color="#AAAAAA",
        linewidth=1,
        linestyle="--",
        label="original",
    )

    # annotate bars: size + saving %
    for bar, sz in zip(bars, sizes_mb, strict=False):
        pct = (1 - sz / baseline_mb) * 100
        ax.text(
            sz + baseline_mb * 0.003,
            bar.get_y() + bar.get_height() / 2,
            f"{sz:.1f} MB  ({pct:+.1f}%)",
            va="center",
            fontsize=8.5,
        )

    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel("Size after (MB)", fontsize=10)
    ax.set_title("Flag variant comparison at level 6", fontsize=11, pad=8)
    ax.invert_yaxis()
    ax.legend(fontsize=8.5, framealpha=0.85)


def plot_report(
    report: FileReport,
    out_dir: Path,
    hw: dict[str, str],
) -> Path:
    """Render a two-panel PNG for *report* and return its path."""

    plain = [
        r for r in report.results if not r.image_data and not r.zip_fallback and r.ok
    ]
    has_flags = any(
        r.ok and r.level == 6 and (r.image_data or r.zip_fallback)
        for r in report.results
    )

    n_cols = 2 if (plain and has_flags) else 1
    fig = _make_figure(report.path.name, hw)

    if n_cols == 2:
        ax_size = fig.add_subplot(1, 2, 1)
        ax_time = ax_size.twinx()
        ax_flags = fig.add_subplot(1, 2, 2)
    else:
        ax_size = fig.add_subplot(1, 1, 1)
        ax_time = ax_size.twinx()
        ax_flags = None

    if plain:
        _plot_levels(ax_size, ax_time, report)
    if ax_flags is not None:
        _plot_flags(ax_flags, report)

    fig.tight_layout(rect=[0, 0, 1, 0.945])

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{report.path.stem}_benchmark.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out_path


def plot_reports(
    reports: list[FileReport],
    out_dir: Path,
    hw: dict[str, str],
) -> list[Path]:
    """Render one PNG per file; return the list of written paths."""
    paths = []
    for report in reports:
        out = plot_report(report, out_dir, hw)
        print(f"Plot written to: {out}")
        paths.append(out)
    return paths


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="benchmark",
        description=(
            "Measure compression time and size across levels 1-9, "
            "--image-data, and --zip-fallback for one or more TIFF files. "
            "Nothing is written to disk - only planning is performed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "files",
        nargs="+",
        type=Path,
        metavar="FILE",
        help="TIFF file(s) to benchmark",
    )
    parser.add_argument(
        "--level-range",
        nargs=2,
        type=int,
        metavar=("MIN", "MAX"),
        default=[1, 9],
        help="range of deflate levels to test (default: 1 9)",
    )
    parser.add_argument(
        "--no-image-data",
        action="store_true",
        help="skip the --image-data variant",
    )
    parser.add_argument(
        "--no-zip-fallback",
        action="store_true",
        help="skip the --zip-fallback variant",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        metavar="FILE",
        help="also write results to a CSV file",
    )
    parser.add_argument(
        "--plots",
        type=Path,
        metavar="DIR",
        help="write one PNG chart per file into DIR",
        nargs="?",
        const=Path("."),  # --plots with no argument -> current dir
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="skip chart generation entirely",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print progress while each variant runs",
    )

    args = parser.parse_args(argv)

    # Validate level range
    lo, hi = args.level_range
    if not (1 <= lo <= hi <= 9):
        print(
            "--level-range must be two integers between 1 and 9 with MIN <= MAX.",
            file=sys.stderr,
        )
        return 2

    levels = range(lo, hi + 1)
    reports: list[FileReport] = []

    hw = collect_hw_info()
    render_hw_info(hw)

    for path in args.files:
        if not path.is_file():
            print(f"File not found: {path}", file=sys.stderr)
            continue

        print(f"\nBenchmarking: {path.name}  ({path.stat().st_size / MB:.1f} MB)")
        if args.verbose:
            print()

        report = benchmark_file(
            path,
            levels=levels,
            image_data=not args.no_image_data,
            zip_fallback=not args.no_zip_fallback,
            verbose=args.verbose,
        )
        reports.append(report)
        render_report(report)

    if args.csv and reports:
        render_csv(reports, args.csv, hw=hw)
        print(f"CSV written to: {args.csv}")

    if not args.no_plots and reports:
        plots_dir = args.plots  # None = next to each source file
        if plots_dir is not None:
            plot_reports(reports, plots_dir, hw)
        else:
            for report in reports:
                out = report.path.parent / (report.path.stem + "_benchmark.png")
                plot_report(report, report.path.parent, hw)
                print(f"Plot written to: {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
