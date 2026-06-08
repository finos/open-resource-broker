"""Tests for provider defaults loading."""

from unittest.mock import patch


def test_load_strategy_defaults_uses_static_provider_defaults_without_provider_bootstrap():
    from orb.config.loader import ConfigurationLoader

    with (
        patch("orb.providers.registration.register_all_provider_types") as register_all,
        patch("orb.providers.registry.get_provider_registry") as get_provider_registry,
    ):
        defaults = ConfigurationLoader._load_strategy_defaults()

    register_all.assert_not_called()
    get_provider_registry.assert_not_called()
    assert "aws" in defaults["provider"]["provider_defaults"]
    assert "azure" in defaults["provider"]["provider_defaults"]
    assert "gcp" in defaults["provider"]["provider_defaults"]
