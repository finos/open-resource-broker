"""Unit tests for system_command_handlers.

Handlers fall into two groups:
- CQRS-compliant: dispatch through QueryBus — tested by asserting the correct query type
  is passed to a mocked bus.
- Non-bus: interact with the container or other infrastructure directly — tested by
  mocking those dependencies.
"""

import argparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _ns(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _mock_container_with_query_bus(query_return=None):
    """Return (container, query_bus) with query_bus.execute returning query_return."""
    from orb.infrastructure.di.buses import QueryBus

    container = MagicMock()
    query_bus = AsyncMock()
    query_bus.execute = AsyncMock(return_value=query_return)
    container.get.side_effect = lambda t: query_bus if t is QueryBus else MagicMock()
    return container, query_bus


# ---------------------------------------------------------------------------
# Stub handlers — not implemented, return error='Not implemented'
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStubHandlers:
    @pytest.mark.asyncio
    async def test_handle_validate_provider_config_returns_not_implemented(self):
        from orb.interface.system_command_handlers import handle_validate_provider_config

        result = await handle_validate_provider_config(_ns())

        assert isinstance(result, dict)
        assert result.get("error") == "Not implemented"

    @pytest.mark.asyncio
    async def test_handle_reload_provider_config_returns_interface_response(self):
        from orb.application.dto.interface_response import InterfaceResponse
        from orb.application.services.provider_registry_service import ProviderRegistryService
        from orb.interface.response_formatting_service import ResponseFormattingService
        from orb.interface.system_command_handlers import handle_reload_provider_config

        mock_formatter = MagicMock(spec=ResponseFormattingService)
        mock_formatter.format_error.return_value = InterfaceResponse(
            data={"success": False, "error": "Reload not supported by current provider registry"},
            exit_code=1,
        )
        mock_registry = MagicMock(spec=ProviderRegistryService)
        mock_container = MagicMock()
        mock_container.get.side_effect = lambda t: (
            mock_formatter if t is ResponseFormattingService else mock_registry
        )

        with patch(
            "orb.interface.system_command_handlers.get_container", return_value=mock_container
        ):
            result = await handle_reload_provider_config(_ns())

        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_handle_execute_provider_operation_returns_not_implemented(self):
        from orb.interface.system_command_handlers import handle_execute_provider_operation

        result = await handle_execute_provider_operation(_ns())

        assert isinstance(result, dict)
        assert result.get("error") == "Not implemented"


# ---------------------------------------------------------------------------
# CQRS handlers — assert correct query type dispatched
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleProviderHealth:
    @pytest.mark.asyncio
    async def test_dispatches_get_system_status_query(self):
        from orb.interface.system_command_handlers import handle_provider_health

        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=MagicMock(health={"status": "ok"}, message="ok")
        )
        container = MagicMock()
        container.get.return_value = orchestrator

        with patch("orb.interface.system_command_handlers.get_container", return_value=container):
            result = await handle_provider_health(_ns())

        orchestrator.execute.assert_awaited_once()
        assert isinstance(result, dict)
        assert "health" in result


@pytest.mark.unit
class TestHandleProviderConfig:
    @pytest.mark.asyncio
    async def test_dispatches_get_provider_config_query(self):
        from orb.interface.system_command_handlers import handle_provider_config

        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=MagicMock(config={"provider": "aws"}, message="ok")
        )
        container = MagicMock()
        container.get.return_value = orchestrator

        with patch("orb.interface.system_command_handlers.get_container", return_value=container):
            result = await handle_provider_config(_ns())

        orchestrator.execute.assert_awaited_once()
        assert isinstance(result, dict)
        assert "config" in result


@pytest.mark.unit
class TestHandleProviderMetrics:
    @pytest.mark.asyncio
    async def test_dispatches_get_provider_metrics_query_with_provider_name(self):
        from orb.interface.system_command_handlers import handle_provider_metrics

        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=MagicMock(metrics={"latency_ms": 42}, message="ok")
        )
        container = MagicMock()
        container.get.return_value = orchestrator

        with patch("orb.interface.system_command_handlers.get_container", return_value=container):
            result = await handle_provider_metrics(_ns(provider="aws"))

        orchestrator.execute.assert_awaited_once()
        call_input = orchestrator.execute.call_args[0][0]
        assert call_input.provider_name == "aws"
        assert "metrics" in result

    @pytest.mark.asyncio
    async def test_dispatches_with_no_provider_name(self):
        from orb.interface.system_command_handlers import handle_provider_metrics

        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=MagicMock(metrics={}, message="ok"))
        container = MagicMock()
        container.get.return_value = orchestrator

        with patch("orb.interface.system_command_handlers.get_container", return_value=container):
            await handle_provider_metrics(_ns())

        call_input = orchestrator.execute.call_args[0][0]
        assert call_input.provider_name is None


