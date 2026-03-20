"""Tests for CLI handler execution."""

import argparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.ports import SchedulerPort
from orb.application.services.orchestration.dtos import ListTemplatesOutput
from orb.application.services.orchestration.get_request_status import (
    GetRequestStatusOrchestrator,
)
from orb.application.services.orchestration.list_templates import ListTemplatesOrchestrator
from orb.infrastructure.di.buses import QueryBus
from orb.infrastructure.di.container import DIContainer
from orb.interface.request_command_handlers import handle_get_request_status
from orb.interface.response_formatting_service import ResponseFormattingService
from orb.interface.scheduler_command_handlers import handle_list_scheduler_strategies
from orb.interface.storage_command_handlers import handle_list_storage_strategies

# Import CLI handlers
from orb.interface.template_command_handlers import handle_list_templates


class TestCLIHandlerExecution:
    """Test CLI handler execution."""

    @pytest.mark.asyncio
    @patch("orb.interface.template_command_handlers.get_container")
    async def test_handle_list_templates(self, mock_get_container):
        """Test that handle_list_templates executes correctly."""
        container = MagicMock(spec=DIContainer)
        formatter = MagicMock(spec=ResponseFormattingService)

        templates = [
            {"id": "template1", "name": "Template 1"},
            {"id": "template2", "name": "Template 2"},
        ]
        orchestrator = AsyncMock(spec=ListTemplatesOrchestrator)
        orchestrator.execute.return_value = ListTemplatesOutput(templates=templates)

        from orb.application.dto.interface_response import InterfaceResponse

        formatted_templates = InterfaceResponse(
            data={
                "templates": templates,
                "count": 2,
                "message": "Retrieved 2 templates successfully",
            }
        )
        formatter.format_template_list = MagicMock(return_value=formatted_templates)

        container.get.side_effect = lambda x: {
            ListTemplatesOrchestrator: orchestrator,
            ResponseFormattingService: formatter,
        }.get(x)

        mock_get_container.return_value = container

        args = argparse.Namespace(
            provider_api=None,
            active_only=True,
        )

        result = await handle_list_templates(args)

        assert result is not None
        assert isinstance(result, InterfaceResponse)
        assert "templates" in result.data

    @pytest.mark.asyncio
    @patch("orb.interface.scheduler_command_handlers.get_container")
    async def test_handle_list_scheduler_strategies(self, mock_get_container):
        """Test that handle_list_scheduler_strategies executes correctly."""
        from orb.application.dto.interface_response import InterfaceResponse
        from orb.application.services.orchestration.dtos import ListSchedulerStrategiesOutput
        from orb.application.services.orchestration.list_scheduler_strategies import (
            ListSchedulerStrategiesOrchestrator,
        )

        container = MagicMock(spec=DIContainer)
        mock_orch = AsyncMock(spec=ListSchedulerStrategiesOrchestrator)
        mock_orch.execute.return_value = ListSchedulerStrategiesOutput(
            strategies=[{"id": "s1"}, {"id": "s2"}], current_strategy="s1", count=2
        )
        mock_formatter = MagicMock(spec=ResponseFormattingService)
        mock_formatter.format_scheduler_strategy_list.return_value = InterfaceResponse(
            data={"strategies": [{"id": "s1"}, {"id": "s2"}], "current_strategy": "s1", "count": 2}
        )

        container.get.side_effect = lambda x: {
            ListSchedulerStrategiesOrchestrator: mock_orch,
            ResponseFormattingService: mock_formatter,
        }.get(x)

        mock_get_container.return_value = container

        args = argparse.Namespace()

        result = await handle_list_scheduler_strategies(args)

        assert isinstance(result, InterfaceResponse)
        assert "strategies" in result.data
        mock_orch.execute.assert_called_once()

    @pytest.mark.asyncio
    @patch("orb.interface.storage_command_handlers.get_container")
    async def test_handle_list_storage_strategies(self, mock_get_container):
        """Test that handle_list_storage_strategies executes correctly."""
        from orb.application.dto.interface_response import InterfaceResponse
        from orb.application.services.orchestration.dtos import ListStorageStrategiesOutput
        from orb.application.services.orchestration.list_storage_strategies import (
            ListStorageStrategiesOrchestrator,
        )

        container = MagicMock(spec=DIContainer)
        mock_orch = AsyncMock(spec=ListStorageStrategiesOrchestrator)
        mock_orch.execute.return_value = ListStorageStrategiesOutput(
            strategies=["json", "sqlite"], current_strategy="json", count=2
        )
        mock_formatter = MagicMock(spec=ResponseFormattingService)
        mock_formatter.format_storage_strategy_list.return_value = InterfaceResponse(
            data={"strategies": ["json", "sqlite"], "current_strategy": "json", "count": 2}
        )

        container.get.side_effect = lambda x: {
            ListStorageStrategiesOrchestrator: mock_orch,
            ResponseFormattingService: mock_formatter,
        }.get(x)

        mock_get_container.return_value = container

        args = argparse.Namespace()

        result = await handle_list_storage_strategies(args)

        assert isinstance(result, InterfaceResponse)
        assert "strategies" in result.data
        mock_orch.execute.assert_called_once()

    @pytest.mark.asyncio
    @patch("orb.interface.request_command_handlers.get_container")
    async def test_handle_get_request_status(self, mock_get_container):
        """Test that handle_get_request_status executes correctly."""
        container = MagicMock(spec=DIContainer)
        query_bus = AsyncMock(spec=QueryBus)
        scheduler_strategy = MagicMock(spec=SchedulerPort)

        request_dto = MagicMock()
        query_bus.execute.return_value = request_dto

        # parse_request_data returns a list for the "requests" branch (both strategies)
        scheduler_strategy.parse_request_data.return_value = [{"request_id": "req-abc123"}]
        scheduler_strategy.format_request_status_response.return_value = {
            "requests": [{"requestId": "req-abc123", "status": "complete"}]
        }

        from orb.application.services.orchestration.dtos import GetRequestStatusOutput

        orchestrator = AsyncMock(spec=GetRequestStatusOrchestrator)
        orchestrator.execute.return_value = GetRequestStatusOutput(
            requests=[{"request_id": "req-abc123", "status": "complete"}]
        )
        formatter = MagicMock(spec=ResponseFormattingService)
        from orb.application.dto.interface_response import InterfaceResponse

        formatter.format_request_status.return_value = InterfaceResponse(
            data={"requests": [{"requestId": "req-abc123", "status": "complete"}]}
        )

        container.get.side_effect = lambda x: {
            QueryBus: query_bus,
            SchedulerPort: scheduler_strategy,
            GetRequestStatusOrchestrator: orchestrator,
            ResponseFormattingService: formatter,
        }.get(x, MagicMock())

        mock_get_container.return_value = container

        args = argparse.Namespace(
            request_id="req-abc123",
            request_ids=[],
            flag_request_ids=[],
            all=False,
        )

        result = await handle_get_request_status(args)

        assert isinstance(result, InterfaceResponse)
        orchestrator.execute.assert_called_once()
        formatter.format_request_status.assert_called_once()


