"""Unit tests for kubernetes provider registration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.defaults_loader import KubernetesDefaultsLoader
from orb.providers.k8s.registration import (
    create_k8s_config,
    create_k8s_resolver,
    create_k8s_strategy,
    create_k8s_validator,
    is_k8s_provider_registered,
    register_k8s_provider,
    register_k8s_provider_settings,
)
from orb.providers.k8s.strategy.k8s_provider_strategy import (
    K8sProviderStrategy,
)
from orb.providers.registration import _REGISTERED_PROVIDERS
from orb.providers.registry.defaults_loader_registry import DefaultsLoaderRegistry


def test_kubernetes_is_in_central_registered_providers_list() -> None:
    """The kubernetes provider name is wired into the central registration list."""
    assert "k8s" in _REGISTERED_PROVIDERS


def test_register_provider_settings_inserts_class() -> None:
    register_k8s_provider_settings()
    assert ProviderSettingsRegistry.get_settings_class("k8s") is K8sProviderConfig
    assert is_k8s_provider_registered() is True


def test_create_k8s_config_from_dict() -> None:
    cfg = create_k8s_config({"namespace": "orb"})
    assert isinstance(cfg, K8sProviderConfig)
    assert cfg.namespace == "orb"


def test_create_k8s_resolver_returns_none() -> None:
    """No provider-side resolver is shipped — generic fallback applies."""
    assert create_k8s_resolver() is None


def test_create_k8s_validator_returns_instance() -> None:
    """create_k8s_validator must return a K8sTemplateValidator regardless of config."""
    from orb.providers.k8s.validation.template_validator import K8sTemplateValidator

    validator = create_k8s_validator(None)
    assert isinstance(validator, K8sTemplateValidator)

    validator_no_arg = create_k8s_validator()
    assert isinstance(validator_no_arg, K8sTemplateValidator)


def test_create_k8s_strategy_initialises_with_dict() -> None:
    """Strategy factory works with a raw config dict and initialises cleanly."""
    with patch(
        "orb.infrastructure.di.container.get_container",
        side_effect=Exception("DI not ready"),
    ):
        strategy = create_k8s_strategy({"namespace": "orb-system"})
    assert isinstance(strategy, K8sProviderStrategy)
    assert strategy.is_initialized is True
    assert strategy._k8s_config.namespace == "orb-system"  # type: ignore[attr-defined]


def test_register_k8s_provider_registers_factories() -> None:
    """``register_k8s_provider`` hits ``register_provider`` on the registry."""
    registry = MagicMock()
    register_k8s_provider(registry=registry)
    registry.register_provider.assert_called_once()
    kwargs = registry.register_provider.call_args.kwargs
    assert kwargs["provider_type"] == "k8s"
    assert kwargs["strategy_class"] is K8sProviderStrategy
    assert kwargs["default_api"] == "Pod"


def test_register_k8s_provider_instance_branch() -> None:
    """When ``instance_name`` is supplied the instance branch is taken."""
    registry = MagicMock()
    register_k8s_provider(registry=registry, instance_name="kubernetes-prod")
    registry.register_provider.assert_not_called()
    registry.register_provider_instance.assert_called_once()
    kwargs = registry.register_provider_instance.call_args.kwargs
    assert kwargs["provider_type"] == "k8s"
    assert kwargs["instance_name"] == "kubernetes-prod"


def test_initialize_registers_defaults_loader() -> None:
    """``initialize_k8s_provider`` registers the defaults loader.

    Snapshots and restores ``DefaultsLoaderRegistry`` state so cross-test
    pollution does not break suites that depend on the AWS loader being
    registered (e.g. ``tests/unit/sdk/test_sdk_init_config_handlers.py``).
    """
    from orb.providers.k8s.registration import initialize_k8s_provider

    snapshot = dict(DefaultsLoaderRegistry.all())
    try:
        initialize_k8s_provider()
        loader = DefaultsLoaderRegistry.get("k8s")
        assert isinstance(loader, KubernetesDefaultsLoader)
        defaults = loader.load_defaults()
        # Sanity-check that the bundled k8s_defaults.json shape is loaded.
        # Exact contents are validated separately in the defaults JSON tests.
        k8s_defaults = defaults["provider"]["provider_defaults"]["k8s"]
        assert "Pod" in k8s_defaults["handlers"]
        assert k8s_defaults["template_defaults"]["provider_api"] == "Pod"
    finally:
        DefaultsLoaderRegistry.clear()
        for provider_type, original_loader in snapshot.items():
            DefaultsLoaderRegistry.register(provider_type, original_loader)


@pytest.mark.parametrize(
    "factory",
    [
        create_k8s_strategy,
        create_k8s_config,
        create_k8s_resolver,
        create_k8s_validator,
        register_k8s_provider,
    ],
)
def test_factories_are_callable(factory) -> None:
    """Smoke test — every public registration callable is importable and callable."""
    assert callable(factory)


# ---------------------------------------------------------------------------
# Health check registration
# ---------------------------------------------------------------------------


def test_create_k8s_strategy_registers_health_checks() -> None:
    """When the DI container is ready, ``create_k8s_strategy`` registers the
    Kubernetes health check with the ``HealthCheckPort``."""
    mock_health_check = MagicMock()
    mock_container = MagicMock()
    mock_container.get.return_value = mock_health_check

    with (
        patch(
            "orb.infrastructure.di.container.get_container",
            return_value=mock_container,
        ),
        patch("orb.providers.k8s.health.register_k8s_health_checks") as mock_register,
    ):
        strategy = create_k8s_strategy({"namespace": "orb-system"})

    # The health check registration must have been called with the
    # HealthCheckPort instance resolved from the container.
    mock_register.assert_called_once()
    args = mock_register.call_args.args
    assert args[0] is mock_health_check
    # Second arg is the kubernetes_client from the strategy.
    assert args[1] is strategy.kubernetes_client


# ---------------------------------------------------------------------------
# TemplateExampleGeneratorPort — DI wiring and example generation
# ---------------------------------------------------------------------------


def test_register_k8s_services_with_di_wires_example_generator() -> None:
    """``register_k8s_services_with_di`` registers ``TemplateExampleGeneratorPort``
    in the DI container when the adapter module is present."""
    from orb.providers.k8s.registration import register_k8s_services_with_di

    registered: dict = {}

    mock_logger = MagicMock()

    def _mock_get(port_type):
        from orb.domain.base.ports import LoggingPort
        from orb.domain.base.ports.template_adapter_port import TemplateAdapterPort

        if port_type is LoggingPort:
            return mock_logger
        if port_type is TemplateAdapterPort:  # returned for the template adapter path
            return MagicMock()
        return MagicMock()

    def _mock_register_singleton(port_type, factory):
        registered[port_type] = factory

    mock_container = MagicMock()
    mock_container.get.side_effect = _mock_get
    mock_container.register_singleton.side_effect = _mock_register_singleton

    with patch(
        "orb.providers.k8s.infrastructure.adapters.template_adapter.create_k8s_template_adapter",
        return_value=MagicMock(),
    ):
        register_k8s_services_with_di(mock_container)

    from orb.domain.base.ports.template_example_generator_port import (
        TemplateExampleGeneratorPort,
    )

    assert TemplateExampleGeneratorPort in registered, (
        "TemplateExampleGeneratorPort was not registered with the DI container"
    )


def test_k8s_example_generator_returns_templates_for_all_handlers() -> None:
    """``KubernetesTemplateExampleGeneratorAdapter`` returns examples from all
    four handler classes (Pod, Deployment, StatefulSet, Job) when
    ``provider_type="k8s"``."""
    from orb.providers.k8s.adapters.template_example_generator_adapter import (
        KubernetesTemplateExampleGeneratorAdapter,
    )

    adapter = KubernetesTemplateExampleGeneratorAdapter()
    templates = adapter.generate_example_templates(provider_type="k8s", provider_name="k8s-default")

    assert len(templates) >= 4, (
        f"Expected at least 4 example templates (one per handler), got {len(templates)}"
    )
    provider_apis = {getattr(t, "provider_api", None) for t in templates}
    assert {"Pod", "Deployment", "StatefulSet", "Job"}.issubset(provider_apis), (
        f"Not all handler provider_api keys present; found: {provider_apis}"
    )


def test_k8s_example_generator_filters_by_provider_api() -> None:
    """Filtering by ``provider_api`` returns only the matching templates."""
    from orb.providers.k8s.adapters.template_example_generator_adapter import (
        KubernetesTemplateExampleGeneratorAdapter,
    )

    adapter = KubernetesTemplateExampleGeneratorAdapter()
    pod_only = adapter.generate_example_templates(
        provider_type="k8s", provider_name="k8s-default", provider_api="Pod"
    )

    assert all(getattr(t, "provider_api", None) == "Pod" for t in pod_only)
    assert len(pod_only) >= 1


def test_k8s_example_generator_ignores_non_k8s_provider_type() -> None:
    """The adapter returns an empty list for any provider type other than ``k8s``."""
    from orb.providers.k8s.adapters.template_example_generator_adapter import (
        KubernetesTemplateExampleGeneratorAdapter,
    )

    adapter = KubernetesTemplateExampleGeneratorAdapter()
    result = adapter.generate_example_templates(provider_type="aws", provider_name="aws-default")
    assert result == []
