"""Unit tests for system_command_handlers and config_command_handlers.

Covers happy path and error/branch paths for provider health, metrics,
reload, config handlers, and system status.
"""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.services.orchestration.dtos import (
    GetProviderConfigOutput,
    GetProviderHealthOutput,
    GetProviderMetricsOutput,
    ListProvidersOutput,
)
from orb.interface.response_formatting_service import ResponseFormattingService
from orb.interface.system_command_handlers import (
    handle_execute_provider_operation,
    handle_list_providers,
    handle_provider_config,
    handle_provider_health,
    handle_provider_metrics,
    handle_reload_provider_config,
    handle_system_metrics,
    handle_validate_provider_config,
)


def _make_formatter() -> MagicMock:
    fmt = MagicMock(spec=ResponseFormattingService)
    fmt.format_success.return_value = InterfaceResponse(data={"ok": True})
    fmt.format_error.return_value = InterfaceResponse(data={"error": "err"}, exit_code=1)
    fmt.format_config.return_value = InterfaceResponse(data={"data": {}})
    fmt.format_system_status.return_value = InterfaceResponse(data={"status": "ok"})
    return fmt


@pytest.mark.unit
class TestHandleProviderHealth:
    """Tests for handle_provider_health."""

    @pytest.mark.asyncio
    async def test_happy_path(self):
        from orb.application.services.orchestration.get_provider_health import (
            GetProviderHealthOrchestrator,
        )

        orch = AsyncMock(spec=GetProviderHealthOrchestrator)
        orch.execute.return_value = GetProviderHealthOutput(
            health={"status": "healthy"}, message="ok"
        )

        container = MagicMock()
        container.get.side_effect = lambda t: {
            GetProviderHealthOrchestrator: orch,
        }.get(t, MagicMock())

        args = Namespace(_container=container, provider_name="aws-1", provider_type="aws")
        result = await handle_provider_health(args)

        orch.execute.assert_awaited_once()
        call_input = orch.execute.call_args[0][0]
        assert call_input.provider_name == "aws-1"
        assert result["message"] == "ok"
        assert "health" in result

    @pytest.mark.asyncio
    async def test_no_provider_filter_still_calls_orchestrator(self):
        from orb.application.services.orchestration.get_provider_health import (
            GetProviderHealthOrchestrator,
        )

        orch = AsyncMock(spec=GetProviderHealthOrchestrator)
        orch.execute.return_value = GetProviderHealthOutput(health={}, message="all healthy")

        container = MagicMock()
        container.get.side_effect = lambda t: {
            GetProviderHealthOrchestrator: orch,
        }.get(t, MagicMock())

        args = Namespace(_container=container)
        result = await handle_provider_health(args)

        orch.execute.assert_awaited_once()
        assert "health" in result


@pytest.mark.unit
class TestHandleListProviders:
    """Tests for handle_list_providers."""

    @pytest.mark.asyncio
    async def test_happy_path(self):
        from orb.application.services.orchestration.list_providers import ListProvidersOrchestrator

        orch = AsyncMock(spec=ListProvidersOrchestrator)
        orch.execute.return_value = ListProvidersOutput(
            providers=[{"name": "aws-1"}],
            count=1,
            selection_policy="first",
            message="ok",
        )

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ListProvidersOrchestrator: orch,
        }.get(t, MagicMock())

        args = Namespace(
            _container=container,
            provider_name=None,
            provider_type=None,
            filter=None,
        )
        result = await handle_list_providers(args)

        orch.execute.assert_awaited_once()
        assert result["count"] == 1
        assert "providers" in result

    @pytest.mark.asyncio
    async def test_filter_expressions_forwarded(self):
        from orb.application.services.orchestration.list_providers import ListProvidersOrchestrator

        orch = AsyncMock(spec=ListProvidersOrchestrator)
        orch.execute.return_value = ListProvidersOutput(
            providers=[], count=0, selection_policy="first", message=""
        )

        container = MagicMock()
        container.get.side_effect = lambda t: {ListProvidersOrchestrator: orch}.get(t, MagicMock())

        args = Namespace(
            _container=container,
            provider_name="k8s-1",
            provider_type="k8s",
            filter=["region=us-east-1"],
        )
        await handle_list_providers(args)

        call_input = orch.execute.call_args[0][0]
        assert call_input.provider_name == "k8s-1"
        assert "region=us-east-1" in call_input.filter_expressions