class TestFormatConversionConsistency:
    """Test format conversion consistency."""

    @pytest.mark.asyncio
    @patch("orb.interface.template_command_handlers.get_container")
    async def test_format_conversion_in_template_handler(self, mock_get_container):
        """Test that format conversion is done using ResponseFormattingService in template handlers."""
        container = MagicMock(spec=DIContainer)
        formatter = MagicMock(spec=ResponseFormattingService)

        templates = [
            {"id": "template1", "name": "Template 1"},
            {"id": "template2", "name": "Template 2"},
        ]
        orchestrator = AsyncMock(spec=ListTemplatesOrchestrator)
        orchestrator.execute.return_value = ListTemplatesOutput(templates=templates)

        from orb.application.dto.interface_response import InterfaceResponse

        formatted_templates = InterfaceResponse(
            data={
                "templates": [
                    {"id": "template1", "formatted": True},
                    {"id": "template2", "formatted": True},
                ],
                "count": 2,
            }
        )
        formatter.format_template_list = MagicMock(return_value=formatted_templates)

        container.get.side_effect = lambda x: {
            ListTemplatesOrchestrator: orchestrator,
            ResponseFormattingService: formatter,
        }.get(x)

        mock_get_container.return_value = container

        args = argparse.Namespace(
            provider_api=None,
            active_only=True,
        )

        result = await handle_list_templates(args)

        assert isinstance(result, InterfaceResponse)
        assert "templates" in result.data
        formatter.format_template_list.assert_called_once_with(templates)
