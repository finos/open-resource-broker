"""Tests for ProviderCapabilityService."""

from unittest.mock import Mock

import pytest

from src.application.services.provider_capability_service import (
    ProviderCapabilityService,
    ValidationLevel,
    ValidationResult,
)
from src.domain.base.ports import LoggingPort
from src.domain.template.aggregate import Template
from src.providers.base.strategy.provider_strategy import ProviderOperationType


class TestProviderCapabilityService:
    """Test suite for ProviderCapabilityService."""

    @pytest.fixture
    def mock_logger(self):
        """Mock logger for testing."""
        return Mock(spec=LoggingPort)

    @pytest.fixture
    def service(self, mock_logger):
        """Create ProviderCapabilityService instance for testing."""
        return ProviderCapabilityService(mock_logger)

    @pytest.fixture
    def aws_template_ec2fleet(self):
        """Template using AWS EC2Fleet API."""
        return Template(
            template_id="aws-ec2fleet-test",
            provider_api="EC2Fleet",
            price_type="ondemand",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=5,
        )

    @pytest.fixture
    def aws_template_spot(self):
        """Template using AWS SpotFleet API with spot pricing."""
        return Template(
            template_id="aws-spot-test",
            provider_api="SpotFleet",
            price_type="spot",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=10,
        )

    @pytest.fixture
    def template_with_fleet_type(self):
        """Template with fleet type in metadata."""
        return Template(
            template_id="fleet-type-test",
            provider_api="EC2Fleet",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=3,
            metadata={"fleet_type": "instant"},
        )

    @pytest.fixture
    def template_high_instance_count(self):
        """Template requesting high instance count."""
        return Template(
            template_id="high-count-test",
            provider_api="RunInstances",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=500,  # Exceeds RunInstances limit
        )

    def test_validate_template_requirements_valid_ec2fleet(self, service, aws_template_ec2fleet):
        """Test validation of valid EC2Fleet template."""
        result = service.validate_template_requirements(
            aws_template_ec2fleet, "aws-us-east-1", ValidationLevel.STRICT
        )

        assert result.is_valid
        assert result.provider_instance == "aws-us-east-1"
        assert len(result.errors) == 0
        assert "API: EC2Fleet" in result.supported_features
        assert "Pricing: On-demand instances" in result.supported_features
        assert "Instance count: 5 (within limit)" in result.supported_features

    def test_validate_template_requirements_valid_spot(self, service, aws_template_spot):
        """Test validation of valid SpotFleet template."""
        result = service.validate_template_requirements(
            aws_template_spot, "aws-us-east-1", ValidationLevel.STRICT
        )

        assert result.is_valid
        assert "API: SpotFleet" in result.supported_features
        assert "Pricing: Spot instances" in result.supported_features

    def test_validate_template_requirements_unsupported_api(self, service):
        """Test validation with unsupported API."""
        template = Template(
            template_id="unsupported-api-test",
            provider_api="UnsupportedAPI",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=1,
        )

        result = service.validate_template_requirements(
            template, "aws-us-east-1", ValidationLevel.STRICT
        )

        assert not result.is_valid
        assert len(result.errors) > 0
        assert any(
            "does not support API 'UnsupportedAPI'" in error for error in result.errors)

    def test_validate_template_requirements_spot_on_runinstances(self, service):
        """Test validation of spot pricing on RunInstances (should fail)."""
        template = Template(
            template_id="spot-runinstances-test",
            provider_api="RunInstances",
            price_type="spot",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=1,
        )

        result = service.validate_template_requirements(
            template, "aws-us-east-1", ValidationLevel.STRICT
        )

        assert not result.is_valid
        assert any("does not support spot instances" in error for error in result.errors)

    def test_validate_template_requirements_high_instance_count(
        self, service, template_high_instance_count
    ):
        """Test validation with instance count exceeding API limits."""
        result = service.validate_template_requirements(
            template_high_instance_count, "aws-us-east-1", ValidationLevel.STRICT
        )

        assert not result.is_valid
        assert any("exceeds API limit" in error for error in result.errors)

    def test_validate_template_requirements_fleet_type_support(
        self, service, template_with_fleet_type
    ):
        """Test validation of fleet type support."""
        result = service.validate_template_requirements(
            template_with_fleet_type, "aws-us-east-1", ValidationLevel.STRICT
        )

        assert result.is_valid
        assert "Fleet type: instant" in result.supported_features

    def test_validate_template_requirements_unsupported_fleet_type(self, service):
        """Test validation with unsupported fleet type."""
        template = Template(
            template_id="unsupported-fleet-test",
            provider_api="SpotFleet",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=1,
            metadata={"fleet_type": "instant"},  # SpotFleet doesn't support instant
        )

        result = service.validate_template_requirements(
            template, "aws-us-east-1", ValidationLevel.STRICT
        )

        assert not result.is_valid
        assert any(
            "does not support fleet type 'instant'" in error for error in result.errors)

    def test_validate_template_requirements_no_api_specified(self, service):
        """Test validation with no provider API specified."""
        template = Template(
            template_id="no-api-test",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=1,
        )

        result = service.validate_template_requirements(
            template, "aws-us-east-1", ValidationLevel.LENIENT
        )

        assert result.is_valid
        assert "No provider API specified in template" in result.warnings

    def test_validate_template_requirements_lenient_mode(self, service):
        """Test validation in lenient mode (warnings don't fail validation)."""
        template = Template(
            template_id="lenient-test",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=1,
        )

        result = service.validate_template_requirements(
            template, "aws-us-east-1", ValidationLevel.LENIENT
        )

        assert result.is_valid
        assert len(result.warnings) > 0
        assert len(result.errors) == 0

    def test_validate_template_requirements_strict_mode_warnings_as_errors(self, service):
        """Test that strict mode treats warnings as errors."""
        # Mock the service to generate warnings
        template = Template(
            template_id="strict-warnings-test",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=1,
        )

        # First get lenient result to see warnings
        lenient_result = service.validate_template_requirements(
            template, "aws-us-east-1", ValidationLevel.LENIENT
        )

        # Then test strict mode
        strict_result = service.validate_template_requirements(
            template, "aws-us-east-1", ValidationLevel.STRICT
        )

        # In strict mode, warnings should become errors if any exist
        if lenient_result.warnings:
            assert not strict_result.is_valid
            assert len(strict_result.errors) >= len(lenient_result.warnings)

    def test_validate_template_requirements_basic_mode(self, service):
        """Test validation in basic mode (only critical errors)."""
        template = Template(
            template_id="basic-test",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=1,
        )

        result = service.validate_template_requirements(
            template, "aws-us-east-1", ValidationLevel.BASIC
        )

        assert result.is_valid
        assert len(result.warnings) == 0  # Basic mode clears warnings

    def test_validate_template_requirements_exception_handling(self, service, mock_logger):
        """Test validation exception handling."""
        # Create a template that will cause an exception in validation
        template = Template(
            template_id="exception-test",
            provider_api="EC2Fleet",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=1,
        )

        # Mock the _get_provider_capabilities to raise an exception
        original_method = service._get_provider_capabilities
        service._get_provider_capabilities = Mock(
            side_effect=Exception("Test exception"))

        result = service.validate_template_requirements(
            template, "aws-us-east-1", ValidationLevel.STRICT
        )

        assert not result.is_valid
        assert any("Validation error: Test exception" in error for error in result.errors)

        # Restore original method
        service._get_provider_capabilities = original_method

    def test_get_provider_api_capabilities(self, service):
        """Test getting provider API capabilities."""
        capabilities = service.get_provider_api_capabilities(
            "aws-us-east-1", "EC2Fleet")

        assert isinstance(capabilities, dict)
        assert "supports_spot" in capabilities
        assert "supports_on_demand" in capabilities
        assert "max_instances" in capabilities
        assert capabilities["supports_spot"] is True
        assert capabilities["supports_on_demand"] is True
        assert capabilities["max_instances"] == 1000

    def test_get_provider_api_capabilities_unknown_api(self, service):
        """Test getting capabilities for unknown API."""
        capabilities = service.get_provider_api_capabilities(
            "aws-us-east-1", "UnknownAPI")

        assert capabilities == {}

    def test_list_supported_apis(self, service):
        """Test listing supported APIs for provider."""
        apis = service.list_supported_apis("aws-us-east-1")

        assert isinstance(apis, list)
        assert "EC2Fleet" in apis
        assert "SpotFleet" in apis
        assert "RunInstances" in apis
        assert "ASG" in apis

    def test_list_supported_apis_unknown_provider(self, service):
        """Test listing APIs for unknown provider."""
        apis = service.list_supported_apis("unknown-provider")

        assert apis == []

    def test_check_api_compatibility_multiple_providers(self, service, aws_template_ec2fleet):
        """Test checking API compatibility across multiple providers."""
        provider_instances = ["aws-us-east-1", "aws-us-west-2", "aws-eu-west-1"]

        results = service.check_api_compatibility(
            aws_template_ec2fleet, provider_instances)

        assert isinstance(results, dict)
        assert len(results) == 3

        for provider_instance in provider_instances:
            assert provider_instance in results
            assert isinstance(results[provider_instance], ValidationResult)

    def test_get_default_capabilities_aws(self, service):
        """Test getting default capabilities for AWS provider."""
        capabilities = service._get_default_capabilities("aws-us-east-1")

        assert capabilities.provider_type == "aws"
        assert ProviderOperationType.CREATE_INSTANCES in capabilities.supported_operations
        assert ProviderOperationType.TERMINATE_INSTANCES in capabilities.supported_operations
        assert ProviderOperationType.GET_INSTANCE_STATUS in capabilities.supported_operations

        # Check features
        supported_apis = capabilities.get_feature("supported_apis", [])
        assert "EC2Fleet" in supported_apis
        assert "SpotFleet" in supported_apis
        assert "RunInstances" in supported_apis
        assert "ASG" in supported_apis

    def test_get_default_capabilities_unknown_provider(self, service):
        """Test getting default capabilities for unknown provider type."""
        capabilities = service._get_default_capabilities("unknown-provider")

        assert capabilities.provider_type == "unknown"
        assert ProviderOperationType.CREATE_INSTANCES in capabilities.supported_operations
        assert len(capabilities.supported_operations) == 1


