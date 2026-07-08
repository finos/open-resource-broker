"""Azure provider registration and default-loading contracts."""

from unittest.mock import MagicMock, patch

from orb.config.schemas.provider_strategy_schema import ProviderInstanceConfig


def _raw_config_with_azure() -> dict:
    return {
        "provider": {
            "providers": [
                {
                    "name": "azure-default",
                    "type": "azure",
                    "enabled": True,
                    "config": {
                        "subscription_id": "12345678-1234-1234-1234-123456789012",
                        "resource_group": "orb-test-rg",
                        "location": "eastus2",
                    },
                }
            ],
            "active_provider": "azure-default",
        }
    }


def test_load_strategy_defaults_includes_azure_defaults_without_provider_bootstrap():
    """Static defaults loading must include Azure without bootstrapping providers."""
    from orb.config.loader import ConfigurationLoader

    with (
        patch("orb.providers.registration.register_all_provider_types") as register_all,
        patch("orb.providers.registry.get_provider_registry") as get_provider_registry,
    ):
        defaults = ConfigurationLoader._load_strategy_defaults()

    register_all.assert_not_called()
    get_provider_registry.assert_not_called()
    assert "azure" in defaults["provider"]["provider_defaults"]


def test_load_strategy_defaults_includes_azure_handler_capabilities_without_provider_bootstrap():
    """Config-based template validation must see Azure provider APIs from defaults."""
    from orb.config.loader import ConfigurationLoader
    from orb.config.schemas.provider_strategy_schema import ProviderConfig, ProviderInstanceConfig

    with (
        patch("orb.providers.registration.register_all_provider_types") as register_all,
        patch("orb.providers.registry.get_provider_registry") as get_provider_registry,
    ):
        defaults = ConfigurationLoader._load_strategy_defaults()

    register_all.assert_not_called()
    get_provider_registry.assert_not_called()
    provider_config = ProviderConfig.model_validate(defaults["provider"])
    provider_instance = ProviderInstanceConfig(
        name="azure-default",
        type="azure",
        enabled=True,
        config={},
    )

    handlers = provider_instance.get_effective_handlers(provider_config.provider_defaults["azure"])

    assert set(handlers) == {"VMSS", "VMSSUniform", "SingleVM", "CycleCloud"}
    assert handlers["VMSS"].model_dump()["supports_spot"] is True
    assert handlers["CycleCloud"].model_dump()["supports_spot"] is False


def test_register_all_provider_types_includes_azure():
    """Canonical provider bootstrap must register Azure."""
    from orb.providers.registration import register_all_provider_types
    from orb.providers.registry import get_provider_registry

    registry = get_provider_registry()
    registry.clear_registrations()

    register_all_provider_types()

    assert registry.is_provider_registered("azure") is True


def test_register_all_provider_types_registers_azure_auth_strategy():
    """Azure auth strategy must be reachable through AuthRegistry lookup."""
    from orb.config.schemas.server_schema import AuthConfig
    from orb.infrastructure.auth.registry import get_auth_registry
    from orb.providers.azure.auth.azure_auth_strategy import AzureAuthStrategy
    from orb.providers.registration import register_all_provider_types

    registry = get_auth_registry()
    if registry.is_registered("azure"):
        registry.unregister_type("azure")

    register_all_provider_types()

    assert registry.is_registered("azure") is True
    strategy = registry.get_strategy("azure", AuthConfig(enabled=True, strategy="azure"))
    assert isinstance(strategy, AzureAuthStrategy)
    assert strategy.is_enabled() is True


def test_provider_config_builder_accepts_azure_provider_instance_config():
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


def test_get_typed_azure_provider_config_via_registry():
    """get_typed(AzureProviderConfig) resolves through ProviderSettingsRegistry."""
    from orb.config.managers.type_converter import ConfigTypeConverter
    from orb.providers.azure.configuration.config import AzureProviderConfig
    from orb.providers.azure.registration import register_azure_provider_settings

    register_azure_provider_settings()

    converter = ConfigTypeConverter(_raw_config_with_azure())
    result = converter.get_typed(AzureProviderConfig)

    assert isinstance(result, AzureProviderConfig)
    assert result.subscription_id == "12345678-1234-1234-1234-123456789012"
    assert result.resource_group == "orb-test-rg"
    assert result.location == "eastus2"


def test_ensure_provider_instance_registered_from_config_supports_azure():
    """Registry auto-registration must work for Azure instances."""
    from orb.providers.registry.provider_registry import ProviderRegistry

    registry = ProviderRegistry()
    registry.clear_registrations()
    provider_instance = ProviderInstanceConfig(  # type: ignore[call-arg]
        name="azure-default",
        type="azure",
        enabled=True,
        config={
            "subscription_id": "test-subscription",
            "tenant_id": "test-tenant",
            "client_id": "test-client",
            "client_secret_path": "/tmp/test-secret",
            "region": "uksouth",
        },
    )

    result = registry.ensure_provider_instance_registered_from_config(provider_instance)

    assert result is True
    assert registry.is_provider_registered("azure") is True
    assert registry.is_provider_instance_registered("azure-default") is True
