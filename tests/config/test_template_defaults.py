"""Tests for template defaults hierarchical resolution."""

from unittest.mock import MagicMock, Mock

import pytest

from src.application.services.template_defaults_service import TemplateDefaultsService
from src.domain.base.ports.logging_port import LoggingPort


class TestTemplateDefaultsService:
    """Test suite for TemplateDefaultsService."""

    @pytest.fixture
    def mock_config_manager(self):
        """Mock configuration manager."""
        mock = MagicMock()
        return mock

    @pytest.fixture
    def mock_logger(self):
        """Mock logger."""
        mock = Mock(spec=LoggingPort)
        return mock

    @pytest.fixture
    def template_defaults_service(self, mock_config_manager, mock_logger):
        """Create TemplateDefaultsService instance."""
        return TemplateDefaultsService(mock_config_manager, mock_logger)

    @pytest.fixture
    def sample_provider_config(self):
        """Sample provider configuration with hierarchical defaults."""
        return MagicMock(
            **{
                "provider_defaults": {
                    "aws": MagicMock(
                        **{
                            "template_defaults": {
                                "image_id": "ami-aws-default",
                                "instance_type": "t2.micro",
                                "provider_api": "EC2Fleet",
                                "price_type": "ondemand",
                                "security_group_ids": ["sg-aws-default"],
                                "subnet_ids": ["subnet-aws-default"],
                            }
                        }
                    )
                },
                "providers": [
                    MagicMock(
                        **{
                            "name": "aws-primary",
                            "type": "aws",
                            "template_defaults": {
                                "provider_api": "SpotFleet",  # Override provider type default
                                "instance_type": "t3.medium",  # Override provider type default
                            },
                        }
                    ),
                    MagicMock(
                        **{
                            "name": "aws-secondary",
                            "type": "aws",
                            "template_defaults": None,  # No instance-specific defaults
                        }
                    ),
                ],
            }
        )

    @pytest.fixture
    def sample_template_config(self):
        """Sample template configuration."""
        return MagicMock(
            **{
                "model_dump.return_value": {
                    "max_number": 10,
                    "ami_resolution": {"enabled": True},
                    "default_price_type": "ondemand",
                    "default_allocation_strategy": "capacityOptimized",
                }
            }
        )

    def test_resolve_template_defaults_hierarchy(
        self,
        template_defaults_service,
        mock_config_manager,
        sample_provider_config,
        sample_template_config,
    ):
        """Test hierarchical default resolution."""
        # Setup mocks
        mock_config_manager.get_template_config.return_value = sample_template_config
        mock_config_manager.get_provider_config.return_value = sample_provider_config

        # Test template with minimal data
        template_dict = {
            "template_id": "test-template",
            "image_id": "ami-specific",  # This should override defaults
        }

        # Resolve defaults for aws-primary provider
        result = template_defaults_service.resolve_template_defaults(
            template_dict, "aws-primary")

        # Verify hierarchical resolution
        assert result["template_id"] == "test-template"
        assert result["image_id"] == "ami-specific"  # Template value (highest priority)
        assert result["provider_api"] == "SpotFleet"  # Provider instance default
        assert result["instance_type"] == "t3.medium"  # Provider instance default
        assert result["security_group_ids"] == [
            "sg-aws-default"]  # Provider type default
        assert result["price_type"] == "ondemand"  # Global default

    def test_resolve_template_defaults_no_provider_instance(
        self,
        template_defaults_service,
        mock_config_manager,
        sample_provider_config,
        sample_template_config,
    ):
        """Test default resolution without provider instance context."""
        # Setup mocks
        mock_config_manager.get_template_config.return_value = sample_template_config
        mock_config_manager.get_provider_config.return_value = sample_provider_config

        template_dict = {"template_id": "test-template"}

        # Resolve defaults without provider instance
        result = template_defaults_service.resolve_template_defaults(
            template_dict, None)

        # Should only have global defaults
        assert result["template_id"] == "test-template"
        assert result["price_type"] == "ondemand"  # Global default
        assert result["max_number"] == 10  # Global default
        # Should not have provider-specific defaults
        assert "image_id" not in result or result["image_id"] is None

    def test_resolve_provider_api_default_hierarchy(
        self,
        template_defaults_service,
        mock_config_manager,
        sample_provider_config,
        sample_template_config,
    ):
        """Test provider_api default resolution hierarchy."""
        # Setup mocks
        mock_config_manager.get_template_config.return_value = sample_template_config
        mock_config_manager.get_provider_config.return_value = sample_provider_config

        # Template specifies provider_api (highest priority)
        template_dict = {"provider_api": "RunInstances"}
        result = template_defaults_service.resolve_provider_api_default(
            template_dict, "aws-primary"
        )
        assert result == "RunInstances"

        # Template doesn't specify, use provider instance default
        template_dict = {}
        result = template_defaults_service.resolve_provider_api_default(
            template_dict, "aws-primary"
        )
        assert result == "SpotFleet"  # From provider instance defaults

        # Provider instance has no override, use provider type default
        result = template_defaults_service.resolve_provider_api_default(
            template_dict, "aws-secondary"
        )
        assert result == "EC2Fleet"  # From provider type defaults

    def test_get_effective_template_defaults(
        self,
        template_defaults_service,
        mock_config_manager,
        sample_provider_config,
        sample_template_config,
    ):
        """Test getting effective defaults for a provider instance."""
        # Setup mocks
        mock_config_manager.get_template_config.return_value = sample_template_config
        mock_config_manager.get_provider_config.return_value = sample_provider_config

        # Get effective defaults for aws-primary
        result = template_defaults_service.get_effective_template_defaults(
            "aws-primary")

        # Should have merged defaults with proper precedence
        assert result["price_type"] == "ondemand"  # Global default
        assert result["image_id"] == "ami-aws-default"  # Provider type default
        assert result["provider_api"] == "SpotFleet"  # Provider instance override
        assert result["instance_type"] == "t3.medium"  # Provider instance override
        assert result["security_group_ids"] == [
            "sg-aws-default"]  # Provider type default

    def test_validate_template_defaults(
        self,
        template_defaults_service,
        mock_config_manager,
        sample_provider_config,
        sample_template_config,
    ):
        """Test template defaults validation."""
        # Setup mocks
        mock_config_manager.get_template_config.return_value = sample_template_config
        mock_config_manager.get_provider_config.return_value = sample_provider_config

        # Validate defaults for aws-primary
        result = template_defaults_service.validate_template_defaults("aws-primary")

        # Should be valid with no errors
        assert result["is_valid"] is True
        assert len(result["errors"]) == 0
        assert result["provider_instance"] == "aws-primary"

        # May have warnings about AWS-specific defaults in global config
        # (depending on the global config structure)

    def test_provider_type_extraction(
        self, template_defaults_service, mock_config_manager, sample_provider_config
    ):
        """Test provider type extraction from provider instance name."""
        mock_config_manager.get_provider_config.return_value = sample_provider_config

        # Test extraction from provider config
        provider_type = template_defaults_service._get_provider_type("aws-primary")
        assert provider_type == "aws"

        # Test fallback extraction from name
        provider_type = template_defaults_service._get_provider_type("provider1-east")
        assert provider_type == "provider1"

        # Test simple name
        provider_type = template_defaults_service._get_provider_type("provider2")
        assert provider_type == "provider2"

    def test_error_handling(self, template_defaults_service, mock_config_manager, mock_logger):
        """Test error handling in defaults resolution."""
        # Setup mock to raise exception
        mock_config_manager.get_template_config.side_effect = Exception("Config error")

        template_dict = {"template_id": "test"}

        # Should handle errors gracefully
        result = template_defaults_service.resolve_template_defaults(
            template_dict, "aws-primary")

        # Should return original template dict
        assert result["template_id"] == "test"

        # Should log warning
        mock_logger.warning.assert_called()

    def test_none_value_handling(
        self,
        template_defaults_service,
        mock_config_manager,
        sample_provider_config,
        sample_template_config,
    ):
        """Test that None values in templates don't override defaults."""
        # Setup mocks
        mock_config_manager.get_template_config.return_value = sample_template_config
        mock_config_manager.get_provider_config.return_value = sample_provider_config

        template_dict = {
            "template_id": "test-template",
            "image_id": None,  # None should not override defaults
            "instance_type": "t3.large",  # Non-None should override
        }

        result = template_defaults_service.resolve_template_defaults(
            template_dict, "aws-primary")

        # None value should be replaced by default
        assert result["image_id"] == "ami-aws-default"  # From provider type defaults
        # Non-None value should be preserved
        assert result["instance_type"] == "t3.large"  # From template


