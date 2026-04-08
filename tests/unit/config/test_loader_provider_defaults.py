"""Tests for provider defaults loading."""

from unittest.mock import MagicMock, patch


def test_load_strategy_defaults_registers_all_provider_types_before_collecting_defaults():
    from orb.config.loader import ConfigurationLoader

    registry = MagicMock()
    registry.collect_defaults.return_value = {
        "provider": {
            "provider_defaults": {
                "azure": {"handlers": {"VMSS": {"enabled": True}}},
            }
        }
    }

    with (
        patch("orb.providers.registration.register_all_provider_types") as register_all,
        patch("orb.providers.registry.get_provider_registry", return_value=registry),
    ):
        defaults = ConfigurationLoader._load_strategy_defaults()

    register_all.assert_called_once_with()
    registry.collect_defaults.assert_called_once_with()
    assert "azure" in defaults["provider"]["provider_defaults"]
