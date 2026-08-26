# Content profiles + a level trade-off benchmark (issue #12)

## TL;DR

- 🎨 `tests/sample_files.py` gains four new content-profile generators (`grain`, `flat`, `detail`, `banded`) alongside the existing `smooth` - each a distinct pixel-compressibility character, all parameterized by `width`/`rows` (defaulting to the module's `WIDTH`/`ROWS`, so nothing existing changes byte-for-byte).
- 📊 `tools/benchmark.py` sweeps deflate levels 1-9 across every content profile at a realistic channel size, and renders a light/dark SVG trade-off chart (time × size, one line per profile) plus a per-profile table - no aggregate "you'll save X%" figure.
- 🔬 If the `grain` profile reproduces the previously-observed level-3/4 inversion on real files, that's recorded as a credibility signal; if not, that's recorded too - neither blocks the PR.
- Split into **two stacked PRs**: content profiles first (small, testable in isolation), the benchmark script second (consumes the first, harder to unit-test meaningfully) - together they'd sit close to the 500-line default limit, and they're cleanly separable.

## Why the structural cases (`CASES`) don't need to change

Issue #12's table shows two axes: file **structure** (the 12 `CASES` - RAW/RLE/ZIP/groups/etc.) and pixel **content** (currently only `smooth`). The benchmark doesn't need a TIFF container at all - it measures `zlib.compress` on raw channel byte buffers directly. So the new content-profile functions are added *alongside* `smooth`, not wired into `CASES`/`build()`/`write()`, which stay completely untouched. This also sidesteps the issue's "make WIDTH/ROWS parameters" ask in its riskiest form: instead of changing the module constants every existing case relies on, each profile function takes `width`/`rows` as keyword parameters defaulting to the module constants - `smooth(seed)` still returns exactly what it always has, and the benchmark calls the same functions with whatever size it wants.

## DRY / SRP placement

- **`tests/sample_files.py`**: gains the profile functions (`grain`, `flat`, `detail`, `banded`) next to `smooth` - they're the same kind of thing (a seeded pixel-content generator), so they belong where `smooth` already lives, not in a new module. A `CONTENT_PROFILES: dict[str, Callable]` registry lets `tools/benchmark.py` iterate generically instead of hardcoding profile names in two places.
- **`tools/benchmark.py`** (new, alongside `affinity_bisect.py`/`mask_check.py`): owns the sweep (levels × profiles × repeats), the median/credibility-check logic, and SVG rendering. No new dependency - a small hand-rolled SVG writer (axes, gridlines, one polyline + labelled points per profile), consistent with this repo's existing plain-script tools and avoiding a `pyproject.toml` change for a chart nobody but this script needs.
- **`README.md`**: embeds the two committed SVGs via `<picture>` + `prefers-color-scheme`, captioned to distinguish it from the real-file-measured headline numbers already there.

## Interfaces

- CLI only: `python tools/benchmark.py --out docs/levels.svg [--size N] [--repeats N]`. No library API beyond the new profile functions (importable, same as `smooth` already is) and no UI surface - this is a documentation/measurement tool.

## Gherkin

```gherkin
Feature: Content profiles

  Scenario: Existing content is untouched
    Given no width/rows override
    When smooth(seed) is called
    Then it returns exactly what it did before (byte-identical, same doctest)

  Scenario: Profiles are deterministic and distinct
    Given the same seed
    When a profile is called twice
    Then it returns identical bytes
    And different profiles produce different bytes for the same seed

  Scenario: Profiles differ in compressibility, in the expected order
    Given a fixed size and seed
    When each profile's bytes are deflated at a fixed level
    Then flat compresses smallest, grain compresses closest to its own size,
      and banded compresses better under prediction (ZIP_PREDICTED) than
      without it

Feature: Benchmark script

  Scenario: The chart is produced from nothing but the repository
    When `python tools/benchmark.py --out docs/levels.svg` runs
    Then it produces docs/levels.svg and docs/levels-dark.svg from the
      generator alone, no external files

  Scenario: The grain profile is checked against the known inversion
    When the sweep completes
    Then the script reports whether grain reproduces the level-3/4
      inversion, without failing the run either way
```

## Acceptance criteria

**Content profiles (PR 2a)**
- [ ] `smooth()`'s existing doctests and every existing `CASES`/`build()`/`write()` caller are unchanged (byte-identical).
- [ ] `grain`, `flat`, `detail`, `banded` are deterministic (same seed ⇒ same bytes) and distinct from each other and from `smooth`.
- [ ] Each profile's compressibility ordering matches its stated character (flat ≪ smooth/detail ≪ grain; banded compresses better with horizontal prediction than without).
- [ ] `ruff format --check .`, `ruff check .`, `pyrefly check`, all three pytest suites pass.

**Benchmark script (PR 2b, stacked on 2a)**
- [ ] `python tools/benchmark.py --out docs/levels.svg` produces both SVGs from the repo alone.
- [ ] Chart is time-on-x, size-on-y, one point per level 1-9 connected in order, one line per profile - not dual-axis.
- [ ] A per-profile table is available (not one aggregate saving figure).
- [ ] Repeats are taken and a median reported.
- [ ] The grain-profile inversion check runs and reports its result either way.
- [ ] Not wired into CI (per the issue: a shared runner's timings measure the runner).
- [ ] `ruff format --check .`, `ruff check .`, `pyrefly check` pass on the new script.

## ADR / performance / security

No `docs/adr` in this repo. No production code touched (`optiff/` untouched in PR 2a; PR 2b adds a standalone `tools/` script). Performance: the benchmark is a manual, non-CI tool by design - its own runtime cost is the point of the exercise, not a concern. Security: local tool, no untrusted input, no new dependency.

## Size

PR 2a (content profiles + tests): ~150-220 lines. PR 2b (benchmark script + README embed): ~300-380 lines, mostly the hand-rolled SVG renderer. Splitting keeps each comfortably under the 500-line default limit; combined they'd sit close to or over it for no benefit, since the two concerns are independently reviewable and mergeable.
