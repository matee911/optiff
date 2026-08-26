"""End-to-end CLI tests, driven through subprocess."""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from optiff.cli import main
from optiff.formatters.optimize import _duration

SECTIONS = [
    "SIZE TREE",
    "EMBEDDED METADATA",
    "PHOTOSHOP IMAGESOURCEDATA",
    "UNACCOUNTED PHYSICAL AREAS",
    "TIFF STRUCTURE",
    "COMPRESSION / IMAGE DATA",
]


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "optiff", *args],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=Path(__file__).resolve().parents[2],
        check=False,
    )


def section(output: str, name: str) -> list[str]:
    """The lines of the named section, without its frame."""
    lines = output.splitlines()
    start = next(index for index, line in enumerate(lines) if line.strip() == name)

    body: list[str] = []

    for line in lines[start + 2 :]:
        if line.startswith("="):
            break
        body.append(line)

    return body


# ============================================================================
# RUNNING IT
# ============================================================================


def test_exits_cleanly_on_valid_tiff(synthetic_psd_tiff: Path):
    # Act
    result = run("analyze", str(synthetic_psd_tiff))

    # Assert
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.rstrip().endswith("=" * 80)


def test_prints_all_sections_in_order(synthetic_psd_tiff: Path):
    # Act
    result = run("analyze", str(synthetic_psd_tiff))

    # Assert
    positions = [result.stdout.index(name) for name in SECTIONS]
    assert positions == sorted(positions)


def test_missing_file_fails_without_traceback(tmp_path: Path):
    # Act
    result = run("analyze", str(tmp_path / "no-such-file.tif"))

    # Assert
    assert result.returncode != 0
    assert "File not found" in result.stderr
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout


def test_non_tiff_fails_without_traceback(tmp_path: Path):
    # Arrange
    path = tmp_path / "not-a-tiff.tif"
    path.write_bytes(b"not a tiff at all" * 20)

    # Act
    result = run("analyze", str(path))

    # Assert
    assert result.returncode != 0
    assert "Traceback" not in result.stderr


def test_version_flag():
    # Act
    result = run("--version")

    # Assert
    assert result.returncode == 0
    assert re.match(r"optiff \d+\.\d+", result.stdout.strip())


def test_main_is_callable_in_process(synthetic_psd_tiff: Path, capsys):
    # Act
    code = main(["analyze", str(synthetic_psd_tiff)])
    captured = capsys.readouterr()

    # Assert
    assert code == 0
    assert "SIZE TREE" in captured.out
    assert captured.err == ""


# ============================================================================
# REPORT CONTENT
# ============================================================================


def test_size_tree_glyphs_are_consistent(synthetic_psd_tiff: Path):
    # Arrange - regresja Bug 6
    result = run("analyze", str(synthetic_psd_tiff))

    # Act
    body = [line for line in section(result.stdout, "SIZE TREE") if line.strip()]
    nodes = [line for line in body if line.startswith(("├──", "└──"))]

    # Assert
    assert sum(line.startswith("└──") for line in nodes) == 1
    assert nodes[-1].startswith("└──")

    last = max(index for index, line in enumerate(body) if line.startswith("└──"))
    assert not any(line.startswith("├──") for line in body[last + 1 :])


def test_metadata_sizes_are_not_zero(synthetic_psd_tiff: Path):
    # Arrange - regresja Bug 9
    result = run("analyze", str(synthetic_psd_tiff))

    # Act
    body = section(result.stdout, "EMBEDDED METADATA / CONTENT")
    xmp = next(line for line in body if line.startswith("XMP:"))

    # Assert
    assert "0.00 B" not in xmp


def test_percentages_do_not_exceed_one_hundred(synthetic_psd_tiff: Path):
    # Act
    result = run("analyze", str(synthetic_psd_tiff))

    # Assert
    percentages = [
        float(value)
        for value in re.findall(
            r"(\d+\.\d\d)%", "\n".join(section(result.stdout, "SIZE TREE"))
        )
    ]
    assert percentages
    assert sum(percentages) <= 100.01


