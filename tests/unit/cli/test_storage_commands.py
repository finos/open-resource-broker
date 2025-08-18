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

        with patch("src.interface.storage_command_handlers.get_container") as mock_get_container:
            mock_container = Mock()
            mock_get_container.return_value = mock_container

            # Mock the storage registry
            mock_registry = Mock()
            mock_registry.get_registered_types.return_value = ["json", "dynamodb"]
            mock_container.get.return_value = mock_registry

            result = await handle_list_storage_strategies(args)

            assert isinstance(result, dict)
            assert "strategies" in result

    @pytest.mark.asyncio
    async def test_handle_show_storage_config(self):
        """Test show storage configuration handler."""
        args = Namespace(resource="storage", action="show")

        with patch("src.interface.storage_command_handlers.get_container") as mock_get_container:
            mock_container = Mock()
            mock_get_container.return_value = mock_container

            # Mock configuration manager
            mock_config_manager = Mock()
            mock_config_manager.get_storage_strategy.return_value = "json"
            mock_config_manager.get_app_config.return_value = Mock()
            mock_container.get.return_value = mock_config_manager

            result = await handle_show_storage_config(args)

            assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_handle_validate_storage_config(self):
        """Test validate storage configuration handler."""
        args = Namespace(resource="storage", action="validate")

        with patch("src.interface.storage_command_handlers.get_container") as mock_get_container:
            mock_container = Mock()
            mock_get_container.return_value = mock_container

            # Mock configuration manager
            mock_config_manager = Mock()
            mock_config_manager.get_storage_strategy.return_value = "json"
            mock_config_manager.get_app_config.return_value = Mock()
            mock_container.get.return_value = mock_config_manager

            result = await handle_validate_storage_config(args)

            assert isinstance(result, dict)
            assert "valid" in result

    @pytest.mark.asyncio
    async def test_handle_test_storage(self):
        """Test storage connection test handler."""
        args = Namespace(resource="storage", action="test")

        with patch("src.interface.storage_command_handlers.get_container") as mock_get_container:
            mock_container = Mock()
            mock_get_container.return_value = mock_container

            # Mock storage registry
            mock_registry = Mock()
            mock_strategy = Mock()
            mock_strategy.test_connection = AsyncMock(return_value=True)
            mock_registry.create_strategy.return_value = mock_strategy
            mock_container.get.return_value = mock_registry

            result = await handle_test_storage(args)

            assert isinstance(result, dict)
            assert "connection_test" in result

    @pytest.mark.asyncio
    async def test_handle_storage_health(self):
        """Test storage health check handler."""
        args = Namespace(resource="storage", action="health")

        with patch("src.interface.storage_command_handlers.get_container") as mock_get_container:
            mock_container = Mock()
            mock_get_container.return_value = mock_container

            # Mock storage registry
            mock_registry = Mock()
            mock_strategy = Mock()
            mock_strategy.health_check = AsyncMock(return_value={"status": "healthy"})
            mock_registry.create_strategy.return_value = mock_strategy
            mock_container.get.return_value = mock_registry

            result = await handle_storage_health(args)

            assert isinstance(result, dict)
            assert "health" in result

    @pytest.mark.asyncio
    async def test_handle_storage_metrics(self):
        """Test storage metrics handler."""
        args = Namespace(resource="storage", action="metrics")

        with patch("src.interface.storage_command_handlers.get_container") as mock_get_container:
            mock_container = Mock()
            mock_get_container.return_value = mock_container

            # Mock storage registry
            mock_registry = Mock()
            mock_strategy = Mock()
            mock_strategy.get_metrics = AsyncMock(return_value={"operations": 100})
            mock_registry.create_strategy.return_value = mock_strategy
            mock_container.get.return_value = mock_registry

            result = await handle_storage_metrics(args)

            assert isinstance(result, dict)
            assert "metrics" in result


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

        # Verify all handlers are callable functions
        assert callable(handle_list_storage_strategies)
        assert callable(handle_show_storage_config)
        assert callable(handle_validate_storage_config)
        assert callable(handle_test_storage)
        assert callable(handle_storage_health)
        assert callable(handle_storage_metrics)
