"""Gap-filling unit tests for K8sProviderStrategy lifecycle methods.

Targets uncovered lines in k8s_provider_strategy.py:
- cleanup() exception paths (362-363, 392-393, node/events watcher errors)
- _maybe_start_watch_manager() manager.start() exception (444-464)
- _run_startup_reconciler() report.completed + watch_manager path (584-617)
- _maybe_start_orphan_gc() no-loop, gc already set, start exception (621-646)
- _stop_orphan_gc_sync() timeout and other exception paths (688-698)
- _maybe_start_node_watcher() node_watcher already set, start exception (722-734)
- _maybe_start_events_watcher() events_watcher set, start exception (764-774)
- register_handler() duplicate class (idempotent) and conflict (ValueError)
- _resolve_native_spec_service() various paths (1343-1463)
- _get_start_stop_service() lazy construction (1526)
- start_daemon_services() idempotency guard (341-342)
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger() -> Any:
    logger = MagicMock()
    for m in ("debug", "info", "warning", "error", "critical"):
        setattr(logger, m, MagicMock())
    return logger


def _make_config(**kwargs: Any) -> K8sProviderConfig:
    defaults: dict[str, Any] = {"namespace": "test-ns"}
    defaults.update(kwargs)
    return K8sProviderConfig(**defaults)  # type: ignore[call-arg]


def _make_mock_client() -> MagicMock:
    client = MagicMock()
    client.core_v1 = MagicMock()
    client.apps_v1 = MagicMock()
    client.batch_v1 = MagicMock()
    client.cleanup = MagicMock()
    return client


def _make_strategy(
    *,
    config: K8sProviderConfig | None = None,
    client: Any = None,
    initialized: bool = True,
    **extra: Any,
) -> K8sProviderStrategy:
    cfg = config or _make_config()
    logger = _make_logger()
    mock_client = client or _make_mock_client()
    strategy = K8sProviderStrategy(
        config=cfg,
        logger=logger,
        kubernetes_client=mock_client,
        **extra,
    )
    strategy._initialized = initialized
    return strategy


# ---------------------------------------------------------------------------
# cleanup() — exception paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cleanup_orphan_gc_exception_is_swallowed() -> None:
    """cleanup() logs and continues when the orphan GC stop raises."""
    strategy = _make_strategy()
    mock_gc = MagicMock()
    mock_gc.is_running.return_value = True
    mock_gc.stop = MagicMock(side_effect=RuntimeError("gc-boom"))
    strategy._orphan_gc = mock_gc

    # _stop_orphan_gc_sync drives gc.stop() — ensure we don't raise
    with patch.object(strategy, "_stop_orphan_gc_sync", side_effect=RuntimeError("gc-boom")):
        strategy.cleanup()  # must not raise

    strategy._logger.warning.assert_called()


@pytest.mark.unit
def test_cleanup_node_watcher_exception_is_swallowed() -> None:
    """cleanup() logs and continues when node watcher stop raises."""
    strategy = _make_strategy()
    mock_node_watcher = MagicMock()
    mock_node_watcher.stop = MagicMock(side_effect=RuntimeError("nw-boom"))
    strategy._node_watcher = mock_node_watcher

    strategy.cleanup()  # must not raise

    strategy._logger.warning.assert_called()


@pytest.mark.unit
def test_cleanup_events_watcher_exception_is_swallowed() -> None:
    """cleanup() logs and continues when events watcher stop raises."""
    strategy = _make_strategy()
    mock_events_watcher = MagicMock()
    mock_events_watcher.stop = MagicMock(side_effect=RuntimeError("ew-boom"))
    strategy._events_watcher = mock_events_watcher

    strategy.cleanup()  # must not raise

    strategy._logger.warning.assert_called()


@pytest.mark.unit
def test_cleanup_client_exception_leaves_initialized_true() -> None:
    """cleanup() does not clear _initialized when client.cleanup() raises."""
    mock_client = _make_mock_client()
    mock_client.cleanup.side_effect = RuntimeError("client-boom")
    strategy = _make_strategy(client=mock_client)
    strategy._initialized = True

    strategy.cleanup()

    # Client cleanup failed — _initialized must remain True
    assert strategy._initialized is True


@pytest.mark.unit
def test_cleanup_watch_manager_exception_is_swallowed() -> None:
    """cleanup() logs and continues when watch manager stop raises."""
    strategy = _make_strategy()
    mock_watcher = MagicMock()
    mock_watcher.is_started.return_value = False
    strategy._watch_manager = mock_watcher

    with patch.object(strategy, "_stop_watch_manager_sync", side_effect=RuntimeError("wm-boom")):
        strategy.cleanup()  # must not raise

    strategy._logger.warning.assert_called()


# ---------------------------------------------------------------------------
# _maybe_start_watch_manager() — manager.start() exception
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_maybe_start_watch_manager_start_exception_logged() -> None:
    """_maybe_start_watch_manager() catches manager.start() exceptions and logs them."""
    strategy = _make_strategy(config=_make_config(watch_enabled=True))
    mock_manager = MagicMock()
    mock_manager.start = MagicMock(side_effect=RuntimeError("watch-start-fail"))
    strategy._watch_manager = mock_manager

    async def _run() -> None:
        strategy._maybe_start_watch_manager()

    asyncio.run(_run())

    strategy._logger.warning.assert_called()


@pytest.mark.unit
def test_maybe_start_watch_manager_no_loop_logs_debug() -> None:
    """_maybe_start_watch_manager() without a running loop logs debug and returns."""
    strategy = _make_strategy(config=_make_config(watch_enabled=True))
    mock_manager = MagicMock()
    strategy._watch_manager = mock_manager

    # No event loop running — synchronous call
    strategy._maybe_start_watch_manager()

    strategy._logger.debug.assert_called()
    mock_manager.start.assert_not_called()


# ---------------------------------------------------------------------------
# _run_startup_reconciler() — uncovered branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_startup_reconciler_incomplete_report_logs_warning() -> None:
    """_run_startup_reconciler() warns when reconciler reports completed=False."""
    strategy = _make_strategy()

    mock_reconciler = MagicMock()
    incomplete_report = SimpleNamespace(completed=False, error="timeout")
    mock_reconciler.run_async = AsyncMock(return_value=incomplete_report)
    strategy._startup_reconciler = mock_reconciler

    asyncio.run(strategy._run_startup_reconciler())

    strategy._logger.warning.assert_called()


@pytest.mark.unit
def test_run_startup_reconciler_completed_with_watch_manager_marks_sync() -> None:
    """_run_startup_reconciler() calls mark_first_sync_complete when report.completed=True."""
    strategy = _make_strategy()

    mock_watch_manager = MagicMock()
    strategy._watch_manager = mock_watch_manager

    mock_reconciler = MagicMock()
    complete_report = SimpleNamespace(completed=True, error=None)
    mock_reconciler.run_async = AsyncMock(return_value=complete_report)
    strategy._startup_reconciler = mock_reconciler

    asyncio.run(strategy._run_startup_reconciler())

    mock_watch_manager.mark_first_sync_complete.assert_called_once()


@pytest.mark.unit
def test_run_startup_reconciler_exception_logs_warning() -> None:
    """_run_startup_reconciler() catches and logs run_async() exceptions."""
    strategy = _make_strategy()

    mock_reconciler = MagicMock()
    mock_reconciler.run_async = AsyncMock(side_effect=RuntimeError("reconcile-bang"))
    strategy._startup_reconciler = mock_reconciler

    asyncio.run(strategy._run_startup_reconciler())  # must not raise

    strategy._logger.warning.assert_called()


@pytest.mark.unit
def test_run_startup_reconciler_lazy_constructs_when_none() -> None:
    """_run_startup_reconciler() builds a StartupReconciler when _startup_reconciler is None."""
    strategy = _make_strategy()
    assert strategy._startup_reconciler is None

    mock_cache = MagicMock()
    mock_manager = MagicMock()
    mock_manager.cache = mock_cache

    complete_report = SimpleNamespace(completed=True, error=None)

    with (
        patch(
            "orb.providers.k8s.strategy.k8s_provider_strategy.StartupReconciler"
        ) as MockReconciler,
        patch.object(strategy, "_ensure_watch_manager", return_value=mock_manager),
    ):
        instance = MagicMock()
        instance.run_async = AsyncMock(return_value=complete_report)
        MockReconciler.return_value = instance

        asyncio.run(strategy._run_startup_reconciler())

    MockReconciler.assert_called_once()
    assert strategy._startup_reconciler is instance


# ---------------------------------------------------------------------------
# _maybe_start_orphan_gc() — branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_maybe_start_orphan_gc_no_loop_logs_debug() -> None:
    """_maybe_start_orphan_gc() without a running loop logs debug and skips."""
    strategy = _make_strategy(config=_make_config(orphan_gc_enabled=True))

    # No event loop — synchronous context
    strategy._maybe_start_orphan_gc()

    strategy._logger.debug.assert_called()


@pytest.mark.unit
def test_maybe_start_orphan_gc_gc_start_exception_logged() -> None:
    """_maybe_start_orphan_gc() catches OrphanGarbageCollector.start() exceptions."""
    strategy = _make_strategy(config=_make_config(orphan_gc_enabled=True))
    mock_gc = MagicMock()
    mock_gc.start = MagicMock(side_effect=RuntimeError("gc-start-fail"))
    strategy._orphan_gc = mock_gc

    async def _run() -> None:
        strategy._maybe_start_orphan_gc()

    asyncio.run(_run())

    strategy._logger.warning.assert_called()


# ---------------------------------------------------------------------------
# _stop_orphan_gc_sync() — timeout and exception paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_stop_orphan_gc_sync_timeout_logs_warning() -> None:
    """_stop_orphan_gc_sync() logs a warning when the future times out."""
    strategy = _make_strategy()
    mock_gc = MagicMock()
    mock_gc.is_running.return_value = True

    async def _slow_stop() -> None:
        import time

        time.sleep(100)

    mock_gc.stop = _slow_stop

    async def _run() -> None:
        strategy._orphan_gc = mock_gc
        # Patch asyncio.run_coroutine_threadsafe to return a future that times out
        mock_future = MagicMock()
        mock_future.result = MagicMock(side_effect=TimeoutError())

        with patch("asyncio.run_coroutine_threadsafe", return_value=mock_future):
            strategy._stop_orphan_gc_sync(stop_timeout=0.01)

    asyncio.run(_run())

    strategy._logger.warning.assert_called()


@pytest.mark.unit
def test_stop_orphan_gc_sync_other_exception_logged_at_debug() -> None:
    """_stop_orphan_gc_sync() logs non-TimeoutError exceptions at debug level."""
    strategy = _make_strategy()
    mock_gc = MagicMock()
    mock_gc.is_running.return_value = True
    mock_gc.stop = AsyncMock()

    async def _run() -> None:
        strategy._orphan_gc = mock_gc
        mock_future = MagicMock()
        mock_future.result = MagicMock(side_effect=ValueError("unexpected"))

        with patch("asyncio.run_coroutine_threadsafe", return_value=mock_future):
            strategy._stop_orphan_gc_sync()

    asyncio.run(_run())

    strategy._logger.debug.assert_called()


# ---------------------------------------------------------------------------
# _maybe_start_node_watcher() — branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_maybe_start_node_watcher_start_exception_logged() -> None:
    """_maybe_start_node_watcher() catches start() exceptions and logs a warning."""
    strategy = _make_strategy(config=_make_config(node_watch_enabled=True))
    mock_watcher = MagicMock()
    mock_watcher.start = MagicMock(side_effect=RuntimeError("nw-start-fail"))
    strategy._node_watcher = mock_watcher

    strategy._maybe_start_node_watcher()

    strategy._logger.warning.assert_called()


@pytest.mark.unit
def test_maybe_start_node_watcher_lazy_constructs_when_none() -> None:
    """_maybe_start_node_watcher() builds K8sNodeWatcher lazily when _node_watcher is None."""
    strategy = _make_strategy(config=_make_config(node_watch_enabled=True))
    assert strategy._node_watcher is None

    with patch("orb.providers.k8s.strategy.k8s_provider_strategy.K8sNodeWatcher") as MockWatcher:
        instance = MagicMock()
        instance.start = MagicMock()
        MockWatcher.return_value = instance

        strategy._maybe_start_node_watcher()

    MockWatcher.assert_called_once()
    assert strategy._node_watcher is instance


# ---------------------------------------------------------------------------
# _maybe_start_events_watcher() — branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_maybe_start_events_watcher_start_exception_logged() -> None:
    """_maybe_start_events_watcher() catches start() exceptions and logs a warning."""
    strategy = _make_strategy(config=_make_config(events_watch_enabled=True))
    mock_watcher = MagicMock()
    mock_watcher.start = MagicMock(side_effect=RuntimeError("ew-start-fail"))
    strategy._events_watcher = mock_watcher

    strategy._maybe_start_events_watcher()

    strategy._logger.warning.assert_called()


@pytest.mark.unit
def test_maybe_start_events_watcher_lazy_constructs_when_none() -> None:
    """_maybe_start_events_watcher() builds K8sEventsWatcher lazily when _events_watcher is None."""
    strategy = _make_strategy(config=_make_config(events_watch_enabled=True))
    assert strategy._events_watcher is None

    with patch("orb.providers.k8s.strategy.k8s_provider_strategy.K8sEventsWatcher") as MockWatcher:
        instance = MagicMock()
        instance.start = MagicMock()
        MockWatcher.return_value = instance

        strategy._maybe_start_events_watcher()

    MockWatcher.assert_called_once()
    assert strategy._events_watcher is instance


# ---------------------------------------------------------------------------
# start_daemon_services() — idempotency guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_start_daemon_services_idempotent() -> None:
    """start_daemon_services() is a no-op on the second call."""
    strategy = _make_strategy()
    strategy._daemon_services_started = True

    asyncio.run(strategy.start_daemon_services())

    strategy._logger.debug.assert_called()


# ---------------------------------------------------------------------------
# register_handler() — idempotent re-registration and conflict
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_register_handler_idempotent_same_class() -> None:
    """Re-registering the same class for the same api is allowed (idempotent)."""
    strategy = _make_strategy()
    dummy_class = MagicMock()

    strategy.register_handler("MyCustomApi", dummy_class)
    strategy.register_handler("MyCustomApi", dummy_class)  # must not raise

    assert strategy._handler_factories["MyCustomApi"] is dummy_class


@pytest.mark.unit
def test_register_handler_conflict_raises_value_error() -> None:
    """Re-registering a DIFFERENT class for the same api raises ValueError."""
    strategy = _make_strategy()
    class_a = MagicMock()
    class_b = MagicMock()

    strategy.register_handler("MyCustomApi", class_a)

    with pytest.raises(ValueError, match="already registered"):
        strategy.register_handler("MyCustomApi", class_b)


@pytest.mark.unit
def test_unregister_handler_removes_entry() -> None:
    """unregister_handler() removes the factory from the registry."""
    strategy = _make_strategy()
    dummy_class = MagicMock()

    strategy.register_handler("TestApi", dummy_class)
    strategy.unregister_handler("TestApi")

    assert "TestApi" not in strategy._handler_factories


@pytest.mark.unit
def test_unregister_handler_noop_for_unknown_key() -> None:
    """unregister_handler() does not raise for an unknown key."""
    strategy = _make_strategy()
    strategy.unregister_handler("NonExistent")  # must not raise


# ---------------------------------------------------------------------------
# _resolve_native_spec_service() — various paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resolve_native_spec_service_cached_after_first_call() -> None:
    """_resolve_native_spec_service() returns the cached value on second call."""
    strategy = _make_strategy()
    strategy._native_spec_service_resolved = True
    strategy._k8s_native_spec_service = MagicMock()

    result = strategy._resolve_native_spec_service()

    assert result is strategy._k8s_native_spec_service


@pytest.mark.unit
def test_resolve_native_spec_service_returns_none_when_disabled() -> None:
    """_resolve_native_spec_service() returns None when native_spec_enabled=False."""
    strategy = _make_strategy(config=_make_config(native_spec_enabled=False))

    result = strategy._resolve_native_spec_service()

    assert result is None


@pytest.mark.unit
def test_resolve_native_spec_service_returns_none_when_no_config_port() -> None:
    """_resolve_native_spec_service() returns None when no ConfigurationPort is set."""
    strategy = _make_strategy(config=_make_config(native_spec_enabled=True))
    strategy._config_port = None

    result = strategy._resolve_native_spec_service()

    assert result is None
    strategy._logger.debug.assert_called()


@pytest.mark.unit
def test_resolve_native_spec_service_returns_none_when_no_injected_service() -> None:
    """_resolve_native_spec_service() returns None when no NativeSpecService was injected."""
    strategy = _make_strategy(config=_make_config(native_spec_enabled=True))
    strategy._config_port = MagicMock()
    strategy._injected_native_spec_service = None

    result = strategy._resolve_native_spec_service()

    assert result is None
    strategy._logger.debug.assert_called()


@pytest.mark.unit
def test_resolve_native_spec_service_construction_exception_returns_none() -> None:
    """_resolve_native_spec_service() catches K8sNativeSpecService construction errors."""
    strategy = _make_strategy(config=_make_config(native_spec_enabled=True))
    strategy._config_port = MagicMock()
    strategy._injected_native_spec_service = MagicMock()

    with patch(
        "orb.providers.k8s.strategy.k8s_provider_strategy.K8sProviderStrategy._resolve_native_spec_service",
        wraps=strategy._resolve_native_spec_service,
    ):
        # Patch the import inside the method to raise
        with patch.dict(
            "sys.modules",
            {"orb.providers.k8s.infrastructure.services.k8s_native_spec_service": None},
        ):
            result = strategy._resolve_native_spec_service()

    # Either None (import failure) or the native spec service — we just need no crash
    # (None is the expected value when the import fails)
    assert result is None


# ---------------------------------------------------------------------------
# _get_start_stop_service() — lazy construction
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_start_stop_service_lazy_construction() -> None:
    """_get_start_stop_service() constructs the service on first access."""
    strategy = _make_strategy(config=_make_config(in_cluster=True))
    assert strategy._start_stop_service is None

    svc = strategy._get_start_stop_service()

    from orb.providers.k8s.services.start_stop_service import K8sStartStopService

    assert isinstance(svc, K8sStartStopService)
    assert strategy._start_stop_service is svc


@pytest.mark.unit
def test_get_start_stop_service_cached_on_second_access() -> None:
    """_get_start_stop_service() returns the same object on repeated calls."""
    strategy = _make_strategy(config=_make_config(in_cluster=True))

    svc1 = strategy._get_start_stop_service()
    svc2 = strategy._get_start_stop_service()

    assert svc1 is svc2


# ---------------------------------------------------------------------------
# Classmethod delegation to K8sCapabilityService
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_cli_infrastructure_defaults_delegates() -> None:
    """get_cli_infrastructure_defaults classmethod delegates to K8sCapabilityService."""
    with patch(
        "orb.providers.k8s.strategy.k8s_provider_strategy.K8sCapabilityService"
        ".get_cli_infrastructure_defaults",
        return_value={"k": "v"},
    ) as mock_method:
        result = K8sProviderStrategy.get_cli_infrastructure_defaults(MagicMock())

    mock_method.assert_called_once()
    assert result == {"k": "v"}


@pytest.mark.unit
def test_get_cli_provider_config_delegates() -> None:
    """get_cli_provider_config classmethod delegates to K8sCapabilityService."""
    with patch(
        "orb.providers.k8s.strategy.k8s_provider_strategy.K8sCapabilityService"
        ".get_cli_provider_config",
        return_value={"ns": "default"},
    ) as mock_method:
        result = K8sProviderStrategy.get_cli_provider_config(MagicMock())

    mock_method.assert_called_once()
    assert result == {"ns": "default"}


@pytest.mark.unit
def test_get_operational_param_choices_delegates() -> None:
    """get_operational_param_choices classmethod delegates to K8sCapabilityService."""
    with patch(
        "orb.providers.k8s.strategy.k8s_provider_strategy.K8sCapabilityService"
        ".get_operational_param_choices",
        return_value=[("a", "A")],
    ) as mock_method:
        result = K8sProviderStrategy.get_operational_param_choices("provider_api")

    mock_method.assert_called_once_with("provider_api")
    assert result == [("a", "A")]


@pytest.mark.unit
def test_get_operational_param_default_delegates() -> None:
    """get_operational_param_default classmethod delegates to K8sCapabilityService."""
    with patch(
        "orb.providers.k8s.strategy.k8s_provider_strategy.K8sCapabilityService"
        ".get_operational_param_default",
        return_value="Pod",
    ) as mock_method:
        result = K8sProviderStrategy.get_operational_param_default("provider_api")

    mock_method.assert_called_once_with("provider_api")
    assert result == "Pod"


@pytest.mark.unit
def test_test_credentials_delegates() -> None:
    """test_credentials classmethod delegates to K8sCapabilityService."""
    with patch(
        "orb.providers.k8s.strategy.k8s_provider_strategy.K8sCapabilityService.test_credentials",
        return_value={"ok": True},
    ) as mock_method:
        result = K8sProviderStrategy.test_credentials("kubeconfig")

    mock_method.assert_called_once()
    assert result == {"ok": True}


@pytest.mark.unit
def test_get_credential_requirements_delegates() -> None:
    """get_credential_requirements classmethod delegates to K8sCapabilityService."""
    with patch(
        "orb.providers.k8s.strategy.k8s_provider_strategy.K8sCapabilityService"
        ".get_credential_requirements",
        return_value={"creds": []},
    ) as mock_method:
        result = K8sProviderStrategy.get_credential_requirements()

    mock_method.assert_called_once()
    assert result == {"creds": []}


@pytest.mark.unit
def test_get_operational_requirements_delegates() -> None:
    """get_operational_requirements classmethod delegates to K8sCapabilityService."""
    with patch(
        "orb.providers.k8s.strategy.k8s_provider_strategy.K8sCapabilityService"
        ".get_operational_requirements",
        return_value={},
    ) as mock_method:
        result = K8sProviderStrategy.get_operational_requirements()

    mock_method.assert_called_once()
    assert result == {}


# ---------------------------------------------------------------------------
# register_health_checks() — client exception path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_register_health_checks_client_exception_logs_debug() -> None:
    """register_health_checks() logs debug and returns when kubernetes_client raises."""
    strategy = _make_strategy()
    # Patch the kubernetes_client property to raise
    with patch.object(
        type(strategy),
        "kubernetes_client",
        new_callable=lambda: property(
            lambda self: (_ for _ in ()).throw(RuntimeError("no-client"))
        ),
    ):
        strategy.register_health_checks(MagicMock())

    strategy._logger.debug.assert_called()
