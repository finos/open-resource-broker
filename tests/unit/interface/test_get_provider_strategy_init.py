"""Tests for _get_provider_strategy returning a strategy CLASS."""

from __future__ import annotations

from unittest.mock import MagicMock

import orb.interface.init_command_handler as _mod


def _mock_strategy_class():
    """Return a mock that behaves like a strategy class with classmethods."""
    cls = MagicMock()
    cls.get_available_regions.return_value = []
    cls.get_default_region.return_value = "us-east-1"
    cls.get_available_credential_sources.return_value = [
        {
            "name": "default",
            "description": "Default profile",
            "config_delta": {"profile": "default"},
        }
    ]
    cls.test_credentials.return_value = {"success": True}
    cls.get_credential_requirements.return_value = {}
    cls.get_operational_requirements.return_value = {
        "region": {"required": True, "description": "AWS region"}
    }
    return cls


def _mock_registry(strategy_class=None):
    """Build a mock registry that exposes the strategy class via registration."""
    registry = MagicMock()
    reg = MagicMock()
    reg.strategy_class = strategy_class
    registry.ensure_provider_type_registered.return_value = True
    registry._get_type_registration.return_value = reg
    return registry


def test_init_handler_returns_strategy_class() -> None:
    """_get_provider_strategy must return the strategy class, not an instance."""
    mock_cls = _mock_strategy_class()
    registry = _mock_registry(strategy_class=mock_cls)

    result = _mod._get_provider_strategy("aws", registry=registry)

    registry.ensure_provider_type_registered.assert_called_once_with("aws")
    registry._get_type_registration.assert_called_once_with("aws")
    assert result is mock_cls


def test_init_handler_returns_strategy_class_k8s() -> None:
    """_get_provider_strategy works for k8s via strategy_class lookup."""
    mock_cls = _mock_strategy_class()
    registry = _mock_registry(strategy_class=mock_cls)

    result = _mod._get_provider_strategy("k8s", registry=registry)

    assert result is mock_cls


def test_init_handler_returns_none_when_registry_not_available() -> None:
    """_get_provider_strategy returns None when no container is provided."""
    # With container=None and no explicit registry, the function cannot resolve
    # ProviderRegistryPort and should return None gracefully.
    result = _mod._get_provider_strategy("aws", container=None)

    assert result is None


def test_init_handler_returns_none_when_no_strategy_class() -> None:
    """_get_provider_strategy returns None when strategy_class is None."""
    registry = _mock_registry(strategy_class=None)

    result = _mod._get_provider_strategy("unknown_provider", registry=registry)

    assert result is None


def test_init_handler_returns_none_on_registration_error() -> None:
    """_get_provider_strategy returns None when _get_type_registration raises."""
    registry = MagicMock()
    registry.ensure_provider_type_registered.return_value = True
    registry._get_type_registration.side_effect = ValueError("not registered")

    result = _mod._get_provider_strategy("notfound", registry=registry)

    assert result is None
