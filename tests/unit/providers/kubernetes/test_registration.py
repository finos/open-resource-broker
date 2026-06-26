"""Unit tests for kubernetes provider registration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry
from orb.providers.kubernetes.configuration.config import KubernetesProviderConfig
from orb.providers.kubernetes.defaults_loader import KubernetesDefaultsLoader
from orb.providers.kubernetes.registration import (
    create_kubernetes_config,
    create_kubernetes_resolver,
    create_kubernetes_strategy,
    create_kubernetes_validator,
    is_kubernetes_provider_registered,
    register_kubernetes_provider,
    register_kubernetes_provider_settings,
)
from orb.providers.kubernetes.strategy.kubernetes_provider_strategy import (
    KubernetesProviderStrategy,
)
from orb.providers.registration import _REGISTERED_PROVIDERS
from orb.providers.registry.defaults_loader_registry import DefaultsLoaderRegistry


def test_kubernetes_is_in_central_registered_providers_list() -> None:
    """The kubernetes provider name is wired into the central registration list."""
    assert "kubernetes" in _REGISTERED_PROVIDERS


def test_register_provider_settings_inserts_class() -> None:
    register_kubernetes_provider_settings()
    assert ProviderSettingsRegistry.get_settings_class("kubernetes") is KubernetesProviderConfig
    assert is_kubernetes_provider_registered() is True


def test_create_kubernetes_config_from_dict() -> None:
    cfg = create_kubernetes_config({"namespace": "orb"})
    assert isinstance(cfg, KubernetesProviderConfig)
    assert cfg.namespace == "orb"


def test_create_kubernetes_resolver_returns_none() -> None:
    """Phase A: no provider-side resolver is shipped yet."""
    assert create_kubernetes_resolver() is None


def test_create_kubernetes_validator_returns_none_when_no_config() -> None:
    assert create_kubernetes_validator(None) is None


def test_create_kubernetes_strategy_initialises_with_dict() -> None:
    """Strategy factory works with a raw config dict and initialises cleanly."""
    with patch(
        "orb.infrastructure.di.container.get_container",
        side_effect=Exception("DI not ready"),
    ):
        strategy = create_kubernetes_strategy({"namespace": "orb-system"})
    assert isinstance(strategy, KubernetesProviderStrategy)
    assert strategy.is_initialized is True
    assert strategy._k8s_config.namespace == "orb-system"  # type: ignore[attr-defined]


def test_register_kubernetes_provider_registers_factories() -> None:
    """``register_kubernetes_provider`` hits ``register_provider`` on the registry."""
    registry = MagicMock()
    register_kubernetes_provider(registry=registry)
    registry.register_provider.assert_called_once()
    kwargs = registry.register_provider.call_args.kwargs
    assert kwargs["provider_type"] == "kubernetes"
    assert kwargs["strategy_class"] is KubernetesProviderStrategy
    assert kwargs["default_api"] == "KubernetesPod"


def test_register_kubernetes_provider_instance_branch() -> None:
    """When ``instance_name`` is supplied the instance branch is taken."""
    registry = MagicMock()
    register_kubernetes_provider(registry=registry, instance_name="kubernetes-prod")
    registry.register_provider.assert_not_called()
    registry.register_provider_instance.assert_called_once()
    kwargs = registry.register_provider_instance.call_args.kwargs
    assert kwargs["provider_type"] == "kubernetes"
    assert kwargs["instance_name"] == "kubernetes-prod"


def test_initialize_registers_defaults_loader() -> None:
    """``initialize_kubernetes_provider`` registers the defaults loader.

    Snapshots and restores ``DefaultsLoaderRegistry`` state so cross-test
    pollution does not break suites that depend on the AWS loader being
    registered (e.g. ``tests/unit/sdk/test_sdk_init_config_handlers.py``).
    """
    from orb.providers.kubernetes.registration import initialize_kubernetes_provider

    snapshot = dict(DefaultsLoaderRegistry.all())
    try:
        initialize_kubernetes_provider()
        loader = DefaultsLoaderRegistry.get("kubernetes")
        assert isinstance(loader, KubernetesDefaultsLoader)
        assert loader.load_defaults() == {}
    finally:
        DefaultsLoaderRegistry.clear()
        for provider_type, original_loader in snapshot.items():
            DefaultsLoaderRegistry.register(provider_type, original_loader)


@pytest.mark.parametrize(
    "factory",
    [
        create_kubernetes_strategy,
        create_kubernetes_config,
        create_kubernetes_resolver,
        create_kubernetes_validator,
        register_kubernetes_provider,
    ],
)
def test_factories_are_callable(factory) -> None:
    """Smoke test — every public registration callable is importable and callable."""
    assert callable(factory)
