"""
Tests for SDK parameter mapping functionality.
"""

from dataclasses import dataclass
from typing import Optional

from orb.sdk.parameter_mapping import ParameterMapper


@dataclass
class MockCreateRequestCommand:
    """Mock command for testing parameter mapping."""

    template_id: str
    requested_count: int
    timeout: Optional[int] = 3600


@dataclass
class MockListTemplatesQuery:
    """Mock query for testing parameter mapping."""

    active_only: Optional[bool] = True
    provider_name: Optional[str] = None


class TestSDKParameterMapping:
    """Test SDK parameter mapping functionality."""

    def test_count_to_requested_count_mapping(self):
        """Test that 'count' maps to 'requested_count'."""
        kwargs = {"template_id": "test-template", "count": 5, "timeout": 1800}

        mapped = ParameterMapper.map_parameters(MockCreateRequestCommand, kwargs)

        assert "count" not in mapped
        assert mapped["requested_count"] == 5
        assert mapped["template_id"] == "test-template"
        assert mapped["timeout"] == 1800

    def test_provider_to_provider_name_mapping(self):
        """Test that 'provider' maps to 'provider_name' when parameter exists."""
        kwargs = {"active_only": False, "provider": "aws-prod"}

        mapped = ParameterMapper.map_parameters(MockListTemplatesQuery, kwargs)

        assert "provider" not in mapped
        assert mapped["provider_name"] == "aws-prod"
        assert mapped["active_only"] is False

    def test_no_mapping_when_target_parameter_missing(self):
        """Test that mapping doesn't occur when target parameter doesn't exist."""
        kwargs = {
            "template_id": "test-template",
            "provider": "aws-prod",  # MockCreateRequestCommand doesn't have provider_name
        }

        mapped = ParameterMapper.map_parameters(MockCreateRequestCommand, kwargs)

        # provider should remain unmapped since MockCreateRequestCommand has no provider_name
        assert "provider" in mapped
        assert "provider_name" not in mapped
        assert mapped["template_id"] == "test-template"

    def test_backward_compatibility_with_cqrs_names(self):
        """Test that CQRS parameter names still work (backward compatibility)."""
        kwargs = {
            "template_id": "test-template",
            "requested_count": 3,  # Using CQRS name directly
        }

        mapped = ParameterMapper.map_parameters(MockCreateRequestCommand, kwargs)

        assert mapped["requested_count"] == 3
        assert mapped["template_id"] == "test-template"

    def test_cqrs_name_precedence_over_cli_name(self):
        """Test that CQRS names take precedence when both are provided."""
        kwargs = {
            "template_id": "test-template",
            "count": 5,  # CLI name
            "requested_count": 3,  # CQRS name
        }

        mapped = ParameterMapper.map_parameters(MockCreateRequestCommand, kwargs)

        # CQRS name should take precedence (no mapping occurs)
        assert "count" in mapped  # count remains unmapped
        assert mapped["requested_count"] == 3
        assert mapped["template_id"] == "test-template"

    def test_get_supported_parameters_includes_aliases(self):
        """Test getting supported parameters including CLI aliases."""
        supported = ParameterMapper.get_supported_parameters(MockCreateRequestCommand)

        # Should include both direct parameters and CLI aliases
        assert "template_id" in supported
        assert "requested_count" in supported
        assert "timeout" in supported
        assert "count" in supported  # CLI alias

        # Verify mappings
        assert supported["count"] == "requested_count"
        assert supported["requested_count"] == "requested_count"
        assert supported["template_id"] == "template_id"

    def test_get_supported_parameters_with_provider_mapping(self):
        """Test getting supported parameters for query with provider_name."""
        supported = ParameterMapper.get_supported_parameters(MockListTemplatesQuery)

        assert "active_only" in supported
        assert "provider_name" in supported
        assert "provider" in supported  # CLI alias

        # Verify mappings
        assert supported["provider"] == "provider_name"
        assert supported["provider_name"] == "provider_name"

    def test_multiple_mappings_in_single_call(self):
        """Test multiple parameter mappings in a single call."""

        # This would be for a hypothetical command that has both mappings
        @dataclass
        class MockComplexCommand:
            template_id: str
            requested_count: int
            provider_name: Optional[str] = None
            timeout: Optional[int] = 3600

        kwargs = {
            "template_id": "test-template",
            "count": 5,  # Should map to requested_count
            "provider": "aws-prod",  # Should map to provider_name
            "timeout": 1800,
        }

        mapped = ParameterMapper.map_parameters(MockComplexCommand, kwargs)

        assert "count" not in mapped
        assert "provider" not in mapped
        assert mapped["requested_count"] == 5
        assert mapped["provider_name"] == "aws-prod"
        assert mapped["template_id"] == "test-template"
        assert mapped["timeout"] == 1800
