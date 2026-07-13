"""Shared helpers for kmock-backed delivery-surface tests.

Provides the k8s equivalents of the helpers exported from
``tests/providers/aws/mocked/conftest.py``:

* ``_inject_kmock_factory`` — post-bootstrap hook that swaps the DI-wired
  K8sProviderStrategy's internal ``_kubernetes_client`` for a K8sClient
  pointed at the live kmock server URL, so every kubernetes SDK call routes
  through the emulator instead of a real apiserver.

* ``_make_k8s_logger`` — produces a MagicMock satisfying LoggingPort.

* ``_register_pod_resource`` / ``_register_deployment_resource`` /
  ``_register_statefulset_resource`` / ``_register_job_resource`` — kmock
  resource-registration helpers, re-exported so delivery tests don't have to
  reach into test_lifecycle_e2e.py.

The ``orb_config_dir_k8s`` fixture lives in ``conftest.py`` (not here) so
pytest discovers it automatically without any re-export tricks.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Logger helper
# ---------------------------------------------------------------------------


def _make_k8s_logger() -> Any:
    """Return a MagicMock satisfying the LoggingPort protocol."""
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


# ---------------------------------------------------------------------------
# kmock resource-registration helpers  (re-exported from test_lifecycle_e2e.py
# so delivery-surface tests only import from this module)
# ---------------------------------------------------------------------------


def _register_pod_resource(kmock_k8s) -> None:
    from kmock import resource

    pod_res = resource("", "v1", "pods")
    kmock_k8s.resources[pod_res] = {
        "namespaced": True,
        "kind": "Pod",
        "singular": "pod",
        "verbs": ["get", "list", "create", "delete", "watch"],
        "shortnames": ["po"],
        "categories": [],
        "subresources": [],
    }


def _register_deployment_resource(kmock_k8s) -> None:
    from kmock import resource

    dep_res = resource("apps", "v1", "deployments")
    kmock_k8s.resources[dep_res] = {
        "namespaced": True,
        "kind": "Deployment",
        "singular": "deployment",
        "verbs": ["get", "list", "create", "patch", "delete", "watch"],
        "shortnames": ["deploy"],
        "categories": [],
        "subresources": ["scale", "status"],
    }


def _register_statefulset_resource(kmock_k8s) -> None:
    from kmock import resource

    sts_res = resource("apps", "v1", "statefulsets")
    kmock_k8s.resources[sts_res] = {
        "namespaced": True,
        "kind": "StatefulSet",
        "singular": "statefulset",
        "verbs": ["get", "list", "create", "patch", "delete", "watch"],
        "shortnames": ["sts"],
        "categories": [],
        "subresources": ["scale", "status"],
    }


def _register_job_resource(kmock_k8s) -> None:
    from kmock import resource

    job_res = resource("batch", "v1", "jobs")
    kmock_k8s.resources[job_res] = {
        "namespaced": True,
        "kind": "Job",
        "singular": "job",
        "verbs": ["get", "list", "create", "patch", "delete", "watch"],
        "shortnames": [],
        "categories": [],
        "subresources": ["status"],
    }


# ---------------------------------------------------------------------------
# kmock injection helper
# ---------------------------------------------------------------------------


def _inject_kmock_factory(kmock_k8s, logger) -> None:
    """Swap the DI-wired K8sProviderStrategy's kubernetes_client for a kmock one.

    Called post-bootstrap (after Application.initialize completes) so that the
    DI container and provider registry are already populated.  Replaces
    ``strategy._kubernetes_client`` in-place so subsequent handler calls go
    through the kmock aiohttp server rather than a real apiserver.

    Args:
        kmock_k8s: A running :class:`kmock.KubernetesEmulator` instance.
        logger: A LoggingPort-compatible object (use ``_make_k8s_logger()``).
    """
    import kubernetes.client as _kc

    from orb.domain.base.ports import ConfigurationPort
    from orb.infrastructure.di.container import get_container
    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient
    from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry
    from orb.providers.registry import get_provider_registry
    from tests.providers.k8s.mocked.conftest import K8S_KMOCK_PROVIDER_NAME

    # Build a kubernetes.client.ApiClient pointed at the kmock server.
    cfg = _kc.Configuration()
    cfg.host = str(kmock_k8s.url).rstrip("/")
    cfg.verify_ssl = False
    api_client = _kc.ApiClient(configuration=cfg)

    k8s_config = K8sProviderConfig(namespace="orb-test")  # type: ignore[call-arg]
    kmock_client = K8sClient(config=k8s_config, logger=logger, api_client=api_client)

    # Ensure the provider instance is registered in the registry.
    registry = get_provider_registry()
    container = get_container()
    cfg_port = container.get(ConfigurationPort)
    registry._config_port = cfg_port

    provider_config = cfg_port.get_provider_config()
    if provider_config:
        for pi in provider_config.get_active_providers():
            if not registry.is_provider_instance_registered(pi.name):
                registry.ensure_provider_instance_registered_from_config(pi)

    strategy = registry.get_or_create_strategy(K8S_KMOCK_PROVIDER_NAME)
    if strategy is None:
        # Strategy may not yet be cached — create it directly with our client.
        from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

        strategy = K8sProviderStrategy(
            config=k8s_config,
            logger=logger,
            provider_name=K8S_KMOCK_PROVIDER_NAME,
            kubernetes_client=kmock_client,
        )
        strategy.initialize()
        registry._strategy_cache[K8S_KMOCK_PROVIDER_NAME] = strategy
    else:
        # Swap out the client on the already-cached strategy.
        strategy._kubernetes_client = kmock_client

        # Rebuild the handler registry so handlers pick up the new client.
        strategy._handler_registry = K8sHandlerRegistry(
            config=strategy._k8s_config,
            logger=strategy._logger,
            client_provider=lambda: strategy.kubernetes_client,
            watch_manager_provider=lambda: None,
            plugin_factories=lambda: {},
            native_spec_service_provider=lambda: None,
        )