@pytest.mark.unit
class TestHandleProviderConfig:
    """Tests for handle_provider_config."""

    @pytest.mark.asyncio
    async def test_happy_path(self):
        from orb.application.services.orchestration.get_provider_config import (
            GetProviderConfigOrchestrator,
        )

        orch = AsyncMock(spec=GetProviderConfigOrchestrator)
        orch.execute.return_value = GetProviderConfigOutput(
            config={"providers": []}, message="config loaded"
        )

        container = MagicMock()
        container.get.side_effect = lambda t: {GetProviderConfigOrchestrator: orch}.get(
            t, MagicMock()
        )

        args = Namespace(_container=container)
        result = await handle_provider_config(args)

        orch.execute.assert_awaited_once()
        assert "config" in result
        assert result["message"] == "config loaded"


@pytest.mark.unit
class TestHandleValidateProviderConfig:
    """Tests for handle_validate_provider_config (stub)."""

    @pytest.mark.asyncio
    async def test_returns_not_implemented_response(self):
        container = MagicMock()
        args = Namespace(_container=container)
        result = await handle_validate_provider_config(args)

        assert isinstance(result, dict)
        assert result.get("error") == "Not implemented"
        assert "validate_provider_config" in result.get("endpoint", "")


@pytest.mark.unit
class TestHandleExecuteProviderOperation:
    """Tests for handle_execute_provider_operation (stub)."""

    @pytest.mark.asyncio
    async def test_returns_not_implemented_response(self):
        container = MagicMock()
        args = Namespace(_container=container)
        result = await handle_execute_provider_operation(args)

        assert isinstance(result, dict)
        assert result.get("error") == "Not implemented"


@pytest.mark.unit
class TestHandleProviderMetrics:
    """Tests for handle_provider_metrics."""

    @pytest.mark.asyncio
    async def test_happy_path(self):
        from orb.application.services.orchestration.get_provider_metrics import (
            GetProviderMetricsOrchestrator,
        )

        orch = AsyncMock(spec=GetProviderMetricsOrchestrator)
        orch.execute.return_value = GetProviderMetricsOutput(metrics={"requests": 5}, message="ok")

        container = MagicMock()
        container.get.side_effect = lambda t: {GetProviderMetricsOrchestrator: orch}.get(
            t, MagicMock()
        )

        args = Namespace(_container=container, provider_name="aws-1", timeframe="1h")
        result = await handle_provider_metrics(args)

        orch.execute.assert_awaited_once()
        call_input = orch.execute.call_args[0][0]
        assert call_input.provider_name == "aws-1"
        assert call_input.timeframe == "1h"
        assert "metrics" in result

    @pytest.mark.asyncio
    async def test_default_timeframe_is_24h(self):
        from orb.application.services.orchestration.get_provider_metrics import (
            GetProviderMetricsOrchestrator,
        )

        orch = AsyncMock(spec=GetProviderMetricsOrchestrator)
        orch.execute.return_value = GetProviderMetricsOutput(metrics={}, message="")

        container = MagicMock()
        container.get.side_effect = lambda t: {GetProviderMetricsOrchestrator: orch}.get(
            t, MagicMock()
        )

        args = Namespace(_container=container, provider_name=None)
        await handle_provider_metrics(args)

        call_input = orch.execute.call_args[0][0]
        assert call_input.timeframe == "24h"


@pytest.mark.unit
class TestHandleReloadProviderConfig:
    """Tests for handle_reload_provider_config."""

    @pytest.mark.asyncio
    async def test_registry_with_reload_calls_reload(self):
        from orb.application.services.provider_registry_service import ProviderRegistryService

        fmt = _make_formatter()
        registry = AsyncMock(spec=ProviderRegistryService)
        registry.reload = AsyncMock()

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ProviderRegistryService: registry,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(_container=container)
        result = await handle_reload_provider_config(args)

        registry.reload.assert_awaited_once()
        fmt.format_success.assert_called_once()
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_registry_without_reload_returns_error(self):
        from orb.application.services.provider_registry_service import ProviderRegistryService

        fmt = _make_formatter()
        registry = MagicMock(spec=ProviderRegistryService)
        # Ensure reload attribute is absent
        del registry.reload

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ProviderRegistryService: registry,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(_container=container)
        result = await handle_reload_provider_config(args)

        fmt.format_error.assert_called_once()
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_reload_exception_returns_error(self):
        from orb.application.services.provider_registry_service import ProviderRegistryService

        fmt = _make_formatter()
        # Use a plain AsyncMock (no spec) so we can add the reload attribute
        registry = MagicMock()
        registry.reload = AsyncMock(side_effect=RuntimeError("registry broken"))

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ProviderRegistryService: registry,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(_container=container)
        result = await handle_reload_provider_config(args)

        fmt.format_error.assert_called()
        err_msg = fmt.format_error.call_args[0][0]
        assert "Reload failed" in err_msg
        assert isinstance(result, InterfaceResponse)


