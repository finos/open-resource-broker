"""Unit tests for the Kubernetes plugin extension point.

Covers :meth:`K8sProviderStrategy.register_handler` /
:meth:`K8sProviderStrategy.unregister_handler` — the public
hook documented at
``docs/root/providers/k8s/plugin-authoring.md``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.handlers.base_handler import K8sHandlerBase
from orb.providers.k8s.strategy.k8s_provider_strategy import (
    K8sProviderStrategy,
)


class _StubHandler(K8sHandlerBase):
    """Smallest possible handler subclass for tests."""

    PROVIDER_API = "KubernetesPluginTest"

    def __init__(self, *args, pod_state_cache=None, cache_alive=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._pod_state_cache = pod_state_cache
        self._cache_alive = cache_alive

    async def acquire_hosts(self, request, template):  # pragma: no cover — not exercised here
        return {"resource_ids": [], "machine_ids": [], "provider_data": {}}

    def check_hosts_status(self, request):  # pragma: no cover — not exercised here
        raise NotImplementedError

    async def release_hosts(self, machine_ids, request):  # pragma: no cover — not exercised here
        return None

    @classmethod
    def get_example_templates(cls):
        return []


class _OtherStubHandler(_StubHandler):
    """Different subclass — used to test the conflict path."""


@pytest.fixture(autouse=True)
def _clean_registry():
    """Snapshot + restore the class-level registry between tests."""
    snapshot = dict(K8sProviderStrategy._HANDLER_FACTORIES)
    try:
        yield
    finally:
        K8sProviderStrategy._HANDLER_FACTORIES = snapshot


def test_register_handler_inserts_into_registry() -> None:
    K8sProviderStrategy.register_handler("KubernetesPluginTest", _StubHandler)
    assert K8sProviderStrategy._HANDLER_FACTORIES["KubernetesPluginTest"] is _StubHandler


def test_register_handler_is_idempotent_for_same_class() -> None:
    K8sProviderStrategy.register_handler("KubernetesPluginTest", _StubHandler)
    # Re-registering the same class is allowed so plugin reloads do not fail.
    K8sProviderStrategy.register_handler("KubernetesPluginTest", _StubHandler)
    assert K8sProviderStrategy._HANDLER_FACTORIES["KubernetesPluginTest"] is _StubHandler


def test_register_handler_rejects_conflicting_class() -> None:
    K8sProviderStrategy.register_handler("KubernetesPluginTest", _StubHandler)
    with pytest.raises(ValueError, match="already registered"):
        K8sProviderStrategy.register_handler("KubernetesPluginTest", _OtherStubHandler)


def test_unregister_handler_removes_entry() -> None:
    K8sProviderStrategy.register_handler("KubernetesPluginTest", _StubHandler)
    K8sProviderStrategy.unregister_handler("KubernetesPluginTest")
    assert "KubernetesPluginTest" not in K8sProviderStrategy._HANDLER_FACTORIES


def test_unregister_handler_is_safe_when_absent() -> None:
    # Must not raise when the key is not registered.
    K8sProviderStrategy.unregister_handler("never-registered")


def test_strategy_dispatches_plugin_handler_via_get_handler() -> None:
    """``_get_handler`` resolves plugin-registered handlers."""
    K8sProviderStrategy.register_handler("KubernetesPluginTest", _StubHandler)

    strategy = K8sProviderStrategy(
        config=K8sProviderConfig(),
        logger=MagicMock(),
        kubernetes_client=MagicMock(),
    )
    handler = strategy._get_handler("KubernetesPluginTest")
    assert isinstance(handler, _StubHandler)
    # The same instance is cached for subsequent lookups.
    assert strategy._get_handler("KubernetesPluginTest") is handler


def test_strategy_still_raises_for_unknown_provider_api() -> None:
    """Unknown provider_api keys raise ``NotImplementedError``."""
    strategy = K8sProviderStrategy(
        config=K8sProviderConfig(),
        logger=MagicMock(),
        kubernetes_client=MagicMock(),
    )
    with pytest.raises(NotImplementedError, match="not yet implemented"):
        strategy._get_handler("KubernetesDoesNotExist")
