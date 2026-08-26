# CLI Command Pattern + Reporting/Formatting Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `optiff`'s flag-driven single-command CLI with a Command-pattern dispatcher over `analyze`/`optimize` subcommands, and split every command's data-producing path from its text-rendering path so a future output format (or subcommand) never has to touch `cli.py`'s dispatch logic.

**Architecture:** `optiff/commands/{base,analyze,optimize}.py` define one `Command` per subcommand; `cli.py` becomes a thin dispatcher (argparse subparsers → `command.run(args)` → pick formatter by result type → print → exit code). `optiff/analysis.py` (new) and `optiff/optimize.py` (already like this) hold pure data collectors; `optiff/formatters/{analyze,optimize}.py` hold pure `result -> str` renderers. `report.py`/`Reporter` (today's tangled read+print class) is retired once its replacement is proven byte-identical.

**Tech Stack:** Python 3.13, argparse, pytest (+ `pytest-cov`), tifffile, existing project modules (`document.py`, `domain.py`, `metadata.py`, `provenance.py`, `psd_layers.py`, `psd_links.py`, `psd_file.py`, `storage.py`, `units.py`, `optimize.py`).

**Spec:** `docs/superpowers/specs/2026-08-26-cli-command-pattern-design.md`

## Global Constraints

- No new output format (`--json`, etc.) in this plan — only the split that enables one later.
- Exit codes keep their exact values/meaning: `EXIT_OK=0`, `EXIT_BAD_FILE=2`, `EXIT_VERIFY_FAILED=3`.
- Every printed report byte must match today's output exactly (verified by parity/golden tests), except the one deliberate, documented API change: `optiff.analyze(path)` (package-level) now returns an `AnalyzeReport` instead of printing and returning `None`.
- New module is `optiff/analysis.py`, never `optiff/analyze.py` (would collide with the `analyze` name `optiff/__init__.py` re-exports).
- New CLI surface: `optiff analyze FILE`, `optiff optimize FILE --out OUT [--level N] [--image-data] [--zip-fallback] [--no-verify]`. This breaks the old `optiff FILE [--optimize OUT]` surface deliberately.
- Follow existing repo conventions: `from __future__ import annotations` at the top of every module, Google-less plain docstrings only where the *why* is non-obvious, AAA-structured tests with `# Arrange` / `# Act` / `# Assert` comments where the existing test files use them.

---

### Task 1: Coverage tooling in CI (independent of everything else)

**Files:**
- Modify: `pyproject.toml:19-25` (dev deps), add `[tool.coverage.run]` / `[tool.coverage.report]`
- Modify: `.github/workflows/ci.yml` (the `- run: pytest` step)

**Interfaces:** None — this task touches no Python source.

- [ ] **Step 1: Add `pytest-cov` to dev dependencies**

In `pyproject.toml`, change:

```toml
dev = [
    "pytest>=8",
    "ruff>=0.16",
    "pyrefly>=1.2",
    "pre-commit>=4",
]
```

to:

```toml
dev = [
    "pytest>=8",
    "pytest-cov>=5",
    "ruff>=0.16",
    "pyrefly>=1.2",
    "pre-commit>=4",
]
```

- [ ] **Step 2: Add coverage configuration**

Append to `pyproject.toml` (after the `[tool.pytest.ini_options]` block, before `[tool.ruff]`):

```toml
[tool.coverage.run]
source = ["optiff"]

[tool.coverage.report]
show_missing = true
```

- [ ] **Step 3: Install the new dependency locally**

Run: `pip install -e ".[dev]"`
Expected: installs `pytest-cov` alongside the existing dev tools.

- [ ] **Step 4: Verify coverage runs locally**

Run: `pytest --cov=optiff --cov-report=term-missing`
Expected: the normal test run, followed by a per-file coverage table. No test failures (this step adds no test logic, only tooling).

- [ ] **Step 5: Update CI to collect and report coverage**

In `.github/workflows/ci.yml`, replace:

```yaml
      # The suite needs no sample files - it generates them.
      - run: pytest
```

with:

```yaml
      # The suite needs no sample files - it generates them.
      - run: pytest --cov=optiff --cov-report=term-missing --cov-report=xml

      - uses: codecov/codecov-action@v5
        with:
          files: ./coverage.xml
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .github/workflows/ci.yml
git commit -m "Add pytest-cov and upload coverage to Codecov in CI"
```

**Note for the human running this plan:** Codecov's tokenless upload works for public GitHub repos, but the repository still needs to be activated on codecov.io (sign in with the GitHub account, add the repo) before the first upload is accepted. This is a one-time manual step outside this plan's scope — do it whenever convenient; CI will simply show a failed/soft-failed upload step until then.

---

### Task 2: `Command` protocol + subcommand dispatch (wraps today's logic unchanged)

This task only changes *how a command is selected and invoked* — `AnalyzeCommand.run()` still calls today's printing `analyze()` from `cli.py`, `OptimizeCommand.run()` still calls today's `optimize()`. The reporting/formatting split happens in later tasks. This keeps the risky, user-visible UX change (breaking CLI surface) isolated from the deeper data/format refactor.

**Files:**
- Create: `optiff/commands/__init__.py` (empty)
- Create: `optiff/commands/base.py`
- Create: `optiff/commands/analyze.py`
- Create: `optiff/commands/optimize.py`
- Modify: `optiff/cli.py` (replace `build_parser()` and `main()`; keep `analyze()`, `print_optimize()`, `_duration()` as they are for now)
- Modify: `tests/e2e/test_cli.py` (every invocation needs the `analyze` subcommand token)
- Test: `tests/unit/test_commands.py` (new)

**Interfaces:**
- Produces: `Command` protocol (`name: str`, `add_arguments(parser)`, `run(args) -> object`); `AnalyzeCommand`, `OptimizeCommand` instances; `COMMANDS: tuple[Command, ...]` registry in `cli.py`.
- Consumes: `optiff.cli.analyze` (existing, unchanged), `optiff.optimize.optimize`/`OptimizeResult`/`OptimizeError` (existing, unchanged).

- [ ] **Step 1: Write `optiff/commands/__init__.py`**

Empty file (the directory is a package; commands are imported explicitly, not auto-discovered).

- [ ] **Step 2: Write the failing unit test for the `Command` protocol shape**

`tests/unit/test_commands.py`:

```python
"""The Command protocol and the two concrete commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from optiff.commands.analyze import AnalyzeCommand
from optiff.commands.optimize import OptimizeCommand
from optiff.optimize import OptimizeResult


def test_analyze_command_has_a_name():
    assert AnalyzeCommand().name == "analyze"


def test_optimize_command_has_a_name():
    assert OptimizeCommand().name == "optimize"


def test_optimize_command_add_arguments_registers_path_and_out():
    # Arrange
    parser = argparse.ArgumentParser()

    # Act
    OptimizeCommand().add_arguments(parser)
    args = parser.parse_args(["in.tif", "--out", "out.tif"])

    # Assert
    assert args.path == Path("in.tif")
    assert args.out == Path("out.tif")
    assert args.level == 4
    assert args.image_data is False
    assert args.zip_fallback is False
    assert args.no_verify is False


def test_optimize_command_run_returns_optimize_result(tmp_path: Path, synthetic_psd_tiff: Path):
    # Arrange
    parser = argparse.ArgumentParser()
    OptimizeCommand().add_arguments(parser)
    args = parser.parse_args(
        [str(synthetic_psd_tiff), "--out", str(tmp_path / "out.tif")]
    )

    # Act
    result = OptimizeCommand().run(args)

    # Assert
    assert isinstance(result, OptimizeResult)


def test_analyze_command_add_arguments_registers_path():
    # Arrange
    parser = argparse.ArgumentParser()

    # Act
    AnalyzeCommand().add_arguments(parser)
    args = parser.parse_args(["in.tif"])

    # Assert
    assert args.path == Path("in.tif")
```

- [ ] **Step 3: Run it to confirm it fails**

Run: `pytest tests/unit/test_commands.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optiff.commands'`.

- [ ] **Step 4: Write `optiff/commands/base.py`**

```python
"""The interface every CLI subcommand implements."""

from __future__ import annotations

import argparse
from typing import Protocol


class Command(Protocol):
    """One CLI subcommand: how to build its parser, and how to run it."""

    name: str

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Registers this command's arguments on its own subparser."""
        ...

    def run(self, args: argparse.Namespace) -> object:
        """Executes the command and returns its result object."""
        ...
```

- [ ] **Step 5: Write `optiff/commands/analyze.py`**

```python
"""The `analyze` subcommand."""

from __future__ import annotations

import argparse
from pathlib import Path

from optiff.cli import analyze as _print_analysis


class AnalyzeCommand:
    name = "analyze"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("path", type=Path)

    def run(self, args: argparse.Namespace) -> None:
        # Task 7 replaces this with `optiff.analysis.analyze(args.path)`,
        # returning an `AnalyzeReport` instead of printing directly.
        _print_analysis(args.path)
        return None
```

- [ ] **Step 6: Write `optiff/commands/optimize.py`**

```python
"""The `optimize` subcommand."""

from __future__ import annotations

import argparse
from pathlib import Path

from optiff.optimize import OptimizeResult, optimize


class OptimizeCommand:
    name = "optimize"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("path", type=Path)

        parser.add_argument(
            "--out",
            metavar="FILE",
            type=Path,
            required=True,
            help=(
                "write an optimized copy to the given path; "
                "the original is left untouched"
            ),
        )

        parser.add_argument(
            "--level",
            type=int,
            default=4,
            choices=range(1, 10),
            metavar="1-9",
            help="deflate level (default 4)",
        )

        parser.add_argument(
            "--image-data",
            action="store_true",
            help=(
                "also pack the flattened pixels (Adobe Deflate, no predictor); "
                "off by default, because that is the only part Capture One and "
                "Affinity read, and reaching the pixels gets slower"
            ),
        )

        parser.add_argument(
            "--zip-fallback",
            action="store_true",
            help=(
                "also pack channels with no readable geometry using plain ZIP "
                "(adjustment layer masks carry a 0x0 rectangle); off by default, "
                "because writing method 2 is not yet widely verified"
            ),
        )

        parser.add_argument(
            "--no-verify",
            action="store_true",
            help=(
                "skip the per-channel SHA256 comparison after writing "
                "(faster, but with no proof of losslessness)"
            ),
        )

    def run(self, args: argparse.Namespace) -> OptimizeResult:
        return optimize(
            args.path,
            args.out,
            level=args.level,
            verify=not args.no_verify,
            image_data=args.image_data,
            zip_fallback=args.zip_fallback,
        )
```

- [ ] **Step 7: Run the unit tests to confirm they pass**

Run: `pytest tests/unit/test_commands.py -v`
Expected: PASS (4 tests). Uses the `synthetic_psd_tiff` fixture from `tests/conftest.py`, already available to any test under `tests/`.

- [ ] **Step 8: Rewrite `cli.py`'s parser and dispatcher**

Modify `optiff/cli.py`. Replace the whole `build_parser()` function (today `cli.py:154-215`) and `main()` (today `cli.py:218-250`) with:

```python
from optiff.commands.analyze import AnalyzeCommand
from optiff.commands.optimize import OptimizeCommand
from optiff.optimize import OptimizeResult

COMMANDS: tuple[object, ...] = (AnalyzeCommand(), OptimizeCommand())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="optiff",
        description="Analyses the physical byte layout of a TIFF and its metadata.",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command_name", required=True)

    for command in COMMANDS:
        subparser = subparsers.add_parser(command.name)
        command.add_arguments(subparser)
        subparser.set_defaults(command=command)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.path.is_file():
        print(f"File not found: {args.path}", file=sys.stderr)
        return EXIT_BAD_FILE

    try:
        result = args.command.run(args)
    except tifffile.TiffFileError as error:
        print(f"Not a readable TIFF: {args.path} ({error})", file=sys.stderr)
        return EXIT_BAD_FILE
    except OptimizeError as error:
        print(f"Cannot optimize: {error}", file=sys.stderr)
        return EXIT_BAD_FILE

    if isinstance(result, OptimizeResult):
        return print_optimize(result)

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
```

Keep everything else in `cli.py` (`analyze()`, `_duration()`, `print_optimize()`, the `EXIT_*` constants, the existing imports of `TiffDocument`, `OptimizeError`, `OptimizeResult`, `optimize`, `TiffPhotoshopAnalyzer`, `Reporter`, `WIDTH`, `format_size`) exactly as they are — this task does not touch them.

Note the import of `Path` is already present in `cli.py` (used by `analyze(path: Path)`); no new import needed there.

- [ ] **Step 9: Update `tests/e2e/test_cli.py` for the new subcommand syntax**

Every call that invoked the old default (implicit analyze) now needs the `"analyze"` token first. Replace the full content of `tests/e2e/test_cli.py` with:

```python
"""End-to-end CLI tests, driven through subprocess."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from optiff.cli import _duration, main

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


def test_duration_formatting():
    # Arrange / Act / Assert - sekundy, minuty i godziny czytelnie
    assert _duration(0.4) == "0.4 s"
    assert _duration(59.9) == "59.9 s"
    assert _duration(60.0) == "1 min 00 s"
    assert _duration(94.0) == "1 min 34 s"
    assert _duration(3725.0) == "1 h 02 min"
```

(This is the same file as before, with `"analyze"` inserted as the first `run()`/`main()` argument everywhere a path used to be given alone. `test_duration_formatting` and its import of `_duration` from `optiff.cli` are unchanged here — Task 4 moves `_duration` and updates this import.)

- [ ] **Step 10: Run the full e2e suite**

Run: `pytest tests/e2e/test_cli.py -v`
Expected: PASS (all tests, including the newly-worded `run("analyze", ...)` calls).

- [ ] **Step 11: Run the full test suite to catch anything else broken by the CLI surface change**

Run: `pytest`
Expected: PASS. (No other test file invokes the CLI's old `--optimize` flag or positional-only syntax — confirmed by grep before writing this plan.)

- [ ] **Step 12: Commit**

```bash
git add optiff/commands optiff/cli.py tests/e2e/test_cli.py tests/unit/test_commands.py
git commit -m "Switch CLI to Command-pattern subcommands (analyze, optimize)"
```

---

### Task 3: Pipeability — `BrokenPipeError` handling + a real-pipe test

**Files:**
- Modify: `optiff/cli.py` (wrap `main()`'s body)
- Test: `tests/e2e/test_cli.py` (add pipe tests)

**Interfaces:**
- Consumes: `optiff.cli.main` (from Task 2, unchanged signature).
- Produces: nothing new consumed by later tasks — `main`'s signature and return type are unchanged.

- [ ] **Step 1: Write the failing e2e test**

Add to `tests/e2e/test_cli.py` (after `test_report_on_real_file`, before `test_duration_formatting`):

```python
# ============================================================================
# PIPES
# ============================================================================


def run_shell(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=Path(__file__).resolve().parents[2],
        check=False,
    )


def test_pipes_into_head_without_traceback(synthetic_psd_tiff: Path):
    # Act - `head -3` closes its end of the pipe before optiff finishes
    # writing, which raises BrokenPipeError on the next print()
    result = run_shell(
        f'{sys.executable} -m optiff analyze "{synthetic_psd_tiff}" | head -3'
    )

    # Assert
    assert result.returncode == 0
    assert "Traceback" not in result.stderr


def test_pipes_into_grep_without_traceback(synthetic_psd_tiff: Path):
    # Act
    result = run_shell(
        f'{sys.executable} -m optiff analyze "{synthetic_psd_tiff}" | grep -q LAYERS'
    )

    # Assert
    assert result.returncode == 0
    assert "Traceback" not in result.stderr
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest tests/e2e/test_cli.py -k pipes -v`
Expected: FAIL — `test_pipes_into_head_without_traceback` shows `Traceback (most recent call last):` and `BrokenPipeError` in `result.stderr`.

- [ ] **Step 3: Wrap `main()` to handle `BrokenPipeError`**

In `optiff/cli.py`, add `import os` to the existing imports, then rename the current `main()` body to a private `_main()` and add a thin public `main()` around it:

```python
def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except BrokenPipeError:
        # Python still holds buffered stdout; redirect it to devnull so the
        # interpreter's exit-time flush doesn't raise a second BrokenPipeError.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return EXIT_OK


def _main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.path.is_file():
        print(f"File not found: {args.path}", file=sys.stderr)
        return EXIT_BAD_FILE

    try:
        result = args.command.run(args)
    except tifffile.TiffFileError as error:
        print(f"Not a readable TIFF: {args.path} ({error})", file=sys.stderr)
        return EXIT_BAD_FILE
    except OptimizeError as error:
        print(f"Cannot optimize: {error}", file=sys.stderr)
        return EXIT_BAD_FILE

    if isinstance(result, OptimizeResult):
        return print_optimize(result)

    return EXIT_OK
```

(This is the same `main()` body from Task 2, Step 8, renamed to `_main`, plus the new wrapping `main()`.)

- [ ] **Step 4: Run the pipe tests again**

Run: `pytest tests/e2e/test_cli.py -k pipes -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full e2e suite to make sure nothing else regressed**

Run: `pytest tests/e2e/test_cli.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add optiff/cli.py tests/e2e/test_cli.py
git commit -m "Handle BrokenPipeError so optiff pipes cleanly into head/grep/etc."
```

---

### Task 4: Split `OptimizeResult` rendering out of `cli.py`

**Files:**
- Create: `optiff/formatters/__init__.py` (empty)
- Create: `optiff/formatters/optimize.py`
- Modify: `optiff/cli.py` (remove `print_optimize()` and `_duration()`; dispatcher calls the new formatter and owns the exit-code/stderr logic)
- Modify: `tests/e2e/test_cli.py` (`_duration` import moves)
- Test: `tests/unit/test_formatters_optimize.py` (new)

**Interfaces:**
- Consumes: `optiff.optimize.OptimizeResult` (existing, unchanged).
- Produces: `render_optimize(result: OptimizeResult) -> str`, `_duration(seconds: float) -> str`, both in `optiff/formatters/optimize.py`.

- [ ] **Step 1: Write `optiff/formatters/__init__.py`**

Empty file.

- [ ] **Step 2: Write the failing unit test**

`tests/unit/test_formatters_optimize.py`:

```python
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
```

- [ ] **Step 3: Run it to confirm it fails**

Run: `pytest tests/unit/test_formatters_optimize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optiff.formatters'`.

- [ ] **Step 4: Write `optiff/formatters/optimize.py`**

This is `cli.py`'s current `_duration()` (today `cli.py:35-52`) and `print_optimize()` (today `cli.py:55-151`), converted from printing to building a string, with the exit-code and stderr-problem-reporting logic removed (that moves to `cli.py`'s dispatcher in Step 5 — a formatter never decides exit codes or writes to stderr).

```python
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

    def p(*args: object, **kwargs: object) -> None:
        print(*args, file=buf, **kwargs)

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
```

- [ ] **Step 5: Update `cli.py`'s dispatcher to use the formatter and own the exit code / stderr**

Remove `_duration()` and `print_optimize()` from `optiff/cli.py` entirely. Remove the now-unused `from optiff.units import format_size` and `WIDTH` import (`from optiff.report import WIDTH, Reporter` stays for `Reporter`/`WIDTH` still used by `analyze()` — only drop `format_size` if nothing else in `cli.py` uses it; check with `grep format_size optiff/cli.py` after editing). Add `from optiff.formatters.optimize import render_optimize`. Change the `isinstance(result, OptimizeResult)` branch in `_main()` (written in Task 3, Step 3) to:

```python
    if isinstance(result, OptimizeResult):
        sys.stdout.write(render_optimize(result))

        if result.comparison is not None and not result.comparison.ok:
            for problem in result.comparison.problems[:10]:
                print(f"  {problem}", file=sys.stderr)
            return EXIT_VERIFY_FAILED

        return EXIT_OK

    return EXIT_OK
```

Use `sys.stdout.write(...)`, not `print(...)` — `render_optimize()`'s returned string already ends in `"\n"` (from its last internal `p("=" * WIDTH)`), so `print()` would add a second, unwanted blank line.

- [ ] **Step 6: Update `tests/e2e/test_cli.py`'s `_duration` import**

Change:

```python
from optiff.cli import _duration, main
```

to:

```python
from optiff.cli import main
from optiff.formatters.optimize import _duration
```

- [ ] **Step 7: Run the new formatter unit tests**

Run: `pytest tests/unit/test_formatters_optimize.py -v`
Expected: PASS (4 tests).

- [ ] **Step 8: Run the full suite**

Run: `pytest`
Expected: PASS. In particular re-check `tests/integration/test_optimize.py` (calls `optimize()` directly, untouched by this task) and any e2e test exercising `--optimize`/`optimize` output (none exist today, per the grep done during planning — if one now fails, it means an assumption here was wrong; stop and investigate rather than adjusting the test to match).

- [ ] **Step 9: Commit**

```bash
git add optiff/formatters optiff/cli.py tests/e2e/test_cli.py tests/unit/test_formatters_optimize.py
git commit -m "Split OptimizeResult rendering into a pure formatter"
```

---

### Task 5: `AnalyzeReport` data model + `analyze()` collector

This task only adds `optiff/analysis.py`. It does not touch `report.py`, `Reporter`, or `cli.py` — both old and new analysis code exist side by side until Task 7.

**Files:**
- Create: `optiff/analysis.py`
- Test: `tests/integration/test_analysis.py` (new)

**Interfaces:**
- Consumes: `optiff.document.TiffDocument`, `optiff.psd_analyzer.TiffPhotoshopAnalyzer`, `optiff.metadata.MetadataAnalyzer`, `optiff.provenance.read_provenance`, `optiff.psd_layers.read_layer_stack`, `optiff.psd_links.read_linked_files`, `optiff.psd_file.parse_document`/`DocumentError`/`EmbeddedDocument`, `optiff.storage.PhysicalStorageAnalyzer`/`PhysicalClassifier` (all existing, unchanged).
- Produces: `AnalyzeReport`, `ProvenanceSection`, `LayersSection`, `LinkedFilesSection`, `EmbeddedDocumentSection`, `GapClassification`, `TagInfo` dataclasses; `analyze(path: Path) -> AnalyzeReport`. All consumed by Task 6's `render_analyze()`.

- [ ] **Step 1: Write the failing integration test**

`tests/integration/test_analysis.py`:

```python
"""AnalyzeReport: the data analyze() collects, with no printing involved."""

from __future__ import annotations

from pathlib import Path

from optiff.analysis import analyze


def test_reports_basic_file_properties(synthetic_tiff: Path):
    # Act
    report = analyze(synthetic_tiff)

    # Assert
    assert report.path == synthetic_tiff
    assert report.file_size == synthetic_tiff.stat().st_size
    assert report.byte_order in ("little-endian", "big-endian")
    assert report.image.width > 0


def test_no_photoshop_tag_reports_no_tag_states(synthetic_tiff: Path):
    # Act
    report = analyze(synthetic_tiff)

    # Assert
    assert report.photoshop.found is False
    assert report.provenance.state in ("no-blocks", "no-tag")
    assert report.layers.state == "no-tag"
    assert report.layers.stack is None
    assert report.linked_files is None


def test_photoshop_tag_reports_layers_and_provenance(synthetic_psd_tiff: Path):
    # Act
    report = analyze(synthetic_psd_tiff)

    # Assert
    assert report.photoshop.found is True
    assert report.layers.state == "found"
    assert report.layers.stack is not None
    assert report.layers.stack.channel_bytes > 0
    assert report.provenance.state == "found"
    assert report.provenance.report is not None


def test_metadata_matches_metadata_analyzer(synthetic_psd_tiff: Path):
    # Arrange
    from optiff.document import TiffDocument
    from optiff.metadata import MetadataAnalyzer

    with TiffDocument(synthetic_psd_tiff) as document:
        expected = MetadataAnalyzer(document).report()

    # Act
    report = analyze(synthetic_psd_tiff)

    # Assert
    assert report.metadata == expected


def test_structure_lists_every_tiff_tag(synthetic_tiff: Path):
    # Arrange
    from optiff.document import TiffDocument

    with TiffDocument(synthetic_tiff) as document:
        expected_count = len(document.first_page.tags)

    # Act
    report = analyze(synthetic_tiff)

    # Assert
    assert len(report.structure) == expected_count
    assert all(item.code > 0 for item in report.structure)
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest tests/integration/test_analysis.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optiff.analysis'`.

- [ ] **Step 3: Write `optiff/analysis.py`**

This is `Reporter`'s nine `_print_*` methods (`report.py:164-483`), converted from "read and print" to "read and return", collected into one function. Same reads, same order, same `try/finally` around every reader.

```python
"""What `analyze()` collects about a TIFF, with no printing involved."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import tifffile

from optiff.document import TiffDocument
from optiff.domain import DataBlock, ImageInfo, PhysicalRange, PhotoshopAnalysis
from optiff.metadata import MetadataAnalyzer
from optiff.provenance import read_provenance
from optiff.psd_analyzer import TiffPhotoshopAnalyzer
from optiff.psd_file import DocumentError, EmbeddedDocument, parse_document
from optiff.psd_layers import LayerStack, read_layer_stack
from optiff.psd_links import LinkedFile, LinkedFiles, read_linked_files
from optiff.storage import PhysicalClassifier, PhysicalStorageAnalyzer
from pathlib import Path


@dataclass(frozen=True)
class ProvenanceSection:
    """The three states `Reporter._print_provenance` distinguishes today."""

    state: Literal["no-blocks", "no-tag", "found"]
    report: dict[str, str] | None  # set only when state == "found"


@dataclass(frozen=True)
class LayersSection:
    """
    The two "nothing here" states plus the found one.

    A bare `LayerStack | None` cannot tell "no tag 37724 at all" apart from
    "the tag exists but has no Lr16/Lr32/Layr block" - the two messages
    `Reporter._print_layers` prints are different.
    """

    state: Literal["no-tag", "no-section", "found"]
    stack: LayerStack | None  # set only when state == "found"


@dataclass(frozen=True)
class TagInfo:
    code: int
    name: str
    dtype_name: str
    count: int
    size: int


@dataclass(frozen=True)
class GapClassification:
    gap: PhysicalRange
    classification: str


@dataclass(frozen=True)
class EmbeddedDocumentSection:
    """
    One linked smart object's embedded PSD/PSB, recursively.

    `EmbeddedDocument` already carries width/height/channels/depth/
    color_mode_name/compression_name/sections/layers/warnings as properties -
    wrap it instead of re-declaring those fields here.
    """

    name: str
    error: str | None  # DocumentError message, when parse_document failed
    document: EmbeddedDocument | None  # None exactly when error is set


@dataclass(frozen=True)
class LinkedFilesSection:
    linked: LinkedFiles
    embedded: dict[int, EmbeddedDocumentSection]  # keyed by LinkedFile.index


@dataclass(frozen=True)
class AnalyzeReport:
    path: Path
    file_size: int
    is_bigtiff: bool
    byte_order: Literal["little-endian", "big-endian"]
    image: ImageInfo
    size_tree: list[DataBlock]
    metadata: dict[str, str]
    provenance: ProvenanceSection
    photoshop: PhotoshopAnalysis
    layers: LayersSection
    linked_files: LinkedFilesSection | None
    physical_gaps: list[GapClassification]
    structure: list[TagInfo]


def _collect_provenance(
    document: TiffDocument, photoshop: PhotoshopAnalysis
) -> ProvenanceSection:
    if not photoshop.blocks:
        return ProvenanceSection("no-blocks", None)

    reader = document.photoshop_source_reader()

    if reader is None:
        return ProvenanceSection("no-tag", None)

    try:
        provenance = read_provenance(photoshop, reader)
    finally:
        reader.close()

    return ProvenanceSection("found", provenance.report())


def _collect_layers(
    document: TiffDocument, photoshop: PhotoshopAnalysis
) -> LayersSection:
    reader = document.photoshop_source_reader()

    if reader is None:
        return LayersSection("no-tag", None)

    try:
        stack = read_layer_stack(photoshop, reader)
    finally:
        reader.close()

    if stack is None:
        return LayersSection("no-section", None)

    return LayersSection("found", stack)


def _collect_embedded(reader, item: LinkedFile) -> EmbeddedDocumentSection:
    try:
        document = parse_document(reader, item.data_offset, item.size)
    except DocumentError as error:
        return EmbeddedDocumentSection(name=item.name, error=str(error), document=None)

    return EmbeddedDocumentSection(name=item.name, error=None, document=document)


def _collect_linked_files(
    document: TiffDocument, photoshop: PhotoshopAnalysis
) -> LinkedFilesSection | None:
    reader = document.photoshop_source_reader()

    if reader is None:
        return None

    try:
        linked = read_linked_files(photoshop, reader)
    finally:
        reader.close()

    if linked is None or not linked.files:
        return None

    embedded: dict[int, EmbeddedDocumentSection] = {}

    reader = document.photoshop_source_reader()

    try:
        for item in linked.files:
            if reader is not None and item.is_embedded:
                embedded[item.index] = _collect_embedded(reader, item)
    finally:
        if reader is not None:
            reader.close()

    return LinkedFilesSection(linked, embedded)


def _collect_physical_gaps(
    document: TiffDocument, storage: PhysicalStorageAnalyzer
) -> list[GapClassification]:
    classifier = PhysicalClassifier(document.path)

    ordered = sorted(
        storage.unaccounted_ranges(), key=lambda item: item.size, reverse=True
    )

    return [GapClassification(gap, classifier.classify(gap)) for gap in ordered]


def _collect_structure(document: TiffDocument) -> list[TagInfo]:
    return [
        TagInfo(
            code=tag.code,
            name=tag.name,
            dtype_name=tifffile.DATATYPE(tag.dtype).name,
            count=tag.count,
            size=document.tag_value_size(tag),
        )
        for tag in document.first_page.tags.values()
    ]


def analyze(path: Path) -> AnalyzeReport:
    with TiffDocument(path) as document:
        photoshop = TiffPhotoshopAnalyzer().analyze(document)
        storage = PhysicalStorageAnalyzer(document)

        return AnalyzeReport(
            path=document.path,
            file_size=document.file_size,
            is_bigtiff=document.tiff.is_bigtiff,
            byte_order=(
                "little-endian" if document.tiff.byteorder == "<" else "big-endian"
            ),
            image=document.image_info,
            size_tree=storage.referenced_blocks(),
            metadata=MetadataAnalyzer(document).report(),
            provenance=_collect_provenance(document, photoshop),
            photoshop=photoshop,
            layers=_collect_layers(document, photoshop),
            linked_files=_collect_linked_files(document, photoshop),
            physical_gaps=_collect_physical_gaps(document, storage),
            structure=_collect_structure(document),
        )
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/integration/test_analysis.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the full suite**

Run: `pytest`
Expected: PASS (this task added a new, self-contained module; nothing existing imports it yet).

- [ ] **Step 6: Commit**

```bash
git add optiff/analysis.py tests/integration/test_analysis.py
git commit -m "Add AnalyzeReport: analyze()'s data, separated from printing"
```

---

### Task 6: `render_analyze()` formatter + parity proof against `Reporter`

**Files:**
- Create: `optiff/formatters/analyze.py`
- Modify: `tests/unit/test_report.py` → rename to `tests/unit/test_formatters_analyze.py`, update its import
- Test: `tests/integration/test_analyze_parity.py` (new, temporary — deleted in Task 7)
- Test: `tests/golden/analyze_synthetic_tiff.txt`, `tests/golden/analyze_synthetic_psd_tiff.txt` (new, generated, committed)

**Interfaces:**
- Consumes: `optiff.analysis.AnalyzeReport` and its nested dataclasses (Task 5).
- Produces: `render_analyze(report: AnalyzeReport) -> str`, plus `render_size_tree`, `_layer_line`, `_compression_summary` (copied here from `report.py` - report.py keeps its own copies until Task 7 deletes it, so `Reporter` keeps working in the meantime).

- [ ] **Step 1: Move (rename) the size-tree unit test file**

`report.py`'s `render_size_tree` is being duplicated into `formatters/analyze.py` in this task. Its existing unit tests in `tests/unit/test_report.py` test pure text output and don't care which module they import from — rename the file and repoint the import, so there's exactly one test file for this function going forward.

```bash
git mv tests/unit/test_report.py tests/unit/test_formatters_analyze.py
```

In `tests/unit/test_formatters_analyze.py`, change:

```python
from optiff.report import render_size_tree
```

to:

```python
from optiff.formatters.analyze import render_size_tree
```

- [ ] **Step 2: Run it to confirm it now fails (module doesn't exist yet)**

Run: `pytest tests/unit/test_formatters_analyze.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optiff.formatters.analyze'`.

- [ ] **Step 3: Write `optiff/formatters/analyze.py`**

This copies `render_size_tree`, `_block_children`, `_layer_line`, `_compression_summary` verbatim from `report.py` (lines 20-155 today), and adds `render_analyze()` - `Reporter`'s nine `_print_*` methods (`report.py:164-483`), rewritten to read from an `AnalyzeReport` instead of live objects, and to build a string instead of printing.

```python
"""The AnalyzeReport, rendered as text."""

from __future__ import annotations

import io

from optiff.analysis import AnalyzeReport
from optiff.domain import DataBlock, ImageInfo
from optiff.psd_layers import Layer, LayerStack
from optiff.units import format_size

WIDTH = 80


def render_size_tree(
    blocks: list[DataBlock],
    file_size: int,
    info: ImageInfo | None = None,
) -> list[str]:
    """
    The size tree as a list of lines.

    The last node at each level gets `└──`, the earlier ones `├──`. A node's
    children follow its own line rather than preceding it; an earlier version
    printed `└── TIFF tag` before further `├──` entries, which produced an
    inconsistent tree.

    >>> from optiff.domain import DataBlock, PhysicalRange
    >>> blocks = [
    ...     DataBlock("XMP", 700, (PhysicalRange(0, 60),)),
    ...     DataBlock("ICC Profile", 34675, (PhysicalRange(60, 100),)),
    ... ]
    >>> for line in render_size_tree(blocks, 100):
    ...     print(line)
          100.00 B  TIFF
    ├──     60.00 B  XMP                                     60.00%
    │   └── TIFF tag 700
    └──     40.00 B  ICC Profile                             40.00%
        └── TIFF tag 34675
    """
    lines = [f"{format_size(file_size):>12}  TIFF"]

    ordered = sorted(blocks, key=lambda block: block.size, reverse=True)

    for index, block in enumerate(ordered):
        is_last = index == len(ordered) - 1

        connector = "└──" if is_last else "├──"
        indent = "    " if is_last else "│   "

        percentage = block.size / file_size * 100 if file_size else 0.0

        lines.append(
            f"{connector} "
            f"{format_size(block.size):>12}  "
            f"{block.name:<38} "
            f"{percentage:>6.2f}%"
        )

        lines.extend(f"{indent}{child}" for child in _block_children(block, info))

    return lines


def _block_children(block: DataBlock, info: ImageInfo | None) -> list[str]:
    """The child lines of a node, already carrying their own glyphs."""
    details: list[str] = []

    if block.name == "IMAGE DATA" and info is not None:
        details.append(f"{info.width} × {info.height}")
        details.append(
            f"{info.samples} samples × {', '.join(map(str, info.bits_per_sample))}-bit"
        )
        details.append(f"Compression: {info.compression_name}")
        details.append(f"Predictor: {info.predictor or 'None'}")

        if block.is_fragmented:
            details.append(f"Strips: {len(block.ranges)}")

    if block.tag is not None:
        details.append(f"TIFF tag {block.tag}")

    return [
        f"{'└──' if index == len(details) - 1 else '├──'} {detail}"
        for index, detail in enumerate(details)
    ]


def _layer_line(layer: Layer) -> str:
    """
    One layer description line, indented by its nesting inside groups.

    >>> line = _layer_line(Layer(0, "Sky", 0, 0, 100, 200, (), "mul ", 128, 0, 0))
    >>> line.split()
    ['Sky', '0.00', 'B', '200x100', '-', 'Multiply', '50%']
    """
    name = "  " * layer.depth + (layer.name or "(unnamed)")

    bounds = "-" if layer.is_empty else f"{layer.width}x{layer.height}"

    marks = []

    if layer.is_hidden:
        marks.append("hidden")

    if layer.section != "layer":
        marks.append(layer.section)

    suffix = f"  [{', '.join(marks)}]" if marks else ""

    return (
        f"{name:<40} "
        f"{format_size(layer.data_size):>11}  "
        f"{bounds:>12}  "
        f"{layer.compression_short:<6} "
        f"{layer.blend_mode_name} {layer.opacity_percent}%"
        f"{suffix}"
    )


def _compression_summary(stack: LayerStack) -> str:
    """
    How many channel bytes fall to each compression method.

    >>> from optiff.psd_layers import LayerChannel, LayerStack
    >>> layer = Layer(
    ...     0, "x", 0, 0, 1, 1,
    ...     (LayerChannel(0, 1002, 0), LayerChannel(1, 502, 3)),
    ...     "norm", 255, 0, 0,
    ... )
    >>> _compression_summary(LayerStack((layer,), 1, False, 0, 0))
    'RAW 1000.00 B, ZIP with prediction 500.00 B'
    """
    totals: dict[str, int] = {}

    for layer in stack.layers:
        for channel in layer.channels:
            if channel.pixel_bytes == 0:
                continue

            name = channel.compression_name
            totals[name] = totals.get(name, 0) + channel.pixel_bytes

    if not totals:
        return "no channel data"

    return ", ".join(
        f"{name} {format_size(size)}"
        for name, size in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    )


def render_analyze(report: AnalyzeReport) -> str:
    buf = io.StringIO()

    def p(*args: object, **kwargs: object) -> None:
        print(*args, file=buf, **kwargs)

    p("=" * WIDTH)
    p("TIFF STORAGE ANALYZER")
    p("=" * WIDTH)

    p(f"File:          {report.path}")

    p(
        f"File size:     "
        f"{format_size(report.file_size)} "
        f"({report.file_size:,} bytes)"
    )

    p(f"Format:        {'BigTIFF' if report.is_bigtiff else 'Classic TIFF'}")
    p(f"Byte order:    {report.byte_order}")
    p(f"Image:         {report.image.width} × {report.image.height}")

    _render_size_tree(p, report)
    _render_metadata(p, report)
    _render_provenance(p, report)
    _render_photoshop(p, report)
    _render_layers(p, report)
    _render_linked_files(p, report)
    _render_physical_gaps(p, report)
    _render_structure(p, report)
    _render_compression(p, report)

    p("=" * WIDTH)
    p("DONE")
    p("=" * WIDTH)

    return buf.getvalue()


def _render_size_tree(p, report: AnalyzeReport) -> None:
    p("\n" + "=" * WIDTH)
    p("SIZE TREE")
    p("=" * WIDTH)
    p()

    lines = render_size_tree(report.size_tree, report.file_size, report.image)

    p("\n".join(lines))


def _render_metadata(p, report: AnalyzeReport) -> None:
    p("\n" + "=" * WIDTH)
    p("EMBEDDED METADATA / CONTENT")
    p("=" * WIDTH)
    p()

    for key, value in report.metadata.items():
        p(f"{key + ':':<32} {value}")


def _render_provenance(p, report: AnalyzeReport) -> None:
    p("\n" + "=" * WIDTH)
    p("PROVENANCE / HISTORY")
    p("=" * WIDTH)
    p()

    provenance = report.provenance

    if provenance.state == "no-blocks":
        p("No ImageSourceData blocks - nothing to read.")
        return

    if provenance.state == "no-tag":
        p("No tag 37724.")
        return

    assert provenance.report is not None
    for key, value in provenance.report.items():
        p(f"{key + ':':<32} {value}")


def _render_photoshop(p, report: AnalyzeReport) -> None:
    p("\n" + "=" * WIDTH)
    p("PHOTOSHOP IMAGESOURCEDATA")
    p("=" * WIDTH)
    p()

    photoshop = report.photoshop

    if not photoshop.found:
        p("Photoshop ImageSourceData: NOT DETECTED")
        return

    p(f"Signature:       {photoshop.signature}")
    p(f"Total size:      {format_size(photoshop.data_size)}")
    p(f"Parsed blocks:   {len(photoshop.blocks)}")

    if photoshop.layer_count is not None:
        p(f"Layer count:     {photoshop.layer_count}")

    p()
    p("BLOCKS")
    p("-" * WIDTH)

    if not photoshop.blocks:
        p("No blocks detected after the Photoshop header.")
        return

    for index, block in enumerate(photoshop.blocks, start=1):
        p(
            f"{index:>3}. "
            f"{block.key:<8} "
            f"{format_size(block.size):>12}  "
            f"offset=0x{block.offset:X}  "
            f"{block.description}"
        )


def _render_layers(p, report: AnalyzeReport) -> None:
    p("\n" + "=" * WIDTH)
    p("LAYERS")
    p("=" * WIDTH)
    p()

    layers = report.layers

    if layers.state == "no-tag":
        p("No tag 37724.")
        return

    if layers.state == "no-section":
        p("No layer section (Lr16 / Lr32 / Layr).")
        return

    stack = layers.stack
    assert stack is not None

    p(f"Layer count:     {abs(stack.declared_count)}")
    p(f"Transparency:    {'yes' if stack.has_transparency else 'no'}")
    p(f"Channel data:    {format_size(stack.channel_bytes)}")
    p(f"Compression:     {_compression_summary(stack)}")

    if not stack.is_complete:
        p(
            f"WARNING: records plus channels give {stack.consumed:,} B, "
            f"the section holds {stack.total:,} B"
        )

    for warning in stack.warnings:
        p(f"UWAGA: {warning.code} @0x{warning.offset:X} {warning.detail}")

    p()
    p(f"{'':>3}  {'NAME':<40} {'SIZE':>11}  {'BOUNDS':>12}  {'COMPR':<6} MODE")
    p("-" * WIDTH)

    for layer in stack.layers:
        p(f"{layer.index:>3}. {_layer_line(layer)}")


def _render_linked_files(p, report: AnalyzeReport) -> None:
    linked_files = report.linked_files

    if linked_files is None:
        return

    linked = linked_files.linked

    p("\n" + "=" * WIDTH)
    p("LINKED SMART OBJECTS")
    p("=" * WIDTH)
    p()

    p(f"Files:           {len(linked.files)}")
    p(f"Embedded data:   {format_size(linked.embedded_bytes)}")

    for warning in linked.warnings:
        p(f"UWAGA: {warning.code} @0x{warning.offset:X} {warning.detail}")

    p()

    for item in linked.files:
        p(
            f"{item.index + 1:>3}. "
            f"{item.name:<40} "
            f"{format_size(item.size):>11}  "
            f"{item.file_type_name} / {item.kind_name}"
        )
        p(f"     uid={item.uid}")

        if item.is_embedded and item.index in linked_files.embedded:
            _render_embedded(p, linked_files.embedded[item.index])

        p()


def _render_embedded(p, embedded) -> None:
    """Breaks an embedded PSD/PSB file down into sections and layers."""
    if embedded.error is not None:
        p(f"     not readable as PSD/PSB: {embedded.error}")
        return

    document = embedded.document
    assert document is not None

    p(
        f"     {document.format_name} "
        f"{document.width}x{document.height} "
        f"{document.channels}ch {document.depth}-bit "
        f"{document.color_mode_name}, "
        f"Image Data: {document.compression_name}"
    )

    for section in document.sections:
        share = section.total_size / document.total * 100 if document.total else 0.0

        p(
            f"       {section.name:<30} "
            f"{format_size(section.total_size):>11}  "
            f"{share:>6.2f}%"
        )

    for warning in document.warnings:
        p(f"       UWAGA: {warning.code} {warning.detail}")

    stack = document.layers

    if stack is None:
        return

    p(f"       layers: {len(stack.layers)}")
    p(f"       compression: {_compression_summary(stack)}")

    for warning in stack.warnings:
        p(f"       UWAGA: {warning.code} {warning.detail}")

    for layer in stack.layers:
        p(f"       {layer.index:>3}. {_layer_line(layer)}")


def _render_physical_gaps(p, report: AnalyzeReport) -> None:
    p("\n" + "=" * WIDTH)
    p("UNACCOUNTED PHYSICAL AREAS")
    p("=" * WIDTH)
    p()

    if not report.physical_gaps:
        p("None.")
        return

    for index, item in enumerate(report.physical_gaps, start=1):
        p(
            f"{index:>2}. "
            f"{format_size(item.gap.size):>12}  "
            f"offset=0x{item.gap.start:X} "
            f"end=0x{item.gap.end:X}"
        )
        p(f"    Type:       {item.classification}")


def _render_structure(p, report: AnalyzeReport) -> None:
    p("\n" + "=" * WIDTH)
    p("TIFF STRUCTURE / ENTRIES")
    p("=" * WIDTH)
    p()

    for tag in report.structure:
        p(
            f"{tag.code:7d}  "
            f"{tag.name:<35} "
            f"{tag.dtype_name:<10} "
            f"count={tag.count:<10} "
            f"size={format_size(tag.size):>10}"
        )


def _render_compression(p, report: AnalyzeReport) -> None:
    p("\n" + "=" * WIDTH)
    p("COMPRESSION / IMAGE DATA")
    p("=" * WIDTH)
    p()

    info = report.image

    p(f"Compression: {info.compression_name}")
    p(f"Predictor:   {info.predictor if info.predictor is not None else 'None'}")
    p(f"Bits/sample: {list(info.bits_per_sample)}")
```

**Note on `_render_embedded`'s share calculation:** `Reporter._print_embedded` divides by `item.size` (the `LinkedFile`'s declared size), not the embedded document's own `total`. Use `item.size` here too - fix the call site: `_render_linked_files` must pass `item` (or `item.size`) into `_render_embedded`, not rely on `document.total`. Update the signature to `_render_embedded(p, item, embedded)` and the call to `_render_embedded(p, item, linked_files.embedded[item.index])`, and inside use `share = section.total_size / item.size * 100 if item.size else 0.0`. This is a direct transcription requirement, not a design choice - get it from `report.py:396-403` exactly.

- [ ] **Step 4: Run the size-tree unit tests**

Run: `pytest tests/unit/test_formatters_analyze.py -v`
Expected: PASS.

- [ ] **Step 5: Write the temporary parity test against `Reporter`**

`tests/integration/test_analyze_parity.py`:

```python
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
```

- [ ] **Step 6: Run it**

Run: `pytest tests/integration/test_analyze_parity.py -v`
Expected: PASS. If it fails, the diff between `old` and `new` tells you exactly which `_render_*` helper drifted from its `Reporter._print_*` source - compare line by line against `report.py` rather than guessing.

- [ ] **Step 7: Generate the permanent golden fixtures**

Reuse the real `synthetic_tiff`/`synthetic_psd_tiff` fixtures from `conftest.py` directly, through a throwaway generator test, rather than hand-reconstructing their inputs (which risks drifting from the real fixture-building code in `_write`/`psd_blob`/`psd_container`).

Create a temporary file `tests/integration/_generate_golden.py`:

```python
"""Throwaway: run once to (re)generate tests/golden/*.txt, then delete this file."""

from __future__ import annotations

from pathlib import Path

from optiff.analysis import analyze
from optiff.formatters.analyze import render_analyze

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden"


def test_generate_golden_files(synthetic_tiff: Path, synthetic_psd_tiff: Path):
    GOLDEN_DIR.mkdir(exist_ok=True)

    for name, path in (
        ("analyze_synthetic_tiff.txt", synthetic_tiff),
        ("analyze_synthetic_psd_tiff.txt", synthetic_psd_tiff),
    ):
        text = render_analyze(analyze(path)).replace(
            f"File:          {path}", "File:          <PATH>"
        )
        (GOLDEN_DIR / name).write_text(text)
```

Run: `pytest tests/integration/_generate_golden.py -v`
Expected: PASS, and `tests/golden/analyze_synthetic_tiff.txt` / `tests/golden/analyze_synthetic_psd_tiff.txt` now exist on disk.

Delete the generator - it did its job and must not linger as a real test (it always passes; it asserts nothing):

```bash
rm tests/integration/_generate_golden.py
```

- [ ] **Step 8: Review the generated golden files**

Open both `tests/golden/analyze_synthetic_tiff.txt` and `tests/golden/analyze_synthetic_psd_tiff.txt`. Confirm each looks like a complete, sane report (all section headers present, `<PATH>` in place of the real path, no Python tracebacks or error text). This is the only place a human (or an LLM standing in for one) needs to eyeball output rather than rely on an assertion.

- [ ] **Step 9: Run the full suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add optiff/formatters/analyze.py tests/unit/test_formatters_analyze.py \
        tests/integration/test_analyze_parity.py tests/golden
git commit -m "Add render_analyze(); prove parity with Reporter; freeze golden output"
```

---

### Task 7: Retire `report.py`/`Reporter`; wire `AnalyzeCommand` to the new path; finalize `optiff/__init__.py`

**Files:**
- Delete: `optiff/report.py`
- Delete: `tests/integration/test_analyze_parity.py` (superseded by the golden-file tests below)
- Modify: `optiff/cli.py` (drop the old `analyze()` function and its now-unused imports)
- Modify: `optiff/commands/analyze.py` (`AnalyzeCommand.run()` returns `AnalyzeReport`)
- Modify: `optiff/cli.py`'s `_main()` (dispatch branch for `AnalyzeReport`)
- Modify: `optiff/__init__.py` (re-export `analyze` from `optiff.analysis`)
- Test: `tests/integration/test_analyze_golden.py` (new, permanent - replaces the deleted parity test)

**Interfaces:**
- Consumes: `optiff.analysis.analyze`, `optiff.formatters.analyze.render_analyze` (Tasks 5-6).
- Produces: none — this is the final task; `optiff.analyze` (package-level) now returns `AnalyzeReport`.

- [ ] **Step 1: Write the permanent golden-file regression test**

`tests/integration/test_analyze_golden.py`:

```python
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
    return render_analyze(analyze(path)).replace(f"File:          {path}", "File:          <PATH>")


def test_matches_golden_output_for_plain_tiff(synthetic_tiff: Path):
    expected = (GOLDEN_DIR / "analyze_synthetic_tiff.txt").read_text()

    assert _normalized(synthetic_tiff) == expected


def test_matches_golden_output_for_psd_tiff(synthetic_psd_tiff: Path):
    expected = (GOLDEN_DIR / "analyze_synthetic_psd_tiff.txt").read_text()

    assert _normalized(synthetic_psd_tiff) == expected
```

- [ ] **Step 2: Run it to confirm it passes against the fixtures generated in Task 6**

Run: `pytest tests/integration/test_analyze_golden.py -v`
Expected: PASS (2 tests) - this doesn't yet prove anything new (the parity test in Task 6 already established this), it just confirms the golden files read back correctly.

- [ ] **Step 3: Delete the temporary parity test**

```bash
git rm tests/integration/test_analyze_parity.py
```

It imports `optiff.report.Reporter`, which this task deletes in Step 5 - it must go first, or `pytest` fails to collect it afterwards.

- [ ] **Step 4: Wire `AnalyzeCommand` to the new data/formatter path**

Rewrite `optiff/commands/analyze.py`:

```python
"""The `analyze` subcommand."""

from __future__ import annotations

import argparse
from pathlib import Path

from optiff.analysis import AnalyzeReport, analyze


class AnalyzeCommand:
    name = "analyze"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("path", type=Path)

    def run(self, args: argparse.Namespace) -> AnalyzeReport:
        return analyze(args.path)
```

- [ ] **Step 5: Delete `report.py` and update `cli.py`**

```bash
git rm optiff/report.py
```

In `optiff/cli.py`:

- Delete the `analyze()` function entirely (today `cli.py:28-32`).
- Delete the now-unused imports: `from optiff.document import TiffDocument`, `from optiff.psd_analyzer import TiffPhotoshopAnalyzer`, `from optiff.report import WIDTH, Reporter`.
- Add `from optiff.analysis import AnalyzeReport` and `from optiff.formatters.analyze import render_analyze`.
- In `_main()`, extend the result-type dispatch:

```python
    if isinstance(result, OptimizeResult):
        sys.stdout.write(render_optimize(result))

        if result.comparison is not None and not result.comparison.ok:
            for problem in result.comparison.problems[:10]:
                print(f"  {problem}", file=sys.stderr)
            return EXIT_VERIFY_FAILED

        return EXIT_OK

    if isinstance(result, AnalyzeReport):
        sys.stdout.write(render_analyze(result))
        return EXIT_OK

    return EXIT_OK
```

Run `grep -n "Path\b" optiff/cli.py` afterwards - `analyze()`'s removal may leave `from pathlib import Path` unused if nothing else in `cli.py` references `Path` directly; keep it only if `build_parser()` or another surviving function still needs it (it doesn't, after this change - remove the import if `ruff check` flags it as unused).

- [ ] **Step 6: Update `optiff/__init__.py`**

```python
"""Analysis of the physical byte layout of TIFF files."""

from __future__ import annotations

from optiff.analysis import analyze
from optiff.cli import __version__
from optiff.document import TiffDocument

__all__ = ["TiffDocument", "__version__", "analyze"]
```

- [ ] **Step 7: Run the full suite**

Run: `pytest`
Expected: PASS. This is the task where everything must line up at once - if `render_analyze`'s output doesn't match the golden files byte-for-byte, the parity test from Task 6 should have caught the underlying bug already (this task only rewires *who calls* `render_analyze`/`analyze`, it doesn't change either function).

- [ ] **Step 8: Run ruff and pyrefly**

Run: `ruff check .` and `ruff format --check .`
Expected: no unused-import warnings for `optiff/cli.py` (see Step 5's note), no formatting diffs.

Run: `pyrefly check --python-interpreter-path "$(which python)"`
Expected: no new type errors.

- [ ] **Step 9: Manually verify the CLI end to end**

Run: `python -m optiff analyze tests/../README.md 2>&1 | head -1` (expect a clean "Not a readable TIFF" error, not a traceback, proving the dispatcher's exception handling still works with the rewired `AnalyzeCommand`).

Run, against any real or synthetic `.tif` on disk: `python -m optiff analyze path/to/file.tif | head -5` and confirm the output looks identical to before this whole plan started.

- [ ] **Step 10: Commit**

```bash
git add optiff/cli.py optiff/commands/analyze.py optiff/__init__.py \
        tests/integration/test_analyze_golden.py
git commit -m "Retire report.py/Reporter; AnalyzeCommand now returns AnalyzeReport"
```

---

## Self-Review Notes (for whoever executes this plan)

- **Spec coverage:** Command pattern (Tasks 2-3), reporting/formatting split for `optimize` (Task 4) and `analyze` (Tasks 5-7), pipeability (Task 3), coverage tooling + Codecov (Task 1). Every section of the spec has a task.
- **`EmbeddedDocumentSection`'s share-percentage bug:** caught during this plan's own drafting (see Task 6, Step 3's note) - `Reporter` divides by the `LinkedFile`'s declared size, not the embedded document's internal total. Get this from `report.py:396-403` at implementation time if anything here reads ambiguously; don't trust a paraphrase, read the source line.
- **Golden-file path normalization** only matters for the *committed* fixtures (Task 6-7); the *live* parity test in Task 6 compares old vs. new output computed on the identical path within the same test run, so no normalization is needed there - only the frozen files need the `<PATH>` substitution.
