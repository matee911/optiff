# CLI: Command pattern + reporting/formatting split

Status: Approved (design), pending implementation plan
Date: 2026-08-26

## Why

`optiff/cli.py` today mixes three concerns in one file: argument parsing,
dispatch (`if args.optimize is not None: ... else: analyze(...)`), and output
formatting (`print_optimize()` builds and prints text in one pass).
`optiff/report.py::Reporter` mixes a fourth: it reads Photoshop layer/provenance
data *and* prints it, interleaved, inside the same methods (e.g.
`_print_layers()` calls `read_layer_stack()` and prints in one method, with
early returns like `print("No tag 37724."); return`).

The project is about to grow more subcommands (`--compatibility` from issue
#10, possibly others). Growing the current `if/else` in `main()` does not
scale, and the tangled read/print methods on `Reporter` make it hard to add a
second output format (e.g. `--json`) or to test reporting logic without
capturing stdout.

This spec commits to two structural changes, applied to *both* existing
commands (`analyze`, `optimize`) at once:

1. **Command pattern** for CLI dispatch — one class per subcommand, an
   explicit registry, argparse subparsers.
2. **Reporting/formatting split** — each command produces a plain data object
   (already true for `optimize` via `OptimizeResult`; new for `analyze` via a
   new `AnalyzeReport`), and a separate, pure "formatter" function renders that
   object to text. No I/O inside a formatter; no argparse or printing inside a
   command's data-producing path.

## Non-goals

- No new output format (`--json`, etc.) — the split only *enables* that later.
- No change to exit code values or their meaning (`EXIT_OK=0`,
  `EXIT_BAD_FILE=2`, `EXIT_VERIFY_FAILED=3`).
- No change to the *content* of any printed report — this is a structural
  refactor, verified to produce byte-identical output (see Testing).
- No new subcommands (e.g. `--compatibility` from #10) — this spec only
  prepares the structure for them.

## UX change

Subcommands replace the current flag-driven dispatch:

- `optiff analyze FILE` (today: `optiff FILE`)
- `optiff optimize FILE --out OUT --level 4 [--image-data] [--zip-fallback] [--no-verify]`
  (today: `optiff FILE --optimize OUT --level 4 ...`)

This is a breaking change to the CLI surface, accepted deliberately to make
room for future subcommands.

## Architecture

### Commands

`optiff/commands/base.py`:

```python
class Command(Protocol):
    name: str

    def add_arguments(self, parser: argparse.ArgumentParser) -> None: ...
    def run(self, args: argparse.Namespace) -> object:  # AnalyzeReport | OptimizeResult
        ...
```

`optiff/commands/analyze.py::AnalyzeCommand` — `run()` calls
`optiff.analysis.analyze(args.path)` and returns an `AnalyzeReport`.

`optiff/commands/optimize.py::OptimizeCommand` — `run()` calls
`optiff.optimize.optimize(args.path, args.out, level=..., verify=..., ...)` and
returns the existing `OptimizeResult`, unchanged.

`optiff/cli.py` becomes the dispatcher only:

- builds the top-level parser, adds one subparser per registered `Command`
  (calling `command.add_arguments(subparser)`),
- resolves the chosen command, calls `command.run(args)`,
- picks the formatter by result type (`render_analyze` for `AnalyzeReport`,
  `render_optimize` for `OptimizeResult`),
- prints the rendered text,
- computes and returns the exit code (see Error handling below),
- catches domain exceptions raised out of `run()`
  (`tifffile.TiffFileError`, `OptimizeError`) and maps them to `EXIT_BAD_FILE`,
  same as today.

### `AnalyzeReport` (new)

New module `optiff/analysis.py` (not `analyze.py`: `optiff/__init__.py` already
does `from optiff.cli import __version__, analyze`, re-exporting `analyze` as
a package-level attribute; a submodule literally named `optiff/analyze.py`
would collide with that attribute the moment it's imported, making
`optiff.analyze` mean two different things depending on import order).
`optiff/analysis.py` mirrors the existing shape of `optiff/optimize.py` (a
plain function returning a plain result object).

**Public API change:** `optiff/__init__.py` moves its re-export from
`optiff.cli.analyze` (today: prints the full report, returns `None`) to
`optiff.analysis.analyze` (returns an `AnalyzeReport`, prints nothing). Anyone
using `optiff.analyze(path)` as a library call gets the report data now, not
printed text - to print, call
`optiff.formatters.analyze.render_analyze(optiff.analyze(path))`. This is a
deliberate, breaking change to the public function's return type, consistent
with the reporting/formatting split; called out explicitly rather than left
implicit.

```python
def analyze(path: Path) -> AnalyzeReport: ...
```

Dataclasses (new, in `optiff/analysis.py` unless noted as already existing in
`optiff/domain.py`):

```
AnalyzeReport
├── path: Path
├── file_size: int
├── is_bigtiff: bool
├── byte_order: str                     # "little-endian" | "big-endian"
├── image: ImageInfo                     # already in domain.py
├── size_tree: list[DataBlock]           # already in domain.py
├── metadata: dict[str, str]             # from MetadataAnalyzer.report()
├── provenance: ProvenanceSection        # new: see below
├── photoshop: PhotoshopAnalysis         # already in domain.py
├── layers: LayersSection                # new: see below
├── linked_files: LinkedFilesSection | None   # new: see below
├── physical_gaps: list[GapClassification]    # new: see below
├── structure: list[TagInfo]             # new: see below
```

```python
@dataclass(frozen=True)
class ProvenanceSection:
    # exactly the three states Reporter._print_provenance distinguishes today
    state: Literal["no-blocks", "no-tag", "found"]
    report: dict[str, str] | None  # Provenance.report(), only when state == "found"

@dataclass(frozen=True)
class LayersSection:
    # Reporter._print_layers distinguishes two different "nothing here"
    # messages ("No tag 37724." vs "No layer section (Lr16 / Lr32 / Layr).") -
    # a bare `LayerStack | None` cannot tell them apart.
    state: Literal["no-tag", "no-section", "found"]
    stack: LayerStack | None  # only when state == "found"

@dataclass(frozen=True)
class GapClassification:
    gap: PhysicalRange
    classification: str  # PhysicalClassifier(...).classify(gap)

@dataclass(frozen=True)
class TagInfo:
    code: int
    name: str
    dtype_name: str
    count: int
    size: int

@dataclass(frozen=True)
class EmbeddedDocumentSection:
    # one linked smart object's embedded PSD/PSB, recursively.
    # `EmbeddedDocument` (psd_file.py) already carries width/height/channels/
    # depth/color_mode_name/compression_name/sections/layers/warnings as
    # properties - wrap it rather than re-declaring those fields here.
    name: str
    error: str | None       # DocumentError message, if parse_document failed
    document: EmbeddedDocument | None   # None when error is set

@dataclass(frozen=True)
class LinkedFilesSection:
    linked: LinkedFiles                          # already in psd_links.py
    embedded: dict[int, EmbeddedDocumentSection]  # keyed by LinkedFile.index
```

The reading logic that produces these (`read_layer_stack`, `read_provenance`,
`read_linked_files`, `parse_document`, `PhysicalClassifier`, opening/closing
`document.photoshop_source_reader()`) moves into `analyze()` **unchanged** —
same calls, same order, same try/finally around readers — just collected into
one function instead of scattered across nine `Reporter._print_*` methods.
`DocumentError` from `parse_document` is caught exactly where it is today
(per embedded linked file) and stored as `EmbeddedDocumentSection.error`,
never propagated to the dispatcher.

### Formatters

`optiff/formatters/analyze.py::render_analyze(report: AnalyzeReport) -> str`
`optiff/formatters/optimize.py::render_optimize(result: OptimizeResult) -> str`

Pure functions: given the data object, return the full report text (what is
printed today). They reuse the existing rendering helpers verbatim:
`render_size_tree`, `_layer_line`, `_compression_summary` (move from
`report.py` to `formatters/analyze.py`), and `_duration` (move from `cli.py`
to `formatters/optimize.py`).

`cli.py` does only `print(formatter(result))` after `run()` returns.

### Final module layout

```
optiff/
  cli.py                  # dispatcher: argparse, subcommand registry, exit codes
  commands/
    base.py               # Command protocol
    analyze.py             # AnalyzeCommand
    optimize.py             # OptimizeCommand
  analysis.py              # AnalyzeReport + analyze() (data only, no printing)
  optimize.py               # OptimizeResult + optimize() (unchanged)
  formatters/
    analyze.py               # render_analyze() + moved rendering helpers
    optimize.py               # render_optimize() + moved _duration()
  report.py                 # removed; contents absorbed into analysis.py / formatters/analyze.py
```

## Error handling / exit codes

No change in values or meaning:

- `EXIT_OK = 0`
- `EXIT_BAD_FILE = 2`
- `EXIT_VERIFY_FAILED = 3`

All three stay computed in `cli.py`, in one place:

- file-not-found check before dispatch (as today),
- `tifffile.TiffFileError` / `OptimizeError` raised out of `command.run()` →
  caught in `cli.py` → `EXIT_BAD_FILE`,
- `OptimizeResult.comparison.ok is False` → `EXIT_VERIFY_FAILED`, decided by
  the dispatcher after `run()` returns (same check as today, just moved out of
  `print_optimize()`).

## Testing

- **Golden-file safety net for the `Reporter` → `AnalyzeReport`+formatter
  refactor**: before refactoring, capture `Reporter(...).print_report()`
  output for the existing sample fixtures as golden text files; after
  refactoring, assert `render_analyze(analyze(path))` is byte-identical. Keep
  the golden comparison as a permanent regression test once the refactor
  lands.
- Existing integration tests (`tests/integration/test_sample_files.py` and any
  other caller of today's `analyze()`/`optimize()` in `cli.py`) updated for the
  new import paths; behavior unchanged.
- New unit tests: `AnalyzeCommand.run()` / `OptimizeCommand.run()` return the
  expected result type given a sample file; `render_analyze()` /
  `render_optimize()` given a hand-built result object produce the expected
  text, without needing a real TIFF fixture.

### Pipeability

New requirement: `optiff`'s subcommands must behave like well-mannered Unix
filters — usable as the *first* stage of a pipeline (`optiff analyze FILE |
grep LAYERS`, `optiff optimize FILE --out OUT | head -5`, `... | wc -l`).

The concrete risk this catches: Python's `print()` to stdout raises
`BrokenPipeError` when the downstream reader (e.g. `head`) closes its end
early, and an uncaught one prints a traceback and exits `1` — surprising for
something piped into `head`. `main()` in `cli.py` wraps the print-and-return
in a `try/except BrokenPipeError`, closes stdout, and exits `0` (the
conventional Unix behavior for SIGPIPE-like conditions), rather than a raw
traceback.

Test: an integration test that runs `optiff analyze FILE | head -3` and
`optiff optimize FILE --out OUT | grep -q FILE` as real subprocesses (matching
how `mp:endpoint-behavior-checker`-style black-box tests work — actual process,
actual pipe), asserting a clean, expected exit status rather than a Python
traceback on stderr.

## Test coverage tooling

Add `pytest-cov` to the `dev` optional-dependencies group in `pyproject.toml`.
Configure `[tool.coverage.run]` with `source = ["optiff"]` and
`[tool.coverage.report]` with `show_missing = true`. In CI
(`.github/workflows/ci.yml`), replace the plain `pytest` step with
`pytest --cov=optiff --cov-report=term-missing --cov-report=xml` (the XML
report is what Codecov's action consumes) — reporting only, no enforced
minimum threshold (not asked for; a fail-under gate is a separate decision).

### Codecov integration

Upload that `coverage.xml` to Codecov on every CI run: add a step using
`codecov/codecov-action@v5` right after the `pytest` step, pointing at
`coverage.xml`. The repo is public (`matee911/optiff`), so Codecov's
tokenless upload for public GitHub repos applies — no `CODECOV_TOKEN` secret
needed for the upload step itself.

**One manual, external step this spec cannot do for you:** the repository
needs to be activated on codecov.io (sign in with the GitHub account, add the
repo) before the first upload will be accepted. I can add the CI step now;
activating the repo on codecov.io is a one-time action on your account that
you'd need to do yourself (or explicitly ask me to walk you through it).
Optionally, a `[![codecov]](...)` badge in `README.md` once that's done.

## Done when

- `optiff analyze FILE` and `optiff optimize FILE --out OUT ...` work via the
  new subcommands, producing output byte-identical to today's equivalent
  invocations (verified by the golden-file test).
- `cli.py` contains no report-building or data-reading logic — only argparse,
  dispatch, and exit-code mapping.
- `report.py` is gone; its logic lives in `optiff/analysis.py` (data) and
  `optiff/formatters/analyze.py` (rendering).
- Adding a future subcommand (e.g. `--compatibility` from #10) requires only:
  a new `Command` subclass, a new result dataclass, a new formatter — no
  change to `cli.py`'s dispatch logic.
- `optiff analyze FILE | head -3` and similar pipelines exit cleanly (no
  Python traceback) when the downstream reader closes early.
- CI reports coverage (`term-missing` + `coverage.xml`) and uploads it to
  Codecov; a badge can be added to `README.md` once the repo is activated on
  codecov.io.
