"""Tests for storage command handlers."""

from argparse import Namespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from interface.storage_command_handlers import (
    handle_list_storage_strategies,
    handle_show_storage_config,
    handle_storage_health,
    handle_storage_metrics,
    handle_test_storage,
    handle_validate_storage_config,
)


class TestStorageCommandHandlers:
    """Test storage command handlers."""

    @pytest.mark.asyncio
    async def test_handle_list_storage_strategies(self):
        """Test list storage strategies handler."""
        args = Namespace(resource="storage", action="list")

        with patch("interface.storage_command_handlers.get_container") as mock_get_container:
            mock_query_bus = AsyncMock()
            mock_result = Mock()
            mock_result.strategies = ["json", "dynamodb"]
            mock_result.total_count = 2
            mock_result.current_strategy = "json"
            mock_query_bus.execute = AsyncMock(return_value=mock_result)

            mock_container = Mock()
            mock_container.get.return_value = mock_query_bus
            mock_get_container.return_value = mock_container

            result = await handle_list_storage_strategies(args)

            assert isinstance(result, dict)
            assert "strategies" in result

    @pytest.mark.asyncio
    async def test_handle_show_storage_config(self):
        """Test show storage configuration handler."""
        args = Namespace(resource="storage", action="show")

        with patch("interface.storage_command_handlers.get_container") as mock_get_container:
            mock_query_bus = AsyncMock()
            mock_query_bus.execute = AsyncMock(return_value={"type": "json", "path": "data"})

            mock_container = Mock()
            mock_container.get.return_value = mock_query_bus
            mock_get_container.return_value = mock_container

            result = await handle_show_storage_config(args)

            assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_handle_validate_storage_config(self):
        """Test validate storage configuration handler."""
        args = Namespace(resource="storage", action="validate")

        with patch("interface.storage_command_handlers.get_container") as mock_get_container:
            mock_query_bus = AsyncMock()
            mock_query_bus.execute = AsyncMock(return_value={"valid": True, "errors": []})

            mock_container = Mock()
            mock_container.get.return_value = mock_query_bus
            mock_get_container.return_value = mock_container

            result = await handle_validate_storage_config(args)

            assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_handle_test_storage(self):
        """Test storage connection test handler."""
        args = Namespace(resource="storage", action="test")

        with patch("interface.storage_command_handlers.get_container") as mock_get_container:
            mock_query_bus = AsyncMock()
            mock_query_bus.execute = AsyncMock(return_value={"success": True})

            mock_container = Mock()
            mock_container.get.return_value = mock_query_bus
            mock_get_container.return_value = mock_container

            result = await handle_test_storage(args)

            assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_handle_storage_health(self):
        """Test storage health check handler."""
        args = Namespace(resource="storage", action="health")

        with patch("interface.storage_command_handlers.get_container") as mock_get_container:
            mock_query_bus = AsyncMock()
            mock_query_bus.execute = AsyncMock(return_value={"status": "healthy"})

            mock_container = Mock()
            mock_container.get.return_value = mock_query_bus
            mock_get_container.return_value = mock_container

            result = await handle_storage_health(args)

            assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_handle_storage_metrics(self):
        """Test storage metrics handler."""
        args = Namespace(resource="storage", action="metrics")

        with patch("interface.storage_command_handlers.get_container") as mock_get_container:
            mock_query_bus = AsyncMock()
            mock_query_bus.execute = AsyncMock(return_value={"operations": 100})

            mock_container = Mock()
            mock_container.get.return_value = mock_query_bus
            mock_get_container.return_value = mock_container

            result = await handle_storage_metrics(args)

            assert isinstance(result, dict)


class TestStorageHandlerImports:
    """Test that storage handlers can be imported correctly."""

    def test_import_storage_handlers(self):
        """Test that all storage handlers can be imported."""
        from interface.storage_command_handlers import (
            handle_list_storage_strategies,
            handle_show_storage_config,
            handle_storage_health,
            handle_storage_metrics,
            handle_test_storage,
            handle_validate_storage_config,
        )

        assert callable(handle_list_storage_strategies)
        assert callable(handle_show_storage_config)
        assert callable(handle_validate_storage_config)
        assert callable(handle_test_storage)
        assert callable(handle_storage_health)
        assert callable(handle_storage_metrics)
