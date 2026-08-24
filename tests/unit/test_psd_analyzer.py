"""Tests for `ImageSourceDataAnalyzer`, on builder-made streams."""

from __future__ import annotations

import pytest

from tests.unit.builders import CONTAINER_HEADER, CONTAINER_SIGNATURE, psd_container
from tiff_analyzer.psd_analyzer import ImageSourceDataAnalyzer
from tiff_analyzer.psd_blocks import walk
from tiff_analyzer.readers import BytesReader


@pytest.fixture
def analyzer() -> ImageSourceDataAnalyzer:
    return ImageSourceDataAnalyzer()


# ============================================================================
# KONTENER
# ============================================================================


def test_empty_data_is_not_found(analyzer):
    # Arrange / Act
    result = analyzer.analyze(b"")

    # Assert
    assert result.found is False
    assert result.blocks == ()
    assert result.warnings == ()


def test_unknown_container_signature_is_reported(analyzer):
    # Arrange / Act
    result = analyzer.analyze(b"This is not a Photoshop block" * 4)

    # Assert
    assert result.found is True
    assert result.signature is None
    assert [warning.code for warning in result.warnings] == ["unknown-container"]


def test_missing_nul_terminator_is_reported(analyzer):
    # Arrange - poprawna sygnatura, ale zamiast NUL od razu blok
    data = CONTAINER_SIGNATURE + b"MIB8" + b"61rL" + bytes(4)

    # Act
    result = analyzer.analyze(data)

    # Assert
    assert result.found is True
    assert [warning.code for warning in result.warnings] == [
        "unterminated-signature"
    ]


@pytest.mark.parametrize("byte_order", ["<", ">"])
def test_analyze_returns_logical_keys(analyzer, byte_order):
    # Arrange
    data = psd_container(
        ("Lr16", b"warstwy"),
        ("Pat2", b""),
        ("cinf", b"x"),
        byte_order=byte_order,
    )

    # Act
    result = analyzer.analyze(data)

    # Assert
    assert result.found is True
    assert result.signature == CONTAINER_SIGNATURE.decode("ascii")
    assert result.data_size == len(data)
    assert [block.key for block in result.blocks] == ["Lr16", "Pat2", "cinf"]
    assert result.warnings == ()


def test_analyze_consumes_container_exactly(analyzer):
    # Arrange
    data = psd_container(("Lr16", b"a" * 17), ("cinf", b"bb"))

    # Act
    result = analyzer.analyze(data)

    # Assert
    assert result.blocks[-1].end == len(data)


def test_first_block_starts_right_after_header(analyzer):
    # Arrange
    data = psd_container(("Lr16", b"ab"))

    # Act
    result = analyzer.analyze(data)

    # Assert
    assert len(CONTAINER_HEADER) == 36
    assert result.blocks[0].offset == 36


# ============================================================================
# SUMMARY
# ============================================================================


def test_summary_groups_and_sorts_by_key(analyzer):
    # Arrange
    data = psd_container(
        ("cinf", b"xxxx"),
        ("Lr16", b"a" * 10),
        ("Lr16", b"b" * 20),
    )
    result = analyzer.analyze(data)

    # Act
    summary = analyzer.summary(result.blocks)

    # Assert
    assert summary == (("Lr16", 2, 30), ("cinf", 1, 4))


def test_summary_of_no_blocks_is_empty(analyzer):
    assert analyzer.summary(()) == ()


# ============================================================================
# PAYLOAD
# ============================================================================


def test_payload_returns_slice(analyzer):
    # Arrange
    data = psd_container(("Lr16", b"hello"))
    reader = BytesReader(data)
    blocks, _ = walk(reader, 36, len(data))

    # Act
    payload = analyzer.payload(reader, blocks[0])

    # Assert
    assert payload == b"hello"


def test_payload_respects_limit(analyzer):
    # Arrange
    data = psd_container(("Lr16", b"x" * 500))
    reader = BytesReader(data)
    blocks, _ = walk(reader, 36, len(data))

    # Act
    payload = analyzer.payload(reader, blocks[0], limit=10)

    # Assert
    assert payload == b"x" * 10


def test_payload_of_zero_length_block_is_empty(analyzer):
    # Arrange
    data = psd_container(("Pat2", b""))
    reader = BytesReader(data)
    blocks, _ = walk(reader, 36, len(data))

    # Act / Assert
    assert analyzer.payload(reader, blocks[0]) == b""


# ============================================================================
# PAYLOAD STATS
# ============================================================================


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"", {"size": 0, "unique_bytes": 0, "entropy": 0.0}),
        (b"AAAA", {"size": 4, "unique_bytes": 1, "entropy": 0.0}),
        (bytes(10), {"size": 10, "unique_bytes": 1, "zero_ratio": 1.0}),
        (bytes(range(256)), {"size": 256, "unique_bytes": 256, "entropy": 8.0}),
    ],
)
def test_payload_stats(analyzer, payload, expected):
    # Act
    stats = analyzer.payload_stats(payload)

    # Assert
    for key, value in expected.items():
        assert stats[key] == pytest.approx(value)


def test_payload_stats_ascii_ratio(analyzer):
    # Arrange - half the bytes are printable
    payload = b"AB" + bytes(2)

    # Act
    stats = analyzer.payload_stats(payload)

    # Assert
    assert stats["ascii_ratio"] == 0.5
    assert stats["zero_ratio"] == 0.5


def test_payload_stats_entropy_is_never_negative_zero(analyzer):
    # Arrange / Act
    entropy = analyzer.payload_stats(b"AAAA")["entropy"]

    # Assert - repr tells 0.0 from -0.0, plain == does not
    assert repr(entropy) == "0.0"
