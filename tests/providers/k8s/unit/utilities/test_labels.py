"""Unit tests for :func:`build_label_selector` and :func:`validate_namespace`."""

from __future__ import annotations

import pytest

from orb.providers.k8s.utilities.labels import (
    K8sValidationError,
    build_label_selector,
    validate_namespace,
)

# ---------------------------------------------------------------------------
# build_label_selector — happy paths
# ---------------------------------------------------------------------------


def test_basic_managed_selector() -> None:
    result = build_label_selector("orb.io", "managed", "true")
    assert result == "orb.io/managed=true"


def test_request_id_selector() -> None:
    result = build_label_selector("orb.io", "request-id", "abc-123")
    assert result == "orb.io/request-id=abc-123"


def test_dotted_prefix() -> None:
    result = build_label_selector("example.com", "managed", "yes")
    assert result == "example.com/managed=yes"


def test_empty_value_allowed() -> None:
    result = build_label_selector("orb.io", "managed", "")
    assert result == "orb.io/managed="


# ---------------------------------------------------------------------------
# build_label_selector — injection / validation rejections
# ---------------------------------------------------------------------------


def test_prefix_with_equals_rejected() -> None:
    with pytest.raises(K8sValidationError):
        build_label_selector("orb.io=bad", "managed", "true")


def test_prefix_with_space_rejected() -> None:
    with pytest.raises(K8sValidationError):
        build_label_selector("orb io", "managed", "true")


def test_empty_prefix_rejected() -> None:
    with pytest.raises(K8sValidationError):
        build_label_selector("", "managed", "true")


def test_key_with_equals_rejected() -> None:
    with pytest.raises(K8sValidationError):
        build_label_selector("orb.io", "managed=evil", "true")


def test_key_too_long_rejected() -> None:
    long_key = "a" * 64
    with pytest.raises(K8sValidationError):
        build_label_selector("orb.io", long_key, "true")


def test_value_with_equals_rejected() -> None:
    with pytest.raises(K8sValidationError):
        build_label_selector("orb.io", "managed", "true=injected")


def test_value_with_exclamation_rejected() -> None:
    with pytest.raises(K8sValidationError):
        build_label_selector("orb.io", "managed", "!notin")


def test_value_too_long_rejected() -> None:
    long_val = "a" * 64
    with pytest.raises(K8sValidationError):
        build_label_selector("orb.io", "managed", long_val)


def test_prefix_too_long_rejected() -> None:
    long_prefix = "a" * 254
    with pytest.raises(K8sValidationError):
        build_label_selector(long_prefix, "managed", "true")


# ---------------------------------------------------------------------------
# validate_namespace — happy paths
# ---------------------------------------------------------------------------


def test_valid_namespace_default() -> None:
    validate_namespace("default")  # must not raise


def test_valid_namespace_with_hyphens() -> None:
    validate_namespace("orb-system")


def test_valid_namespace_alphanumeric() -> None:
    validate_namespace("ns123")


# ---------------------------------------------------------------------------
# validate_namespace — rejections
# ---------------------------------------------------------------------------


def test_empty_namespace_rejected() -> None:
    with pytest.raises(K8sValidationError):
        validate_namespace("")


def test_namespace_with_slash_rejected() -> None:
    with pytest.raises(K8sValidationError):
        validate_namespace("orb/system")


def test_namespace_with_uppercase_rejected() -> None:
    with pytest.raises(K8sValidationError):
        validate_namespace("OrbSystem")


def test_namespace_too_long_rejected() -> None:
    with pytest.raises(K8sValidationError):
        validate_namespace("a" * 64)


def test_namespace_with_underscore_rejected() -> None:
    with pytest.raises(K8sValidationError):
        validate_namespace("orb_system")


def test_namespace_starting_with_hyphen_rejected() -> None:
    with pytest.raises(K8sValidationError):
        validate_namespace("-orb")


def test_namespace_ending_with_hyphen_rejected() -> None:
    with pytest.raises(K8sValidationError):
        validate_namespace("orb-")
