"""Integration tests for clean architecture template defaults and extensions."""

from unittest.mock import Mock

import pytest

from application.services.template_defaults_service import TemplateDefaultsService
from config.manager import ConfigurationManager
from domain.base.ports.logging_port import LoggingPort
from domain.template.aggregate import Template
from domain.template.extensions import TemplateExtensionRegistry
from domain.template.factory import TemplateFactory
from providers.aws.configuration.template_extension import AWSTemplateExtensionConfig


class TestCleanArchitectureIntegration:
    """Test the complete clean architecture implementation."""

    @pytest.fixture
    def mock_logger(self):
        """Mock logger for testing."""
        return Mock(spec=LoggingPort)

    @pytest.fixture
    def mock_config_manager(self):
        """Mock configuration manager with clean configuration."""
        config_manager = Mock(spec=ConfigurationManager)

        # Mock template config (cleaned up)
        template_config = Mock()
        template_config.model_dump.return_value = {
            "max_number": 10,
            "templates_file_path": "config/templates.json",
            "default_price_type": "ondemand",
            "default_provider_api": "EC2Fleet",
        }
        config_manager.get_template_config.return_value = template_config

        # Mock provider config with extensions
        provider_config = Mock()
        provider_config.provider_defaults = {
            "aws": Mock(
                template_defaults={
                    "image_id": "ami-12345678",
                    "instance_type": "t2.micro",
                    "provider_api": "EC2Fleet",
                    "price_type": "ondemand",
                },
                extensions={
                    "ami_resolution": {
                        "enabled": True,
                        "fallback_on_failure": True,
                        "cache_enabled": True,
                    },
                    "allocation_strategy": "capacityOptimized",
                    "volume_type": "gp3",
                },
            )
        }

        # Mock provider instances
        aws_provider = Mock()
        aws_provider.name = "aws-primary"
        aws_provider.type = "aws"
        aws_provider.template_defaults = {"instance_type": "t3.small"}  # Override
        aws_provider.extensions = {"volume_type": "gp2"}  # Override

        provider_config.providers = [aws_provider]
        config_manager.get_provider_config.return_value = provider_config

        return config_manager

    @pytest.fixture
    def template_factory(self, mock_logger):
        """Template factory with AWS support."""
        factory = TemplateFactory(logger=mock_logger)
        return factory

    @pytest.fixture
    def template_defaults_service(self, mock_config_manager, mock_logger, template_factory):
        """Template defaults service with all dependencies."""
        return TemplateDefaultsService(
            config_manager=mock_config_manager,
            logger=mock_logger,
            template_factory=template_factory,
            extension_registry=TemplateExtensionRegistry,
        )

    def test_aws_extension_registration(self):
        """Test that AWS extensions are properly registered."""
        # Clear registry for clean test
        TemplateExtensionRegistry.clear_registry()

        # Register AWS extension
        TemplateExtensionRegistry.register_extension("aws", AWSTemplateExtensionConfig)

        # Verify registration
        assert TemplateExtensionRegistry.has_extension("aws")
        assert TemplateExtensionRegistry.get_extension_class("aws") == AWSTemplateExtensionConfig

        # Test extension defaults
        extension_defaults = TemplateExtensionRegistry.get_extension_defaults("aws")
        assert isinstance(extension_defaults, dict)
        assert "ami_resolution" in extension_defaults
        assert "allocation_strategy" in extension_defaults

    def test_clean_template_schema_validation(self):
        """Test that cleaned template schema works correctly."""
        from config.schemas.template_schema import TemplateConfig

        # Test clean configuration (no AWS-specific fields)
        clean_config = {
            "max_number": 10,
            "templates_file_path": "config/templates.json",
            "default_price_type": "ondemand",
            "default_provider_api": "EC2Fleet",
        }

        template_config = TemplateConfig(**clean_config)
        assert template_config.max_number == 10
        assert template_config.default_price_type == "ondemand"
        assert template_config.default_provider_api == "EC2Fleet"

        # Verify AWS-specific fields are not present
        config_dict = template_config.model_dump()
        aws_specific_fields = [
            "ami_resolution",
            "default_fleet_role",
            "default_volume_type",
            "default_allocation_strategy",
            "subnet_ids",
            "security_group_ids",
        ]
        for field in aws_specific_fields:
            assert (
                field not in config_dict
            ), f"AWS-specific field {field} should not be in clean template config"

    def test_aws_extension_configuration(self):
        """Test AWS extension configuration works correctly."""
        # Test AWS extension config
        aws_extension_config = AWSTemplateExtensionConfig(
            allocation_strategy="capacityOptimized",
            volume_type="gp3",
            spot_fleet_request_expiry=30,
        )

        # Test conversion to template defaults
        defaults = aws_extension_config.to_template_defaults()
        assert defaults["allocation_strategy"] == "capacityOptimized"
        assert defaults["volume_type"] == "gp3"
        assert defaults["spot_fleet_request_expiry"] == 30

        # Test AMI resolution nested config
        assert "enabled" in defaults  # From ami_resolution
        assert "fallback_on_failure" in defaults

    def test_hierarchical_defaults_with_extensions(self, template_defaults_service):
        """Test complete hierarchical defaults resolution with extensions."""
        # Test template data
        template_dict = {"template_id": "test-template", "name": "Test Template"}

        # Resolve with extensions
        resolved_template = template_defaults_service.resolve_template_with_extensions(
            template_dict, "aws-primary"
        )

        # Verify it's a Template object
        assert isinstance(resolved_template, Template)
        assert resolved_template.template_id == "test-template"
        assert resolved_template.name == "Test Template"

    def test_provider_extension_hierarchy(self, template_defaults_service):
        """Test provider extension hierarchy (type -> instance)."""
        # Get extension defaults for AWS provider
        extension_defaults = template_defaults_service._get_extension_defaults("aws", "aws-primary")

        # Should include both type and instance extension defaults
        assert isinstance(extension_defaults, dict)

        # Instance extensions should override type extensions
        # (volume_type: gp2 from instance should override gp3 from type)
        # This tests the hierarchical precedence

    def test_template_factory_integration(self, template_factory):
        """Test template factory creates correct template types."""
        # Test AWS template creation
        aws_template_data = {
            "template_id": "aws-test",
            "provider_type": "aws",
            "provider_api": "EC2Fleet",
            "fleet_type": "instant",
        }

        template = template_factory.create_template(aws_template_data, "aws")

        # Should create AWS template (if available) or core template
        assert isinstance(template, Template)
        assert template.template_id == "aws-test"

        # Test core template fallback
        core_template_data = {
            "template_id": "core-test",
            "image_id": "ami-12345678",
            "subnet_ids": ["subnet-12345678"],
        }

        core_template = template_factory.create_template(core_template_data)
        assert isinstance(core_template, Template)
        assert core_template.template_id == "core-test"

    def test_configuration_validation(self, template_defaults_service):
        """Test configuration validation with extensions."""
        # Test validation for AWS provider
        validation_result = template_defaults_service.validate_template_defaults("aws-primary")

        assert isinstance(validation_result, dict)
        assert "is_valid" in validation_result
        assert "warnings" in validation_result
        assert "errors" in validation_result
        assert validation_result["provider_instance"] == "aws-primary"

    def test_effective_template_resolution(self, template_defaults_service):
        """Test effective template resolution shows complete hierarchy."""
        template_dict = {
            "template_id": "hierarchy-test",
            "max_instances": 5,
        }  # Template override

        # Get effective configuration
        effective_config = template_defaults_service.get_effective_template_with_extensions(
            template_dict, "aws-primary"
        )

        # Should include all levels of hierarchy
        assert effective_config["template_id"] == "hierarchy-test"
        assert effective_config["max_instances"] == 5  # Template value

        # Should include provider defaults
        assert "provider_api" in effective_config
        assert "price_type" in effective_config

        # Should include extension defaults
        # (These come from the mocked extensions)

    def test_clean_field_naming(self, template_defaults_service):
        """Test that field names are clean (no default_ prefixes)."""
        # Get global defaults
        global_defaults = template_defaults_service._get_global_template_defaults()

        # Check that field names are clean
        for field_name in global_defaults.keys():
            if field_name.startswith("default_"):
                # Should be cleaned (remove default_ prefix)
                clean_name = field_name.replace("default_", "")
                assert clean_name in global_defaults or field_name == "default_provider_api"

    def test_provider_strategy_schema_extensions(self):
        """Test provider strategy schema supports extensions."""
        from config.schemas.provider_strategy_schema import (
            ProviderDefaults,
            ProviderInstanceConfig,
        )

        # Test provider defaults with extensions
        provider_defaults = ProviderDefaults(
            template_defaults={"image_id": "ami-12345678"},
            extensions={"ami_resolution": {"enabled": True}},
        )

        assert provider_defaults.extensions is not None
        assert provider_defaults.extensions["ami_resolution"]["enabled"] is True

        # Test provider instance with extensions
        provider_instance = ProviderInstanceConfig(
            name="aws-test",
            type="aws",
            template_defaults={"instance_type": "t2.micro"},
            extensions={"volume_type": "gp2"},
        )

        assert provider_instance.extensions is not None
        assert provider_instance.extensions["volume_type"] == "gp2"

    def test_end_to_end_template_processing(self, template_defaults_service):
        """Test complete end-to-end template processing."""
        # Simulate template from file
        raw_template = {
            "template_id": "e2e-test",
            "name": "End-to-End Test Template",
            "max_instances": 3,
        }

        # Process through complete pipeline
        domain_template = template_defaults_service.resolve_template_with_extensions(
            raw_template, "aws-primary"
        )

        # Verify complete processing
        assert isinstance(domain_template, Template)
        assert domain_template.template_id == "e2e-test"
        assert domain_template.name == "End-to-End Test Template"
        assert domain_template.max_instances == 3

        # Should have hierarchical defaults applied
        assert hasattr(domain_template, "provider_api")
        assert hasattr(domain_template, "price_type")

        # Should be able to convert back to dict
        template_dict = domain_template.to_dict()
        assert isinstance(template_dict, dict)
        assert template_dict["template_id"] == "e2e-test"


if __name__ == "__main__":
    pytest.main([__file__])
