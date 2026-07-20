"""Unit tests for application/queries/storage_handlers.py."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from orb.application.dto.system import (
    StorageHealthResponse,
    StorageMetricsResponse,
    StorageStrategyListResponse,
)
from orb.application.queries.storage import (
    GetStorageHealthQuery,
    GetStorageMetricsQuery,
    ListStorageStrategiesQuery,
)
from orb.application.queries.storage_handlers import (
    GetStorageHealthHandler,
    GetStorageMetricsHandler,
    ListStorageStrategiesHandler,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_uow_factory():
    uow = MagicMock()
    uow.requests.find_all.return_value = []

    @contextmanager
    def _create():
        yield uow

    factory = MagicMock()
    factory.create_unit_of_work.side_effect = _create
    return factory


def _make_container(current_strategy: str = "memory"):
    container = MagicMock()
    cfg_mgr = MagicMock()
    cfg_mgr.get.return_value = current_strategy
    container.get.return_value = cfg_mgr
    return container


def _make_storage_service(strategies=None, health_status=None):
    svc = MagicMock()
    svc.get_available_storage_types.return_value = strategies or ["memory", "dynamodb"]
    if health_status is not None:
        svc.get_storage_health.return_value = health_status
    else:
        svc.get_storage_health.return_value = {"status": "healthy"}
    return svc


def _make_logger():
    return MagicMock()


def _make_error_handler():
    return MagicMock()


# ---------------------------------------------------------------------------
# ListStorageStrategiesHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListStorageStrategiesHandler:
    def _handler(self, strategies=None, current_strategy="memory"):
        filter_svc = MagicMock()
        filter_svc.apply_filters.side_effect = lambda items, _: items
        return ListStorageStrategiesHandler(
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            storage_service=_make_storage_service(strategies),
            generic_filter_service=filter_svc,
            container=_make_container(current_strategy),
        )

    @pytest.mark.asyncio
    async def test_returns_all_strategies(self):
        h = self._handler(strategies=["memory", "dynamodb", "sql"])
        query = ListStorageStrategiesQuery(include_current=False, include_details=False)
        result = await h.execute_query(query)
        assert isinstance(result, StorageStrategyListResponse)
        assert result.total_count == 3

    @pytest.mark.asyncio
    async def test_marks_current_when_include_current(self):
        h = self._handler(strategies=["memory", "dynamodb"], current_strategy="memory")
        query = ListStorageStrategiesQuery(include_current=True, include_details=False)
        result = await h.execute_query(query)
        active = [s for s in result.strategies if s.active]
        assert len(active) == 1
        assert active[0].name == "memory"

    @pytest.mark.asyncio
    async def test_no_active_when_include_current_false(self):
        h = self._handler(strategies=["memory"], current_strategy="memory")
        query = ListStorageStrategiesQuery(include_current=False, include_details=False)
        result = await h.execute_query(query)
        assert all(not s.active for s in result.strategies)

    @pytest.mark.asyncio
    async def test_include_details_adds_description(self):
        h = self._handler(strategies=["memory"])
        query = ListStorageStrategiesQuery(include_current=False, include_details=True)
        result = await h.execute_query(query)
        assert result.strategies[0].description is not None

    @pytest.mark.asyncio
    async def test_filter_expressions_applied(self):
        filter_svc = MagicMock()
        filter_svc.apply_filters.return_value = []  # filter out everything

        from orb.application.queries.storage_handlers import ListStorageStrategiesHandler as H

        handler = H(
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            storage_service=_make_storage_service(["memory"]),
            generic_filter_service=filter_svc,
            container=_make_container(),
        )
        query = ListStorageStrategiesQuery(
            include_current=False, include_details=False, filter_expressions=["name=nope"]
        )
        result = await handler.execute_query(query)
        assert result.total_count == 0
        filter_svc.apply_filters.assert_called_once()


# ---------------------------------------------------------------------------
# GetStorageHealthHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetStorageHealthHandler:
    def _handler(self, uow_factory=None, storage_service=None):
        return GetStorageHealthHandler(
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            storage_service=storage_service or _make_storage_service(),
            uow_factory=uow_factory or _make_uow_factory(),
        )

    @pytest.mark.asyncio
    async def test_current_strategy_returns_operational(self):
        h = self._handler()
        query = GetStorageHealthQuery(strategy_name=None, verbose=False)
        result = await h.execute_query(query)
        assert isinstance(result, StorageHealthResponse)
        assert result.strategy_name == "current"
        assert result.healthy is True
        assert result.status == "operational"

    @pytest.mark.asyncio
    async def test_named_strategy_delegates_to_storage_service(self):
        svc = _make_storage_service(health_status={"status": "healthy"})
        h = self._handler(storage_service=svc)
        query = GetStorageHealthQuery(strategy_name="dynamodb", verbose=False)
        result = await h.execute_query(query)
        svc.get_storage_health.assert_called_once_with("dynamodb")
        assert result.healthy is True

    @pytest.mark.asyncio
    async def test_named_strategy_with_error_status(self):
        svc = _make_storage_service(health_status={"status": "error", "detail": "conn refused"})
        h = self._handler(storage_service=svc)
        query = GetStorageHealthQuery(strategy_name="dynamodb", verbose=False)
        result = await h.execute_query(query)
        assert result.healthy is False
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_verbose_includes_latency_details(self):
        uow_factory = _make_uow_factory()
        h = self._handler(uow_factory=uow_factory)
        query = GetStorageHealthQuery(strategy_name=None, verbose=True)
        result = await h.execute_query(query)
        assert "latency_ms" in result.details
        assert "connection_status" in result.details

    @pytest.mark.asyncio
    async def test_verbose_calls_find_all(self):
        # Track UoW calls via a mutable list
        called = []

        @contextmanager
        def _create():
            uow = MagicMock()
            uow.requests.find_all.side_effect = lambda: called.append(True) or []
            yield uow

        factory = MagicMock()
        factory.create_unit_of_work.side_effect = _create
        h = self._handler(uow_factory=factory)
        query = GetStorageHealthQuery(strategy_name=None, verbose=True)
        await h.execute_query(query)
        assert called, "find_all should have been called during verbose health check"


# ---------------------------------------------------------------------------
# GetStorageMetricsHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetStorageMetricsHandler:
    def _handler(self, storage_service=None):
        return GetStorageMetricsHandler(
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            storage_service=storage_service or _make_storage_service(),
        )

    @pytest.mark.asyncio
    async def test_returns_metrics_response(self):
        # This handler is currently a stub that returns zeroed metrics and does
        # NOT consult the injected storage service. Pin that stub contract so a
        # future real metric-collection implementation will fail here.
        storage_service = _make_storage_service()
        h = self._handler(storage_service=storage_service)
        query = GetStorageMetricsQuery(
            strategy_name=None, time_range=None, include_operations=False
        )
        result = await h.execute_query(query)
        assert isinstance(result, StorageMetricsResponse)
        assert result.strategy_name == "current"
        assert result.time_range == ""
        assert result.operations_count == 0
        assert result.average_latency == 0.0
        assert result.error_rate == 0.0
        assert result.details == {}
        # The stub ignores the storage service entirely.
        storage_service.get_available_storage_types.assert_not_called()
        storage_service.get_storage_health.assert_not_called()

    @pytest.mark.asyncio
    async def test_strategy_name_forwarded(self):
        h = self._handler()
        query = GetStorageMetricsQuery(
            strategy_name="dynamodb", time_range="1h", include_operations=False
        )
        result = await h.execute_query(query)
        assert result.strategy_name == "dynamodb"

    @pytest.mark.asyncio
    async def test_time_range_forwarded(self):
        h = self._handler()
        query = GetStorageMetricsQuery(
            strategy_name=None, time_range="24h", include_operations=False
        )
        result = await h.execute_query(query)
        assert result.time_range == "24h"

    @pytest.mark.asyncio
    async def test_include_operations_adds_details(self):
        # Stub contract: include_operations=True yields the fixed zeroed
        # {"read_ops": 0, "write_ops": 0} payload.
        h = self._handler()
        query = GetStorageMetricsQuery(strategy_name=None, time_range=None, include_operations=True)
        result = await h.execute_query(query)
        assert result.details == {"read_ops": 0, "write_ops": 0}

    @pytest.mark.asyncio
    async def test_no_include_operations_empty_details(self):
        h = self._handler()
        query = GetStorageMetricsQuery(
            strategy_name=None, time_range=None, include_operations=False
        )
        result = await h.execute_query(query)
        assert result.details == {}

    @pytest.mark.asyncio
    async def test_strategy_name_defaults_to_current(self):
        h = self._handler()
        query = GetStorageMetricsQuery(
            strategy_name=None, time_range=None, include_operations=False
        )
        result = await h.execute_query(query)
        assert result.strategy_name == "current"
