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

## Development

```bash
pre-commit install    # ruff format, ruff check, pyrefly on every commit
pre-commit run --all-files
```

## Tests

```bash
pytest                            # no sample files needed, ~2 s
pytest -m slow --tiff-dir /path   # against real files
```

The suite needs no sample files: it generates them. Every case we have met in
a real file has a recipe in `tests/sample_files.py` and reaches tests through
the `sample_file` fixture, which writes into pytest's `tmp_path` - nothing is
kept in the repository.

| case | what it reproduces |
|---|---|
| `raw-layers` | layers stored uncompressed - the ordinary win |
| `rle-layers` | layers as RLE, Photoshop's own default, deliberately left alone |
| `packed-layers` | already ZIP with prediction: nothing left to gain |
| `mixed-layers` | one channel packed, the rest raw - a half-processed file |
| `adjustment-mask` | a 0x0 rectangle with the bytes in the mask; needs `--zip-fallback` |
| `grouped-layers` | layers nested in a group (`lsct` dividers) |
| `large-document-container` | the `V0002` container with 8-byte lengths |
| `smart-object` | a whole PSB embedded in an `lnk2` record |
| `compressible-image` | flattened image stored raw, so `--image-data` has work |
| `compressed-image` | flattened image already deflated - a note, not a failure |
| `photoshop-not-last` | tag 37724 before the image data, so the file is refused |
| `no-photoshop` | a plain TIFF with no Photoshop block |

To open them in Photoshop or Affinity:

```bash
python -m tests.sample_files /some/directory
```

Tests that need real files read their mapping from `tests/samples.local.json`
(template: `tests/samples.example.json`). That file is git-ignored so private
file names never reach the repository.

## Documentation

- [`AFFINITY_NOTES.md`](AFFINITY_NOTES.md) - the measured 2 GB limit above
  which Affinity stops reading layers from a TIFF. It applies to files straight
  out of Photoshop too; optimization fixes it.

## Licence

To be decided.
