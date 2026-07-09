"""Tests for GCP provider registration."""

from unittest.mock import MagicMock

from orb.config.schemas.provider_strategy_schema import ProviderInstanceConfig


def _raw_config_with_gcp() -> dict:
    return {
        "provider": {
            "providers": [
                {
                    "name": "gcp-default",
                    "type": "gcp",
                    "enabled": True,
                    "config": {
                        "project_id": "orb-example-12345",
                        "region": "us-central1",
                        "zones": ["us-central1-a", "us-central1-b"],
                    },
                }
            ],
            "active_provider": "gcp-default",
        }
    }


def test_create_gcp_strategy_builds_initialized_strategy() -> None:
    from orb.providers.gcp.registration import create_gcp_strategy

    strategy = create_gcp_strategy(
        {
            "project_id": "orb-example-12345",
            "region": "us-central1",
            "zones": ["us-central1-a"],
        }
    )

    assert strategy.is_initialized is True


def test_register_gcp_provider_registers_cli_spec() -> None:
    from orb.providers.gcp.registration import register_gcp_provider
    from orb.providers.registry import get_provider_registry

    registry = get_provider_registry()
    registry.clear_registrations()

    register_gcp_provider(registry=registry, logger=MagicMock())

    assert registry.is_provider_registered("gcp") is True


def test_load_strategy_defaults_includes_gcp_defaults_without_provider_bootstrap() -> None:
    """Static defaults loading must include GCP without bootstrapping providers."""
    from unittest.mock import patch

    from orb.config.loader import ConfigurationLoader

    with (
        patch("orb.providers.registration.register_all_provider_types") as register_all,
        patch("orb.providers.registry.get_provider_registry") as get_provider_registry,
    ):
        defaults = ConfigurationLoader._load_strategy_defaults()

    register_all.assert_not_called()
    get_provider_registry.assert_not_called()
    assert "gcp" in defaults["provider"]["provider_defaults"]


def test_register_all_provider_types_includes_gcp() -> None:
    """Canonical provider bootstrap must register GCP."""
    from orb.providers.registration import register_all_provider_types
    from orb.providers.registry import get_provider_registry

    registry = get_provider_registry()
    registry.clear_registrations()

    register_all_provider_types()

    assert registry.is_provider_registered("gcp") is True


def test_provider_config_builder_accepts_gcp_provider_instance_config() -> None:
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


def test_get_typed_gcp_provider_config_via_registry() -> None:
    """get_typed(GCPProviderConfig) resolves through ProviderSettingsRegistry."""
    from orb.config.managers.type_converter import ConfigTypeConverter
    from orb.providers.gcp.configuration.config import GCPProviderConfig
    from orb.providers.gcp.registration import register_gcp_provider_settings

    register_gcp_provider_settings()

    converter = ConfigTypeConverter(_raw_config_with_gcp())
    result = converter.get_typed(GCPProviderConfig)

    assert isinstance(result, GCPProviderConfig)
    assert result.project_id == "orb-example-12345"
    assert result.region == "us-central1"
    assert result.zones == ["us-central1-a", "us-central1-b"]


def test_ensure_provider_instance_registered_from_config_supports_gcp() -> None:
    """Registry auto-registration must work for GCP instances."""
    from orb.providers.registry.provider_registry import ProviderRegistry

    registry = ProviderRegistry()
    registry.clear_registrations()
    provider_instance = ProviderInstanceConfig(  # type: ignore[call-arg]
        name="gcp-default",
        type="gcp",
        enabled=True,
        config={
            "project_id": "test-project",
            "region": "us-central1",
            "zones": ["us-central1-a"],
        },
    )

    result = registry.ensure_provider_instance_registered_from_config(provider_instance)

    assert result is True
    assert registry.is_provider_registered("gcp") is True
    assert registry.is_provider_instance_registered("gcp-default") is True
