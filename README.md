# optiff

Lossless recompression of layered TIFF files carrying a Photoshop block,
including the layers **inside embedded smart objects**.

On a nine-file test set: **22 GB down to 10 GB (54%)**, and with
`--zip-fallback` individual files reach **33% of the original**.

## Principles

- **The original is never touched.** The result is written alongside it under
  a new name.
- **Every write is verified.** SHA256 of each channel's pixels before and
  after; on a mismatch the output file is deleted.
- **Only channel data and its size fields change.** The rest of the structure
  - rectangles, blend modes, masks, names, extra blocks, the ICC profile, XMP
  - is copied byte for byte.

## Usage

```bash
python -m tiff_analyzer FILE.tif                       # analysis only
python -m tiff_analyzer FILE.tif --optimize RESULT.tif # compress the layers
```

| flag | default | what it does |
|---|---|---|
| `--level 1-9` | **4** | deflate level; above 4 the time climbs steeply for little gain |
| `--image-data` | off | also pack the flattened image |
| `--zip-fallback` | off | pack channels with no readable geometry (adjustment layer masks) |
| `--no-verify` | off | skip the SHA256 check |

## Tools

| script | purpose |
|---|---|
| `tools/mask_check.py` | compares two files through Photoshop, mask by mask |
| `tools/verify_in_photoshop.jsx` | checks a whole batch of source/result pairs |
| `tools/affinity_bisect.py` | builds variants of one file at chosen tag sizes |

## Tests

```bash
pytest                            # no sample files needed, ~2 s
pytest -m slow --tiff-dir /path   # against real files
```

Tests that need specific files read their mapping from
`tests/samples.local.json` (template: `tests/samples.example.json`). That file
is git-ignored so private file names never reach the repository.

## Documentation

- [`AFFINITY_NOTES.md`](AFFINITY_NOTES.md) - the measured 2 GB limit above
  which Affinity stops reading layers from a TIFF. It applies to files straight
  out of Photoshop too; optimization fixes it.

## Licence

To be decided.
