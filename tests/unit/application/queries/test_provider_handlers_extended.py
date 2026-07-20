"""Unit tests for application/queries/provider_handlers.py — extended coverage."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from orb.application.provider.queries import (
    GetProviderCapabilitiesQuery,
    GetProviderHealthQuery,
    GetProviderMetricsQuery,
    GetProviderStrategyConfigQuery,
    ListAvailableProvidersQuery,
)
from orb.application.queries.provider_handlers import (
    GetProviderCapabilitiesHandler,
    GetProviderHealthHandler,
    GetProviderMetricsHandler,
    GetProviderStrategyConfigHandler,
    ListAvailableProvidersHandler,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger():
    return MagicMock()


def _make_error_handler():
    return MagicMock()


def _make_timestamp_service():
    svc = MagicMock()
    svc.current_timestamp.return_value = "2026-01-01T00:00:00Z"
    svc.format_for_display.return_value = "2026-01-01 00:00:00"
    return svc


def _make_uow_factory(requests=None):
    uow = MagicMock()
    uow.requests.find_all.return_value = requests or []
    uow.requests.find_by_date_range.return_value = requests or []

    @contextmanager
    def _create():
        yield uow

    factory = MagicMock()
    factory.create_unit_of_work.side_effect = _create
    return factory


def _make_provider_registry_service(health=None, capabilities=None, select_result=None):
    svc = MagicMock()
    if health is not None:
        svc.check_strategy_health.return_value = health
    else:
        h = MagicMock()
        h.is_healthy = True
        h.message = "all good"
        h.details = {}
        h.timestamp = 0.0
        svc.check_strategy_health.return_value = h

    if capabilities is not None:
        svc.get_strategy_capabilities.return_value = capabilities
    else:
        cap = MagicMock()
        cap.supported_apis = ["api1", "api2"]
        cap.supported_operations = ["acquire", "return"]
        cap.features = {"spot": True}
        svc.get_strategy_capabilities.return_value = cap

    if select_result is not None:
        svc.select_active_provider.return_value = select_result
    else:
        r = MagicMock()
        r.provider_name = "aws"
        svc.select_active_provider.return_value = r

    return svc


# ---------------------------------------------------------------------------
# GetProviderHealthHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetProviderHealthHandler:
    def _handler(self, registry_svc=None):
        return GetProviderHealthHandler(
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            timestamp_service=_make_timestamp_service(),
            provider_registry_service=registry_svc or _make_provider_registry_service(),
        )

    @pytest.mark.asyncio
    async def test_named_provider_healthy(self):
        h = self._handler()
        q = GetProviderHealthQuery(provider_name="aws", provider_type=None)
        result = await h.execute_query(q)
        assert result["provider_name"] == "aws"
        assert result["health"] == "healthy"

    @pytest.mark.asyncio
    async def test_unhealthy_provider_reported(self):
        health = MagicMock()
        health.is_healthy = False
        health.message = "degraded"
        health.details = {}
        health.timestamp = 0.0
        svc = _make_provider_registry_service(health=health)
        h = self._handler(registry_svc=svc)
        q = GetProviderHealthQuery(provider_name="aws", provider_type=None)
        result = await h.execute_query(q)
        assert result["health"] == "unhealthy"

    @pytest.mark.asyncio
    async def test_no_provider_name_uses_active_provider(self):
        h = self._handler()
        q = GetProviderHealthQuery(provider_name=None, provider_type="aws")
        result = await h.execute_query(q)
        # Falls back to active provider "aws"
        assert result["provider_name"] == "aws"

    @pytest.mark.asyncio
    async def test_no_active_provider_returns_not_found(self):
        svc = _make_provider_registry_service()
        svc.select_active_provider.side_effect = RuntimeError("no provider")
        h = self._handler(registry_svc=svc)
        q = GetProviderHealthQuery(provider_name=None, provider_type=None)
        result = await h.execute_query(q)
        assert result["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_exception_returns_error_response(self):
        svc = _make_provider_registry_service()
        svc.check_strategy_health.side_effect = RuntimeError("registry broken")
        h = self._handler(registry_svc=svc)
        q = GetProviderHealthQuery(provider_name="aws", provider_type=None)
        result = await h.execute_query(q)
        assert result["status"] == "error"
        assert result["health"] == "unhealthy"

    @pytest.mark.asyncio
    async def test_health_status_none_still_returns_response(self):
        svc = _make_provider_registry_service()
        svc.check_strategy_health.return_value = None
        h = self._handler(registry_svc=svc)
        q = GetProviderHealthQuery(provider_name="aws", provider_type=None)
        result = await h.execute_query(q)
        # None health → health field is "unhealthy" and the fallback message applies.
        assert result["provider_name"] == "aws"
        assert result["health"] == "unhealthy"
        assert result["message"] == "No health data available"


# ---------------------------------------------------------------------------
# ListAvailableProvidersHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListAvailableProvidersHandler:
    def _make_provider(self, name="aws", ptype="aws", enabled=True, weight=100, priority=1):
        p = MagicMock()
        p.name = name
        p.type = ptype
        p.enabled = enabled
        p.weight = weight
        p.priority = priority
        p.get_effective_handlers.return_value = {"acquire": "mock", "return": "mock"}
        return p

    def _handler(self, providers=None, config_raises=False):
        config_mgr = MagicMock()
        if config_raises:
            config_mgr.get_provider_config.side_effect = RuntimeError("config error")
        elif providers is None:
            config_mgr.get_provider_config.return_value = None
        else:
            pc = MagicMock()
            pc.get_active_providers.return_value = providers
            pc.selection_policy = "round_robin"
            pc.provider_defaults = {}
            config_mgr.get_provider_config.return_value = pc

        filter_svc = MagicMock()
        filter_svc.apply_filters.side_effect = lambda items, _: items
        return ListAvailableProvidersHandler(
            config_manager=config_mgr,
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            generic_filter_service=filter_svc,
        )

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_config(self):
        h = self._handler(providers=None)
        q = ListAvailableProvidersQuery()
        result = await h.execute_query(q)
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_returns_all_providers(self):
        providers = [self._make_provider("aws"), self._make_provider("k8s", ptype="k8s")]
        h = self._handler(providers=providers)
        q = ListAvailableProvidersQuery()
        result = await h.execute_query(q)
        assert result["count"] == 2

    @pytest.mark.asyncio
    async def test_filter_by_provider_name(self):
        providers = [self._make_provider("aws"), self._make_provider("k8s", ptype="k8s")]
        h = self._handler(providers=providers)
        q = ListAvailableProvidersQuery(provider_name="aws")
        result = await h.execute_query(q)
        assert result["count"] == 1
        assert result["providers"][0]["name"] == "aws"

    @pytest.mark.asyncio
    async def test_filter_by_provider_name_not_found(self):
        providers = [self._make_provider("aws")]
        h = self._handler(providers=providers)
        q = ListAvailableProvidersQuery(provider_name="nonexistent")
        result = await h.execute_query(q)
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_filter_by_provider_type(self):
        providers = [
            self._make_provider("aws", ptype="aws"),
            self._make_provider("k8s", ptype="k8s"),
        ]
        h = self._handler(providers=providers)
        q = ListAvailableProvidersQuery(provider_type="k8s")
        result = await h.execute_query(q)
        assert result["count"] == 1
        assert result["providers"][0]["type"] == "k8s"

    @pytest.mark.asyncio
    async def test_exception_returns_empty_list(self):
        h = self._handler(config_raises=True)
        q = ListAvailableProvidersQuery()
        result = await h.execute_query(q)
        assert result["count"] == 0
        assert "Failed" in result["message"]

    @pytest.mark.asyncio
    async def test_provider_status_disabled(self):
        providers = [self._make_provider("aws", enabled=False)]
        h = self._handler(providers=providers)
        q = ListAvailableProvidersQuery()
        result = await h.execute_query(q)
        assert result["providers"][0]["status"] == "disabled"


# ---------------------------------------------------------------------------
# GetProviderCapabilitiesHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetProviderCapabilitiesHandler:
    def _handler(self, registry_svc=None):
        return GetProviderCapabilitiesHandler(
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            provider_registry_service=registry_svc or _make_provider_registry_service(),
        )

    @pytest.mark.asyncio
    async def test_returns_capabilities(self):
        h = self._handler()
        q = GetProviderCapabilitiesQuery(provider_name="aws")
        result = await h.execute_query(q)
        assert result["provider_name"] == "aws"
        assert "api1" in result["capabilities"]

    @pytest.mark.asyncio
    async def test_none_capabilities_returns_empty_lists(self):
        svc = _make_provider_registry_service(capabilities=None)
        svc.get_strategy_capabilities.return_value = None
        h = self._handler(registry_svc=svc)
        q = GetProviderCapabilitiesQuery(provider_name="aws")
        result = await h.execute_query(q)
        assert result["capabilities"] == []
        assert result["supported_operations"] == []

    @pytest.mark.asyncio
    async def test_exception_re_raises(self):
        svc = _make_provider_registry_service()
        svc.get_strategy_capabilities.side_effect = RuntimeError("registry gone")
        h = self._handler(registry_svc=svc)
        q = GetProviderCapabilitiesQuery(provider_name="aws")
        with pytest.raises(RuntimeError, match="registry gone"):
            await h.execute_query(q)


# ---------------------------------------------------------------------------
# GetProviderMetricsHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetProviderMetricsHandler:
    def _handler(self, requests=None, config_enrichment_error=False):
        config_port = MagicMock()
        if config_enrichment_error:
            config_port.get_provider_config.side_effect = RuntimeError("cfg err")
        else:
            config_port.get_provider_config.return_value = None

        return GetProviderMetricsHandler(
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            uow_factory=_make_uow_factory(requests=requests),
            config_port=config_port,
        )

    @pytest.mark.asyncio
    async def test_returns_metrics_dto(self):
        from orb.application.dto.system import ProviderMetricsDTO

        h = self._handler()
        q = GetProviderMetricsQuery(provider_name="aws", timeframe="1h")
        result = await h.execute_query(q)
        assert isinstance(result, ProviderMetricsDTO)
        assert result.provider_name == "aws"

    @pytest.mark.asyncio
    async def test_no_provider_name_defaults_to_all(self):
        h = self._handler()
        q = GetProviderMetricsQuery(provider_name=None, timeframe="1h")
        result = await h.execute_query(q)
        assert result.provider_name == "all"

    @pytest.mark.asyncio
    async def test_24h_timeframe(self):
        h = self._handler()
        q = GetProviderMetricsQuery(provider_name=None, timeframe="24h")
        result = await h.execute_query(q)
        assert result is not None

    @pytest.mark.asyncio
    async def test_7d_timeframe(self):
        h = self._handler()
        q = GetProviderMetricsQuery(provider_name=None, timeframe="7d")
        result = await h.execute_query(q)
        assert result is not None

    @pytest.mark.asyncio
    async def test_unknown_timeframe_falls_back_to_1h(self):
        h = self._handler()
        q = GetProviderMetricsQuery(provider_name=None, timeframe="bogus")
        result = await h.execute_query(q)
        assert result is not None

    @pytest.mark.asyncio
    async def test_config_enrichment_error_logged_not_raised(self):
        logger = _make_logger()
        config_port = MagicMock()
        config_port.get_provider_config.side_effect = RuntimeError("cfg error")
        h = GetProviderMetricsHandler(
            logger=logger,
            error_handler=_make_error_handler(),
            uow_factory=_make_uow_factory(),
            config_port=config_port,
        )
        q = GetProviderMetricsQuery(provider_name=None, timeframe="1h")
        result = await h.execute_query(q)
        # Should succeed (enrichment is optional) and log warning
        assert result is not None
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_uow_exception_propagates(self):
        factory = MagicMock()
        factory.create_unit_of_work.side_effect = RuntimeError("db gone")
        h = GetProviderMetricsHandler(
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            uow_factory=factory,
            config_port=MagicMock(),
        )
        q = GetProviderMetricsQuery(provider_name=None, timeframe="1h")
        with pytest.raises(RuntimeError, match="db gone"):
            await h.execute_query(q)


# ---------------------------------------------------------------------------
# GetProviderStrategyConfigHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetProviderStrategyConfigHandler:
    def _handler(self, registry_svc=None):
        return GetProviderStrategyConfigHandler(
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            provider_registry_service=registry_svc or _make_provider_registry_service(),
        )

    @pytest.mark.asyncio
    async def test_returns_stub_config_without_consulting_registry(self):
        # This handler is currently a stub: it returns a fixed config and does
        # NOT consult the injected registry service. Pin that stub contract so a
        # future real implementation (which must read the registry) will fail here.
        registry_svc = _make_provider_registry_service()
        h = self._handler(registry_svc=registry_svc)

        result = await h.execute_query(GetProviderStrategyConfigQuery())

        assert result == {"strategy_type": "registry_managed", "is_registered": True}
        # No registry method is invoked by the stub.
        registry_svc.check_strategy_health.assert_not_called()
        registry_svc.get_strategy_capabilities.assert_not_called()
        registry_svc.select_active_provider.assert_not_called()
