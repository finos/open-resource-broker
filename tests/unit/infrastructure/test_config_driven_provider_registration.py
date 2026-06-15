"""Tests for config-driven provider registration."""

from unittest.mock import MagicMock, patch

from orb.config.schemas.provider_strategy_schema import (
    ProviderConfig,
    ProviderInstanceConfig,
)


class TestConfigDrivenProviderRegistration:
    """Test config-driven provider registration functionality."""

    def test_register_all_provider_types_includes_azure_and_gcp(self):
        """Canonical provider bootstrap must register Azure and GCP alongside AWS."""
        from orb.providers.registration import register_all_provider_types
        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()
        registry.clear_registrations()

        register_all_provider_types()

        assert registry.is_provider_registered("aws") is True
        assert registry.is_provider_registered("azure") is True
        assert registry.is_provider_registered("gcp") is True

    def test_provider_config_builder_accepts_azure_provider_instance_config(self):
        """Azure config creation must accept the canonical ProviderInstanceConfig input."""
        from orb.providers.config_builder import ProviderConfigBuilder
        from orb.providers.registration import register_all_provider_types
        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()
        registry.clear_registrations()
        register_all_provider_types()

        logger = MagicMock()
        builder = ProviderConfigBuilder(logger, registry)
        provider_instance = ProviderInstanceConfig(  # type: ignore[call-arg]
            name="azure-default",
            type="azure",
            enabled=True,
            config={
                "subscription_id": "11111111-1111-1111-1111-111111111111",
                "client_id": "test-client",
                "region": "uksouth",
            },
        )

        azure_config = builder.build_config(provider_instance)

        assert azure_config.subscription_id == "11111111-1111-1111-1111-111111111111"
        assert azure_config.region == "uksouth"

    def test_provider_config_builder_accepts_aws_provider_instance_config(self):
        """AWS config creation must use the provider instance's config mapping."""
        from orb.providers.config_builder import ProviderConfigBuilder
        from orb.providers.registration import register_all_provider_types
        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()
        registry.clear_registrations()
        register_all_provider_types()

        logger = MagicMock()
        builder = ProviderConfigBuilder(logger, registry)
        provider_instance = ProviderInstanceConfig(  # type: ignore[call-arg]
            name="aws-default",
            type="aws",
            enabled=True,
            config={"region": "eu-west-1", "profile": "test-profile"},
        )

        aws_config = builder.build_config(provider_instance)

        assert aws_config.region == "eu-west-1"
        assert aws_config.profile == "test-profile"

    def test_provider_config_builder_accepts_gcp_provider_instance_config(self):
        """GCP config creation must accept the canonical ProviderInstanceConfig input."""
        from orb.providers.config_builder import ProviderConfigBuilder
        from orb.providers.registration import register_all_provider_types
        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()
        registry.clear_registrations()
        register_all_provider_types()

        logger = MagicMock()
        builder = ProviderConfigBuilder(logger, registry)
        provider_instance = ProviderInstanceConfig(  # type: ignore[call-arg]
            name="gcp-default",
            type="gcp",
            enabled=True,
            config={
                "project_id": "orb-example-12345",
                "region": "us-central1",
                "zones": ["us-central1-a", "us-central1-b"],
            },
        )

        gcp_config = builder.build_config(provider_instance)

        assert gcp_config.project_id == "orb-example-12345"
        assert gcp_config.region == "us-central1"
        assert gcp_config.zones == ["us-central1-a", "us-central1-b"]

    def test_register_providers_with_valid_config(self):
        """Test provider registration with valid configuration."""
        from orb.bootstrap.provider_services import register_provider_services
        from orb.infrastructure.di.container import DIContainer

        container = DIContainer()

        with (
            patch("orb.bootstrap.provider_services._register_application_services") as mock_app,
            patch(
                "orb.bootstrap.provider_services._register_provider_utility_services"
            ) as mock_util,
        ):
            register_provider_services(container)

            mock_app.assert_called_once_with(container)
            mock_util.assert_called_once_with(container)

    def test_register_provider_utility_services_aws_available(self):
        """Test provider utility registration when AWS provider is available."""
        from orb.bootstrap.provider_services import _register_provider_utility_services
        from orb.infrastructure.di.container import DIContainer

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
        from orb.bootstrap.provider_services import _register_provider_utility_services
        from orb.infrastructure.di.container import DIContainer

        container = DIContainer()

        with patch("importlib.util.find_spec", return_value=None):
            # Should not raise even when AWS is unavailable
            _register_provider_utility_services(container)

    def test_register_provider_utility_services_handles_import_error(self):
        """Test provider utility registration handles ImportError gracefully."""
        from orb.bootstrap.provider_services import _register_provider_utility_services
        from orb.infrastructure.di.container import DIContainer

        container = DIContainer()

        with patch("importlib.util.find_spec", side_effect=ImportError("no module")):
            # Should not raise
            _register_provider_utility_services(container)

    def test_register_provider_utility_services_handles_exception(self):
        """Test provider utility registration handles general exceptions gracefully."""
        from orb.bootstrap.provider_services import _register_provider_utility_services
        from orb.infrastructure.di.container import DIContainer

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
