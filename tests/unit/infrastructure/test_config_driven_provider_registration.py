"""Tests for config-driven provider registration."""

from unittest.mock import MagicMock, patch

from orb.config.schemas.provider_strategy_schema import (
    ProviderConfig,
    ProviderInstanceConfig,
)


class TestConfigDrivenProviderRegistration:
    """Test config-driven provider registration functionality."""

    def test_register_providers_with_valid_config(self):
        """Test provider registration with valid configuration."""
        from orb.infrastructure.di.container import DIContainer
        from orb.infrastructure.di.provider_services import register_provider_services

        container = DIContainer()

        with (
            patch(
                "orb.infrastructure.di.provider_services._register_application_services"
            ) as mock_app,
            patch(
                "orb.infrastructure.di.provider_services._register_provider_utility_services"
            ) as mock_util,
        ):
            register_provider_services(container)

            mock_app.assert_called_once_with(container)
            mock_util.assert_called_once_with(container)

    def test_register_provider_utility_services_aws_available(self):
        """Test provider utility registration when AWS provider is available."""
        from orb.infrastructure.di.container import DIContainer
        from orb.infrastructure.di.provider_services import _register_provider_utility_services

        container = DIContainer()

        with (
            patch("importlib.util.find_spec", return_value=MagicMock()),
            patch(
                "orb.providers.aws.registration.register_aws_services_with_di"
            ) as mock_aws_register,
        ):
            _register_provider_utility_services(container)
            mock_aws_register.assert_called_once_with(container)

    def test_register_provider_utility_services_aws_unavailable(self):
        """Test provider utility registration when AWS provider is unavailable."""
        from orb.infrastructure.di.container import DIContainer
        from orb.infrastructure.di.provider_services import _register_provider_utility_services

        container = DIContainer()

        with patch("importlib.util.find_spec", return_value=None):
            # Should not raise even when AWS is unavailable
            _register_provider_utility_services(container)

    def test_register_provider_utility_services_handles_import_error(self):
        """Test provider utility registration handles ImportError gracefully."""
        from orb.infrastructure.di.container import DIContainer
        from orb.infrastructure.di.provider_services import _register_provider_utility_services

        container = DIContainer()

        with patch("importlib.util.find_spec", side_effect=ImportError("no module")):
            # Should not raise
            _register_provider_utility_services(container)

    def test_register_provider_utility_services_handles_exception(self):
        """Test provider utility registration handles general exceptions gracefully."""
        from orb.infrastructure.di.container import DIContainer
        from orb.infrastructure.di.provider_services import _register_provider_utility_services

        container = DIContainer()

        with (
            patch("importlib.util.find_spec", return_value=MagicMock()),
            patch(
                "orb.providers.aws.registration.register_aws_services_with_di",
                side_effect=RuntimeError("registration failed"),
            ),
        ):
            # Should not raise - errors are caught and logged as warnings
            _register_provider_utility_services(container)

    def test_provider_config_with_valid_providers(self):
        """Test ProviderConfig creation with valid providers."""
        provider_config = ProviderConfig(  # type: ignore[call-arg]
            selection_policy="FIRST_AVAILABLE",
            providers=[
                ProviderInstanceConfig(  # type: ignore[call-arg]
                    name="aws-test",
                    type="aws",
                    enabled=True,
                    config={"region": "us-east-1"},
                )
            ],
        )

        assert provider_config.selection_policy == "FIRST_AVAILABLE"
        assert len(provider_config.providers) == 1
        assert provider_config.providers[0].name == "aws-test"
        assert provider_config.providers[0].type == "aws"
        assert provider_config.providers[0].enabled is True

    def test_provider_config_with_disabled_provider(self):
        """Test ProviderConfig with a disabled provider."""
        provider_config = ProviderConfig(  # type: ignore[call-arg]
            selection_policy="FIRST_AVAILABLE",
            providers=[
                ProviderInstanceConfig(  # type: ignore[call-arg]
                    name="aws-disabled",
                    type="aws",
                    enabled=False,
                    config={"region": "us-east-1"},
                )
            ],
        )

        assert len(provider_config.providers) == 1
        assert provider_config.providers[0].enabled is False

        # Only enabled providers should be considered active
        enabled = [p for p in provider_config.providers if p.enabled]
        assert len(enabled) == 0

    def test_provider_config_with_multiple_instances(self):
        """Test ProviderConfig with multiple provider instances."""
        provider_config = ProviderConfig(  # type: ignore[call-arg]
            selection_policy="ROUND_ROBIN",
            providers=[
                ProviderInstanceConfig(  # type: ignore[call-arg]
                    name="aws-us-east-1",
                    type="aws",
                    enabled=True,
                    config={"region": "us-east-1"},
                ),
                ProviderInstanceConfig(  # type: ignore[call-arg]
                    name="aws-us-west-2",
                    type="aws",
                    enabled=True,
                    config={"region": "us-west-2"},
                ),
            ],
        )

        assert provider_config.selection_policy == "ROUND_ROBIN"
        assert len(provider_config.providers) == 2
        enabled = [p for p in provider_config.providers if p.enabled]
        assert len(enabled) == 2

    def test_provider_config_default_values(self):
        """Test ProviderConfig has sensible defaults."""
        provider_config = ProviderConfig(  # type: ignore[call-arg]
            providers=[ProviderInstanceConfig(name="aws-test", type="aws", enabled=True)]  # type: ignore[call-arg]
        )

        assert provider_config.selection_policy == "FIRST_AVAILABLE"
        assert provider_config.health_check_interval > 0

    def test_provider_instance_config_defaults(self):
        """Test ProviderInstanceConfig has sensible defaults."""
        instance = ProviderInstanceConfig(name="aws-test", type="aws", enabled=True)  # type: ignore[call-arg]

        assert instance.name == "aws-test"
        assert instance.type == "aws"
        assert instance.enabled is True
        assert instance.priority >= 0
