"""K8s provider DI wiring verification tests.

Mirrors tests/providers/aws/mocked/test_di_wiring.py for the k8s provider.

These tests boot the K8sProviderStrategy and K8sHandlerRegistry with mocked
dependencies (no real cluster needed) and assert that critical services are
properly resolved and wired.  They exist to surface DI wiring mistakes — for
example a K8sClient or handler factory that silently receives None for a
dependency that should be injected.

Tests do NOT boot the global ORB DI container (which requires ORB_CONFIG_DIR
and real provider config) — they use the k8s-local construction path so they
run identically in CI with no external dependencies.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger() -> Any:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def _make_k8s_config(namespace: str = "orb-test") -> Any:
    from orb.providers.k8s.configuration.config import K8sProviderConfig

    return K8sProviderConfig(namespace=namespace)  # type: ignore[call-arg]


def _make_fake_k8s_client() -> Any:
    """Return a MagicMock that looks like a K8sClient with a functional core_v1."""
    from types import SimpleNamespace

    mock_client = MagicMock()
    # Health check calls core_v1.get_api_resources()
    mock_client.core_v1.get_api_resources.return_value = SimpleNamespace(
        group_version="v1", resources=[object(), object(), object()]
    )
    return mock_client


# ---------------------------------------------------------------------------
# 1. K8sProviderStrategy initialises without errors
# ---------------------------------------------------------------------------


def test_k8s_strategy_initialises() -> None:
    """K8sProviderStrategy.initialize() returns True with a mocked K8sClient."""
    from orb.providers.k8s.strategy.k8s_provider_strategy import (
        K8sProviderStrategy,
    )

    strategy = K8sProviderStrategy(
        config=_make_k8s_config(),
        logger=_make_logger(),
        kubernetes_client=_make_fake_k8s_client(),
    )
    assert strategy.initialize() is True


# ---------------------------------------------------------------------------
# 2. K8sHandlerRegistry resolves all four handler types
# ---------------------------------------------------------------------------


def test_handler_registry_resolves_pod_handler() -> None:
    """K8sHandlerRegistry resolves a K8sPodHandler for provider_api='Pod'."""
    from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler
    from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry

    registry = K8sHandlerRegistry(
        config=_make_k8s_config(),
        logger=_make_logger(),
        client_provider=_make_fake_k8s_client,
        watch_manager_provider=lambda: None,
        plugin_factories=lambda: {},
        native_spec_service_provider=lambda: None,
    )

    handler = registry.get_handler("Pod")
    assert handler is not None
    assert isinstance(handler, K8sPodHandler)


def test_handler_registry_resolves_deployment_handler() -> None:
    """K8sHandlerRegistry resolves a K8sDeploymentHandler for provider_api='Deployment'."""
    from orb.providers.k8s.infrastructure.handlers.deployment_handler import (
        K8sDeploymentHandler,
    )
    from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry

    registry = K8sHandlerRegistry(
        config=_make_k8s_config(),
        logger=_make_logger(),
        client_provider=_make_fake_k8s_client,
        watch_manager_provider=lambda: None,
        plugin_factories=lambda: {},
        native_spec_service_provider=lambda: None,
    )

    handler = registry.get_handler("Deployment")
    assert isinstance(handler, K8sDeploymentHandler)


def test_handler_registry_resolves_statefulset_handler() -> None:
    """K8sHandlerRegistry resolves a K8sStatefulSetHandler for provider_api='StatefulSet'."""
    from orb.providers.k8s.infrastructure.handlers.statefulset_handler import (
        K8sStatefulSetHandler,
    )
    from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry

    registry = K8sHandlerRegistry(
        config=_make_k8s_config(),
        logger=_make_logger(),
        client_provider=_make_fake_k8s_client,
        watch_manager_provider=lambda: None,
        plugin_factories=lambda: {},
        native_spec_service_provider=lambda: None,
    )

    handler = registry.get_handler("StatefulSet")
    assert isinstance(handler, K8sStatefulSetHandler)


def test_handler_registry_resolves_job_handler() -> None:
    """K8sHandlerRegistry resolves a K8sJobHandler for provider_api='Job'."""
    from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler
    from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry

    registry = K8sHandlerRegistry(
        config=_make_k8s_config(),
        logger=_make_logger(),
        client_provider=_make_fake_k8s_client,
        watch_manager_provider=lambda: None,
        plugin_factories=lambda: {},
        native_spec_service_provider=lambda: None,
    )

    handler = registry.get_handler("Job")
    assert isinstance(handler, K8sJobHandler)


# ---------------------------------------------------------------------------
# 3. Resolved handlers have non-None kubernetes_client wired in
# ---------------------------------------------------------------------------


def test_resolved_handlers_have_kubernetes_client() -> None:
    """All four handler types have a non-None kubernetes_client after registry construction."""
    from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry

    fake_client = _make_fake_k8s_client()
    registry = K8sHandlerRegistry(
        config=_make_k8s_config(),
        logger=_make_logger(),
        client_provider=lambda: fake_client,
        watch_manager_provider=lambda: None,
        plugin_factories=lambda: {},
        native_spec_service_provider=lambda: None,
    )

    for api in ("Pod", "Deployment", "StatefulSet", "Job"):
        handler = registry.get_handler(api)
        assert handler is not None, f"resolve_handler({api!r}) returned None"
        assert handler.client is not None, (
            f"{api} handler has kubernetes_client=None — DI wiring bug"
        )


# ---------------------------------------------------------------------------
# 4. Strategy's capabilities advertises all four supported APIs
# ---------------------------------------------------------------------------


def test_strategy_capabilities_cover_all_apis() -> None:
    """K8sProviderStrategy.get_capabilities returns all four provider APIs."""
    from orb.providers.base.strategy import ProviderOperationType
    from orb.providers.k8s.strategy.k8s_provider_strategy import (
        K8sProviderStrategy,
    )

    strategy = K8sProviderStrategy(
        config=_make_k8s_config(),
        logger=_make_logger(),
        kubernetes_client=_make_fake_k8s_client(),
    )
    strategy.initialize()
    caps = strategy.get_capabilities()

    assert set(caps.supported_apis) == {"Pod", "Deployment", "StatefulSet", "Job"}
    assert caps.supports_operation(ProviderOperationType.CREATE_INSTANCES) is True
    assert caps.supports_operation(ProviderOperationType.TERMINATE_INSTANCES) is True


# ---------------------------------------------------------------------------
# 5. K8sTemplateAdapter can be constructed with a mocked K8sClient
# ---------------------------------------------------------------------------


def test_template_adapter_constructed_with_mocked_client() -> None:
    """K8sTemplateAdapter can be built without a real cluster."""
    from orb.providers.k8s.infrastructure.adapters.template_adapter import (
        K8sTemplateAdapter,
    )

    adapter = K8sTemplateAdapter(
        template_config_manager=MagicMock(),
        kubernetes_client=MagicMock(),
        logger=_make_logger(),
    )

    assert adapter is not None
    assert adapter.get_provider_api() == "Pod"
    assert "namespace" in adapter.get_supported_fields()


# ---------------------------------------------------------------------------
# 6. K8sHandlerRegistry generate_example_templates returns templates
# ---------------------------------------------------------------------------


def test_handler_registry_generates_example_templates() -> None:
    """K8sHandlerRegistry.generate_example_templates returns at least one template per API."""
    from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry

    templates = K8sHandlerRegistry.generate_example_templates()

    assert len(templates) >= 4, "Expected at least one example template per handler type"
    api_names = {getattr(t, "provider_api", None) for t in templates}
    assert "Pod" in api_names
    assert "Deployment" in api_names
    assert "StatefulSet" in api_names
    assert "Job" in api_names
