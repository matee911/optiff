"""
Porownuje dwa files oczami Photoshopa: strukture warstw i kazda maske osobno.

Po co: nasza wlasna weryfikacja SHA256 dowodzi, ze data_bytes rozpakowuja sie na te
same pixels. It does not prove Photoshop reads them the same way, and those
rzeczy, gdy zmieniamy metode kompresji channel.

What this script DELIBERATELY does not do: compare the flattened image. A
layer covering the whole canvas hides everything beneath it, so such a
comparison passes
na pusto, nawet gdyby masks nizej byly rozsypane. Kazda mask jest czytana
wprost.

Uzycie:
    python tools/mask_check.py ORYGINAL WYNIK
    python tools/mask_check.py ORYGINAL WYNIK --pixels     # + porownanie pikseli
    python tools/mask_check.py ORYGINAL WYNIK --pixels --threshold 50 --keep
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

TU = Path(__file__).resolve().parent
JSX = TU / "mask_check.jsx"
WEJSCIE = TU / ".mask_check_in.json"
WYJSCIE = TU / ".mask_check_out.json"

#: osascript przerywa AppleEvent po 60 s, a otwarcie pliku 2 GB trwa dluzej.
#: Without that wrapper we get error -1712 halfway through.
TIMEOUT_APPLEEVENT = 3000

DEFAULT_APP = "Adobe Photoshop 2026"


def run_photoshop(app: str) -> str:
    """Uruchamia JSX i zwraca to, co skrypt zwrocil."""
    skrypt = (
        f"with timeout of {TIMEOUT_APPLEEVENT} seconds\n"
        f'\ttell application "{app}"\n'
        f'\t\tdo javascript file "{JSX}"\n'
        f"\tend tell\n"
        f"end timeout\n"
    )

    done = subprocess.run(
        ["osascript", "-"],
        input=skrypt,
        capture_output=True,
        text=True,
        check=False,
    )

    if done.returncode != 0:
        raise SystemExit(f"Photoshop odmowil: {done.stderr.strip()}")

    return done.stdout.strip()


def compare_header(a: dict, b: dict) -> list[str]:
    problemy = []

    for file in (a, b):
        if not file.get("otwarty"):
            problemy.append(
                f"nie otworzyl sie: {file['file']} ({file.get('error', '?')})"
            )

    if problemy:
        return problemy

    for pole in ("width", "height", "tryb", "bity"):
        if a.get(pole) != b.get(pole):
            problemy.append(f"{pole}: {a.get(pole)} != {b.get(pole)}")

    return problemy


def compare_layers(a: dict, b: dict) -> list[str]:
    wa = [(w["sciezka"], w["typ"], w["widoczna"]) for w in a.get("warstwy", [])]
    wb = [(w["sciezka"], w["typ"], w["widoczna"]) for w in b.get("warstwy", [])]

    if wa == wb:
        return []

    problemy = [f"drzewo warstw sie rozni: {len(wa)} vs {len(wb)} pozycji"]

    for x, y in zip(wa, wb, strict=False):
        if x != y:
            problemy.append(f"  {x} != {y}")

    return problemy


def compare_pixels(path_a: str, path_b: str) -> str | None:
    """Zwraca label roznicy albo None, gdy piksele sa identyczne."""
    x = tifffile.imread(path_a)
    y = tifffile.imread(path_b)

    if x.shape != y.shape or x.dtype != y.dtype:
        return f"rozny ksztalt: {x.shape} {x.dtype} vs {y.shape} {y.dtype}"

    if np.array_equal(x, y):
        return None

    roznica = np.abs(x.astype("int64") - y.astype("int64"))

    return (
        f"{int((roznica > 0).sum()):,} roznych pikseli, "
        f"najwieksza roznica {int(roznica.max())}"
    )


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0912  - raport ma wiele przypadkow do pokazania
    parser = argparse.ArgumentParser(
        prog="mask_check",
        description="Porownuje dwa files oczami Photoshopa, mask po masce.",
    )
    parser.add_argument("oryginal", type=Path)
    parser.add_argument("result", type=Path)
    parser.add_argument(
        "--pixels",
        action="store_true",
        help="dodatkowo eksportuj i porownaj piksele bogatszych masek",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=100,
        help="ile niepustych koszykow histogramu kwalifikuje maske do "
        "eksportu pikseli (domyslnie 100); mask jednolita niczego nie dowodzi",
    )
    parser.add_argument("--keep", action="store_true", help="nie kasuj eksportow")
    parser.add_argument("--app", default=DEFAULT_APP)

    args = parser.parse_args(argv)

    for sciezka in (args.oryginal, args.result):
        if not sciezka.is_file():
            print(f"Nie znaleziono pliku: {sciezka}", file=sys.stderr)
            return 2

    katalog = Path(tempfile.mkdtemp(prefix="mask_check_"))

    WEJSCIE.write_text(
        json.dumps(
            {
                "files": [str(args.oryginal.resolve()), str(args.result.resolve())],
                "piksele": bool(args.pixels),
                "threshold": args.threshold,
                "katalog": str(katalog),
            }
        )
    )

    run_photoshop(args.app)

    a, b = json.loads(WYJSCIE.read_text())

    problemy = compare_header(a, b)

    if not problemy:
        problemy += compare_layers(a, b)

        # The key is positional rather than by name: layer paths can
        # powtorzone (dwie warstwy "More texture" w tej samej grupie), a
        # slownik po nazwie po cichu gubilby jedna z masek.
        masks_a = {(i, m["sciezka"]): m for i, m in enumerate(a.get("masks", []))}
        masks_b = {(i, m["sciezka"]): m for i, m in enumerate(b.get("masks", []))}

        if len(masks_a) != len(masks_b):
            problemy.append(
                f"inna liczba masek: {len(masks_a)} vs {len(masks_b)}"
            )

        for klucz in sorted(set(masks_a) - set(masks_b)):
            problemy.append(f"mask zniknela w wyniku: {klucz[1]}")
        for klucz in sorted(set(masks_b) - set(masks_a)):
            problemy.append(f"mask pojawila sie w wyniku: {klucz[1]}")

        print(f"{'mask':<48}{'koszyki':>9}{'histogram':>12}{'piksele':>12}")
        print("-" * 81)

        for klucz in sorted(set(masks_a) & set(masks_b)):
            s = klucz[1]
            ma, mb = masks_a[klucz], masks_b[klucz]
            hist_ok = ma["histogram"] == mb["histogram"] and ma["total"] == mb["total"]

            px = "-"

            if ma.get("piksele") and mb.get("piksele"):
                roznica = compare_pixels(ma["piksele"], mb["piksele"])
                px = "OK" if roznica is None else "BLAD"

                if roznica is not None:
                    problemy.append(f"{s}: {roznica}")

            if not hist_ok:
                problemy.append(f"{s}: histogram masks sie rozni")

            print(
                f"{s[-46:]:<48}{ma['koszyki']:>9}"
                f"{'OK' if hist_ok else 'BLAD':>12}{px:>12}"
            )

        print("-" * 81)
        print(f"masek: {len(masks_a)}")

    if not args.keep:
        for file in katalog.glob("*.tif"):
            file.unlink()
        katalog.rmdir()
    else:
        print(f"eksporty zostawione w {katalog}")

    WEJSCIE.unlink(missing_ok=True)
    WYJSCIE.unlink(missing_ok=True)

    if problemy:
        print("\nPROBLEMY:")
        for p in problemy:
            print(f"  {p}")
        return 1

    print("\nNO DIFFERENCES - Photoshop reads both files the same way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
