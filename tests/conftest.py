"""
Fixtures for the integration and e2e tests.

By default the tests run on synthetic TIFFs built on the fly, so `pytest`
needs none of the multi-gigabyte production files. Tests against real files
are marked `slow` / `bigfile` and are deselected by default (see `addopts`
in pyproject.toml).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
import tifffile

from tests.sample_files import CASES, write
from tests.unit.builders import psd_container
from tests.unit.test_psd_links import link_record

PHOTOSHOP_TAG = 37724
XMP_TAG = 700

XMP_SAMPLE = (
    b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
    b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'</rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--tiff-dir",
        action="store",
        default=os.environ.get("TIFF_TEST_DIR"),
        help="Directory holding real TIFF files (defaults to env TIFF_TEST_DIR).",
    )


@pytest.fixture(scope="session")
def tiff_dir(request: pytest.FixtureRequest) -> Path:
    value = request.config.getoption("--tiff-dir")

    if not value:
        pytest.skip("No --tiff-dir / TIFF_TEST_DIR.")

    path = Path(value)

    if not path.is_dir():
        pytest.skip(f"Directory does not exist: {path}")

    return path


@pytest.fixture(scope="session")
def sample_tiff(tiff_dir: Path) -> Path:
    path = tiff_dir / "test1.tif"

    if not path.exists():
        pytest.skip(f"Missing {path}")

    return path


@pytest.fixture(scope="session")
def sample_named(tiff_dir: Path):
    """
    A test file selected by role rather than by name.

    Private file names must not reach the repository, so the role -> name
    mapping lives in `tests/samples.local.json`, which git ignores. Copy
    `tests/samples.example.json` as a template.
    """
    mapping_path = Path(__file__).parent / "samples.local.json"

    if not mapping_path.is_file():
        pytest.skip("No tests/samples.local.json (template: samples.example.json)")

    mapping = json.loads(mapping_path.read_text())

    def take(role: str) -> Path:
        name = mapping.get(role)

        if not name:
            pytest.skip(f"No entry '{role}' in samples.local.json")

        path = tiff_dir / name

        if not path.is_file():
            pytest.skip(f"No file for role '{role}': {path}")

        return path

    return take


def _image(height: int = 64, width: int = 48) -> np.ndarray:
    rng = np.random.default_rng(20250823)

    return rng.integers(0, 65535, size=(height, width, 3), dtype=np.uint16)


def _write(path: Path, *, extratags=(), **kwargs) -> Path:
    tifffile.imwrite(
        path,
        _image(),
        photometric="rgb",
        extratags=list(extratags),
        **kwargs,
    )

    return path


@pytest.fixture(scope="session")
def synthetic_tiff(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A minimal TIFF with XMP and no Photoshop block."""
    path = tmp_path_factory.mktemp("tiff") / "plain.tif"

    return _write(
        path,
        extratags=[(XMP_TAG, 1, len(XMP_SAMPLE), XMP_SAMPLE, True)],
    )


@pytest.fixture(scope="session")
def psd_blob() -> bytes:
    """Tag 37724 content built with the same builder the unit tests use."""
    return psd_container(
        ("Lr16", b"layers" * 64),
        ("LMsk", b"\x00" * 14),
        ("Pat2", b""),
        ("CAI ", b"content-credentials"),
        ("cinf", b'{"compositor": "test"}'),
        byte_order="<",
    )


@pytest.fixture(scope="session")
def synthetic_psd_tiff(
    tmp_path_factory: pytest.TempPathFactory,
    psd_blob: bytes,
) -> Path:
    """A TIFF with a realistic Photoshop ImageSourceData block and XMP."""
    path = tmp_path_factory.mktemp("tiff") / "psd.tif"

    return _write(
        path,
        extratags=[
            (XMP_TAG, 1, len(XMP_SAMPLE), XMP_SAMPLE, True),
            (PHOTOSHOP_TAG, 7, len(psd_blob), psd_blob, True),
        ],
    )


@pytest.fixture(scope="session")
def psd_blob_with_linked_file() -> bytes:
    """Like `psd_blob`, plus an `lnk2` block with one embedded PSB."""
    embedded_psb = (
        b"8BPS"
        + (2).to_bytes(2, "big")
        + bytes(6)
        + (3).to_bytes(2, "big")
        + (600).to_bytes(4, "big")
        + (800).to_bytes(4, "big")
        + (16).to_bytes(2, "big")
        + (3).to_bytes(2, "big")
        + (0).to_bytes(4, "big")  # Color Mode Data
        + (4).to_bytes(4, "big")
        + b"abcd"  # Image Resources
        + (0).to_bytes(8, "big")  # Layer and Mask Information
        + (1).to_bytes(2, "big")  # Image Data compression (RLE)
    )

    record = link_record(
        name="embedded.psb", size=len(embedded_psb), payload=embedded_psb
    )

    return psd_container(
        ("Lr16", b"layers" * 64),
        ("LMsk", b"\x00" * 14),
        ("Pat2", b""),
        ("CAI ", b"content-credentials"),
        ("cinf", b'{"compositor": "test"}'),
        ("lnk2", record),
        byte_order="<",
    )


@pytest.fixture(scope="session")
def synthetic_psd_tiff_with_linked_file(
    tmp_path_factory: pytest.TempPathFactory,
    psd_blob_with_linked_file: bytes,
) -> Path:
    path = tmp_path_factory.mktemp("tiff") / "psd-linked.tif"

    return _write(
        path,
        extratags=[
            (XMP_TAG, 1, len(XMP_SAMPLE), XMP_SAMPLE, True),
            (
                PHOTOSHOP_TAG,
                7,
                len(psd_blob_with_linked_file),
                psd_blob_with_linked_file,
                True,
            ),
        ],
    )


@pytest.fixture(scope="session")
def synthetic_striped_tiff(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A TIFF written in several strips, forcing exact ranges instead of a hull."""
    path = tmp_path_factory.mktemp("tiff") / "striped.tif"

    return _write(path, rowsperstrip=8)


@pytest.fixture
def sample_file(tmp_path: Path):
    """
    Generates one file from the case catalogue and returns its path.

    The cases were found in real client files, which must never enter the
    repository, so what is kept is the recipe rather than the file. See
    `tests/sample_files.py` for what each case reproduces.

        def test_something(sample_file):
            path = sample_file("adjustment-mask")
    """

    def make(name: str) -> Path:
        return write(name, tmp_path)

    return make


@pytest.fixture(scope="session")
def every_sample(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Every case, generated once per session, for tests that only read."""
    directory = tmp_path_factory.mktemp("samples")

    return {name: write(name, directory) for name in sorted(CASES)}
