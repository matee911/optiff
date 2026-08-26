"""Size tree invariants - Bug 6."""

from __future__ import annotations

import pytest

from optiff.domain import DataBlock, ImageInfo, PhysicalRange
from optiff.report import render_size_tree

IMAGE_INFO = ImageInfo(
    width=100,
    height=200,
    samples=3,
    bits_per_sample=(16, 16, 16),
    compression=1,
    compression_name="NONE",
    predictor=1,
)


def block(name: str, size: int, tag: int | None = None, *, start: int = 0):
    return DataBlock(name, tag, (PhysicalRange(start, start + size),))


def top_level(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith(("├──", "└──"))]


def children(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith(("│   ", "    "))]


# ============================================================================
# GLIFY
# ============================================================================


def test_exactly_one_top_level_node_uses_last_glyph():
    # Arrange
    blocks = [block("A", 30, 700), block("B", 20, 34675), block("C", 10)]

    # Act
    lines = render_size_tree(blocks, 100)

    # Assert
    nodes = top_level(lines)
    assert sum(line.startswith("└──") for line in nodes) == 1
    assert nodes[-1].startswith("└──")


def test_no_sibling_appears_after_last_node():
    # Arrange
    blocks = [block("A", 30, 700), block("B", 20, 34675)]

    # Act
    lines = render_size_tree(blocks, 100)

    # Assert - no further ├── may follow a └── line at the same level
    last_index = max(
        index for index, line in enumerate(lines) if line.startswith("└──")
    )
    assert not any(line.startswith("├──") for line in lines[last_index + 1 :])


def test_children_follow_their_parent():
    # Arrange
    blocks = [block("A", 60, 700), block("B", 40, 34675)]

    # Act
    lines = render_size_tree(blocks, 100)

    # Assert
    assert lines[1].startswith("├──")
    assert "A" in lines[1]
    assert lines[2].startswith("│   ")
    assert "TIFF tag 700" in lines[2]
    assert lines[3].startswith("└──")
    assert "B" in lines[3]
    assert lines[4].startswith("    ")
    assert "TIFF tag 34675" in lines[4]


def test_last_child_of_a_node_uses_last_glyph():
    # Arrange - IMAGE DATA has several children
    blocks = [block("IMAGE DATA", 90), block("XMP", 10, 700)]

    # Act
    lines = render_size_tree(blocks, 100, IMAGE_INFO)

    # Assert
    image_children = [
        line.removeprefix("│   ") for line in lines if line.startswith("│   ")
    ]
    assert len(image_children) >= 4
    assert sum(line.startswith("└──") for line in image_children) == 1
    assert image_children[-1].startswith("└──")
    assert all(line.startswith("├──") for line in image_children[:-1])


def test_image_data_children_come_before_next_sibling():
    # Arrange
    blocks = [block("IMAGE DATA", 90), block("XMP", 10, 700)]

    # Act
    lines = render_size_tree(blocks, 100, IMAGE_INFO)

    # Assert
    xmp_index = next(index for index, line in enumerate(lines) if "XMP" in line)
    before = lines[2:xmp_index]
    assert before, "IMAGE DATA must have children before the next node"
    assert all(line.startswith("│   ") for line in before)


# ============================================================================
# CONTENT
# ============================================================================


def test_root_line_shows_file_size():
    assert render_size_tree([], 2048)[0].strip() == "2.00 KB  TIFF"


def test_percentages_never_exceed_one_hundred():
    # Arrange
    blocks = [block("A", 60, 700), block("B", 40, 34675)]

    # Act
    lines = render_size_tree(blocks, 100)

    # Assert
    percentages = [
        float(line.rsplit(" ", 1)[-1].rstrip("%")) for line in top_level(lines)
    ]
    assert sum(percentages) == pytest.approx(100.0)


def test_zero_file_size_does_not_divide_by_zero():
    # Arrange / Act
    lines = render_size_tree([block("A", 0, 700)], 0)

    # Assert
    assert "0.00%" in lines[1]


def test_fragmented_image_reports_strip_count():
    # Arrange
    fragmented = DataBlock(
        "IMAGE DATA",
        None,
        (PhysicalRange(0, 10), PhysicalRange(20, 30)),
    )

    # Act
    lines = render_size_tree([fragmented], 100, IMAGE_INFO)

    # Assert
    assert any("Strips: 2" in line for line in lines)


def test_contiguous_image_does_not_report_strip_count():
    # Act
    lines = render_size_tree([block("IMAGE DATA", 90)], 100, IMAGE_INFO)

    # Assert
    assert not any("Strips:" in line for line in lines)


def test_blocks_are_sorted_by_size_descending():
    # Arrange
    blocks = [block("small", 10, 1), block("big", 80, 2), block("mid", 10, 3)]

    # Act
    lines = top_level(render_size_tree(blocks, 100))

    # Assert
    assert "big" in lines[0]


def test_empty_block_list_renders_only_root():
    assert len(render_size_tree([], 100)) == 1
