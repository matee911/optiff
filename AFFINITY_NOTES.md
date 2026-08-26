# Affinity limits on layered TIFF files

Measured 2026-08-24 against Affinity 3.2.0 on macOS, using nine layered TIFF
files of 2.0-2.9 GB written by Photoshop.

## The rule

> Affinity opens a file **without layers**, as a single flat layer, when
> **tag 37724 exceeds 2 GB** **or** the **decompressed layer data exceeds 2 GB**.

Both limits are the same constant, **2^31 = 2 147 483 648**, which looks like a
signed 32-bit offset overflowing. Neither threshold was fitted to the data; the
rule agrees with **13 observations out of 13**.

## The threshold, narrowed by halving the interval

Seven variants of **the same file** with **identical pixels** (SHA256 equal
across all 59 channels), differing **only in how many bytes they occupy on
disk**. Only part of the channels was packed in each; the rest was copied
byte for byte.

| tag 37724 | vs 2^31 | Affinity |
|---|---|---|
| 1 103 433 980 | -996 MB | layers |
| 1 696 502 956 | -430 MB | layers |
| 1 990 934 788 | -149 MB | layers |
| **2 095 383 396** | **-50 MB** | **layers** |
| **2 208 740 932** | **+58 MB** | **flat** |
| 2 393 823 148 | +235 MB | flat |
| 2 437 333 656 | +276 MB | flat |

**The transition happens once and in one direction.** The boundary sits inside
a **108 MB** window, with **2^31** falling in the middle of it.

This is the strongest evidence in these notes: one variable, seven points,
identical content. It rules out "amount of content" as the cause, since the
layer count, their dimensions and every pixel are the same in all seven.

For the second quantity - decompressed layer data - the window is wider
(2033 MiB works, 2191 MiB fails) and 2^31 falls inside it too, but that
threshold was not narrowed further.

## Two quantities, only one of them fixable

| | what it is | does compression change it? |
|---|---|---|
| **tag 37724** | how many bytes it takes on disk | **yes** - that is what `optiff optimize FILE --out OUT` does |
| **decompressed layer data** | the sum of every channel's pixels once decoded | **no** |

The second quantity is `width x height x bytes_per_pixel` summed over the
channels. **Compression does not touch it**, because it does not change how
many pixels there are. Only removing or shrinking layers would, and that is a
lossy operation.

Proof that this is what blocks three of the files: their tag is **0.33-0.84 GB**,
far below the limit, and they still open flat.

## State of the test set

All nine originals have a tag of at least 1.99 GB, **eight of them above 2 GB**,
so out of Photoshop they do not open with layers. After
`optiff optimize FILE --out OUT` the tag drops to 0.6-1.3 GB.

| file | tag after `optiff optimize FILE --out OUT` | decompressed | outcome |
|---|---|---|---|
| `sample-a0400` | 1.25 GB | 0.31 GB | fixed |
| `sample-a0432` | 0.76 GB | 0.54 GB | fixed |
| `sample-d0493` | 1.03 GB | 0.82 GB | fixed |
| `sample-b0196` | 0.85 GB | 0.43 GB | fixed |
| `sample-b0635` | 0.63 GB | 0.33 GB | fixed |
| `sample-c0001` | 0.88 GB | 1.99 GB | fixed, but with **10 MB to spare** |
| `sample-d0353` | 0.82 GB | **2.14 GB** | beyond saving |
| `sample-d0676` | 1.26 GB | **2.25 GB** | beyond saving |
| `sample-a0442` | 0.84 GB | **2.38 GB** | beyond saving |

**Optimization fixes Affinity compatibility rather than breaking it.**
`sample-d0493` straight out of Photoshop (tag 2.27 GB) opens flat; after
`optiff optimize FILE --out OUT` (1.03 GB) it shows its layers.

How much would have to disappear from the unfixable files, counted in
full-canvas channels:

| file | excess | that many channels |
|---|---|---|
| `sample-d0353` | 143 MB | 2.4 |
| `sample-d0676` | 251 MB | 4.3 |
| `sample-a0442` | 390 MB | 8.0 |

## Compatibility has a price: different rendering

When Affinity **cannot** read the layers it displays the **composite embedded
in the file**, that is the image rendered by Photoshop. When it **can**, it
composites the image **itself**, with its own engine.

A file repaired for compatibility may therefore look **different than before**
in Affinity, even though the data is untouched. Observed on a
`Generative Expand` layer that carries **both an alpha channel and a mask**,
the hardest case to composite.

Verified that the data is not at fault:

| what | original vs our result |
|---|---|
| ICC profile (560 B) | identical |
| Photoshop Image Resources | identical |
| XMP | identical |
| embedded composite (TIFF pixels) | identical |
| pixels of the `Generative Expand` layer, extracted by Photoshop | identical, 92 006 928 samples |

In Photoshop the original and our result look **the same**. The difference
exists only between Photoshop and Affinity for **the same** file, so it lives
in Affinity's compositing engine, not in the file.

## What this is NOT about

- **The compression method does not matter.** A variant with not a single RAW
  channel is still flat. Neither RAW, nor ZIP without prediction (method 2),
  nor ZIP with prediction is to blame.
