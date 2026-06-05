"""Unit tests for OCI provider base registration."""

from orb.providers.oci.registration import create_oci_strategy, register_oci_provider
from orb.providers.oci.strategy.oci_provider_strategy import OCIProviderStrategy
from orb.providers.registry.provider_registry import ProviderRegistry
from orb.domain.base.ports.provider_cli_spec_port import CLISpecRegistry


def test_register_oci_provider_registers_provider_type():
    registry = ProviderRegistry()
    register_oci_provider(registry=registry)
    assert registry.is_provider_registered("oci") is True


def test_create_oci_strategy_initializes():
    strategy = create_oci_strategy({"region": "us-phoenix-1", "profile": "DEFAULT"})
    assert strategy.provider_type == "oci"
    assert strategy.is_initialized is True


def test_create_oci_strategy_unwraps_nested_provider_config():
    strategy = create_oci_strategy(
        {
            "name": "oci-default",
            "type": "oci",
            "enabled": True,
            "config": {
                "region": "us-phoenix-1",
                "credential_source": "instance_principal",
            },
        }
    )
    assert strategy._compute_handler._credential_source == "instance_principal"


def test_oci_strategy_defaults_load():
    defaults = OCIProviderStrategy.get_defaults_config()
    provider_defaults = defaults["provider"]["provider_defaults"]["oci"]
    assert "handlers" in provider_defaults
    assert provider_defaults["template_defaults"]["provider_api"] == "OCICompute"
    assert provider_defaults["template_defaults"]["capacity_type"] == "ondemand"


def test_oci_cli_spec_registered_in_registry():
    register_oci_provider(registry=ProviderRegistry())
    assert CLISpecRegistry.get("oci") is not None