def test_photoshop_blocks_are_listed(synthetic_psd_tiff: Path):
    # Act
    result = run("analyze", str(synthetic_psd_tiff))
    body = "\n".join(section(result.stdout, "PHOTOSHOP IMAGESOURCEDATA"))

    # Assert - logical keys and resolved descriptions (Bug 4 regression)
    assert "Lr16" in body
    assert "Layers (16-bit)" in body
    assert "Content Authenticity (C2PA)" in body


def test_tiff_without_photoshop_reports_not_detected(synthetic_tiff: Path):
    # Act
    result = run("analyze", str(synthetic_tiff))

    # Assert
    assert result.returncode == 0
    assert "NOT DETECTED" in result.stdout


@pytest.mark.slow
def test_report_on_real_file(sample_tiff: Path):
    # Act
    result = run("analyze", str(sample_tiff))

    # Assert
    assert result.returncode == 0
    assert result.stderr == ""

    tree = "\n".join(section(result.stdout, "SIZE TREE"))
    assert "XMP" in tree
    assert "Photoshop Image Resources" in tree

    blocks = "\n".join(section(result.stdout, "PHOTOSHOP IMAGESOURCEDATA"))
    assert "Parsed blocks:   7" in blocks


# ============================================================================
# PIPES
# ============================================================================


def run_piped(command: str) -> subprocess.CompletedProcess[str]:
    """
    Runs `command` under bash with `pipefail`, so the returncode reflects
    the FIRST failing stage (optiff), not just the last one (head/grep).
    Plain `sh -c "a | b"` (dash on most Linux distros has no `pipefail`)
    would report `b`'s exit code even if `a` crashed - exactly the failure
    mode this test exists to catch, so it must not silently pass anyway.
    """
    return subprocess.run(
        ["bash", "-c", f"set -o pipefail; {command}"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=Path(__file__).resolve().parents[2],
        check=False,
    )


def test_pipes_into_head_without_traceback(synthetic_psd_tiff: Path):
    # Act - `head -3` closes its end of the pipe before optiff finishes
    # writing, which raises BrokenPipeError on the next print()
    quoted = shlex.quote(str(synthetic_psd_tiff))
    result = run_piped(f"{sys.executable} -m optiff analyze {quoted} | head -3")

    # Assert
    assert result.returncode == 0
    assert "Traceback" not in result.stderr


def test_pipes_into_grep_without_traceback(synthetic_psd_tiff: Path):
    # Act
    quoted = shlex.quote(str(synthetic_psd_tiff))
    result = run_piped(f"{sys.executable} -m optiff analyze {quoted} | grep -q LAYERS")

    # Assert
    assert result.returncode == 0
    assert "Traceback" not in result.stderr


def test_main_survives_broken_pipe(monkeypatch, synthetic_psd_tiff: Path):
    # Arrange
    import optiff.cli as cli_module

    def _raise(argv):
        raise BrokenPipeError

    monkeypatch.setattr(cli_module, "_main", _raise)
    monkeypatch.setattr(cli_module.os, "open", lambda *a, **k: -1)
    monkeypatch.setattr(cli_module.os, "dup2", lambda *a, **k: None)
    monkeypatch.setattr(cli_module.os, "close", lambda *a, **k: None)

    # Act
    code = main(["analyze", str(synthetic_psd_tiff)])

    # Assert
    assert code == 0


def test_duration_formatting():
    # Arrange / Act / Assert - sekundy, minuty i godziny czytelnie
    assert _duration(0.4) == "0.4 s"
    assert _duration(59.9) == "59.9 s"
    assert _duration(60.0) == "1 min 00 s"
    assert _duration(94.0) == "1 min 34 s"
    assert _duration(3725.0) == "1 h 02 min"
