"""Tests for multi-instance provider support."""

from unittest.mock import Mock, patch

import pytest

from config.schemas.provider_strategy_schema import (
    ProviderConfig,
    ProviderInstanceConfig,
)
from infrastructure.registry.provider_registry import ProviderRegistry


class TestMultiInstanceProviderSupport:
    """Test multi-instance provider support functionality."""

    def test_provider_registry_register_instance(self):
        """Test registering named provider instances."""
        registry = ProviderRegistry()

        # Mock factory functions
        strategy_factory = Mock()
        config_factory = Mock()

        # Register first instance
        registry.register_provider_instance(
            provider_type="aws",
            instance_name="aws-us-east-1",
            strategy_factory=strategy_factory,
            config_factory=config_factory,
        )

        # Register second instance
        registry.register_provider_instance(
            provider_type="aws",
            instance_name="aws-eu-west-1",
            strategy_factory=strategy_factory,
            config_factory=config_factory,
        )

        # Verify instances are registered
        assert registry.is_provider_instance_registered("aws-us-east-1")
        assert registry.is_provider_instance_registered("aws-eu-west-1")
        assert not registry.is_provider_instance_registered("aws-ap-south-1")

        # Verify instance list
        instances = registry.get_registered_provider_instances()
        assert "aws-us-east-1" in instances
        assert "aws-eu-west-1" in instances
        assert len(instances) == 2

    def test_provider_registry_duplicate_instance_error(self):
        """Test error when registering duplicate instance names."""
        registry = ProviderRegistry()

        strategy_factory = Mock()
        config_factory = Mock()

        # Register first instance
        registry.register_provider_instance(
            provider_type="aws",
            instance_name="aws-us-east-1",
            strategy_factory=strategy_factory,
            config_factory=config_factory,
        )

        # Try to register duplicate instance name
        with pytest.raises(
            ValueError, match="Provider instance 'aws-us-east-1' is already registered"
        ):
            registry.register_provider_instance(
                provider_type="aws",
                instance_name="aws-us-east-1",  # Duplicate name
                strategy_factory=strategy_factory,
                config_factory=config_factory,
            )

    def test_provider_registry_unregister_instance(self):
        """Test unregistering provider instances."""
        registry = ProviderRegistry()

        strategy_factory = Mock()
        config_factory = Mock()

        # Register instance
        registry.register_provider_instance(
            provider_type="aws",
            instance_name="aws-us-east-1",
            strategy_factory=strategy_factory,
            config_factory=config_factory,
        )

        # Verify registered
        assert registry.is_provider_instance_registered("aws-us-east-1")

        # Unregister
        result = registry.unregister_provider_instance("aws-us-east-1")
        assert result is True

        # Verify unregistered
        assert not registry.is_provider_instance_registered("aws-us-east-1")

        # Try to unregister non-existent instance
        result = registry.unregister_provider_instance("non-existent")
        assert result is False

    def test_provider_registry_create_strategy_from_instance(self):
        """Test creating strategy from named instance."""
        registry = ProviderRegistry()

        # Mock strategy factory
        mock_strategy = Mock()
        strategy_factory = Mock(return_value=mock_strategy)
        config_factory = Mock()

        # Register instance
        registry.register_provider_instance(
            provider_type="aws",
            instance_name="aws-us-east-1",
            strategy_factory=strategy_factory,
            config_factory=config_factory,
        )

        # Create strategy from instance
        config = {"region": "us-east-1"}
        strategy = registry.create_strategy_from_instance("aws-us-east-1", config)

        # Verify strategy was created
        assert strategy == mock_strategy
        strategy_factory.assert_called_once_with(config)

    def test_multi_instance_registration_flow(self):
        """Test complete multi-instance registration flow."""
        # Create test configuration with multiple instances
        provider_config = ProviderConfig(
            selection_policy="ROUND_ROBIN",
            providers=[
                ProviderInstanceConfig(
                    name="aws-us-east-1",
                    type="aws",
                    enabled=True,
                    config={"region": "us-east-1"},
                ),
                ProviderInstanceConfig(
                    name="aws-eu-west-1",
                    type="aws",
                    enabled=True,
                    config={"region": "eu-west-1"},
                ),
                ProviderInstanceConfig(
                    name="aws-ap-south-1",
                    type="aws",
                    enabled=False,  # Disabled
                    config={"region": "ap-south-1"},
                ),
            ],
        )

        # Mock configuration manager
        mock_config_manager = Mock()
        mock_config_manager.get_provider_config.return_value = provider_config

        # Mock AWS registration
        with (
            patch(
                "src.infrastructure.di.provider_services.get_config_manager",
                return_value=mock_config_manager,
            ),
            patch("src.providers.aws.registration.register_aws_provider") as mock_aws_register,
            patch("src.infrastructure.di.provider_services.get_logger") as mock_logger,
        ):
            from infrastructure.di.provider_services import _register_providers

            # Execute registration
            _register_providers()

            # Verify AWS provider was registered twice (2 enabled instances)
            assert mock_aws_register.call_count == 2

            # Verify instance names were passed
            call_args_list = mock_aws_register.call_args_list
            instance_names = [call.kwargs.get("instance_name") for call in call_args_list]
            assert "aws-us-east-1" in instance_names
            assert "aws-eu-west-1" in instance_names
            assert "aws-ap-south-1" not in instance_names  # Disabled

    def test_provider_strategy_factory_with_instances(self):
        """Test ProviderStrategyFactory with named instances."""
        from infrastructure.factories.provider_strategy_factory import (
            ProviderStrategyFactory,
        )

        # Mock logger and config
        mock_logger = Mock()
        mock_config = Mock()

        factory = ProviderStrategyFactory(logger=mock_logger, config=mock_config)

        # Mock provider instance config
        provider_config = ProviderInstanceConfig(
            name="aws-us-east-1",
            type="aws",
            enabled=True,
            config={"region": "us-east-1"},
        )

        # Mock registry and strategy
        mock_strategy = Mock()
        mock_registry = Mock()
        mock_registry.is_provider_instance_registered.return_value = True
        mock_registry.create_strategy_from_instance.return_value = mock_strategy

        with patch(
            "src.infrastructure.factories.provider_strategy_factory.get_provider_registry",
            return_value=mock_registry,
        ):
            strategy = factory._create_provider_strategy(provider_config)

            # Verify instance-based creation was used
            mock_registry.is_provider_instance_registered.assert_called_once_with("aws-us-east-1")
            mock_registry.create_strategy_from_instance.assert_called_once_with(
                "aws-us-east-1", provider_config
            )

            # Verify strategy name was set
            assert hasattr(mock_strategy, "name")
            assert strategy == mock_strategy

    def test_provider_strategy_factory_fallback_to_type(self):
        """Test ProviderStrategyFactory fallback to provider type."""
        from infrastructure.factories.provider_strategy_factory import (
            ProviderStrategyFactory,
        )

        # Mock logger and config
        mock_logger = Mock()
        mock_config = Mock()

        factory = ProviderStrategyFactory(logger=mock_logger, config=mock_config)

        # Mock provider instance config
        provider_config = ProviderInstanceConfig(
            name="aws-legacy", type="aws", enabled=True, config={"region": "us-east-1"}
        )

        # Mock registry and strategy
        mock_strategy = Mock()
        mock_registry = Mock()
        mock_registry.is_provider_instance_registered.return_value = False  # No named instance
        mock_registry.create_strategy.return_value = mock_strategy

        with patch(
            "src.infrastructure.factories.provider_strategy_factory.get_provider_registry",
            return_value=mock_registry,
        ):
            strategy = factory._create_provider_strategy(provider_config)

            # Verify fallback to type-based creation
            mock_registry.is_provider_instance_registered.assert_called_once_with("aws-legacy")
            mock_registry.create_strategy.assert_called_once_with("aws", provider_config)

            assert strategy == mock_strategy