- **The layer count does not matter.** A file with 21 layers works; a file
  with 8 layers fails.
- **Affinity always flattens embedded smart objects.** That is normal
  behaviour, not a symptom. Only the TIFF's own layer tree counts.
- **Photoshop has no such limit.** It opens every one of these files with the
  full structure.

## Method: how this was measured and how to repeat it

### The problem with "let us compare two files"

The first approach was wrong: comparing **different files** and looking for what
sets them apart. There were too many candidates at once - size, layer count,
compression method, presence of a smart object, blend modes. Every pair
differed in several ways simultaneously, so no result proved anything.

### The fix: one variable

Instead of hunting for two similar files, **produce a series of variants of one
file** that differ only in how many bytes they occupy:

- only the **first N channels** are packed, the rest copied byte for byte
- pixels identical across all variants
- layer count, dimensions, blend modes and masks unchanged
- ICC profile, XMP, Image Resources and the embedded composite unchanged

Choosing N gives any tag size between "nothing packed" and "everything packed".
**One** full planning pass is enough to learn each channel's saving; after that
N is pure arithmetic, with no further passes.

### The tool

```bash
python tools/affinity_bisect.py FILE.tif 2.20 2.05 1.95 1.80 1.55 --verify
```

The arguments are target tag sizes in GB. `--verify` compares the SHA256 of
every channel against the source - **a corrupted variant would show up flat for
the wrong reason and wreck the whole experiment**, so in practice that step is
not optional.

The implementation swaps `_plan_channel` for a wrapper that returns a copy
segment once the limit is passed. The rest of the production path - container
planning, offset arithmetic, writing - runs unchanged, so the variants are not
"artificial" files.

### Verification on the Photoshop side

```bash
python tools/mask_check.py ORIGINAL RESULT --pixels
```

Reads **every mask separately** by duplicating the mask channel, compares
histograms, and for masks with a rich distribution exports and compares the
pixels.

> **This cannot be done by comparing the flattened image.** A layer covering
> the whole canvas hides everything beneath it, so such a comparison passes
> vacuously even if the masks below were scrambled. Verified: switching hidden
> layers on changes 77.9% of the pixels, so flattening *without* that step does
> not exercise them at all.

> **`osascript` aborts an AppleEvent after 60 s.** Opening a 2 GB file takes
> longer, so without `with timeout of 3000 seconds` you get error `-1712`
> halfway through.

### The sequence that works

1. Measure the suspect property on the files you already have - that gives a
   first bracket
2. Find a pair of **the same file** differing in one thing
3. If no such pair exists, **produce one**
4. Verify the variants are lossless before drawing any conclusion
5. Halve the interval until the constant becomes recognisable

## Hypotheses falsified along the way

Worth recording, because each looked convincing at its stage.

| hypothesis | what killed it |
|---|---|
| **Our optimization breaks files** | the untouched original opens flat too |
| **Method 2 from `--zip-fallback` is to blame** | the same file without method 2 is also flat |
| **RAW channels are to blame** | a variant with no RAW at all is still flat |
| **The layer count is to blame** | 21 layers works, 8 layers fails |
| **The layer block size is to blame** | 894 MB works, 839 MB fails |
| **The threshold is ~1.7 GB** | a bug in my own metric, see below |

### The measurement trap that produced a false threshold

When counting "decompressed layer data" you **must not** use the stored size for
layers with a `0x0` rectangle - that is, adjustment layers whose mask carries
all the data. A packed mask gives a different number than the same mask raw, so
**variants of one file stop being comparable**.

The correct computation:

| case | decompressed size |
|---|---|
| `RAW` channel | `pixel_bytes` - the bytes already are the pixels |
| known layer rectangle | `width x height x bytes_per_pixel` |
| anything else | an actual `zlib.decompress` |

The sanity check that catches this: **variants of the same file must produce the
same number.** Before that check existed, identical content measured as either
1667 or 2033 MiB depending on whether the masks were packed, and the rule came
out false.

## Environment

| | |
|---|---|
| Affinity | **3.2.0** |
| Photoshop | 2026 |
| platform | macOS |
| files | 9 layered TIFFs, 2.0-2.9 GB, 16-bit RGB |
| observations | 15 opens in Affinity, 7 of them variants of one file |

## Open questions

- **The threshold for decompressed data** was narrowed only to the
  2033-2191 MiB window. Closing it needs a file inside that range; the test set
  has none.
- **Whether the constant really is 2^31**, or some other value inside the
  108 MB window. Without Affinity's sources that cannot be settled, but 2^31 is
  the only round value in the window and explains both limits with one cause.
- **Whether the limit applies to Affinity's other input formats** (PSD, PSB) or
  only to the Photoshop block inside a TIFF.

## A possible `--compatibility-mode`

The realistic scope is **reporting**, not repair: the analyser knows both
quantities, so before writing it can say whether a file will open with layers in
Affinity and **which of the two quantities blocks it**. Only the first is
fixable, and plain `optiff optimize FILE --out OUT` already does that.
