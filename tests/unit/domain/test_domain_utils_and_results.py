"""Unit tests for domain utility functions and result value objects."""

import pytest

from orb.domain.base.results import (
    ProviderSelectionResult,
    ValidationLevel,
    ValidationResult,
)
from orb.domain.base.utils import extract_provider_type

# ---------------------------------------------------------------------------
# extract_provider_type
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractProviderType:
    def test_underscore_separator(self):
        assert extract_provider_type("aws_us_east_1") == "aws"

    def test_hyphen_separator(self):
        assert extract_provider_type("aws-us-east-1") == "aws"

    def test_no_separator_returns_full_name(self):
        assert extract_provider_type("aws") == "aws"

    def test_underscore_takes_precedence_over_hyphen(self):
        # Contains both _ and -, underscore check is first
        assert extract_provider_type("aws_us-east-1") == "aws"

    def test_empty_string_returns_empty_string(self):
        assert extract_provider_type("") == ""


# ---------------------------------------------------------------------------
# ProviderSelectionResult
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderSelectionResult:
    def test_defaults_alternatives_to_empty_list(self):
        result = ProviderSelectionResult(
            provider_type="aws",
            provider_name="aws-us-east-1",
            selection_reason="lowest cost",
        )
        assert result.alternatives == []

    def test_provider_instance_alias(self):
        result = ProviderSelectionResult(
            provider_type="aws",
            provider_name="aws-us-east-1",
            selection_reason="latency",
        )
        assert result.provider_instance == "aws-us-east-1"

    def test_confidence_defaults_to_1(self):
        result = ProviderSelectionResult(
            provider_type="aws",
            provider_name="aws-us-east-1",
            selection_reason="only option",
        )
        assert result.confidence == 1.0

    def test_with_alternatives(self):
        result = ProviderSelectionResult(
            provider_type="aws",
            provider_name="aws-us-east-1",
            selection_reason="primary",
            alternatives=["aws-eu-west-1", "aws-ap-southeast-1"],
        )
        assert len(result.alternatives) == 2


# ---------------------------------------------------------------------------
# ValidationLevel
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidationLevel:
    def test_all_members_present(self):
        members = {e.value for e in ValidationLevel}
        assert "strict" in members
        assert "permissive" in members
        assert "warn_only" in members

    def test_is_str_enum(self):
        assert isinstance(ValidationLevel.STRICT, str)


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidationResult:
    def test_creates_valid_result(self):
        r = ValidationResult(
            is_valid=True,
            provider_instance="aws-us-east-1",
            errors=[],
            warnings=[],
            supported_features=["EC2Fleet"],
            unsupported_features=[],
        )
        assert r.is_valid is True
        assert r.errors == []

    def test_none_errors_defaults_to_empty_list(self):
        r = ValidationResult(
            is_valid=False,
            provider_instance="aws-us-east-1",
            errors=None,  # type: ignore[arg-type]
            warnings=None,  # type: ignore[arg-type]
            supported_features=[],
            unsupported_features=[],
        )
        assert r.errors == []
        assert r.warnings == []