class TestTemplateDefaultsIntegration:
    """Integration tests for template defaults with other components."""

    def test_scheduler_strategy_integration(self):
        """Test integration with scheduler strategy."""
        from src.infrastructure.scheduler.strategies.symphony_hostfactory import (
            SymphonyHostFactorySchedulerStrategy,
        )

        # Create mock dependencies
        mock_config_manager = Mock()
        mock_logger = Mock()
        mock_template_defaults_service = Mock()

        # Setup mock template defaults service
        mock_template_defaults_service.resolve_provider_api_default.return_value = "SpotFleet"

        # Create scheduler strategy with template defaults service
        scheduler = SymphonyHostFactorySchedulerStrategy(
            mock_config_manager, mock_logger, mock_template_defaults_service
        )

        # Test template field mapping with defaults service
        template_dict = {
            "templateId": "test-template",
            "maxNumber": 5,
            "imageId": "ami-12345",
            # No providerApi specified
        }

        result = scheduler._map_template_fields(template_dict)

        # Should use defaults service for provider_api
        mock_template_defaults_service.resolve_provider_api_default.assert_called_once_with(
            template_dict
        )
        assert result["provider_api"] == "SpotFleet"

    def test_template_configuration_manager_integration(self):
        """Test integration with template configuration manager."""
        from src.infrastructure.template.configuration_manager import (
            TemplateConfigurationManager,
        )
        from src.infrastructure.template.dtos import TemplateDTO

        # Create mock dependencies
        mock_config_manager = Mock()
        mock_scheduler_strategy = Mock()
        mock_logger = Mock()
        mock_template_defaults_service = Mock()

        # Setup mock template defaults service
        mock_template_defaults_service.resolve_template_defaults.return_value = {
            "template_id": "test-template",
            "provider_api": "EC2Fleet",
            "image_id": "ami-default",
            "instance_type": "t2.micro",
        }

        # Create template configuration manager
        manager = TemplateConfigurationManager(
            mock_config_manager,
            mock_scheduler_strategy,
            mock_logger,
            template_defaults_service=mock_template_defaults_service,
        )

        # Create mock file metadata
        from datetime import datetime
        from pathlib import Path

        from src.infrastructure.template.configuration_manager import (
            TemplateFileMetadata,
        )

        file_metadata = TemplateFileMetadata(
            path=Path("/fake/path"),
            provider="aws-primary",
            file_type="main",
            priority=1,
            last_modified=datetime.now(),
        )

        # Mock the path exists check
        with pytest.mock.patch.object(Path, "exists", return_value=True):
            # Test template conversion with defaults
            template_dict = {"template_id": "test-template"}

            result = manager._convert_dict_to_template_dto(template_dict, file_metadata)

            # Should apply defaults through service
            mock_template_defaults_service.resolve_template_defaults.assert_called_once()
            assert isinstance(result, TemplateDTO)
            assert result.template_id == "test-template"
            assert result.provider_api == "EC2Fleet"


@pytest.mark.integration
class TestTemplateDefaultsEndToEnd:
    """End-to-end tests for template defaults system."""

    def test_complete_defaults_workflow(self):
        """Test complete workflow from configuration to template loading."""
        # This would be a more comprehensive test that sets up
        # real configuration files and tests the entire flow
        # For now, we'll keep it as a placeholder for future implementation
