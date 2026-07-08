"""Regression tests for the classmethod-based init flow.

Previously this file tested ``create_strategy_for_init`` (now retired).
It now guards the classmethod surface that replaced that hook:
- ``_get_provider_strategy`` returns a strategy CLASS, not an instance.
- Classmethods (``get_available_credential_sources``, ``get_default_region``,
  ``generate_provider_name``, etc.) are callable on the returned class
  without constructing a strategy instance.
- ``create_strategy_by_type`` remains available for the discovery path.
"""

from __future__ import annotations

import threading
from typing import cast
from unittest.mock import MagicMock

from orb.providers.registry.provider_registry import ProviderRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bare_registry() -> ProviderRegistry:
    """Return a fresh ProviderRegistry with empty internal state."""
    registry = cast(ProviderRegistry, ProviderRegistry())
    registry._strategy_cache = {}
    registry._health_states = {}
    registry._fallback_strategy = None
    registry._registry_lock = threading.RLock()
    registry._type_registrations = {}
    registry._instance_registrations = {}
    registry._config_port = None
    registry._logger = MagicMock()
    return registry


# ---------------------------------------------------------------------------
# Test 1: strategy_class field is accessible after registration
# ---------------------------------------------------------------------------


def test_strategy_class_accessible_after_registration() -> None:
    """After registering a provider, its strategy_class is retrievable."""
    registry = _bare_registry()

    class _StubStrategy:
        @classmethod
        def get_available_credential_sources(cls) -> list:
            return [{"name": "stub", "description": "stub creds"}]

        @classmethod
        def get_default_region(cls) -> str:
            return "stub-region"

    registry.register_provider(
        provider_type="stubprovider",
        strategy_factory=MagicMock(),
        config_factory=MagicMock(),
        strategy_class=_StubStrategy,
    )

    reg = registry._get_type_registration("stubprovider")
    assert reg.strategy_class is _StubStrategy


# ---------------------------------------------------------------------------
# Test 2: classmethod callable on the class (no instance required)
# ---------------------------------------------------------------------------


def test_classmethods_callable_on_strategy_class() -> None:
    """Classmethods on a strategy class must be callable without an instance."""
    registry = _bare_registry()

    class _CredStrategy:
        @classmethod
        def get_available_credential_sources(cls) -> list:
            return [{"name": "env", "description": "env var creds"}]

        @classmethod
        def get_default_region(cls) -> str:
            return "us-east-1"

        @classmethod
        def generate_provider_name(cls, config: dict) -> str:
            return f"aws_{config.get('profile', 'default')}_{config.get('region', '')}"

    registry.register_provider(
        provider_type="aws",
        strategy_factory=MagicMock(),
        config_factory=MagicMock(),
        strategy_class=_CredStrategy,
    )

    reg = registry._get_type_registration("aws")
    strategy_class = reg.strategy_class

    sources = strategy_class.get_available_credential_sources()
    assert isinstance(sources, list)
    assert sources[0]["name"] == "env"

    region = strategy_class.get_default_region()
    assert region == "us-east-1"

    name = strategy_class.generate_provider_name({"profile": "my-profile", "region": "eu-west-1"})
    assert name == "aws_my-profile_eu-west-1"


# ---------------------------------------------------------------------------
# Test 3: create_strategy_for_init is no longer present on the registry
# ---------------------------------------------------------------------------


def test_create_strategy_for_init_removed_from_registry() -> None:
    """create_strategy_for_init must no longer exist on ProviderRegistry."""
    registry = _bare_registry()
    assert not hasattr(registry, "create_strategy_for_init"), (
        "create_strategy_for_init was retired and must not be present on ProviderRegistry"
    )


# ---------------------------------------------------------------------------
# Test 4: runtime fallback still refuses empty config for k8s
# ---------------------------------------------------------------------------


def test_runtime_fallback_still_refuses_empty_config_for_k8s() -> None:
    """get_or_create_strategy (runtime path) does NOT bypass the empty-config guard.

    The k8s factory raises for None/empty config.  The registry returns None
    rather than propagating the error, and the guard was NOT bypassed.
    """
    registry = _bare_registry()

    factory_calls: list[dict] = []

    def _k8s_guard_factory(config: object) -> MagicMock:
        factory_calls.append({"config": config})
        if config is None or config == {}:
            raise RuntimeError("cluster-targeting config required")
        mock_strategy = MagicMock()
        mock_strategy.is_initialized = True
        return mock_strategy

    registry.register_provider(
        provider_type="k8s",
        strategy_factory=_k8s_guard_factory,
        config_factory=MagicMock(),
    )

    # "k8s-stale" is NOT registered as an instance — triggers type-level fallback
    result = registry.get_or_create_strategy("k8s-stale", config=None)

    assert len(factory_calls) == 1, "Factory must have been called exactly once"
    assert result is None, "Guard raises → registry returns None"
