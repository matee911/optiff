"""Tests for reading provenance (CAI / GenI / cinf)."""

from __future__ import annotations

from optiff.provenance import (
    ContentCredentials,
    GenerativeInfo,
    _models,
    _version,
)
from optiff.psd_descriptor import Descriptor

# ============================================================================
# CONTENT CREDENTIALS
# ============================================================================


def test_missing_block_is_not_found():
    assert ContentCredentials(present=False).summary() == "NOT FOUND"


def test_small_block_is_marker_not_manifest():
    # Arrange - a real case: 77 B is the marker on its own
    credentials = ContentCredentials(present=True, enabled=False, block_size=77)

    # Assert
    assert credentials.has_manifest is False
    assert "marker only" in credentials.summary()
    assert "disabled" in credentials.summary()


def test_large_block_counts_as_signed_manifest():
    # Arrange
    credentials = ContentCredentials(present=True, enabled=True, block_size=9000)

    # Assert
    assert credentials.has_manifest is True
    assert credentials.summary().startswith("FOUND (signed manifest")


def test_guid_is_shown_when_present():
    # Arrange
    credentials = ContentCredentials(
        present=True, enabled=True, generational_guid="abc-123", block_size=90
    )

    # Assert
    assert "abc-123" in credentials.summary()


def test_enabled_none_defaults_to_enabled_wording():
    # Arrange
    credentials = ContentCredentials(present=True, block_size=50)

    # Assert
    assert "enabled" in credentials.summary()


# ============================================================================
# GENERATIVE AI
# ============================================================================


def test_generative_missing():
    assert GenerativeInfo(present=False).summary() == "NOT FOUND"


def test_generative_not_used():
    assert GenerativeInfo(present=True, used=False).summary() == (
        "NO (isUsingGenTech=0)"
    )


def test_generative_used_without_model_names():
    assert GenerativeInfo(present=True, used=True).summary() == (
        "YES (model not named)"
    )


def test_generative_used_with_models():
    # Arrange
    info = GenerativeInfo(present=True, used=True, models=("Firefly", "X"))

    # Assert
    assert info.summary() == "YES (Firefly, X)"


def test_generative_unknown_flag():
    assert GenerativeInfo(present=True, used=None).summary() == "UNKNOWN"


# ============================================================================
# WERSJE
# ============================================================================


def test_version_joins_parts():
    # Arrange
    descriptor = Descriptor("", "null", {"major": 26, "minor": 8, "fix": 1})

    # Assert
    assert _version(descriptor) == "26.8.1"


def test_version_of_none_is_none():
    assert _version(None) is None


def test_version_with_missing_part_is_none():
    # Arrange
    descriptor = Descriptor("", "null", {"major": 26, "minor": 8})

    # Assert
    assert _version(descriptor) is None


# ============================================================================
# MODELE
# ============================================================================


def test_models_from_plain_strings():
    assert _models(["Firefly", "Other"]) == ("Firefly", "Other")


def test_models_from_descriptors():
    # Arrange
    items = [Descriptor("", "null", {"modelName": "Firefly"})]

    # Assert
    assert _models(items) == ("Firefly",)


def test_models_from_empty_list():
    assert _models([]) == ()


def test_models_from_non_list():
    assert _models(None) == ()
    assert _models(42) == ()
