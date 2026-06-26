"""Unit tests for the Kubernetes plugin extension point.

Covers :meth:`KubernetesProviderStrategy.register_handler` /
:meth:`KubernetesProviderStrategy.unregister_handler` — the public
hook documented at
``docs/root/providers/kubernetes/plugin-authoring.md``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.providers.kubernetes.configuration.config import KubernetesProviderConfig
from orb.providers.kubernetes.handlers.base_handler import KubernetesHandlerBase
from orb.providers.kubernetes.strategy.kubernetes_provider_strategy import (
    KubernetesProviderStrategy,
)


class _StubHandler(KubernetesHandlerBase):
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
    snapshot = dict(KubernetesProviderStrategy._HANDLER_FACTORIES)
    try:
        yield
    finally:
        KubernetesProviderStrategy._HANDLER_FACTORIES = snapshot


def test_register_handler_inserts_into_registry() -> None:
    KubernetesProviderStrategy.register_handler("KubernetesPluginTest", _StubHandler)
    assert KubernetesProviderStrategy._HANDLER_FACTORIES["KubernetesPluginTest"] is _StubHandler


def test_register_handler_is_idempotent_for_same_class() -> None:
    KubernetesProviderStrategy.register_handler("KubernetesPluginTest", _StubHandler)
    # Re-registering the same class is allowed so plugin reloads do not fail.
    KubernetesProviderStrategy.register_handler("KubernetesPluginTest", _StubHandler)
    assert KubernetesProviderStrategy._HANDLER_FACTORIES["KubernetesPluginTest"] is _StubHandler


def test_register_handler_rejects_conflicting_class() -> None:
    KubernetesProviderStrategy.register_handler("KubernetesPluginTest", _StubHandler)
    with pytest.raises(ValueError, match="already registered"):
        KubernetesProviderStrategy.register_handler("KubernetesPluginTest", _OtherStubHandler)


def test_unregister_handler_removes_entry() -> None:
    KubernetesProviderStrategy.register_handler("KubernetesPluginTest", _StubHandler)
    KubernetesProviderStrategy.unregister_handler("KubernetesPluginTest")
    assert "KubernetesPluginTest" not in KubernetesProviderStrategy._HANDLER_FACTORIES


def test_unregister_handler_is_safe_when_absent() -> None:
    # Must not raise when the key is not registered.
    KubernetesProviderStrategy.unregister_handler("never-registered")


def test_strategy_dispatches_plugin_handler_via_get_handler() -> None:
    """``_get_handler`` resolves plugin-registered handlers."""
    KubernetesProviderStrategy.register_handler("KubernetesPluginTest", _StubHandler)

    strategy = KubernetesProviderStrategy(
        config=KubernetesProviderConfig(),
        logger=MagicMock(),
        kubernetes_client=MagicMock(),
    )
    handler = strategy._get_handler("KubernetesPluginTest")
    assert isinstance(handler, _StubHandler)
    # The same instance is cached for subsequent lookups.
    assert strategy._get_handler("KubernetesPluginTest") is handler


def test_strategy_still_raises_for_unknown_provider_api() -> None:
    """Unknown provider_api keys raise ``NotImplementedError``."""
    strategy = KubernetesProviderStrategy(
        config=KubernetesProviderConfig(),
        logger=MagicMock(),
        kubernetes_client=MagicMock(),
    )
    with pytest.raises(NotImplementedError, match="not yet implemented"):
        strategy._get_handler("KubernetesDoesNotExist")
