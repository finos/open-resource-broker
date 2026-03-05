"""Unit tests for scheduler command handlers."""

from argparse import Namespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from interface.scheduler_command_handlers import (
    handle_list_scheduler_strategies,
    handle_show_scheduler_config,
    handle_validate_scheduler_config,
)


class TestSchedulerCommandHandlers:
    """Test scheduler command handlers."""

    @pytest.mark.asyncio
    async def test_handle_list_scheduler_strategies(self):
        """Test list scheduler strategies handler."""
        args = Namespace(resource="scheduler", action="list")

        with patch("interface.scheduler_command_handlers.get_container") as mock_get_container:
            mock_container = Mock()
            mock_get_container.return_value = mock_container

            mock_query_bus = Mock()
            mock_query_bus.execute = AsyncMock(return_value=["simple", "advanced"])
            mock_container.get.return_value = mock_query_bus

            result = await handle_list_scheduler_strategies(args)

            assert isinstance(result, dict)
            assert "strategies" in result

    @pytest.mark.asyncio
    async def test_handle_show_scheduler_config(self):
        """Test show scheduler configuration handler."""
        args = Namespace(resource="scheduler", action="show")

        with patch("interface.scheduler_command_handlers.get_container") as mock_get_container:
            mock_container = Mock()
            mock_get_container.return_value = mock_container

            mock_query_bus = Mock()
            mock_query_bus.execute = AsyncMock(return_value=Mock())
            mock_container.get.return_value = mock_query_bus

            result = await handle_show_scheduler_config(args)

            assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_handle_validate_scheduler_config(self):
        """Test validate scheduler configuration handler."""
        args = Namespace(resource="scheduler", action="validate")

        with patch("interface.scheduler_command_handlers.get_container") as mock_get_container:
            mock_container = Mock()
            mock_get_container.return_value = mock_container

            mock_query_bus = Mock()
            mock_query_bus.execute = AsyncMock(return_value=Mock())
            mock_container.get.return_value = mock_query_bus

            result = await handle_validate_scheduler_config(args)

            assert isinstance(result, dict)
            assert "validation" in result


class TestSchedulerHandlerImports:
    """Test that scheduler handlers can be imported correctly."""

    def test_import_scheduler_handlers(self):
        """Test that all scheduler handlers can be imported."""
        from interface.scheduler_command_handlers import (
            handle_list_scheduler_strategies,
            handle_show_scheduler_config,
            handle_validate_scheduler_config,
        )

        # Verify all handlers are callable functions
        assert callable(handle_list_scheduler_strategies)
        assert callable(handle_show_scheduler_config)
        assert callable(handle_validate_scheduler_config)
