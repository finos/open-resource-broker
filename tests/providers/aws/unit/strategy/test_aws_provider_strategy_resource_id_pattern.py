"""Tests for AWSProviderStrategy.get_resource_id_pattern()."""

import re

from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy
from orb.providers.base.strategy.provider_strategy import ProviderStrategy


_PATTERN = AWSProviderStrategy.get_resource_id_pattern()


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


def test_returns_non_none():
    """AWS must override the base None default and return a pattern string."""
    assert _PATTERN is not None


def test_return_type_is_str():
    """Pattern must be a plain string, not bytes or another type."""
    assert isinstance(_PATTERN, str)


def test_overrides_base_class_default():
    """AWSProviderStrategy must return a different value than the base class."""
    assert _PATTERN != ProviderStrategy.get_resource_id_pattern()


def test_callable_on_class_without_instance():
    """Must be callable at class level — no boto3 session, config, or I/O needed."""
    # No AWSProviderConfig constructed here, no AWS credentials required.
    result = AWSProviderStrategy.get_resource_id_pattern()
    assert result is not None


# ---------------------------------------------------------------------------
# Pattern correctness — AWS instance IDs
# ---------------------------------------------------------------------------


def test_matches_8_char_hex_instance_id():
    """Standard 8-char hex suffix instance ID (older format)."""
    assert re.fullmatch(_PATTERN, "i-0abcdef1")  # type: ignore[arg-type]


def test_matches_17_char_hex_instance_id():
    """17-char hex suffix instance ID (current nitro format)."""
    assert re.fullmatch(_PATTERN, "i-0abcdef12345678a")  # type: ignore[arg-type]


def test_matches_typical_production_instance_id():
    """Typical production-style instance ID used in AWS documentation."""
    assert re.fullmatch(_PATTERN, "i-0abcdef12345")  # type: ignore[arg-type]


def test_rejects_missing_i_prefix():
    """IDs without the 'i-' prefix must not match."""
    assert not re.fullmatch(_PATTERN, "0abcdef1234567890")  # type: ignore[arg-type]


def test_rejects_azure_style_id():
    """Azure VM IDs (subscription-scoped ARM paths) must not match."""
    assert not re.fullmatch(  # type: ignore[arg-type]
        _PATTERN,
        "/subscriptions/abc/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm",
    )


def test_rejects_gcp_numeric_id():
    """GCP instance numeric IDs must not match."""
    assert not re.fullmatch(_PATTERN, "1234567890123456789")  # type: ignore[arg-type]


def test_rejects_arbitrary_string():
    """Arbitrary non-ID strings must not match."""
    assert not re.fullmatch(_PATTERN, "not-an-id")  # type: ignore[arg-type]


def test_rejects_uppercase_hex():
    """AWS instance IDs use lowercase hex only; uppercase must be rejected."""
    assert not re.fullmatch(_PATTERN, "i-0ABCDEF1")  # type: ignore[arg-type]


def test_rejects_empty_string():
    """Empty string must not match."""
    assert not re.fullmatch(_PATTERN, "")  # type: ignore[arg-type]


def test_rejects_id_with_g_char():
    """Characters outside [a-f0-9] after the prefix must be rejected."""
    assert not re.fullmatch(_PATTERN, "i-0abcdefg")  # type: ignore[arg-type]
