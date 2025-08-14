"""Integration tests for field mapping implementation.

This module tests the complete field mapping flow from HostFactory JSON
through the scheduler strategy to the AWS template creation.
"""

from unittest.mock import Mock

import pytest

from src.config.manager import ConfigurationManager
from src.infrastructure.scheduler.hostfactory.field_mappings import (
    HostFactoryFieldMappings,
)
from src.infrastructure.scheduler.hostfactory.strategy import (
    HostFactorySchedulerStrategy,
)
from src.infrastructure.scheduler.hostfactory.transformations import (
    HostFactoryTransformations,
)
from src.providers.aws.domain.template.aggregate import AWSTemplate


class TestFieldMappingIntegration:
    """Test the complete field mapping integration."""

    def setup_method(self):
        """Set up test fixtures."""
        self.config_manager = Mock(spec=ConfigurationManager)
        self.logger = Mock()

        # Mock provider config
        provider_config = Mock()
        provider_config.active_provider = "aws-default"
        self.config_manager.get_provider_config.return_value = provider_config

        self.scheduler_strategy = HostFactorySchedulerStrategy(
            config_manager=self.config_manager, logger=self.logger
        )

    def test_field_mapping_registry_basic(self):
        """Test basic field mapping registry functionality."""
        # Test getting mappings for HostFactory + AWS
        mappings = HostFactoryFieldMappings.get_mappings("aws")

        # Verify generic mappings are present
        assert "templateId" in mappings
        assert mappings["templateId"] == "template_id"
        assert "vmType" in mappings
        assert mappings["vmType"] == "instance_type"
        assert "vmTypes" in mappings
        assert mappings["vmTypes"] == "instance_types"

        # Verify AWS-specific mappings are present
        assert "vmTypesOnDemand" in mappings
        assert mappings["vmTypesOnDemand"] == "instance_types_ondemand"
        assert "percentOnDemand" in mappings
        assert mappings["percentOnDemand"] == "percent_on_demand"
        assert "fleetRole" in mappings
        assert mappings["fleetRole"] == "fleet_role"

    def test_field_transformations(self):
        """Test field transformation utilities."""
        # Test subnet ID transformation
        single_subnet = HostFactoryTransformations.transform_subnet_id("subnet-123")
        assert single_subnet == ["subnet-123"]

        list_subnets = HostFactoryTransformations.transform_subnet_id(["subnet-123", "subnet-456"])
        assert list_subnets == ["subnet-123", "subnet-456"]

        # Test instance tags transformation
        tag_string = "env=prod;team=backend;cost-center=engineering"
        tags_dict = HostFactoryTransformations.transform_instance_tags(tag_string)
        expected_tags = {"env": "prod", "team": "backend", "cost-center": "engineering"}
        assert tags_dict == expected_tags

        # Test instance type consistency
        mapped_data = {"instance_types": {"t2.micro": 1, "t2.small": 2}}
        result = HostFactoryTransformations.ensure_instance_type_consistency(mapped_data)
        assert result["instance_type"] == "t2.micro"  # First from instance_types

    def test_scheduler_field_mapping_ondemand(self):
        """Test scheduler field mapping for OnDemand template."""
        hf_template = {
            "templateId": "OnDemand-Template",
            "maxNumber": 5,
            "imageId": "ami-12345678",
            "subnetId": "subnet-abcd1234",
            "vmType": "t2.micro",
            "securityGroupIds": ["sg-12345678"],
            "priceType": "ondemand",
            "keyName": "my-key",
            "instanceTags": "env=test;team=dev",
        }

        # Map fields using scheduler strategy
        mapped = self.scheduler_strategy._map_template_fields(hf_template)

        # Verify core field mappings
        assert mapped["template_id"] == "OnDemand-Template"
        assert mapped["max_instances"] == 5
        assert mapped["image_id"] == "ami-12345678"
        assert mapped["subnet_ids"] == ["subnet-abcd1234"]  # Transformed to list
        assert mapped["instance_type"] == "t2.micro"
        assert mapped["security_group_ids"] == ["sg-12345678"]
        assert mapped["price_type"] == "ondemand"
        assert mapped["key_name"] == "my-key"

        # Verify tag transformation
        expected_tags = {"env": "test", "team": "dev"}
        assert mapped["tags"] == expected_tags

    def test_scheduler_field_mapping_spot(self):
        """Test scheduler field mapping for Spot template."""
        hf_template = {
            "templateId": "Spot-Template",
            "maxNumber": 10,
            "imageId": "ami-87654321",
            "subnetId": ["subnet-1111", "subnet-2222"],
            "vmTypes": {"t2.medium": 1, "t3.medium": 2},
            "priceType": "spot",
            "maxSpotPrice": "0.05",
            "allocationStrategy": "diversified",
            "fleetRole": "arn:aws:iam::123456789012:role/aws-ec2-spot-fleet-role",
        }

        # Map fields using scheduler strategy
        mapped = self.scheduler_strategy._map_template_fields(hf_template)

        # Verify core mappings
        assert mapped["template_id"] == "Spot-Template"
        assert mapped["max_instances"] == 10
        assert mapped["image_id"] == "ami-87654321"
        assert mapped["subnet_ids"] == ["subnet-1111", "subnet-2222"]
        assert mapped["instance_types"] == {"t2.medium": 1, "t3.medium": 2}
        assert mapped["instance_type"] == "t2.medium"  # First from instance_types
        assert mapped["price_type"] == "spot"
        assert mapped["max_price"] == "0.05"
        assert mapped["allocation_strategy"] == "diversified"

        # Verify AWS-specific mappings
        assert mapped["fleet_role"] == "arn:aws:iam::123456789012:role/aws-ec2-spot-fleet-role"

    def test_scheduler_field_mapping_heterogeneous(self):
        """Test scheduler field mapping for Heterogeneous template."""
        hf_template = {
            "templateId": "Hetero-Template",
            "maxNumber": 20,
            "imageId": "ami-11223344",
            "subnetId": ["subnet-aaaa", "subnet-bbbb"],
            "vmTypes": {"t2.medium": 1, "t3.large": 2},
            "vmTypesOnDemand": {"t2.medium": 1},
            "priceType": "heterogeneous",
            "percentOnDemand": 30,
            "allocationStrategy": "capacityOptimized",
            "allocationStrategyOnDemand": "prioritized",
            "fleetRole": "AWSServiceRoleForEC2SpotFleet",
            "spotFleetRequestExpiry": 60,
            "poolsCount": 2,
        }

        # Map fields using scheduler strategy
        mapped = self.scheduler_strategy._map_template_fields(hf_template)

        # Verify core mappings
        assert mapped["template_id"] == "Hetero-Template"
        assert mapped["instance_types"] == {"t2.medium": 1, "t3.large": 2}
        assert mapped["instance_type"] == "t2.medium"
        assert mapped["price_type"] == "heterogeneous"

        # Verify AWS-specific heterogeneous mappings
        assert mapped["instance_types_ondemand"] == {"t2.medium": 1}
        assert mapped["percent_on_demand"] == 30
        assert mapped["allocation_strategy"] == "capacityOptimized"
        assert mapped["allocation_strategy_ondemand"] == "prioritized"
        assert mapped["fleet_role"] == "AWSServiceRoleForEC2SpotFleet"
        assert mapped["spot_fleet_request_expiry"] == 60
        assert mapped["pools_count"] == 2

    def test_aws_template_field_inheritance(self):
        """Test that AWSTemplate properly inherits fields from CoreTemplate."""
        template_data = {
            "template_id": "test-template",
            "instance_type": "t2.micro",
            "instance_types": {"t2.micro": 1, "t2.small": 2},
            "image_id": "ami-12345678",
            "subnet_ids": ["subnet-12345"],
            "provider_api": "EC2Fleet",
            "price_type": "ondemand",
            "instance_types_ondemand": {"t2.small": 1},
            "percent_on_demand": 50,
            "fleet_role": "test-role",
        }

        # Create AWSTemplate
        aws_template = AWSTemplate(**template_data)

        # Verify inherited fields from CoreTemplate
        assert aws_template.instance_type == "t2.micro"
        assert aws_template.instance_types == {"t2.micro": 1, "t2.small": 2}
        assert aws_template.image_id == "ami-12345678"
        assert aws_template.subnet_ids == ["subnet-12345"]

        # Verify AWS-specific extensions
        assert aws_template.instance_types_ondemand == {"t2.small": 1}
        assert aws_template.percent_on_demand == 50
        assert aws_template.fleet_role == "test-role"

    def test_provider_specific_field_detection(self):
        """Test provider-specific field detection."""
        # Test AWS-specific field detection
        is_aws_specific = HostFactoryFieldMappings.is_provider_specific_field(
            "aws", "vmTypesOnDemand"
        )
        assert is_aws_specific is True

        # Test generic field detection
        is_generic = HostFactoryFieldMappings.is_provider_specific_field("aws", "vmType")
        assert is_generic is False  # vmType is in generic mappings

    def test_supported_schedulers_and_providers(self):
        """Test supported schedulers and providers listing."""
        providers = HostFactoryFieldMappings.get_supported_providers()
        assert "aws" in providers


if __name__ == "__main__":
    pytest.main([__file__])
