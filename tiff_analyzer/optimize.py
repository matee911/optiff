"""
Shrinking a TIFF by recompressing the channels inside tag 37724.

In every file examined, tag 37724 sits right at the end, behind the image
data. Shortening it therefore **moves no offset at all**: only
the ``count`` field of the IFD entry needs fixing. When the tag is not
last, the optimizer refuses rather than relocating structures it does not
przepisuje (sub-IFD Exif, Image Resources).

The original is never modified. The result is written alongside it and, once
written, parsed from scratch and compared with the original channel by
channel.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from tiff_analyzer.document import TiffDocument
from tiff_analyzer.optimize_image import (
    ImageDataError,
    ImagePlan,
    plan_image_data,
    shift_patches,
)
from tiff_analyzer.optimize_psd import plan_container
from tiff_analyzer.psd_analyzer import ImageSourceDataAnalyzer, TiffPhotoshopAnalyzer
from tiff_analyzer.readers import FileWindowReader
from tiff_analyzer.segments import (
    Copy,
    Literal,
    Segment,
    apply_patches,
    rebase,
    total_size,
    write_plan,
)
from tiff_analyzer.verify import ChannelDigest, Comparison, channel_digests, compare

PHOTOSHOP_TAG = 37724

#: How many padding bytes at the end of the file may be ignored when
#: checking whether tag 37724 really terminates it.
TAIL_SLACK = 16


class OptimizeError(RuntimeError):
    """The file cannot be optimized safely."""


@dataclass
class OptimizeResult:
    source: Path
    output: Path | None
    size_before: int
    size_after: int
    channels_total: int = 0
    channels_changed: int = 0
    tag_before: int = 0
    tag_after: int = 0
    channel_bytes_before: int = 0
    channel_bytes_after: int = 0
    image_before: int = 0
    image_after: int = 0
    #: Time in seconds, broken down by stage.
    seconds_plan: float = 0.0
    seconds_write: float = 0.0
    seconds_verify: float = 0.0
    comparison: Comparison | None = None
    skipped: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def saved(self) -> int:
        return self.size_before - self.size_after

    @property
    def channel_saved(self) -> int:
        """Saving on channel data alone: the source of the whole gain."""
        return self.channel_bytes_before - self.channel_bytes_after

    @property
    def tag_saved(self) -> int:
        return self.tag_before - self.tag_after

    @property
    def padding_saved(self) -> int:
        """
        How much of the saving came from 4-byte block padding.

        When a payload shrinks its padding changes too, at every nested
        level. This should amount to single bytes.
        """
        return self.tag_saved - self.channel_saved

    @property
    def image_saved(self) -> int:
        return self.image_before - self.image_after

    @property
    def tail_saved(self) -> int:
        """Padding dropped behind the tag value, at the end of the file."""
        return self.saved - self.tag_saved - self.image_saved

    @property
    def ratio(self) -> float:
        return self.size_after / self.size_before if self.size_before else 1.0

    @property
    def verified(self) -> bool:
        return self.comparison is not None and self.comparison.ok

    @property
    def wrote_file(self) -> bool:
        return self.output is not None

    @property
    def seconds_total(self) -> float:
        return self.seconds_plan + self.seconds_write + self.seconds_verify

    @property
    def throughput(self) -> float:
        """Source bytes processed per second."""
        return self.size_before / self.seconds_total if self.seconds_total else 0.0


@contextmanager
def _stopwatch(sink: list[float]):
    """Mierzy elapsed bloku i dopisuje go do listy."""
    began = time.monotonic()

    try:
        yield
    finally:
        sink.append(time.monotonic() - began)


def _tag_is_last(document: TiffDocument, tag) -> bool:
    """Whether nothing but padding follows the tag value."""
    end = tag.valueoffset + document.tag_value_size(tag)

    return document.file_size - end <= TAIL_SLACK


def _count_field(tag) -> int:
    """Pozycja pola `count` we wpisie IFD: kod(2) + typ(2)."""
    return tag.offset + 4


def channel_digests_of(path: Path) -> list[ChannelDigest]:
    """Checksums for every channel in the file."""
    analyzer = ImageSourceDataAnalyzer()

    with TiffDocument(path) as document:
        analysis = TiffPhotoshopAnalyzer(analyzer).analyze(document)
        reader = document.photoshop_source_reader()

        if reader is None:
            return []

        try:
            return channel_digests(reader, analysis)
        finally:
            reader.close()


def plan_file(
    path: Path,
    *,
    level: int = 6,
    image_data: bool = False,
    zip_fallback: bool = False,
):
    """
    Zwraca `(segmenty, kontener, plan_obrazu, uwagi)` dla nowego pliku.

    The segments refer to the whole source file, not to the tag window.
    When `image_data` is on, the pixels are packed with Adobe Deflate and the
    offsets behind them are shifted by the difference.
    """
    analyzer = ImageSourceDataAnalyzer()

    with TiffDocument(path) as document:
        tag = document.tag(PHOTOSHOP_TAG)

        if tag is None:
            raise OptimizeError("no tag 37724 - nothing to compress")

        if not _tag_is_last(document, tag):
            raise OptimizeError(
                "tag 37724 is not last in the file; shortening it would "
                "require moving offsets, which this version does not do"
            )

        analysis = TiffPhotoshopAnalyzer(analyzer).analyze(document)
        reader = document.photoshop_source_reader()

        if reader is None:
            raise OptimizeError("cannot read the value of tag 37724")

        try:
            container = plan_container(
                reader, analysis, level=level, blind=zip_fallback
            )
        finally:
            reader.close()

        page = document.first_page
        order = "little" if document.tiff.byteorder == "<" else "big"

        value_start = tag.valueoffset
        count_at = _count_field(tag)

        image: ImagePlan | None = None
        notes: list[str] = []
        whole = FileWindowReader(path, 0, document.file_size)

        try:
            if image_data:
                try:
                    image = plan_image_data(
                        whole, page, order=order, level=level
                    )
                except ImageDataError as error:
                    # Pixels are a bonus, not a precondition. When there is nothing to
                    # pack there - because Photoshop already did it, or we
                    # did on a previous run - we do the layers only and say
                    # o tym wprost. Przerwanie calej optymalizacji odbieraloby
                    # zysk z warstw, ktore moga byc jeszcze nietkniete.
                    notes.append(f"image pixels skipped - {error}")

            if image is None or not image.worth_it:
                # Ogon pliku podmieniamy, reszta bez zmian.
                segments: list[Segment] = [
                    Copy(0, count_at),
                    Literal(container.size.to_bytes(4, order)),
                    Copy(count_at + 4, value_start - (count_at + 4)),
                    *rebase(container.segments, value_start),
                ]

                return segments, container, None, notes

            image_start = int(page.dataoffsets[0])
            image_end = image_start + image.before
            delta = image.saved

            patches = [
                *image.patches,
                *shift_patches(
                    whole,
                    page,
                    delta=delta,
                    boundary=image_end,
                    order=order,
                ),
                (count_at, container.size.to_bytes(4, order)),
            ]

            head = [item for item in patches if item[0] < image_start]
            tail = [item for item in patches if item[0] >= image_end]

            if len(head) + len(tail) != len(patches):
                raise OptimizeError(
                    "a patch landed inside the image data - unexpected layout"
                )

            segments = [
                *apply_patches(0, image_start, head),
                Literal(image.data),
                *apply_patches(image_end, value_start, tail),
                *rebase(container.segments, value_start),
            ]

            return segments, container, image, notes
        finally:
            whole.close()


def optimize(  # noqa: PLR0913  - kazdy przelacznik zmienia zachowanie zapisu
    path: Path,
    output: Path,
    *,
    level: int = 6,
    verify: bool = True,
    keep_mtime: bool = True,
    image_data: bool = False,
    zip_fallback: bool = False,
) -> OptimizeResult:
    """
    Writes an optimized copy of `path` to `output`.

    The original is left alone. If verification fails the output file is
    deleted: better than leaving behind something we do not trust.
    """
    if output.resolve() == path.resolve():
        raise OptimizeError("the output file cannot be the source file")

    size_before = path.stat().st_size

    timings: list[float] = []

    with _stopwatch(timings):
        segments, container, image, notes = plan_file(
            path,
            level=level,
            image_data=image_data,
            zip_fallback=zip_fallback,
        )

    result = OptimizeResult(
        source=path,
        output=None,
        size_before=size_before,
        size_after=size_before,
        channels_total=len(container.results),
        channels_changed=sum(1 for item in container.results if item.changed),
        tag_before=container.source_size,
        tag_after=container.size,
        channel_bytes_before=container.before,
        channel_bytes_after=container.after,
        seconds_plan=timings[0],
        image_before=image.before if image else 0,
        image_after=image.after if image else 0,
        notes=notes,
    )

    if not container.changed and image is None:
        result.skipped = "nothing to compress - everything is already packed"
        return result

    verifying: list[float] = []

    with _stopwatch(verifying):
        before = channel_digests_of(path) if verify else []

    writing: list[float] = []

    with _stopwatch(writing):
        source = FileWindowReader(path, 0, size_before)

        try:
            output.parent.mkdir(parents=True, exist_ok=True)

            with output.open("wb") as handle:
                written = write_plan(segments, source, handle)
        finally:
            source.close()

    result.seconds_write = writing[0]

    expected = total_size(segments)

    if written != expected:
        output.unlink(missing_ok=True)
        raise OptimizeError(
            f"wrote {written} B, the plan expected {expected} B"
        )

    result.output = output
    result.size_after = written

    if verify:
        with _stopwatch(verifying):
            after = channel_digests_of(output)

        result.comparison = compare(before, after)

        if not result.comparison.ok:
            output.unlink(missing_ok=True)
            result.output = None
            result.seconds_verify = sum(verifying)

            return result

    result.seconds_verify = sum(verifying)

    _check_consistency(result)

    if keep_mtime:
        stat = path.stat()
        os.utime(output, (stat.st_atime, stat.st_mtime))

    return result


#: How many block padding bytes may be counted towards the saving before we
#: call it suspicious. In practice this is single bytes per level.
PADDING_SLACK = 4096


def _check_consistency(result: OptimizeResult) -> None:
    """
    The saving must come from channels, not from nowhere.

    The difference between levels comes from padding alone. Anything larger
    means we dropped something we were not supposed to touch.
    """
    if abs(result.padding_saved) > PADDING_SLACK:
        raise OptimizeError(
            f"the tag saving ({result.tag_saved:,} B) does not match the "
            f"channel saving ({result.channel_saved:,} B) - a difference of "
            f"{result.padding_saved:,} B is too much for padding"
        )

    if not 0 <= result.tail_saved <= TAIL_SLACK:
        raise OptimizeError(
            f"the file shrank {result.tail_saved:,} B more than the tag; "
            f"something other than padding sat behind the tag value"
        )
