"""Integration tests for OCP compliance implementation."""

from unittest.mock import Mock, patch

import pytest

from src.config.schemas.provider_strategy_schema import ProviderInstanceConfig
from src.infrastructure.registry.provider_registry import get_provider_registry


@pytest.mark.integration
class TestOCPComplianceIntegration:
    """Integration tests for OCP compliance with provider registry."""

    def setup_method(self):
        """Set up test fixtures."""
        # Clear registry for each test
        registry = get_provider_registry()
        registry.clear_registrations()

    def test_provider_registry_integration(self):
        """Test complete provider registry integration."""
        registry = get_provider_registry()

        # Mock provider factories
        def mock_strategy_factory(config):
            return f"strategy_{config.name}_{config.type}"

        def mock_config_factory(data):
            return f"config_{data}"

        # Register test provider
        registry.register_provider(
            provider_type="test_provider",
            strategy_factory=mock_strategy_factory,
            config_factory=mock_config_factory,
        )

        # Verify registration
        assert "test_provider" in registry.get_registered_providers()

        # Test strategy creation
        config = ProviderInstanceConfig(
            name="test-instance",
            type="test_provider",
            enabled=True,
            config={"key": "value"},
        )

        strategy = registry.create_strategy("test_provider", config)
        assert strategy == "strategy_test-instance_test_provider"

        # Test config creation
        config_obj = registry.create_config("test_provider", {"test": "data"})
        assert config_obj == "config_{'test': 'data'}"

    @patch("src.providers.aws.registration.AWSProviderStrategy")
    @patch("src.providers.aws.registration.AWSConfig")
    def test_aws_provider_registration_integration(self, mock_aws_config, mock_aws_strategy):
        """Test AWS provider registration integration."""
        from src.providers.aws.registration import register_aws_provider

        # Setup mocks
        mock_config_instance = Mock()
        mock_aws_config.return_value = mock_config_instance
        mock_strategy_instance = Mock()
        mock_aws_strategy.return_value = mock_strategy_instance

        # Register AWS provider
        register_aws_provider()

        # Verify AWS provider is registered
        registry = get_provider_registry()
        assert "aws" in registry.get_registered_providers()

        # Test AWS strategy creation
        config = ProviderInstanceConfig(
            name="aws-test",
            type="aws",
            enabled=True,
            config={"region": "us-east-1", "profile": "default"},
        )

        strategy = registry.create_strategy("aws", config)

        # Verify AWS config was created correctly
        mock_aws_config.assert_called_once_with(region="us-east-1", profile="default")
        mock_aws_strategy.assert_called_once()
        assert strategy == mock_strategy_instance

    @patch("src.infrastructure.factories.provider_strategy_factory.get_provider_registry")
    def test_provider_strategy_factory_integration(self, mock_get_registry):
        """Test provider strategy factory integration with registry."""
        from src.config.manager import ConfigurationManager
        from src.infrastructure.factories.provider_strategy_factory import (
            ProviderStrategyFactory,
        )
        from src.infrastructure.logging.logger import get_logger

        # Setup mock registry
        mock_registry = Mock()
        mock_strategy = Mock()
        mock_strategy.name = "test-strategy"
        mock_registry.create_strategy.return_value = mock_strategy
        mock_get_registry.return_value = mock_registry

        # Create factory
        config_manager = Mock(spec=ConfigurationManager)
        logger = get_logger(__name__)
        factory = ProviderStrategyFactory(config_manager, logger)

        # Test strategy creation
        provider_config = ProviderInstanceConfig(
            name="test-provider",
            type="test_type",
            enabled=True,
            config={"key": "value"},
        )

        result = factory._create_provider_strategy(provider_config)

        # Verify registry was used
        mock_get_registry.assert_called_once()
        mock_registry.create_strategy.assert_called_once_with("test_type", provider_config)
        assert result == mock_strategy
        assert result.name == "test-provider"

    def test_template_services_integration(self):
        """Test template services integration with registry."""
        from src.domain.template.aggregate import Template
        from src.domain.template.value_objects import TemplateId
        from src.infrastructure.template.template_resolver_service import (
            TemplateResolverService,
        )
        from src.infrastructure.template.template_validator_service import (
            TemplateValidatorService,
        )

        # Setup registry with mock resolver/validator
        registry = get_provider_registry()

        mock_resolver = Mock()
        mock_resolver.resolve_template_resources = Mock(return_value="resolved_template")

        mock_validator = Mock()
        mock_validator.validate_template_config = Mock(return_value=[])

        def resolver_factory():
            return mock_resolver

        def validator_factory():
            return mock_validator

        registry.register_provider(
            provider_type="test_provider",
            strategy_factory=lambda x: Mock(),
            config_factory=lambda x: Mock(),
            resolver_factory=resolver_factory,
            validator_factory=validator_factory,
        )

        # Test resolver service
        resolver_service = TemplateResolverService()
        template = Template(
            template_id=TemplateId("test-template"),
            name="Test Template",
            provider_api="test_provider",
            configuration={},
        )

        with patch.object(registry, "create_resolver", return_value=mock_resolver):
            result = resolver_service.resolve_template_resources(template, "test_provider")
            assert result == "resolved_template"

        # Test validator service
        validator_service = TemplateValidatorService()
        config = {
            "provider_api": "test_provider",
            "configuration": {"instance_type": "t2.micro", "image_id": "ami-12345"},
        }

        with patch.object(registry, "create_validator", return_value=mock_validator):
            errors = validator_service._validate_configuration_consistency(config)
            # Should have no errors since mock validator returns empty list
            assert len([e for e in errors if "require" in e]) == 0

    def test_command_handler_integration(self):
        """Test command handler integration with registry."""
        from src.application.commands.provider_handlers import (
            RegisterProviderStrategyHandler,
        )
        from src.application.provider.commands import RegisterProviderStrategyCommand

        # Setup mock dependencies
        mock_provider_context = Mock()
        mock_event_publisher = Mock()
        mock_logger = Mock()

        # Setup registry with mock strategy
        registry = get_provider_registry()
        mock_strategy = Mock()

        def strategy_factory(config):
            return mock_strategy

        registry.register_provider(
            provider_type="test_provider",
            strategy_factory=strategy_factory,
            config_factory=lambda x: Mock(),
        )

        # Create handler
        handler = RegisterProviderStrategyHandler(
            provider_context=mock_provider_context,
            event_publisher=mock_event_publisher,
            logger=mock_logger,
        )

        # Create command
        command = RegisterProviderStrategyCommand(
            strategy_name="test-strategy",
            provider_type="test_provider",
            strategy_config={"key": "value"},
        )

        # Execute command
        with patch(
            "src.application.commands.provider_handlers.get_provider_registry",
            return_value=registry,
        ):
            result = handler.handle(command)

        # Verify strategy was registered with context
        mock_provider_context.register_strategy.assert_called_once_with(
            mock_strategy, "test-strategy"
        )
        assert result is not None

    def test_no_hard_coded_conditionals(self):
        """Test that no hard-coded provider conditionals exist in key files."""
        import os

        # Files that should not have hard-coded provider conditionals
        files_to_check = [
            "src/infrastructure/factories/provider_strategy_factory.py",
            "src/infrastructure/template/template_resolver_service.py",
            "src/infrastructure/template/template_validator_service.py",
            "src/application/commands/provider_handlers.py",
        ]

        for file_path in files_to_check:
            if os.path.exists(file_path):
                with open(file_path, "r") as f:
                    content = f.read()

                # Check for hard-coded provider conditionals
                hard_coded_patterns = [
                    'if provider_type == "aws"',
                    "if provider_type == 'aws'",
                    'elif provider_type == "aws"',
                    "elif provider_type == 'aws'",
                    'provider_type.lower() == "aws"',
                    "provider_type.lower() == 'aws'",
                ]

                for pattern in hard_coded_patterns:
                    assert (
                        pattern not in content
                    ), f"Found hard-coded conditional '{pattern}' in {file_path}"

    def test_configuration_schema_no_legacy_mode(self):
        """Test that configuration schema no longer supports legacy mode."""
        from src.config.schemas.provider_strategy_schema import (
            ProviderMode,
            IntegratedProviderConfig,
        )

        # Verify LEGACY mode is not in enum
        assert not hasattr(ProviderMode, "LEGACY")
        assert ProviderMode.LEGACY not in [mode.value for mode in ProviderMode]

        # Verify configuration doesn't detect legacy mode
        config = IntegratedProviderConfig(
            providers=[
                ProviderInstanceConfig(
                    name="aws-default",
                    type="aws",
                    enabled=True,
                    config={"region": "us-east-1"},
                )
            ]
        )

        # Should be SINGLE mode, not LEGACY
        assert config.get_mode() == ProviderMode.SINGLE
        assert not hasattr(config, "is_legacy_mode") or not config.is_legacy_mode()

    def teardown_method(self):
        """Clean up after each test."""
        # Clear registry
        registry = get_provider_registry()
        registry.clear_registrations()
