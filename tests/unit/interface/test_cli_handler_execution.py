"""Tests for CLI handler execution."""

import argparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.ports import SchedulerPort
from orb.infrastructure.di.buses import QueryBus
from orb.infrastructure.di.container import DIContainer
from orb.interface.request_command_handlers import handle_get_request_status
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
        query_bus = AsyncMock(spec=QueryBus)
        scheduler_strategy = MagicMock(spec=SchedulerPort)

        templates = [
            {"id": "template1", "name": "Template 1"},
            {"id": "template2", "name": "Template 2"},
        ]
        query_bus.execute = AsyncMock(return_value=templates)

        formatted_templates = {
            "templates": templates,
            "count": 2,
            "message": "Retrieved 2 templates successfully",
        }
        scheduler_strategy.format_templates_response = MagicMock(return_value=formatted_templates)

        container.get.side_effect = lambda x: {
            QueryBus: query_bus,
            SchedulerPort: scheduler_strategy,
        }.get(x)

        mock_get_container.return_value = container

        args = argparse.Namespace(
            provider_api=None,
            active_only=True,
        )

        result = await handle_list_templates(args)

        # handle_list_templates returns the scheduler strategy's formatted response
        assert isinstance(result, dict)
        assert "templates" in result

    @pytest.mark.asyncio
    @patch("orb.interface.scheduler_command_handlers.get_container")
    async def test_handle_list_scheduler_strategies(self, mock_get_container):
        """Test that handle_list_scheduler_strategies executes correctly."""
        container = MagicMock(spec=DIContainer)
        query_bus = AsyncMock(spec=QueryBus)
        scheduler_strategy = MagicMock(spec=SchedulerPort)

        strategies = [
            {"id": "strategy1", "name": "Strategy 1"},
            {"id": "strategy2", "name": "Strategy 2"},
        ]
        query_bus.execute.return_value = strategies

        container.get.side_effect = lambda x: {
            QueryBus: query_bus,
            SchedulerPort: scheduler_strategy,
        }.get(x)

        mock_get_container.return_value = container

        args = argparse.Namespace()

        result = await handle_list_scheduler_strategies(args)

        assert result["strategies"] == strategies
        assert result["count"] == len(strategies)
        assert "message" in result

        query_bus.execute.assert_called_once()

    @pytest.mark.asyncio
    @patch("orb.interface.storage_command_handlers.get_container")
    async def test_handle_list_storage_strategies(self, mock_get_container):
        """Test that handle_list_storage_strategies executes correctly."""
        container = MagicMock(spec=DIContainer)
        query_bus = AsyncMock(spec=QueryBus)
        scheduler_strategy = MagicMock(spec=SchedulerPort)

        # handle_list_storage_strategies accesses .strategies, .total_count, .current_strategy
        mock_result = MagicMock()
        mock_result.strategies = ["json", "sqlite"]
        mock_result.total_count = 2
        mock_result.current_strategy = "json"
        query_bus.execute.return_value = mock_result

        container.get.side_effect = lambda x: {
            QueryBus: query_bus,
            SchedulerPort: scheduler_strategy,
        }.get(x)

        mock_get_container.return_value = container

        args = argparse.Namespace()

        result = await handle_list_storage_strategies(args)

        assert "strategies" in result
        assert "count" in result
        assert "message" in result

        query_bus.execute.assert_called_once()

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

        container.get.side_effect = lambda x: {
            QueryBus: query_bus,
            SchedulerPort: scheduler_strategy,
        }.get(x)

        mock_get_container.return_value = container

        args = argparse.Namespace(
            request_id="req-abc123",
            request_ids=[],
            flag_request_ids=[],
            all=False,
        )

        result = await handle_get_request_status(args)

        assert isinstance(result, dict)
        # Verify the query bus was actually called — proves request_id was extracted correctly
        query_bus.execute.assert_called_once()
        scheduler_strategy.format_request_status_response.assert_called_once()


class TestFormatConversionConsistency:
    """Test format conversion consistency."""

    @pytest.mark.asyncio
    @patch("orb.interface.template_command_handlers.get_container")
    async def test_format_conversion_in_template_handler(self, mock_get_container):
        """Test that format conversion is done using the scheduler strategy in template handlers."""
        container = MagicMock(spec=DIContainer)
        query_bus = AsyncMock(spec=QueryBus)
        scheduler_strategy = MagicMock(spec=SchedulerPort)

        templates = [
            {"id": "template1", "name": "Template 1"},
            {"id": "template2", "name": "Template 2"},
        ]
        query_bus.execute = AsyncMock(return_value=templates)

        formatted_templates = {
            "templates": [
                {"id": "template1", "formatted": True},
                {"id": "template2", "formatted": True},
            ],
            "count": 2,
        }
        scheduler_strategy.format_templates_response = MagicMock(return_value=formatted_templates)

        container.get.side_effect = lambda x: {
            QueryBus: query_bus,
            SchedulerPort: scheduler_strategy,
        }.get(x)

        mock_get_container.return_value = container

        args = argparse.Namespace(
            provider_api=None,
            active_only=True,
        )

        result = await handle_list_templates(args)

        assert isinstance(result, dict)
        assert "templates" in result

        # Verify scheduler strategy was called for format conversion
        scheduler_strategy.format_templates_response.assert_called_once_with(templates)
