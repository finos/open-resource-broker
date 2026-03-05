"""Integration tests for OCP compliance implementation."""

import os
from unittest.mock import Mock, patch

import pytest

from config.schemas.provider_strategy_schema import ProviderConfig, ProviderInstanceConfig
from providers.registry import get_provider_registry


@pytest.mark.integration
class TestOCPComplianceIntegration:
    """Integration tests for OCP compliance with provider registry."""

    def setup_method(self):
        """Set up test fixtures."""
        registry = get_provider_registry()
        registry.clear_registrations()

    def test_provider_registry_integration(self):
        """Test complete provider registry integration."""
        registry = get_provider_registry()

        def mock_strategy_factory(config):
            return f"strategy_{config.name}_{config.type}"

        def mock_config_factory(data):
            return f"config_{data}"

        registry.register_provider(
            provider_type="aws",
            strategy_factory=mock_strategy_factory,
            config_factory=mock_config_factory,
        )

        assert "aws" in registry.get_registered_providers()

        config = ProviderInstanceConfig(
            name="test-instance",
            type="aws",
            enabled=True,
            config={"key": "value"},
        )

        strategy = registry.create_strategy("aws", config)
        assert strategy == "strategy_test-instance_aws"

        config_obj = registry.create_config("aws", {"test": "data"})
        assert config_obj == "config_{'test': 'data'}"

    @patch("providers.aws.strategy.aws_provider_strategy.AWSProviderStrategy")
    def test_aws_provider_registration_integration(self, mock_aws_strategy):
        """Test AWS provider registration integration."""
        from providers.aws.registration import register_aws_provider

        mock_strategy_instance = Mock()
        mock_aws_strategy.return_value = mock_strategy_instance

        register_aws_provider()

        registry = get_provider_registry()
        assert "aws" in registry.get_registered_providers()

        config = ProviderInstanceConfig(
            name="aws-test",
            type="aws",
            enabled=True,
            config={"region": "us-east-1", "profile": "default"},
        )

        strategy = registry.create_strategy("aws", config)
        assert strategy is not None

    def test_provider_strategy_factory_integration(self):
        """Test provider strategy factory integration with registry."""
        registry = get_provider_registry()

        mock_strategy = Mock()
        mock_strategy.name = "test-strategy"

        def strategy_factory(config):
            return mock_strategy

        registry.register_provider(
            provider_type="test_type",
            strategy_factory=strategy_factory,
            config_factory=lambda x: Mock(),
        )

        provider_config = ProviderInstanceConfig(
            name="test-provider",
            type="test_type",
            enabled=True,
            config={"key": "value"},
        )

        result = registry.create_strategy("test_type", provider_config)

        assert result == mock_strategy
        assert result.name == "test-strategy"

    def test_template_services_integration(self):
        """Test template services integration with registry."""
        registry = get_provider_registry()

        mock_resolver = Mock()
        mock_resolver.resolve_template_resources = Mock(return_value="resolved_template")

        mock_validator = Mock()
        mock_validator.validate_template_config = Mock(return_value=[])

        registry.register_provider(
            provider_type="test_provider",
            strategy_factory=lambda x: Mock(),
            config_factory=lambda x: Mock(),
            resolver_factory=lambda: mock_resolver,
            validator_factory=lambda: mock_validator,
        )

        # Verify resolver and validator can be created from registry
        resolver = registry.create_resolver("test_provider")
        assert resolver is not None

        validator = registry.create_validator("test_provider")
        assert validator is not None

        # Verify they work as expected
        result = resolver.resolve_template_resources(Mock(), "test_provider")
        assert result == "resolved_template"

        errors = validator.validate_template_config(Mock())
        assert errors == []

    def test_command_handler_integration(self):
        """Test command handler integration with registry."""
        from application.commands.provider_handlers import RegisterProviderStrategyHandler
        from application.provider.commands import RegisterProviderStrategyCommand

        registry = get_provider_registry()
        mock_strategy = Mock()

        registry.register_provider(
            provider_type="test_provider",
            strategy_factory=lambda config: mock_strategy,
            config_factory=lambda x: Mock(),
        )

        # Build handler with correct constructor signature
        mock_container = Mock()
        mock_logger = Mock()
        mock_event_publisher = Mock()
        mock_error_handler = Mock()
        mock_registry_service = Mock()
        mock_registry_service.register_provider_strategy.return_value = True

        handler = RegisterProviderStrategyHandler(
            container=mock_container,
            logger=mock_logger,
            event_publisher=mock_event_publisher,
            error_handler=mock_error_handler,
            provider_registry_service=mock_registry_service,
        )

        command = RegisterProviderStrategyCommand(
            strategy_name="test-strategy",
            provider_type="test_provider",
            strategy_config={"key": "value"},
        )

        import asyncio

        result = asyncio.run(handler.handle(command))

        mock_registry_service.register_provider_strategy.assert_called_once_with(
            "test_provider", {"key": "value"}
        )
        assert result is None  # CQRS: command handlers return void

    def test_no_hard_coded_conditionals(self):
        """Test that no hard-coded provider conditionals exist in key files."""
        files_to_check = [
            "src/application/commands/provider_handlers.py",
        ]

        for file_path in files_to_check:
            full_path = os.path.join(os.path.dirname(__file__), "../..", file_path)
            if os.path.exists(full_path):
                with open(full_path) as f:
                    content = f.read()

                hard_coded_patterns = [
                    'if provider_type == "aws"',
                    "if provider_type == 'aws'",
                    'elif provider_type == "aws"',
                    "elif provider_type == 'aws'",
                ]

                for pattern in hard_coded_patterns:
                    assert pattern not in content, (
                        f"Found hard-coded conditional '{pattern}' in {file_path}"
                    )

    def test_configuration_schema_no_legacy_mode(self):
        """Test that configuration schema no longer supports legacy mode."""
        from config.schemas.provider_strategy_schema import ProviderMode

        # Verify LEGACY mode is not in enum
        assert not hasattr(ProviderMode, "LEGACY")
        mode_values = [mode.value for mode in ProviderMode]
        assert "legacy" not in mode_values

        # Verify ProviderConfig works with single provider (SINGLE mode)
        config = ProviderConfig(
            providers=[
                ProviderInstanceConfig(
                    name="aws-default",
                    type="aws",
                    enabled=True,
                    config={"region": "us-east-1"},
                )
            ]
        )

        assert config.get_mode() == ProviderMode.SINGLE

    def teardown_method(self):
        """Clean up after each test."""
        registry = get_provider_registry()
        registry.clear_registrations()
