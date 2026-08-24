"""
Compares two files through Photoshop's eyes: the layer tree and every mask.

Why: our own SHA256 verification proves the bytes decompress to the same
pixels. It does not prove Photoshop reads them the same way, and that is the
thing at stake whenever we change a channel's compression method.

What this script DELIBERATELY does not do: compare the flattened image. A
layer covering the whole canvas hides everything beneath it, so such a
comparison passes vacuously even if every mask below were scrambled. Each mask
is read directly instead.

Usage:
    python tools/mask_check.py ORIGINAL RESULT
    python tools/mask_check.py ORIGINAL RESULT --pixels
    python tools/mask_check.py ORIGINAL RESULT --pixels --threshold 50 --keep
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import tifffile

HERE = Path(__file__).resolve().parent
JSX = HERE / "mask_check.jsx"
INPUT = HERE / ".mask_check_in.json"
OUTPUT = HERE / ".mask_check_out.json"

#: osascript aborts an AppleEvent after 60 s, and opening a 2 GB file takes
#: longer. Without that wrapper we get error -1712 halfway through.
TIMEOUT_APPLEEVENT = 3000

DEFAULT_APP = "Adobe Photoshop 2026"


def run_photoshop(app: str) -> str:
    """Runs the JSX script and returns whatever it returned."""
    script = (
        f"with timeout of {TIMEOUT_APPLEEVENT} seconds\n"
        f'\ttell application "{app}"\n'
        f'\t\tdo javascript file "{JSX}"\n'
        f"\tend tell\n"
        f"end timeout\n"
    )

    done = subprocess.run(
        ["osascript", "-"],
        input=script,
        capture_output=True,
        text=True,
        check=False,
    )

    if done.returncode != 0:
        raise SystemExit(f"Photoshop refused: {done.stderr.strip()}")

    return done.stdout.strip()


def compare_header(a: dict, b: dict) -> list[str]:
    problems = []

    for entry in (a, b):
        if not entry.get("opened"):
            problems.append(
                f"did not open: {entry['file']} ({entry.get('error', '?')})"
            )

    if problems:
        return problems

    for field in ("width", "height", "mode", "depth"):
        if a.get(field) != b.get(field):
            problems.append(f"{field}: {a.get(field)} != {b.get(field)}")

    return problems


def compare_layers(a: dict, b: dict) -> list[str]:
    left = [
        (item["path"], item["kind"], item["visible"]) for item in a.get("layers", [])
    ]
    right = [
        (item["path"], item["kind"], item["visible"]) for item in b.get("layers", [])
    ]

    if left == right:
        return []

    problems = [f"layer trees differ: {len(left)} vs {len(right)} entries"]

    for x, y in zip(left, right, strict=False):
        if x != y:
            problems.append(f"  {x} != {y}")

    return problems


def compare_pixels(path_a: str, path_b: str) -> str | None:
    """A description of the difference, or None when the pixels are identical."""
    x = tifffile.imread(path_a)
    y = tifffile.imread(path_b)

    if x.shape != y.shape or x.dtype != y.dtype:
        return f"different shape: {x.shape} {x.dtype} vs {y.shape} {y.dtype}"

    if np.array_equal(x, y):
        return None

    difference = np.abs(x.astype("int64") - y.astype("int64"))

    return (
        f"{int((difference > 0).sum()):,} differing pixels, "
        f"largest difference {int(difference.max())}"
    )


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0912  - the report has many cases to show
    parser = argparse.ArgumentParser(
        prog="mask_check",
        description="Compares two files through Photoshop, mask by mask.",
    )
    parser.add_argument("original", type=Path)
    parser.add_argument("result", type=Path)
    parser.add_argument(
        "--pixels",
        action="store_true",
        help="also export and compare the pixels of the richer masks",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=100,
        help="how many non-empty histogram buckets qualify a mask for a pixel "
        "export (default 100); a flat mask proves nothing",
    )
    parser.add_argument("--keep", action="store_true", help="do not delete the exports")
    parser.add_argument("--app", default=DEFAULT_APP)

    args = parser.parse_args(argv)

    for path in (args.original, args.result):
        if not path.is_file():
            print(f"File not found: {path}", file=sys.stderr)
            return 2

    directory = Path(tempfile.mkdtemp(prefix="mask_check_"))

    INPUT.write_text(
        json.dumps(
            {
                "files": [str(args.original.resolve()), str(args.result.resolve())],
                "pixels": bool(args.pixels),
                "threshold": args.threshold,
                "directory": str(directory),
            }
        )
    )

    run_photoshop(args.app)

    a, b = json.loads(OUTPUT.read_text())

    problems = compare_header(a, b)

    if not problems:
        problems += compare_layers(a, b)

        # The key is positional rather than by name: layer paths repeat (two
        # "More texture" layers in one group), and a dict keyed by name would
        # silently drop one of the masks.
        masks_a = {(i, m["path"]): m for i, m in enumerate(a.get("masks", []))}
        masks_b = {(i, m["path"]): m for i, m in enumerate(b.get("masks", []))}

        if len(masks_a) != len(masks_b):
            problems.append(f"mask counts differ: {len(masks_a)} vs {len(masks_b)}")

        for key in sorted(set(masks_a) - set(masks_b)):
            problems.append(f"mask lost in the result: {key[1]}")
        for key in sorted(set(masks_b) - set(masks_a)):
            problems.append(f"mask appeared in the result: {key[1]}")

        print(f"{'mask':<48}{'buckets':>9}{'histogram':>12}{'pixels':>12}")
        print("-" * 81)

        for key in sorted(set(masks_a) & set(masks_b)):
            name = key[1]
            ma, mb = masks_a[key], masks_b[key]
            hist_ok = ma["histogram"] == mb["histogram"] and ma["total"] == mb["total"]

            px = "-"

            if ma.get("pixels") and mb.get("pixels"):
                difference = compare_pixels(ma["pixels"], mb["pixels"])
                px = "OK" if difference is None else "FAIL"

                if difference is not None:
                    problems.append(f"{name}: {difference}")

            if not hist_ok:
                problems.append(f"{name}: mask histograms differ")

            print(
                f"{name[-46:]:<48}{ma['buckets']:>9}"
                f"{'OK' if hist_ok else 'FAIL':>12}{px:>12}"
            )

        print("-" * 81)
        print(f"masks: {len(masks_a)}")

    if not args.keep:
        for exported in directory.glob("*.tif"):
            exported.unlink()
        directory.rmdir()
    else:
        print(f"exports left in {directory}")

    INPUT.unlink(missing_ok=True)
    OUTPUT.unlink(missing_ok=True)

    if problems:
        print("\nPROBLEMS:")
        for problem in problems:
            print(f"  {problem}")
        return 1

    print("\nNO DIFFERENCES - Photoshop reads both files the same way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
