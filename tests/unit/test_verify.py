"""Tests for comparing channel checksums."""

from __future__ import annotations

from tiff_analyzer.verify import ChannelDigest, compare


def digest(
    where: str = "tiff",
    layer: str = "0:Tlo",
    channel: str = "0:R",
    value: str = "aaa",
    source: str = "pixels",
) -> ChannelDigest:
    return ChannelDigest(
        where=where, layer=layer, channel=channel, digest=value, source=source
    )


def test_identical_sets_pass():
    # Arrange
    items = [digest(), digest(channel="1:G", value="bbb")]

    # Act
    result = compare(items, items)

    # Assert
    assert result.ok
    assert result.total == 2
    assert result.problems == ()


def test_changed_digest_is_reported():
    # Arrange
    before = [digest(value="aaa")]
    after = [digest(value="zzz")]

    # Act
    result = compare(before, after)

    # Assert
    assert not result.ok
    assert "aaa" in result.problems[0]
    assert "zzz" in result.problems[0]


def test_missing_channel_is_reported():
    # Act
    result = compare([digest(), digest(channel="1:G")], [digest()])

    # Assert
    assert not result.ok
    assert "channel count: 2 in source, 1 in result" in result.problems


def test_extra_channel_is_reported():
    # Act
    result = compare([digest()], [digest(), digest(channel="1:G")])

    # Assert
    assert not result.ok
    assert "channel count: 1 in source, 2 in result" in result.problems


def test_reordered_channels_are_reported():
    # Arrange - channels must come out in the same order
    before = [digest(channel="0:R"), digest(channel="1:G")]
    after = [digest(channel="1:G"), digest(channel="0:R")]

    # Act
    result = compare(before, after)

    # Assert
    assert not result.ok
    assert any("mismatch" in problem for problem in result.problems)


def test_channel_moved_between_documents_is_reported():
    # Arrange - the same channel, but in a different embedded file
    before = [digest(where="tiff")]
    after = [digest(where="embedded:0:a.psb")]

    # Act
    result = compare(before, after)

    # Assert
    assert not result.ok


def test_empty_sets_are_equal():
    # Act / Assert
    assert compare([], []).ok


def test_every_difference_is_listed():
    # Arrange
    before = [digest(channel=f"{i}:R", value=f"a{i}") for i in range(3)]
    after = [digest(channel=f"{i}:R", value=f"b{i}") for i in range(3)]

    # Act
    result = compare(before, after)

    # Assert - we do not want to report only the first difference
    assert len(result.problems) == 3
