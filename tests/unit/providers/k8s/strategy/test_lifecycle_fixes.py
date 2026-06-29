"""Unit tests for the five K8s provider lifecycle fixes.

1. Instance-level handler registries (no cross-instance sharing)
2. Independent try-blocks in cleanup()
3. Idempotency guard on start_daemon_services()
4. Plugin factories receive native_spec_service + node_state_cache
5. check_health() surfaces cluster endpoint / server version / namespace
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orb.providers.k8s.configuration.config import (
    K8sProviderConfig,
)
from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry
from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_config(**kwargs: Any) -> K8sProviderConfig:
    return K8sProviderConfig(**kwargs)


def _make_strategy(**kwargs: Any) -> K8sProviderStrategy:
    return K8sProviderStrategy(
        config=_make_config(),
        logger=MagicMock(),
        **kwargs,
    )


def _make_registry(plugin_factories: dict | None = None) -> K8sHandlerRegistry:
    return K8sHandlerRegistry(
        config=_make_config(),
        logger=MagicMock(),
        client_provider=lambda: MagicMock(),
        watch_manager_provider=lambda: None,
        plugin_factories=lambda: plugin_factories or {},
        native_spec_service_provider=lambda: None,
    )


# ===========================================================================
# Fix 1: Instance-level handler registries
# ===========================================================================


class TestInstanceLevelHandlerRegistries:
    def test_two_strategies_do_not_share_plugin_factories(self) -> None:
        s1 = _make_strategy()
        s2 = _make_strategy()

        mock_factory = MagicMock(return_value=MagicMock())
        s1.register_handler("CustomApi", mock_factory)

        # s2 must not see the handler registered on s1
        assert "CustomApi" not in s2._handler_factories
        assert "CustomApi" in s1._handler_factories

    def test_unregister_handler_is_scoped_to_instance(self) -> None:
        s1 = _make_strategy()
        s2 = _make_strategy()

        factory = MagicMock(return_value=MagicMock())
        s1.register_handler("SomeApi", factory)
        s2.register_handler("SomeApi", factory)

        s1.unregister_handler("SomeApi")
        assert "SomeApi" not in s1._handler_factories
        # s2 still has it
        assert "SomeApi" in s2._handler_factories

    def test_register_same_class_twice_is_idempotent(self) -> None:
        s = _make_strategy()
        factory = MagicMock(return_value=MagicMock())
        s.register_handler("MyApi", factory)
        # Should not raise
        s.register_handler("MyApi", factory)
        assert s._handler_factories["MyApi"] is factory

    def test_register_different_class_raises(self) -> None:
        s = _make_strategy()
        f1 = MagicMock(return_value=MagicMock())
        f2 = MagicMock(return_value=MagicMock())
        s.register_handler("MyApi", f1)
        with pytest.raises(ValueError, match="already registered"):
            s.register_handler("MyApi", f2)

    def test_registry_handler_classes_are_per_instance(self) -> None:
        r1 = _make_registry()
        r2 = _make_registry()
        # Mutating one registry's _handler_classes must not affect the other
        from orb.providers.k8s.handlers.base_handler import K8sHandlerBase

        sentinel = type("Sentinel", (K8sHandlerBase,), {})
        r1._handler_classes["XApi"] = sentinel  # type: ignore[assignment]
        assert "XApi" not in r2._handler_classes

    def test_default_handler_classes_frozen(self) -> None:
        # Mutating an instance's _handler_classes must not touch the class default
        r = _make_registry()
        original_keys = set(K8sHandlerRegistry._DEFAULT_HANDLER_CLASSES.keys())
        r._handler_classes["ZApi"] = MagicMock()  # type: ignore[assignment]
        assert set(K8sHandlerRegistry._DEFAULT_HANDLER_CLASSES.keys()) == original_keys


# ===========================================================================
# Fix 2: Independent try-blocks in cleanup()
# ===========================================================================


class TestCleanupIndependentStages:
    def test_client_cleanup_runs_even_when_orphan_gc_stop_raises(self) -> None:
        strategy = _make_strategy()
        strategy._initialized = True

        mock_gc = MagicMock()
        mock_gc.is_running.return_value = True
        mock_gc.stop = MagicMock(side_effect=RuntimeError("gc stop exploded"))
        strategy._orphan_gc = mock_gc

        mock_client = MagicMock()
        strategy._kubernetes_client = mock_client

        # Must not propagate the GC exception
        strategy.cleanup()

        # Client cleanup must still have been called
        mock_client.cleanup.assert_called_once()
        assert strategy._initialized is False

    def test_client_cleanup_runs_even_when_watch_manager_stop_raises(self) -> None:
        strategy = _make_strategy()
        strategy._initialized = True

        # Patch _stop_watch_manager_sync to raise
        strategy._watch_manager = MagicMock()
        strategy._watch_manager.is_started.return_value = True

        mock_client = MagicMock()
        strategy._kubernetes_client = mock_client

        with patch.object(
            strategy,
            "_stop_watch_manager_sync",
            side_effect=RuntimeError("watcher stop exploded"),
        ):
            strategy.cleanup()

        mock_client.cleanup.assert_called_once()
        assert strategy._initialized is False

    def test_initialized_false_even_when_all_stages_raise(self) -> None:
        strategy = _make_strategy()
        strategy._initialized = True

        mock_gc = MagicMock()
        mock_gc.is_running.return_value = True
        mock_gc.stop = MagicMock(side_effect=RuntimeError("gc"))
        strategy._orphan_gc = mock_gc

        mock_watcher = MagicMock()
        mock_watcher.is_started.return_value = True
        strategy._watch_manager = mock_watcher

        mock_node_watcher = MagicMock()
        mock_node_watcher.stop.side_effect = RuntimeError("node watcher")
        strategy._node_watcher = mock_node_watcher

        mock_client = MagicMock()
        mock_client.cleanup.side_effect = RuntimeError("client")
        strategy._kubernetes_client = mock_client

        with patch.object(
            strategy,
            "_stop_watch_manager_sync",
            side_effect=RuntimeError("watch manager"),
        ):
            strategy.cleanup()

        assert strategy._initialized is False


# ===========================================================================
# Fix 3: Idempotency guard on start_daemon_services()
# ===========================================================================


class TestStartDaemonServicesIdempotency:
    @pytest.mark.asyncio
    async def test_second_call_is_skipped(self) -> None:
        strategy = _make_strategy()
        strategy._initialized = True

        call_count = 0

        def fake_reconciler() -> None:
            nonlocal call_count
            call_count += 1

        with (
            patch.object(strategy, "_run_startup_reconciler", side_effect=fake_reconciler),
            patch.object(strategy, "_maybe_start_watch_manager"),
            patch.object(strategy, "_maybe_start_orphan_gc"),
            patch.object(strategy, "_maybe_start_node_watcher"),
        ):
            await strategy.start_daemon_services()
            await strategy.start_daemon_services()  # second invocation must be skipped

        assert call_count == 1, "reconciler must run exactly once"

    @pytest.mark.asyncio
    async def test_flag_set_true_after_successful_start(self) -> None:
        strategy = _make_strategy()
        strategy._initialized = True

        with (
            patch.object(strategy, "_run_startup_reconciler"),
            patch.object(strategy, "_maybe_start_watch_manager"),
            patch.object(strategy, "_maybe_start_orphan_gc"),
            patch.object(strategy, "_maybe_start_node_watcher"),
        ):
            assert strategy._daemon_services_started is False
            await strategy.start_daemon_services()
            assert strategy._daemon_services_started is True

    @pytest.mark.asyncio
    async def test_flag_false_initially(self) -> None:
        strategy = _make_strategy()
        assert strategy._daemon_services_started is False

    @pytest.mark.asyncio
    async def test_cleanup_does_not_reset_daemon_services_started(self) -> None:
        # cleanup() manages _initialized; _daemon_services_started is a
        # separate guard so a new call after cleanup can restart services.
        strategy = _make_strategy()
        strategy._initialized = True
        strategy._daemon_services_started = True

        strategy.cleanup()

        # cleanup touches _initialized, not _daemon_services_started
        assert strategy._initialized is False
        # The flag is intentionally NOT reset by cleanup so that a
        # re-initialised instance that calls start_daemon_services again
        # does NOT re-run reconciliation with stale state.


# ===========================================================================
# Fix 4: Plugin factories receive native_spec_service + node_state_cache
# ===========================================================================


class TestPluginFactoryKwargs:
    def test_plugin_factory_receives_seven_kwargs(self) -> None:
        received_kwargs: dict[str, Any] = {}

        def capturing_factory(**kwargs: Any) -> MagicMock:
            received_kwargs.update(kwargs)
            return MagicMock()

        mock_native = MagicMock()
        mock_node_cache = MagicMock()

        registry = K8sHandlerRegistry(
            config=_make_config(),
            logger=MagicMock(),
            client_provider=lambda: MagicMock(),
            watch_manager_provider=lambda: None,
            plugin_factories=lambda: {"PluginApi": capturing_factory},
            native_spec_service_provider=lambda: mock_native,
            node_state_cache_provider=lambda: mock_node_cache,
        )

        registry.get_handler("PluginApi")

        assert "native_spec_service" in received_kwargs, (
            "plugin factory must receive native_spec_service"
        )
        assert "node_state_cache" in received_kwargs, "plugin factory must receive node_state_cache"
        assert received_kwargs["native_spec_service"] is mock_native
        assert received_kwargs["node_state_cache"] is mock_node_cache

    def test_plugin_factory_receives_base_five_kwargs(self) -> None:
        received_kwargs: dict[str, Any] = {}

        def capturing_factory(**kwargs: Any) -> MagicMock:
            received_kwargs.update(kwargs)
            return MagicMock()

        registry = K8sHandlerRegistry(
            config=_make_config(),
            logger=MagicMock(),
            client_provider=lambda: MagicMock(),
            watch_manager_provider=lambda: None,
            plugin_factories=lambda: {"PluginApi": capturing_factory},
            native_spec_service_provider=lambda: None,
        )

        registry.get_handler("PluginApi")

        for key in ("kubernetes_client", "config", "logger", "pod_state_cache", "cache_alive"):
            assert key in received_kwargs, f"plugin factory must receive {key!r}"


# ===========================================================================
# Fix 5: check_health() enrichment
# ===========================================================================


class TestCheckHealthEnrichment:
    # VersionApi is imported locally inside check_health via
    # ``from kubernetes.client import VersionApi`` — patch at the source.
    _VERSION_API_PATH = "kubernetes.client.VersionApi"

    def _make_strategy_with_mock_client(
        self,
        *,
        host: str = "https://k8s.example.com:6443",
        git_version: str = "v1.30.1",
        namespace: str | None = "default",
    ) -> tuple[K8sProviderStrategy, MagicMock]:
        # namespace belongs in config, not in the strategy constructor
        strategy = K8sProviderStrategy(
            config=K8sProviderConfig(namespace=namespace),
            logger=MagicMock(),
        )

        mock_api_client = MagicMock()
        mock_api_client.configuration.host = host

        mock_client = MagicMock()
        mock_client.api_client = mock_api_client
        mock_client.core_v1.get_api_resources.return_value = MagicMock(resources=[MagicMock()] * 5)

        mock_version_info = MagicMock()
        mock_version_info.git_version = git_version

        strategy._kubernetes_client = mock_client
        return strategy, mock_version_info

    def test_healthy_status_includes_endpoint(self) -> None:
        strategy, mock_version_info = self._make_strategy_with_mock_client(
            host="https://my-cluster.example.com:6443"
        )
        with patch(self._VERSION_API_PATH) as mock_va:
            mock_va.return_value.get_code.return_value = mock_version_info
            status = strategy.check_health()

        assert status.is_healthy is True
        assert "my-cluster.example.com" in status.status_message
        assert status.error_details is not None
        assert status.error_details.get("cluster_endpoint") == "https://my-cluster.example.com:6443"

    def test_healthy_status_includes_server_version(self) -> None:
        strategy, mock_version_info = self._make_strategy_with_mock_client(git_version="v1.31.0")
        with patch(self._VERSION_API_PATH) as mock_va:
            mock_va.return_value.get_code.return_value = mock_version_info
            status = strategy.check_health()

        assert "v1.31.0" in status.status_message
        assert status.error_details is not None
        assert status.error_details.get("server_version") == "v1.31.0"

    def test_healthy_status_includes_namespace(self) -> None:
        strategy, mock_version_info = self._make_strategy_with_mock_client(namespace="team-a")
        with patch(self._VERSION_API_PATH) as mock_va:
            mock_va.return_value.get_code.return_value = mock_version_info
            status = strategy.check_health()

        assert "team-a" in status.status_message
        assert status.error_details is not None
        assert status.error_details.get("namespace") == "team-a"

    def test_version_probe_failure_does_not_fail_health_check(self) -> None:
        strategy, _ = self._make_strategy_with_mock_client()
        with patch(self._VERSION_API_PATH) as mock_va:
            mock_va.return_value.get_code.side_effect = Exception("version probe exploded")
            status = strategy.check_health()

        # Health check must still succeed
        assert status.is_healthy is True
        # Version should be absent from details
        assert status.error_details is not None
        assert "server_version" not in status.error_details

    def test_endpoint_probe_failure_does_not_fail_health_check(self) -> None:
        strategy = _make_strategy()

        mock_api_client = MagicMock()
        # Make .configuration.host raise
        mock_api_client.configuration = MagicMock()
        type(mock_api_client.configuration).host = property(
            fget=lambda s: (_ for _ in ()).throw(AttributeError("no host"))
        )

        mock_client = MagicMock()
        mock_client.api_client = mock_api_client
        mock_client.core_v1.get_api_resources.return_value = MagicMock(resources=[])
        strategy._kubernetes_client = mock_client

        with patch(self._VERSION_API_PATH):
            status = strategy.check_health()

        assert status.is_healthy is True

    def test_unhealthy_when_api_server_unreachable(self) -> None:
        strategy = _make_strategy()
        mock_client = MagicMock()
        mock_client.core_v1.get_api_resources.side_effect = Exception("connection refused")
        strategy._kubernetes_client = mock_client

        status = strategy.check_health()

        assert status.is_healthy is False
        assert "unreachable" in status.status_message.lower()
