# Streaming the sample generator past 2^31 (issue #13)

## TL;DR

- `tests/sample_files.py` builds every case fully in memory; fine at 4.6 kB/channel, impossible at gigabytes.
- Add `--scale N` to `python -m tests.sample_files`, but only for `raw-layers` — the one case whose channels are RAW (no RLE table, no zlib), so every length is known analytically and nothing needs seek-based patching.
- A chunked version of the existing `smooth()` random walk is proven byte-identical to the current one (verified empirically: same `numpy.random.Generator`, called in row-chunks instead of one call, produces the exact same bytes). This is what lets the writer stream to disk without holding gigabytes in RAM.
- `--scale` past an estimated-size threshold (`SIZE_WARN_BYTES`, printed up front) refuses without `--yes`.
- `smart-object` scaling is explicitly **out of scope** here — it wraps a nested nested PSB/link-record layout and doubles the surface for a case the issue's acceptance criteria don't require. Follow-up if ever needed.
- Closes #13 in one PR. Issue #12 (content profiles, benchmark script) is separate, stacked follow-up work — not designed here.

## Why the RAW-only restriction removes the hard part

The issue asks for "write the container, then emit channel data block by block, patching the length fields once the sizes are known" — implying sizes aren't known up front. But every case that needs to scale (`raw-layers`) only uses RAW-encoded channels: `RAW.to_bytes(2) + width*rows*bpp` bytes, no compression, no per-row table. That size is `2 + width*rows*bpp` — computable before a single byte of pixel data is generated. So the header/IFD/layer-record/PSD-block-length fields can all be written correctly up front, and the streaming step is pure sequential writing: no seek, no patch. Building the generic "patch it later" machinery the issue describes would be speculative generality for a case this repo doesn't have (no scalable case uses a size that isn't known analytically) — left out per this repo's "no speculative generality" rule.

## DRY / SRP placement

- **`tests/unit/builders.py`** (shared, used by every case, in-memory only): extract the TIFF header+IFD byte-offset math out of `build_tiff()` into `_tiff_header_and_ifd(*, width, height, photoshop_last, image_len, photoshop_len, compression)`. `build_tiff()` becomes a thin wrapper calling it with `len(image)`/`len(photoshop)` and concatenating the tail — **zero behavior change**, same doctests. This is the one prerequisite refactor: it's the only place that already knows the offset arithmetic, and duplicating it in `sample_files.py` would be a second copy of fiddly, easy-to-desync logic (DRY).
- **`tests/sample_files.py`** (owns turning a case into bytes/a file): gains
  - `_walk_chunk(rng, width, n_rows)` — the random-walk math, extracted from `smooth()`. `smooth(seed)` becomes `_walk_chunk(np.random.default_rng(seed), WIDTH, ROWS)` (one chunk, unchanged bytes).
  - `write_scaled_raw_layers(path, scale, *, yes)` — the streaming writer for the one scalable case. Not shared with the in-memory builders; it owns *this file's* streaming concern (SRP), calling into `builders._tiff_header_and_ifd` for the part that's genuinely shared.
  - CLI additions to `main()`: `--scale` (default 1), `--yes`.
- **`tests/integration/test_psd_parsing.py`**: one new `@pytest.mark.bigfile` test that generates a `raw-layers` file past 2^31 via the new generator and checks block layout / zero warnings / an offset past 2^31 — the properties the issue says "do not need real files, only large ones." Existing `bigfile` tests keep using `sample_named`/`samples.local.json` unchanged: they assert exact byte offsets and sizes tied to specific real client files (e.g. `PROBKA_A_END`, `linked.size == 2_033_731_564`), which a synthetic file cannot reproduce and isn't meant to.
- **`AFFINITY_NOTES.md`**: one added line under the existing 2^31 section, naming the exact `--scale` value that crosses the boundary for `raw-layers` at today's `WIDTH`/`ROWS` — so the claim is reproducible by command, not by description of files nobody else has.

## Interfaces

- CLI only: `python -m tests.sample_files DIR --only raw-layers --scale N [--yes]`. No UI surface (this is a test/dev tool), no library API beyond the existing `write()`/`build()` functions gaining an optional `scale` keyword.

## Gherkin

```gherkin
Feature: Streaming large sample generation

  Scenario: Default scale stays exactly as it was
    Given no --scale flag is passed
    When "raw-layers" is written
    Then the bytes are identical to today's in-memory build()

  Scenario: A large file streams instead of loading into RAM
    Given --scale large enough to exceed 2^31 bytes of layer data
    And --yes is passed
    When "raw-layers" is written
    Then the file is produced without materialising the channel payload in memory
    And re-running with the same seed and scale produces byte-identical output

  Scenario: Accidental disk fill is refused
    Given --scale large enough to estimate past the size threshold
    And --yes is NOT passed
    When generation is attempted
    Then it refuses, printing the estimated size, and writes nothing

  Scenario: Scaling an unsupported case is rejected
    Given --scale > 1
    And --only names a case other than "raw-layers"
    When generation is attempted
    Then it raises/errors clearly, naming which cases can scale
```

## Acceptance criteria

- [ ] `smooth(seed)` and `build("raw-layers")` are byte-identical to before (doctests unchanged, no diff).
- [ ] `python -m tests.sample_files DIR --only raw-layers --scale N --yes` produces a file, streaming channel data rather than building it fully in memory.
- [ ] Same seed + same scale ⇒ identical bytes on a second run (covered by a unit test comparing two writes).
- [ ] A scale estimated below the size threshold does not require `--yes`; above it, requires `--yes` and prints the estimated size first.
- [ ] `--scale > 1` with `--only` naming a non-scalable case fails with a clear error instead of silently building the small version.
- [ ] A new `bigfile`-marked integration test generates a file past 2^31 via the generator (no `samples.local.json` needed) and asserts block layout, zero warnings, and an offset past 2^31.
- [ ] `AFFINITY_NOTES.md` names the exact reproducing command.
- [ ] `ruff format --check .`, `ruff check .`, `pyrefly check`, and all three pytest suites (unit / integration+e2e / doctest) pass.

## Size

Estimated diff: ~150-220 lines across `builders.py` (extraction, ~25 lines moved/added), `sample_files.py` (~120-150 lines: chunked walk, streaming writer, CLI flags), one new integration test (~30 lines), `AFFINITY_NOTES.md` (~3 lines), plus a unit test file for the writer (~40-60 lines). Comfortably under the 500-line default limit — **one PR, not a stack**, for issue #13. Issue #12 remains a separate, later, stacked PR once this lands.

## ADR / performance / security

No `docs/adr` in this repo. No production code touched (`optiff/` package is untouched — this is entirely test tooling). No performance concern beyond the explicit point of the change (bounded memory instead of O(size) RAM). No security concern: local dev/test tool, writes only where the user points it, and the size-threshold gate is exactly the safety measure the issue asked for.
