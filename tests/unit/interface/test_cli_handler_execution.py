"""Tests for CLI handler execution."""

import argparse
from unittest.mock import AsyncMock, MagicMock, patch

from domain.base.ports import SchedulerPort
from infrastructure.di.buses import QueryBus
from infrastructure.di.container import DIContainer
from interface.request_command_handlers import handle_get_request_status
from interface.scheduler_command_handlers import handle_list_scheduler_strategies
from interface.storage_command_handlers import handle_list_storage_strategies

# Import CLI handlers
from interface.template_command_handlers import handle_list_templates


class TestCLIHandlerExecution:
    """Test CLI handler execution."""

    @patch("src.interface.template_command_handlers.get_container")
    async def test_handle_list_templates(self, mock_get_container):
        """Test that handle_list_templates executes correctly."""
        # Arrange
        container = MagicMock(spec=DIContainer)
        query_bus = MagicMock(spec=QueryBus)
        scheduler_strategy = MagicMock(spec=SchedulerPort)

        # Mock query_bus.execute to return a list of templates
        templates = [
            {"id": "template1", "name": "Template 1"},
            {"id": "template2", "name": "Template 2"},
        ]
        query_bus.execute = MagicMock(return_value=templates)

        # Mock scheduler_strategy.format_templates_response to return formatted templates
        formatted_templates = {"templates": templates}
        scheduler_strategy.format_templates_response = MagicMock(return_value=formatted_templates)

        # Set up container.get to return the mocked objects
        container.get.side_effect = lambda x: {
            QueryBus: query_bus,
            SchedulerPort: scheduler_strategy,
        }.get(x)

        mock_get_container.return_value = container

        # Create args with default values
        args = argparse.Namespace(provider_api=None, active_only=True, include_config=False)

        # Act
        result = await handle_list_templates(args)

        # Assert
        assert result["success"] is True
        assert result["templates"] == templates
        assert result["total_count"] == len(templates)
        assert "message" in result

        # Verify that the query bus was called with the correct query
        query_bus.execute.assert_called_once()

        # Verify that the scheduler strategy was used for format conversion
        scheduler_strategy.format_templates_response.assert_called_once_with(templates)

    @patch("src.interface.scheduler_command_handlers.get_container")
    async def test_handle_list_scheduler_strategies(self, mock_get_container):
        """Test that handle_list_scheduler_strategies executes correctly."""
        # Arrange
        container = MagicMock(spec=DIContainer)
        query_bus = AsyncMock(spec=QueryBus)
        scheduler_strategy = MagicMock(spec=SchedulerPort)

        # Mock query_bus.execute to return a list of strategies
        strategies = [
            {"id": "strategy1", "name": "Strategy 1"},
            {"id": "strategy2", "name": "Strategy 2"},
        ]
        query_bus.execute.return_value = strategies

        # Set up container.get to return the mocked objects
        container.get.side_effect = lambda x: {
            QueryBus: query_bus,
            SchedulerPort: scheduler_strategy,
        }.get(x)

        mock_get_container.return_value = container

        # Create args with default values
        args = argparse.Namespace()

        # Act
        result = await handle_list_scheduler_strategies(args)

        # Assert
        assert result["strategies"] == strategies
        assert result["count"] == len(strategies)
        assert "message" in result

        # Verify that the query bus was called
        query_bus.execute.assert_called_once()

    @patch("src.interface.storage_command_handlers.get_container")
    async def test_handle_list_storage_strategies(self, mock_get_container):
        """Test that handle_list_storage_strategies executes correctly."""
        # Arrange
        container = MagicMock(spec=DIContainer)
        query_bus = AsyncMock(spec=QueryBus)
        scheduler_strategy = MagicMock(spec=SchedulerPort)

        # Mock query_bus.execute to return a list of strategies
        strategies = [
            {"id": "strategy1", "name": "Strategy 1"},
            {"id": "strategy2", "name": "Strategy 2"},
        ]
        query_bus.execute.return_value = strategies

        # Set up container.get to return the mocked objects
        container.get.side_effect = lambda x: {
            QueryBus: query_bus,
            SchedulerPort: scheduler_strategy,
        }.get(x)

        mock_get_container.return_value = container

        # Create args with default values
        args = argparse.Namespace()

        # Act
        result = await handle_list_storage_strategies(args)

        # Assert
        assert result["strategies"] == strategies
        assert result["count"] == len(strategies)
        assert "message" in result

        # Verify that the query bus was called
        query_bus.execute.assert_called_once()

    @patch("src.interface.request_command_handlers.get_container")
    async def test_handle_get_request_status(self, mock_get_container):
        """Test that handle_get_request_status executes correctly."""
        # Arrange
        container = MagicMock(spec=DIContainer)
        query_bus = AsyncMock(spec=QueryBus)
        scheduler_strategy = MagicMock(spec=SchedulerPort)

        # Mock query_bus.execute to return request status
        request_status = {"id": "request1", "status": "completed", "machines": []}
        query_bus.execute.return_value = request_status

        # Set up container.get to return the mocked objects
        container.get.side_effect = lambda x: {
            QueryBus: query_bus,
            SchedulerPort: scheduler_strategy,
        }.get(x)

        mock_get_container.return_value = container

        # Create args with request_id
        args = argparse.Namespace(request_id="request1")

        # Act
        result = await handle_get_request_status(args)

        # Assert
        assert result["request"] == request_status
        assert "message" in result

        # Verify that the query bus was called with the correct query
        query_bus.execute.assert_called_once()


class TestFormatConversionConsistency:
    """Test format conversion consistency."""

    @patch("src.interface.template_command_handlers.get_container")
    async def test_format_conversion_in_template_handler(self, mock_get_container):
        """Test that format conversion is done using the scheduler strategy in template handlers."""
        # Arrange
        container = MagicMock(spec=DIContainer)
        query_bus = MagicMock(spec=QueryBus)
        scheduler_strategy = MagicMock(spec=SchedulerPort)

        # Mock query_bus.execute to return a list of templates
        templates = [
            {"id": "template1", "name": "Template 1"},
            {"id": "template2", "name": "Template 2"},
        ]
        query_bus.execute = MagicMock(return_value=templates)

        # Mock scheduler_strategy.format_templates_response to return formatted templates
        formatted_templates = {
            "templates": [
                {"id": "template1", "formatted": True},
                {"id": "template2", "formatted": True},
            ]
        }
        scheduler_strategy.format_templates_response = MagicMock(return_value=formatted_templates)

        # Set up container.get to return the mocked objects
        container.get.side_effect = lambda x: {
            QueryBus: query_bus,
            SchedulerPort: scheduler_strategy,
        }.get(x)

        mock_get_container.return_value = container

        # Create args with default values
        args = argparse.Namespace(provider_api=None, active_only=True, include_config=False)

        # Act
        result = await handle_list_templates(args)

        # Assert
        assert result["templates"] == formatted_templates.get("templates", templates)

        # Verify that the scheduler strategy was used for format conversion
        scheduler_strategy.format_templates_response.assert_called_once_with(templates)