class TestValidationResult:
    """Test suite for ValidationResult dataclass."""

    def test_validation_result_creation(self):
        """Test ValidationResult creation."""
        result = ValidationResult(
            is_valid=True,
            provider_instance="aws-us-east-1",
            errors=["error1"],
            warnings=["warning1"],
            supported_features=["feature1"],
            unsupported_features=["feature2"],
        )

        assert result.is_valid
        assert result.provider_instance == "aws-us-east-1"
        assert result.errors == ["error1"]
        assert result.warnings == ["warning1"]
        assert result.supported_features == ["feature1"]
        assert result.unsupported_features == ["feature2"]

    def test_validation_result_defaults(self):
        """Test ValidationResult with default values."""
        result = ValidationResult(
            is_valid=False,
            provider_instance="test-provider",
            errors=[],
            warnings=[],
            supported_features=[],
            unsupported_features=[],
        )

        assert not result.is_valid
        assert result.provider_instance == "test-provider"
        assert result.errors == []
        assert result.warnings == []
        assert result.supported_features == []
        assert result.unsupported_features == []


class TestValidationLevel:
    """Test suite for ValidationLevel enum."""

    def test_validation_level_values(self):
        """Test ValidationLevel enum values."""
        assert ValidationLevel.STRICT == "strict"
        assert ValidationLevel.LENIENT == "lenient"
        assert ValidationLevel.BASIC == "basic"
