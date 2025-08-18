"""Tests for AWS template fleet_type default assignment."""

import pytest

from providers.aws.domain.template.aggregate import AWSTemplate
from providers.aws.domain.template.value_objects import AWSFleetType, ProviderApi


class TestAWSTemplateDefaults:
    """Test AWS template default assignment logic."""

    def test_ec2_fleet_gets_default_fleet_type(self):
        """Test that EC2Fleet handler gets default fleet_type when not specified."""
        template_data = {
            "template_id": "test-template",
            "provider_api": ProviderApi.EC2_FLEET,
            "image_id": "ami-12345678",
            "subnet_ids": ["subnet-12345"],
            "instance_type": "t2.micro",
        }

        template = AWSTemplate(**template_data)

        # Should auto-assign fleet_type based on simple default (no config dependency)
        assert template.fleet_type is not None
        assert isinstance(template.fleet_type, AWSFleetType)
        # Default should be REQUEST (simple fallback)
        assert template.fleet_type == AWSFleetType.REQUEST

    def test_spot_fleet_gets_default_fleet_type(self):
        """Test that SpotFleet handler gets default fleet_type when not specified."""
        template_data = {
            "template_id": "test-template",
            "provider_api": ProviderApi.SPOT_FLEET,
            "image_id": "ami-12345678",
            "subnet_ids": ["subnet-12345"],
            "instance_type": "t2.micro",
        }

        template = AWSTemplate(**template_data)

        # Should auto-assign fleet_type based on simple default (no config dependency)
        assert template.fleet_type is not None
        assert isinstance(template.fleet_type, AWSFleetType)
        # Default should be REQUEST (simple fallback)
        assert template.fleet_type == AWSFleetType.REQUEST

    def test_run_instances_no_fleet_type(self):
        """Test that RunInstances handler doesn't get fleet_type."""
        template_data = {
            "template_id": "test-template",
            "provider_api": ProviderApi.RUN_INSTANCES,
            "image_id": "ami-12345678",
            "subnet_ids": ["subnet-12345"],
            "instance_type": "t2.micro",
        }

        template = AWSTemplate(**template_data)

        # Should NOT assign fleet_type for RunInstances
        assert template.fleet_type is None

    def test_asg_no_fleet_type(self):
        """Test that ASG handler doesn't get fleet_type."""
        template_data = {
            "template_id": "test-template",
            "provider_api": ProviderApi.ASG,
            "image_id": "ami-12345678",
            "subnet_ids": ["subnet-12345"],
            "instance_type": "t2.micro",
        }

        template = AWSTemplate(**template_data)

        # Should NOT assign fleet_type for ASG
        assert template.fleet_type is None

    def test_explicit_fleet_type_preserved(self):
        """Test that explicitly provided fleet_type is preserved."""
        template_data = {
            "template_id": "test-template",
            "provider_api": ProviderApi.EC2_FLEET,
            "fleet_type": AWSFleetType.MAINTAIN,
            "image_id": "ami-12345678",
            "subnet_ids": ["subnet-12345"],
            "instance_type": "t2.micro",
        }

        template = AWSTemplate(**template_data)

        # Should preserve explicitly provided fleet_type
        assert template.fleet_type == AWSFleetType.MAINTAIN


class TestAWSTemplateExtensionConfig:
    """Test AWS template extension configuration."""

    def test_fleet_type_field_exists(self):
        """Test that fleet_type field exists in extension config."""
        from providers.aws.configuration.template_extension import (
            AWSTemplateExtensionConfig,
        )

        config = AWSTemplateExtensionConfig()

        # Should have fleet_type field
        assert hasattr(config, "fleet_type")
        assert config.fleet_type is None  # Default should be None

    def test_fleet_type_in_template_defaults(self):
        """Test that fleet_type is included in template defaults."""
        from providers.aws.configuration.template_extension import (
            AWSTemplateExtensionConfig,
        )

        config = AWSTemplateExtensionConfig(fleet_type="request")
        defaults = config.to_template_defaults()

        # Should include fleet_type in defaults
        assert "fleet_type" in defaults
        assert defaults["fleet_type"] == "request"

    def test_fleet_type_none_not_in_defaults(self):
        """Test that None fleet_type is not included in defaults."""
        from providers.aws.configuration.template_extension import (
            AWSTemplateExtensionConfig,
        )

        config = AWSTemplateExtensionConfig(fleet_type=None)
        defaults = config.to_template_defaults()

        # Should NOT include None values in defaults
        assert "fleet_type" not in defaults


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
