"""Tests for AWS validation adapter - infrastructure layer validation."""

from unittest.mock import Mock

import pytest

from src.domain.base.ports.logging_port import LoggingPort
from src.providers.aws.configuration.validator import AWSProviderConfig
from src.providers.aws.infrastructure.adapters.aws_validation_adapter import (
    AWSValidationAdapter,
)


class TestAWSValidationAdapter:
    """Test AWS validation adapter functionality."""

    @pytest.fixture
    def mock_logger(self):
        """Create mock logger."""
        return Mock(spec=LoggingPort)

    @pytest.fixture
    def mock_aws_config(self):
        """Create mock AWS configuration."""
        config = Mock(spec=AWSProviderConfig)

        # Mock handlers configuration
        config.handlers = Mock()
        config.handlers.types = {
            "ec2_fleet": "EC2Fleet",
            "spot_fleet": "SpotFleet",
            "asg": "ASG",
            "run_instances": "RunInstances",
        }

        # Mock handler capabilities
        config.handlers.capabilities = {
            "EC2Fleet": Mock(
                default_fleet_type="instant",
                supported_fleet_types=["instant", "request", "maintain"],
            ),
            "SpotFleet": Mock(
                default_fleet_type="request",
                supported_fleet_types=["request", "maintain"],
            ),
        }

        return config

    @pytest.fixture
    def validation_adapter(self, mock_aws_config, mock_logger):
        """Create AWS validation adapter with mocked dependencies."""
        return AWSValidationAdapter(mock_aws_config, mock_logger)

    def test_get_provider_type(self, validation_adapter):
        """Test provider type identification."""
        assert validation_adapter.get_provider_type() == "aws"

    def test_validate_provider_api_valid(self, validation_adapter):
        """Test validation of valid provider API."""
        assert validation_adapter.validate_provider_api("EC2Fleet") is True
        assert validation_adapter.validate_provider_api("SpotFleet") is True
        assert validation_adapter.validate_provider_api("ASG") is True
        assert validation_adapter.validate_provider_api("RunInstances") is True

    def test_validate_provider_api_invalid(self, validation_adapter):
        """Test validation of invalid provider API."""
        assert validation_adapter.validate_provider_api("InvalidAPI") is False
        assert validation_adapter.validate_provider_api("") is False
        assert validation_adapter.validate_provider_api(
            "ec2fleet") is False  # Case sensitive

    def test_get_supported_provider_apis(self, validation_adapter):
        """Test getting supported provider APIs."""
        supported_apis = validation_adapter.get_supported_provider_apis()

        assert "EC2Fleet" in supported_apis
        assert "SpotFleet" in supported_apis
        assert "ASG" in supported_apis
        assert "RunInstances" in supported_apis
        assert len(supported_apis) == 4

    def test_get_default_fleet_type_for_api(self, validation_adapter):
        """Test getting default fleet type for API."""
        assert validation_adapter.get_default_fleet_type_for_api(
            "EC2Fleet") == "instant"
        assert validation_adapter.get_default_fleet_type_for_api(
            "SpotFleet") == "request"

    def test_get_default_fleet_type_for_unsupported_api(self, validation_adapter):
        """Test getting default fleet type for unsupported API raises error."""
        with pytest.raises(ValueError, match="Unsupported AWS provider API: InvalidAPI"):
            validation_adapter.get_default_fleet_type_for_api("InvalidAPI")

    def test_get_valid_fleet_types_for_api(self, validation_adapter):
        """Test getting valid fleet types for API."""
        ec2_fleet_types = validation_adapter.get_valid_fleet_types_for_api("EC2Fleet")
        assert "instant" in ec2_fleet_types
        assert "request" in ec2_fleet_types
        assert "maintain" in ec2_fleet_types

        spot_fleet_types = validation_adapter.get_valid_fleet_types_for_api("SpotFleet")
        assert "request" in spot_fleet_types
        assert "maintain" in spot_fleet_types
        assert "instant" not in spot_fleet_types

    def test_validate_fleet_type_for_api_valid(self, validation_adapter):
        """Test validation of valid fleet type for API."""
        assert validation_adapter.validate_fleet_type_for_api(
            "instant", "EC2Fleet") is True
        assert validation_adapter.validate_fleet_type_for_api(
            "request", "EC2Fleet") is True
        assert validation_adapter.validate_fleet_type_for_api(
            "request", "SpotFleet") is True
        assert validation_adapter.validate_fleet_type_for_api(
            "maintain", "SpotFleet") is True

    def test_validate_fleet_type_for_api_invalid(self, validation_adapter):
        """Test validation of invalid fleet type for API."""
        assert validation_adapter.validate_fleet_type_for_api(
            "instant", "SpotFleet") is False
        assert validation_adapter.validate_fleet_type_for_api(
            "invalid", "EC2Fleet") is False

    def test_validate_template_configuration_valid(self, validation_adapter):
        """Test validation of valid template configuration."""
        template_config = {
            "provider_api": "EC2Fleet",
            "fleet_type": "instant",
            "image_id": "ami-12345678",
            "instance_type": "t3.micro",
            "subnet_ids": ["subnet-12345678"],
            "security_group_ids": ["sg-12345678"],
            "percent_on_demand": 50,
        }

        result = validation_adapter.validate_template_configuration(template_config)

        assert result["valid"] is True
        assert len(result["errors"]) == 0
        assert "provider_api" in result["validated_fields"]
        assert "fleet_type" in result["validated_fields"]
        assert "image_id" in result["validated_fields"]

    def test_validate_template_configuration_invalid_provider_api(self, validation_adapter):
        """Test validation with invalid provider API."""
        template_config = {"provider_api": "InvalidAPI", "fleet_type": "request"}

        result = validation_adapter.validate_template_configuration(template_config)

        assert result["valid"] is False
        assert any(
            "Unsupported AWS provider API: InvalidAPI" in error for error in result["errors"]
        )

    def test_validate_template_configuration_incompatible_fleet_type(self, validation_adapter):
        """Test validation with incompatible fleet type."""
        template_config = {
            "provider_api": "SpotFleet",
            "fleet_type": "instant",  # Invalid for SpotFleet
        }

        result = validation_adapter.validate_template_configuration(template_config)

        assert result["valid"] is False
        assert any("not compatible with AWS API" in error for error in result["errors"])

    def test_validate_template_configuration_invalid_ami_id(self, validation_adapter):
        """Test validation with invalid AMI ID."""
        template_config = {
            "provider_api": "EC2Fleet",
            "image_id": "invalid-ami-id",  # Should start with 'ami-'
        }

        result = validation_adapter.validate_template_configuration(template_config)

        assert result["valid"] is False
        assert any("Invalid AWS AMI ID format" in error for error in result["errors"])

    def test_validate_template_configuration_invalid_subnet_id(self, validation_adapter):
        """Test validation with invalid subnet ID."""
        template_config = {
            "provider_api": "EC2Fleet",
            "subnet_ids": ["invalid-subnet-id"],  # Should start with 'subnet-'
        }

        result = validation_adapter.validate_template_configuration(template_config)

        assert result["valid"] is False
        assert any(
            "Invalid AWS subnet ID format" in error for error in result["errors"])

    def test_validate_template_configuration_invalid_percent_on_demand(self, validation_adapter):
        """Test validation with invalid percent_on_demand."""
        template_config = {
            "provider_api": "EC2Fleet",
            "percent_on_demand": 150,
        }  # Should be 0-100

        result = validation_adapter.validate_template_configuration(template_config)

        assert result["valid"] is False
        assert any(
            "percent_on_demand must be between 0 and 100" in error for error in result["errors"]
        )

    def test_is_valid_instance_type(self, validation_adapter):
        """Test instance type validation."""
        # Valid instance types
        assert validation_adapter._is_valid_instance_type("t3.micro") is True
        assert validation_adapter._is_valid_instance_type("m5.large") is True
        assert validation_adapter._is_valid_instance_type("c5.xlarge") is True

        # Invalid instance types
        assert validation_adapter._is_valid_instance_type("invalid") is False
        assert validation_adapter._is_valid_instance_type("t3") is False
        assert validation_adapter._is_valid_instance_type("") is False


class TestAWSValidationAdapterErrorHandling:
    """Test error handling in AWS validation adapter."""

    @pytest.fixture
    def mock_logger(self):
        """Create mock logger."""
        return Mock(spec=LoggingPort)

    @pytest.fixture
    def broken_aws_config(self):
        """Create AWS configuration that raises errors."""
        config = Mock(spec=AWSProviderConfig)
        config.handlers = Mock()
        config.handlers.types = Mock(side_effect=Exception("Config error"))
        return config

    def test_validate_provider_api_with_config_error(self, broken_aws_config, mock_logger):
        """Test provider API validation when config access fails."""
        adapter = AWSValidationAdapter(broken_aws_config, mock_logger)

        result = adapter.validate_provider_api("EC2Fleet")

        assert result is False
        mock_logger.error.assert_called()

    def test_get_supported_provider_apis_with_config_error(self, broken_aws_config, mock_logger):
        """Test getting supported APIs when config access fails."""
        adapter = AWSValidationAdapter(broken_aws_config, mock_logger)

        result = adapter.get_supported_provider_apis()

        assert result == []
        mock_logger.error.assert_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