@pytest.mark.unit
class TestHandleSystemMetrics:
    """Tests for handle_system_metrics."""

    @pytest.mark.asyncio
    async def test_returns_empty_metrics_when_prometheus_not_installed(self):
        args = Namespace(_container=MagicMock())

        with patch.dict("sys.modules", {"prometheus_client": None}):
            result = await handle_system_metrics(args)

        assert isinstance(result, InterfaceResponse)
        assert result.data.get("metrics") == {} or "metrics" in result.data

    @pytest.mark.asyncio
    async def test_returns_metrics_dict_when_prometheus_available(self):
        args = Namespace(_container=MagicMock())

        mock_prometheus = MagicMock()
        mock_prometheus.generate_latest.return_value = (
            b"# HELP http_requests_total\n"
            b"# TYPE http_requests_total counter\n"
            b"http_requests_total 42\n"
        )
        mock_prometheus.REGISTRY = MagicMock()

        with patch.dict("sys.modules", {"prometheus_client": mock_prometheus}):
            result = await handle_system_metrics(args)

        assert isinstance(result, InterfaceResponse)
        assert "metrics" in result.data
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_prometheus_exception_returns_error_response(self):
        args = Namespace(_container=MagicMock())

        mock_prometheus = MagicMock()
        mock_prometheus.generate_latest.side_effect = RuntimeError("prometheus exploded")
        mock_prometheus.REGISTRY = MagicMock()

        with patch.dict("sys.modules", {"prometheus_client": mock_prometheus}):
            result = await handle_system_metrics(args)

        assert isinstance(result, InterfaceResponse)
        assert result.exit_code == 1
        assert "error" in result.data


@pytest.mark.unit
class TestHandleConfigCommandHandlers:
    """Tests for config_command_handlers functions."""

    @pytest.mark.asyncio
    async def test_get_configuration_no_key_returns_error(self):
        from orb.interface.config_command_handlers import handle_get_configuration

        fmt = _make_formatter()
        container = MagicMock()
        container.get.return_value = fmt

        args = Namespace(_container=container, key=None, flag_key=None)
        result = await handle_get_configuration(args)

        fmt.format_error.assert_called_once()
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_set_configuration_no_key_returns_error(self):
        from orb.interface.config_command_handlers import handle_set_configuration

        fmt = _make_formatter()
        container = MagicMock()
        container.get.return_value = fmt

        args = Namespace(_container=container, key=None, value="v")
        result = await handle_set_configuration(args)

        fmt.format_error.assert_called_once()
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_set_configuration_no_value_returns_error(self):
        from orb.interface.config_command_handlers import handle_set_configuration

        fmt = _make_formatter()
        container = MagicMock()
        container.get.return_value = fmt

        args = Namespace(_container=container, key="k", value=None)
        result = await handle_set_configuration(args)

        fmt.format_error.assert_called_once()
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_get_configuration_happy_path(self):
        from orb.infrastructure.di.buses import QueryBus
        from orb.interface.config_command_handlers import handle_get_configuration

        fmt = _make_formatter()
        fmt.format_config.return_value = InterfaceResponse(data={"key": "foo", "value": "bar"})
        mock_bus = AsyncMock(spec=QueryBus)
        mock_bus.execute.return_value = {"key": "foo", "value": "bar"}

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ResponseFormattingService: fmt,
            QueryBus: mock_bus,
        }.get(t, MagicMock())

        args = Namespace(_container=container, key="foo", flag_key=None)
        result = await handle_get_configuration(args)

        mock_bus.execute.assert_awaited_once()
        fmt.format_config.assert_called_once()
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_set_configuration_happy_path(self):
        from orb.infrastructure.di.buses import CommandBus
        from orb.interface.config_command_handlers import handle_set_configuration

        fmt = _make_formatter()
        fmt.format_config.return_value = InterfaceResponse(data={"success": True})
        mock_bus = AsyncMock(spec=CommandBus)
        mock_bus.execute.return_value = {"success": True}

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ResponseFormattingService: fmt,
            CommandBus: mock_bus,
        }.get(t, MagicMock())

        args = Namespace(_container=container, key="foo", value="bar")
        result = await handle_set_configuration(args)

        mock_bus.execute.assert_awaited_once()
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_get_system_config_happy_path(self):
        from orb.infrastructure.di.buses import QueryBus
        from orb.interface.config_command_handlers import handle_get_system_config

        fmt = _make_formatter()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {"scheduler_type": "default"}
        mock_bus = AsyncMock(spec=QueryBus)
        mock_bus.execute.return_value = mock_result

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ResponseFormattingService: fmt,
            QueryBus: mock_bus,
        }.get(t, MagicMock())

        args = Namespace(_container=container, verbose=False)
        result = await handle_get_system_config(args)

        mock_bus.execute.assert_awaited_once()
        fmt.format_config.assert_called_once()
        assert isinstance(result, InterfaceResponse)
