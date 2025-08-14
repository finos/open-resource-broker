"""Tests for config-driven provider registration."""

from unittest.mock import Mock, patch

from src.config.schemas.provider_strategy_schema import (
    ProviderConfig,
    ProviderInstanceConfig,
)


class TestConfigDrivenProviderRegistration:
    """Test config-driven provider registration functionality."""

    def test_register_providers_with_valid_config(self):
        """Test provider registration with valid configuration."""
        # Create test configuration
        provider_config = ProviderConfig(
            selection_policy="FIRST_AVAILABLE",
            providers=[
                ProviderInstanceConfig(
                    name="aws-test",
                    type="aws",
                    enabled=True,
                    config={"region": "us-east-1"},
                )
            ],
        )

        # Mock configuration manager
        mock_config_manager = Mock()
        mock_config_manager.get_provider_config.return_value = provider_config

        # Mock AWS registration
        with patch(
            "src.infrastructure.di.provider_services.get_config_manager",
            return_value=mock_config_manager,
        ), patch(
            "src.providers.aws.registration.register_aws_provider"
        ) as mock_aws_register, patch(
            "src.infrastructure.di.provider_services.get_logger"
        ) as mock_logger:

            from src.infrastructure.di.provider_services import _register_providers

            # Execute registration
            _register_providers()

            # Verify AWS provider was registered
            mock_aws_register.assert_called_once()

            # Verify logging
            mock_logger.return_value.info.assert_called()

    def test_register_providers_with_no_config(self):
        """Test provider registration with no configuration."""
        # Mock configuration manager returning None
        mock_config_manager = Mock()
        mock_config_manager.get_provider_config.return_value = None

        with patch(
            "src.infrastructure.di.provider_services.get_config_manager",
            return_value=mock_config_manager,
        ), patch(
            "src.providers.aws.registration.register_aws_provider"
        ) as mock_aws_register, patch(
            "src.infrastructure.di.provider_services.get_logger"
        ) as mock_logger:

            from src.infrastructure.di.provider_services import _register_providers

            # Execute registration
            _register_providers()

            # Verify AWS provider was NOT registered
            mock_aws_register.assert_not_called()

            # Verify warning was logged
            mock_logger.return_value.warning.assert_called()

    def test_register_providers_with_disabled_provider(self):
        """Test provider registration with disabled provider."""
        # Create test configuration with disabled provider
        provider_config = ProviderConfig(
            selection_policy="FIRST_AVAILABLE",
            providers=[
                ProviderInstanceConfig(
                    name="aws-disabled",
                    type="aws",
                    enabled=False,  # Disabled
                    config={"region": "us-east-1"},
                )
            ],
        )

        # Mock configuration manager
        mock_config_manager = Mock()
        mock_config_manager.get_provider_config.return_value = provider_config

        with patch(
            "src.infrastructure.di.provider_services.get_config_manager",
            return_value=mock_config_manager,
        ), patch(
            "src.providers.aws.registration.register_aws_provider"
        ) as mock_aws_register, patch(
            "src.infrastructure.di.provider_services.get_logger"
        ) as mock_logger:

            from src.infrastructure.di.provider_services import _register_providers

            # Execute registration
            _register_providers()

            # Verify AWS provider was NOT registered (disabled)
            mock_aws_register.assert_not_called()

    def test_register_providers_with_multiple_instances(self):
        """Test provider registration with multiple provider instances."""
        # Create test configuration with multiple providers
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
                    name="aws-us-west-2",
                    type="aws",
                    enabled=True,
                    config={"region": "us-west-2"},
                ),
            ],
        )

        # Mock configuration manager
        mock_config_manager = Mock()
        mock_config_manager.get_provider_config.return_value = provider_config

        with patch(
            "src.infrastructure.di.provider_services.get_config_manager",
            return_value=mock_config_manager,
        ), patch(
            "src.providers.aws.registration.register_aws_provider"
        ) as mock_aws_register, patch(
            "src.infrastructure.di.provider_services.get_logger"
        ) as mock_logger:

            from src.infrastructure.di.provider_services import _register_providers

            # Execute registration
            _register_providers()

            # Verify AWS provider was registered twice (once for each instance)
            assert mock_aws_register.call_count == 2

    def test_validate_provider_config_valid(self):
        """Test provider configuration validation with valid config."""
        provider_config = ProviderConfig(
            selection_policy="FIRST_AVAILABLE",
            providers=[ProviderInstanceConfig(
                name="aws-test", type="aws", enabled=True)],
        )

        with patch("src.infrastructure.di.provider_services.get_logger"):
            from src.infrastructure.di.provider_services import (
                _validate_provider_config,
            )

            result = _validate_provider_config(provider_config)
            assert result is True

    def test_validate_provider_config_no_providers(self):
        """Test provider configuration validation with no providers."""
        provider_config = ProviderConfig(
            selection_policy="FIRST_AVAILABLE", providers=[]  # No providers
        )

        with patch("src.infrastructure.di.provider_services.get_logger"):
            from src.infrastructure.di.provider_services import (
                _validate_provider_config,
            )

            result = _validate_provider_config(provider_config)
            assert result is False
