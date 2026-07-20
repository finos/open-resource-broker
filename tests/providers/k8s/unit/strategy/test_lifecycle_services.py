"""Unit tests for the extracted k8s strategy lifecycle services.

These cover the cohesive service classes carved out of the
``K8sProviderStrategy`` god-class:

* :class:`K8sWatchManagerLifecycle` — watch fan-out + metrics + shutdown
* :class:`K8sReconciliationLifecycle` — startup reconciler + orphan GC
* :class:`K8sNodeWatchLifecycle` — node watcher + events watcher
* :class:`K8sNativeSpecResolver` — native-spec DI resolution

They exercise the services directly (not through the strategy shell) so a
regression in an extracted unit is pinned to that unit.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.strategy.native_spec_resolver import K8sNativeSpecResolver
from orb.providers.k8s.strategy.node_watch_lifecycle import K8sNodeWatchLifecycle
from orb.providers.k8s.strategy.reconciliation_lifecycle import K8sReconciliationLifecycle
from orb.providers.k8s.strategy.watch_lifecycle import K8sWatchManagerLifecycle


def _config(**kwargs: Any) -> K8sProviderConfig:
    return K8sProviderConfig(**kwargs)


# ===========================================================================
# K8sWatchManagerLifecycle
# ===========================================================================


class TestWatchManagerLifecycle:
    def test_ensure_is_lazy_and_cached(self) -> None:
        injected = MagicMock()
        svc = K8sWatchManagerLifecycle(
            config=_config(),
            logger=MagicMock(),
            client_provider=MagicMock,
            watch_manager=injected,
        )
        # Pre-injected manager is returned without construction.
        assert svc.ensure() is injected
        assert svc.watch_manager is injected

    def test_shared_cache_comes_from_watch_manager(self) -> None:
        injected = MagicMock()
        svc = K8sWatchManagerLifecycle(
            config=_config(),
            logger=MagicMock(),
            client_provider=MagicMock,
            watch_manager=injected,
        )
        assert svc.shared_cache() is injected.cache

    def test_get_metrics_returns_none_when_disabled(self) -> None:
        svc = K8sWatchManagerLifecycle(
            config=_config(metrics_enabled=False),
            logger=MagicMock(),
            client_provider=MagicMock,
        )
        assert svc.get_metrics() is None

    def test_maybe_start_noop_when_watch_disabled(self) -> None:
        svc = K8sWatchManagerLifecycle(
            config=_config(watch_enabled=False),
            logger=MagicMock(),
            client_provider=MagicMock,
        )
        svc.maybe_start()
        # Nothing constructed / started.
        assert svc.watch_manager is None

    def test_maybe_start_skips_without_running_loop(self) -> None:
        manager = MagicMock()
        svc = K8sWatchManagerLifecycle(
            config=_config(watch_enabled=True),
            logger=MagicMock(),
            client_provider=MagicMock,
            watch_manager=manager,
        )
        # No running loop in this synchronous context → start is skipped.
        svc.maybe_start()
        manager.start.assert_not_called()

    def test_stop_sync_noop_when_not_started(self) -> None:
        manager = MagicMock()
        manager.is_started.return_value = False
        svc = K8sWatchManagerLifecycle(
            config=_config(),
            logger=MagicMock(),
            client_provider=MagicMock,
            watch_manager=manager,
        )
        # Should not attempt to drive stop().
        svc.stop_sync()
        manager.stop.assert_not_called()

    def test_stop_sync_no_loop_runs_synchronously(self) -> None:
        completed = {"n": 0}

        async def _stop() -> None:
            completed["n"] += 1

        manager = MagicMock()
        manager.is_started.return_value = True
        manager.stop = AsyncMock(side_effect=_stop)
        svc = K8sWatchManagerLifecycle(
            config=_config(),
            logger=MagicMock(),
            client_provider=MagicMock,
            watch_manager=manager,
        )
        svc.stop_sync()
        assert completed["n"] == 1


# ===========================================================================
# K8sReconciliationLifecycle
# ===========================================================================


class TestReconciliationLifecycle:
    def _make(self, **kwargs: Any) -> tuple[K8sReconciliationLifecycle, MagicMock]:
        watch_manager: MagicMock = kwargs.pop("watch_manager", MagicMock())
        watch = K8sWatchManagerLifecycle(
            config=kwargs.get("config", _config()),
            logger=MagicMock(),
            client_provider=MagicMock,
            watch_manager=watch_manager,
        )
        svc = K8sReconciliationLifecycle(
            config=kwargs.get("config", _config()),
            logger=MagicMock(),
            client_provider=MagicMock,
            watch_lifecycle=watch,
            known_request_ids=lambda: (),
            startup_reconciler=kwargs.get("startup_reconciler"),
            orphan_gc=kwargs.get("orphan_gc"),
        )
        return svc, watch_manager

    @pytest.mark.asyncio
    async def test_run_startup_reconciler_records_report(self) -> None:
        report = MagicMock()
        report.completed = True
        reconciler = MagicMock()
        reconciler.run_async = AsyncMock(return_value=report)
        svc, watch_manager = self._make(startup_reconciler=reconciler)

        await svc.run_startup_reconciler()

        assert svc.last_reconciliation_report is report
        # first-sync-complete is signalled on the shared watch manager.
        watch_manager.mark_first_sync_complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_startup_reconciler_incomplete_does_not_mark_sync(self) -> None:
        report = MagicMock()
        report.completed = False
        report.error = "boom"
        reconciler = MagicMock()
        reconciler.run_async = AsyncMock(return_value=report)
        svc, watch_manager = self._make(startup_reconciler=reconciler)

        await svc.run_startup_reconciler()

        watch_manager.mark_first_sync_complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_startup_reconciler_swallows_exception(self) -> None:
        reconciler = MagicMock()
        reconciler.run_async = AsyncMock(side_effect=RuntimeError("kaboom"))
        svc, _ = self._make(startup_reconciler=reconciler)
        # Must not raise — provider continues.
        await svc.run_startup_reconciler()

    def test_maybe_start_orphan_gc_noop_when_disabled(self) -> None:
        gc = MagicMock()
        svc, _ = self._make(config=_config(orphan_gc_enabled=False), orphan_gc=gc)
        svc.maybe_start_orphan_gc()
        gc.start.assert_not_called()

    def test_maybe_start_orphan_gc_skips_without_loop(self) -> None:
        gc = MagicMock()
        svc, _ = self._make(config=_config(orphan_gc_enabled=True), orphan_gc=gc)
        # No running loop → GC start skipped.
        svc.maybe_start_orphan_gc()
        gc.start.assert_not_called()

    def test_stop_orphan_gc_sync_noop_when_not_running(self) -> None:
        gc = MagicMock()
        gc.is_running.return_value = False
        svc, _ = self._make(orphan_gc=gc)
        svc.stop_orphan_gc_sync()
        gc.stop.assert_not_called()

    def test_stop_orphan_gc_sync_no_loop_runs_synchronously(self) -> None:
        completed = {"n": 0}

        async def _stop() -> None:
            completed["n"] += 1

        gc = MagicMock()
        gc.is_running.return_value = True
        gc.stop = AsyncMock(side_effect=_stop)
        svc, _ = self._make(orphan_gc=gc)
        svc.stop_orphan_gc_sync()
        assert completed["n"] == 1


# ===========================================================================
# K8sNodeWatchLifecycle
# ===========================================================================


class TestNodeWatchLifecycle:
    def test_caches_always_present(self) -> None:
        svc = K8sNodeWatchLifecycle(
            config=_config(),
            logger=MagicMock(),
            client_provider=MagicMock,
        )
        assert svc.node_state_cache is not None
        assert svc.node_events_cache is not None

    def test_maybe_start_node_watcher_noop_when_disabled(self) -> None:
        watcher = MagicMock()
        svc = K8sNodeWatchLifecycle(
            config=_config(node_watch_enabled=False),
            logger=MagicMock(),
            client_provider=MagicMock,
            node_watcher=watcher,
        )
        svc.maybe_start_node_watcher()
        watcher.start.assert_not_called()

    def test_maybe_start_node_watcher_starts_when_enabled(self) -> None:
        watcher = MagicMock()
        svc = K8sNodeWatchLifecycle(
            config=_config(node_watch_enabled=True),
            logger=MagicMock(),
            client_provider=MagicMock,
            node_watcher=watcher,
        )
        svc.maybe_start_node_watcher()
        watcher.start.assert_called_once()

    def test_maybe_start_events_watcher_noop_when_disabled(self) -> None:
        watcher = MagicMock()
        svc = K8sNodeWatchLifecycle(
            config=_config(events_watch_enabled=False),
            logger=MagicMock(),
            client_provider=MagicMock,
            events_watcher=watcher,
        )
        svc.maybe_start_events_watcher()
        watcher.start.assert_not_called()

    def test_maybe_start_events_watcher_starts_when_enabled(self) -> None:
        watcher = MagicMock()
        svc = K8sNodeWatchLifecycle(
            config=_config(events_watch_enabled=True),
            logger=MagicMock(),
            client_provider=MagicMock,
            events_watcher=watcher,
        )
        svc.maybe_start_events_watcher()
        watcher.start.assert_called_once()

    def test_stop_node_watcher_clears_reference(self) -> None:
        watcher = MagicMock()
        svc = K8sNodeWatchLifecycle(
            config=_config(),
            logger=MagicMock(),
            client_provider=MagicMock,
            node_watcher=watcher,
        )
        svc.stop_node_watcher()
        watcher.stop.assert_called_once()
        assert svc.node_watcher is None

    def test_stop_events_watcher_clears_reference(self) -> None:
        watcher = MagicMock()
        svc = K8sNodeWatchLifecycle(
            config=_config(),
            logger=MagicMock(),
            client_provider=MagicMock,
            events_watcher=watcher,
        )
        svc.stop_events_watcher()
        watcher.stop.assert_called_once()
        assert svc.events_watcher is None


# ===========================================================================
# K8sNativeSpecResolver
# ===========================================================================


class TestNativeSpecResolver:
    def test_returns_none_when_disabled(self) -> None:
        resolver = K8sNativeSpecResolver(
            config=_config(native_spec_enabled=False),
            logger=MagicMock(),
            config_port=MagicMock(),
            injected_native_spec_service=MagicMock(),
        )
        assert resolver.resolve() is None

    def test_returns_none_when_no_config_port(self) -> None:
        resolver = K8sNativeSpecResolver(
            config=_config(native_spec_enabled=True),
            logger=MagicMock(),
            config_port=None,
            injected_native_spec_service=MagicMock(),
        )
        assert resolver.resolve() is None

    def test_returns_none_when_no_injected_service(self) -> None:
        resolver = K8sNativeSpecResolver(
            config=_config(native_spec_enabled=True),
            logger=MagicMock(),
            config_port=MagicMock(),
            injected_native_spec_service=None,
        )
        assert resolver.resolve() is None

    def test_resolution_is_cached(self) -> None:
        # Disabled config short-circuits, but the resolved flag must be
        # sticky so a second call does not re-run the gating logic.
        resolver = K8sNativeSpecResolver(
            config=_config(native_spec_enabled=False),
            logger=MagicMock(),
            config_port=MagicMock(),
            injected_native_spec_service=MagicMock(),
        )
        first = resolver.resolve()
        second = resolver.resolve()
        assert first is second is None