@pytest.mark.unit
class TestHandleSystemStatus:
    @pytest.mark.asyncio
    async def test_dispatches_get_system_status_query_with_flags(self):
        from orb.application.dto.interface_response import InterfaceResponse
        from orb.application.queries.system import GetSystemStatusQuery
        from orb.interface.response_formatting_service import ResponseFormattingService
        from orb.interface.system_command_handlers import handle_system_status

        container, query_bus = _mock_container_with_query_bus(query_return={"healthy": True})
        mock_formatter = MagicMock(spec=ResponseFormattingService)
        mock_formatter.format_system_status.return_value = InterfaceResponse(
            data={"system_status": {"healthy": True}}, exit_code=0
        )
        container.get.side_effect = lambda t: (
            query_bus if t.__name__ == "QueryBus" else mock_formatter
        )

        with patch("orb.interface.system_command_handlers.get_container", return_value=container):
            result = await handle_system_status(_ns(verbose=True))

        query_bus.execute.assert_awaited_once()
        q = query_bus.execute.call_args[0][0]
        assert isinstance(q, GetSystemStatusQuery)
        assert q.include_provider_health is True
        assert q.verbose is True
        assert isinstance(result, InterfaceResponse)
        assert "system_status" in result.data

    @pytest.mark.asyncio
    async def test_detailed_defaults_to_false_when_not_set(self):
        from orb.application.dto.interface_response import InterfaceResponse
        from orb.application.queries.system import GetSystemStatusQuery
        from orb.interface.response_formatting_service import ResponseFormattingService
        from orb.interface.system_command_handlers import handle_system_status

        container, query_bus = _mock_container_with_query_bus(query_return={})
        mock_formatter = MagicMock(spec=ResponseFormattingService)
        mock_formatter.format_system_status.return_value = InterfaceResponse(data={}, exit_code=0)
        container.get.side_effect = lambda t: (
            query_bus if t.__name__ == "QueryBus" else mock_formatter
        )

        with patch("orb.interface.system_command_handlers.get_container", return_value=container):
            await handle_system_status(_ns())

        q = query_bus.execute.call_args[0][0]
        assert isinstance(q, GetSystemStatusQuery)
        assert q.verbose is False


# ---------------------------------------------------------------------------
# handle_list_providers — uses ConfigurationPort via container
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleListProviders:
    @pytest.mark.asyncio
    async def test_returns_providers_from_config_port(self):
        from orb.interface.system_command_handlers import handle_list_providers

        mock_result = MagicMock()
        mock_result.providers = [{"name": "aws-default", "type": "aws", "region": "us-east-1"}]
        mock_result.count = 1
        mock_result.selection_policy = "round-robin"
        mock_result.message = ""

        mock_orchestrator = AsyncMock()
        mock_orchestrator.execute.return_value = mock_result

        container = MagicMock()
        container.get.return_value = mock_orchestrator

        with patch("orb.interface.system_command_handlers.get_container", return_value=container):
            result = await handle_list_providers(_ns())

        assert result["count"] == 1
        assert result["providers"][0]["name"] == "aws-default"
        assert result["providers"][0]["type"] == "aws"

    @pytest.mark.asyncio
    async def test_no_provider_config_returns_empty_list(self):
        from orb.interface.system_command_handlers import handle_list_providers

        mock_result = MagicMock()
        mock_result.providers = []
        mock_result.count = 0
        mock_result.selection_policy = ""
        mock_result.message = ""

        mock_orchestrator = AsyncMock()
        mock_orchestrator.execute.return_value = mock_result

        container = MagicMock()
        container.get.return_value = mock_orchestrator

        with patch("orb.interface.system_command_handlers.get_container", return_value=container):
            result = await handle_list_providers(_ns())

        assert result["count"] == 0
        assert result["providers"] == []

    @pytest.mark.asyncio
    async def test_exception_raises(self):
        from orb.interface.system_command_handlers import handle_list_providers

        container = MagicMock()
        container.get.side_effect = RuntimeError("container exploded")

        with patch("orb.interface.system_command_handlers.get_container", return_value=container):
            with pytest.raises(Exception, match="container exploded"):
                await handle_list_providers(_ns())


