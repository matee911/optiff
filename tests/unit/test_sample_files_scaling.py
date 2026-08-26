"""
The streaming `--scale` path in `tests/sample_files.py`.

`raw-layers` is the only case whose channels are RAW, so it's the only one
that can stream: every length is known before a byte of pixel data exists.
These tests stay small (`scale` in the tens, not the thousands) - the point
is proving the mechanism, not building a multi-gigabyte file on every run.
"""

from __future__ import annotations

import pytest

from tests import sample_files


def test_streamed_bytes_match_the_estimate(tmp_path):
    # Arrange
    scale = 5

    # Act
    path = sample_files.write("raw-layers", tmp_path, scale=scale)

    # Assert
    assert path.stat().st_size == sample_files.estimated_scaled_size(scale)


def test_same_seed_and_scale_are_deterministic(tmp_path):
    # Arrange
    scale = 5
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()

    # Act
    first = sample_files.write("raw-layers", tmp_path / "a", scale=scale)
    second = sample_files.write("raw-layers", tmp_path / "b", scale=scale)

    # Assert
    assert first.read_bytes() == second.read_bytes()


def test_scaling_an_unsupported_case_is_rejected(tmp_path):
    # Act / Assert
    with pytest.raises(ValueError, match="rle-layers"):
        sample_files.write("rle-layers", tmp_path, scale=5)


def test_scale_one_is_untouched(tmp_path):
    # Act
    path = sample_files.write("raw-layers", tmp_path, scale=1)

    # Assert - the small path never runs through the streaming writer
    assert path.read_bytes() == sample_files.build("raw-layers")


def test_cli_refuses_past_the_threshold_without_yes(tmp_path, monkeypatch):
    # Arrange - a low threshold so the test doesn't need a huge estimate
    monkeypatch.setattr(sample_files, "SIZE_WARN_BYTES", 1_000)

    # Act
    with pytest.raises(SystemExit):
        sample_files.main([str(tmp_path), "--only", "raw-layers", "--scale", "5"])

    # Assert
    assert not (tmp_path / "raw-layers.tif").exists()


def test_cli_writes_past_the_threshold_with_yes(tmp_path, monkeypatch):
    # Arrange
    monkeypatch.setattr(sample_files, "SIZE_WARN_BYTES", 1_000)

    # Act
    sample_files.main([str(tmp_path), "--only", "raw-layers", "--scale", "5", "--yes"])

    # Assert
    assert (tmp_path / "raw-layers.tif").exists()


def test_cli_rejects_scale_without_only(tmp_path):
    # Act / Assert
    with pytest.raises(SystemExit):
        sample_files.main([str(tmp_path), "--scale", "5"])


def test_cli_rejects_scale_on_an_unsupported_case(tmp_path):
    # Act / Assert
    with pytest.raises(SystemExit):
        sample_files.main([str(tmp_path), "--only", "rle-layers", "--scale", "5"])


def test_scale_to_cross_finds_the_minimal_scale():
    # Act
    scale = sample_files.scale_to_cross(sample_files.estimated_scaled_size(20))

    # Assert
    layout_at_scale = sample_files._scaled_layout(scale)
    layout_below = sample_files._scaled_layout(scale - 1)
    assert layout_at_scale.photoshop_len > sample_files.estimated_scaled_size(20)
    assert layout_below.photoshop_len <= sample_files.estimated_scaled_size(20)
