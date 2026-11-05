"""Registrations that defer their strategy import must degrade gracefully."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.providers.registry.provider_registry import ProviderRegistry


def _missing_sdk() -> type:
    raise ImportError("No module named 'google'")


def _good_class() -> type:
    class Strategy:
        @classmethod
        def get_defaults_config(cls) -> dict[str, Any]:
            return {"provider": {"ok": True}}

        @classmethod
        def get_ui_column_schema(cls) -> list[Any]:
            return []

    return Strategy


def _registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._type_registrations.clear()
    registry._logger = MagicMock()
    return registry


@pytest.mark.unit
def test_collect_defaults_skips_provider_with_missing_sdk() -> None:
    registry = _registry()
    for name, loader in (("broken", _missing_sdk), ("fine", _good_class)):
        registry.register_provider(
            provider_type=name,
            strategy_factory=lambda cfg: None,
            config_factory=lambda cfg: None,
            strategy_class_loader=loader,
        )

    assert registry.collect_defaults() == {"provider": {"ok": True}}
    message = registry._logger.warning.call_args.args
    rendered = message[0] % message[1:]
    assert "broken requires the broken extra" in rendered
    assert "No module named 'google'" in rendered


@pytest.mark.unit
def test_provider_schema_route_helper_survives_missing_sdk(caplog) -> None:
    from orb.api.routers.providers import _get_schema_for_provider_type
    from orb.providers.registry.provider_registry import get_provider_registry

    registry = get_provider_registry()
    name = "lazy-missing-sdk"
    registry.register_provider(
        provider_type=name,
        strategy_factory=lambda cfg: None,
        config_factory=lambda cfg: None,
        strategy_class_loader=_missing_sdk,
    )
    try:
        with caplog.at_level(logging.WARNING):
            assert _get_schema_for_provider_type(name) == []
    finally:
        registry._type_registrations.pop(name, None)

    assert f"Provider '{name}' requires the {name} extra" in caplog.text