# ---------------------------------------------------------------------------
# handle_system_health — delegates to handle_health_check via executor
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleSystemHealth:
    @pytest.mark.asyncio
    async def test_returns_success_when_health_check_returns_0(self):
        from orb.application.dto.interface_response import InterfaceResponse
        from orb.interface.system_command_handlers import handle_system_health

        mock_response = {"success": True, "status": "healthy", "checks": []}
        with patch(
            "orb.interface.health_command_handler.handle_health_check", return_value=mock_response
        ) as mock_hc:
            result = await handle_system_health(_ns())

        mock_hc.assert_called_once()
        assert isinstance(result, InterfaceResponse)
        assert result.data["success"] is True
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_returns_error_when_health_check_returns_nonzero(self):
        from orb.application.dto.interface_response import InterfaceResponse
        from orb.interface.system_command_handlers import handle_system_health

        mock_response = {"success": False, "status": "unhealthy", "checks": []}
        with patch(
            "orb.interface.health_command_handler.handle_health_check", return_value=mock_response
        ):
            result = await handle_system_health(_ns())

        assert isinstance(result, InterfaceResponse)
        assert result.data["success"] is False
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# handle_system_metrics — uses MetricsCollector via container.get_optional
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleSystemMetrics:
    @pytest.mark.asyncio
    async def test_returns_metrics_when_collector_available(self):
        from orb.interface.system_command_handlers import handle_system_metrics

        mock_metrics = MagicMock()
        mock_metrics.get_metrics.return_value = {"requests_total": 5}

        container = MagicMock()
        container.get_optional.return_value = mock_metrics

        with patch("orb.interface.system_command_handlers.get_container", return_value=container):
            result = await handle_system_metrics(_ns())

        assert result.data["metrics"] == {"requests_total": 5}

    @pytest.mark.asyncio
    async def test_returns_empty_metrics_when_collector_unavailable(self):
        from orb.interface.system_command_handlers import handle_system_metrics

        container = MagicMock()
        container.get_optional.return_value = None

        with patch("orb.interface.system_command_handlers.get_container", return_value=container):
            result = await handle_system_metrics(_ns())

        assert result.data["metrics"] == {}

    @pytest.mark.asyncio
    async def test_returns_error_dict_when_get_metrics_raises(self):
        from orb.interface.system_command_handlers import handle_system_metrics

        mock_metrics = MagicMock()
        mock_metrics.get_metrics.side_effect = RuntimeError("metrics broken")

        container = MagicMock()
        container.get_optional.return_value = mock_metrics

        with patch("orb.interface.system_command_handlers.get_container", return_value=container):
            result = await handle_system_metrics(_ns())

        assert result.data["metrics"] == {}
        assert "error" in result.data


# ---------------------------------------------------------------------------
# handle_select_provider_strategy — reads args.provider, no bus
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleSelectProviderStrategy:
    @pytest.mark.asyncio
    async def test_delegates_to_set_default(self):
        """handle_select_provider_strategy delegates to handle_provider_set_default."""
        from orb.interface.system_command_handlers import handle_select_provider_strategy

        with patch("orb.interface.system_command_handlers.handle_select_provider_strategy"):
            with patch(
                "orb.interface.provider_config_handler.handle_provider_set_default",
                new_callable=AsyncMock,
                return_value={"message": "Default provider set", "provider": "aws"},
            ) as mock_set_default:
                result = await handle_select_provider_strategy(_ns(provider_name="aws"))

        mock_set_default.assert_called_once()
        assert result["provider"] == "aws"

    @pytest.mark.asyncio
    async def test_propagates_error_from_set_default(self):
        from orb.interface.system_command_handlers import handle_select_provider_strategy

        with patch(
            "orb.interface.provider_config_handler.handle_provider_set_default",
            new_callable=AsyncMock,
            return_value={"error": True, "message": "Provider 'bad' not found", "exit_code": 1},
        ):
            result = await handle_select_provider_strategy(_ns(provider_name="bad"))

        assert result["error"] is True
