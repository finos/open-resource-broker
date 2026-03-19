"""Tests for storage command handlers."""

from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.interface.storage_command_handlers import (
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
        from orb.application.dto.interface_response import InterfaceResponse
        from orb.application.services.orchestration.dtos import ListStorageStrategiesOutput
        from orb.application.services.orchestration.list_storage_strategies import (
            ListStorageStrategiesOrchestrator,
        )
        from orb.application.services.response_formatting_service import ResponseFormattingService

        args = Namespace(resource="storage", action="list")

        with patch("orb.interface.storage_command_handlers.get_container") as mock_get_container:
            mock_container = MagicMock()
            mock_get_container.return_value = mock_container

            mock_orch = AsyncMock(spec=ListStorageStrategiesOrchestrator)
            mock_orch.execute.return_value = ListStorageStrategiesOutput(
                strategies=["json", "dynamodb"], current_strategy="json", count=2
            )
            mock_formatter = MagicMock(spec=ResponseFormattingService)
            mock_formatter.format_storage_strategy_list.return_value = InterfaceResponse(
                data={"strategies": ["json", "dynamodb"], "current_strategy": "json", "count": 2}
            )

            mock_container.get.side_effect = lambda t: {
                ListStorageStrategiesOrchestrator: mock_orch,
                ResponseFormattingService: mock_formatter,
            }.get(t, MagicMock())

            result = await handle_list_storage_strategies(args)

            assert isinstance(result, InterfaceResponse)
            assert "strategies" in result.data

    @pytest.mark.asyncio
    async def test_handle_show_storage_config(self):
        """Test show storage configuration handler."""
        from orb.application.dto.interface_response import InterfaceResponse
        from orb.application.services.orchestration.dtos import GetStorageConfigOutput
        from orb.application.services.orchestration.get_storage_config import (
            GetStorageConfigOrchestrator,
        )
        from orb.application.services.response_formatting_service import ResponseFormattingService

        args = Namespace(resource="storage", action="show")

        with patch("orb.interface.storage_command_handlers.get_container") as mock_get_container:
            mock_container = MagicMock()
            mock_get_container.return_value = mock_container

            mock_orch = AsyncMock(spec=GetStorageConfigOrchestrator)
            mock_orch.execute.return_value = GetStorageConfigOutput(config={"type": "json"})
            mock_formatter = MagicMock(spec=ResponseFormattingService)
            mock_formatter.format_storage_config.return_value = InterfaceResponse(
                data={"config": {"type": "json"}}
            )

            mock_container.get.side_effect = lambda t: {
                GetStorageConfigOrchestrator: mock_orch,
                ResponseFormattingService: mock_formatter,
            }.get(t, MagicMock())

            result = await handle_show_storage_config(args)

            assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_handle_validate_storage_config(self):
        """Test validate storage configuration handler."""
        args = Namespace(resource="storage", action="validate")

        with patch("orb.interface.storage_command_handlers.get_container") as mock_get_container:
            mock_query_bus = AsyncMock()
            mock_query_bus.execute = AsyncMock(return_value={"valid": True, "errors": []})

            mock_container = MagicMock()
            mock_container.get.return_value = mock_query_bus
            mock_get_container.return_value = mock_container

            result = await handle_validate_storage_config(args)

            assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_handle_test_storage(self):
        """Test storage connection test handler."""
        from orb.application.dto.interface_response import InterfaceResponse
        from orb.application.services.response_formatting_service import ResponseFormattingService

        args = Namespace(resource="storage", action="test")

        with patch("orb.interface.storage_command_handlers.get_container") as mock_get_container:
            mock_query_bus = AsyncMock()
            mock_query_bus.execute = AsyncMock(return_value={"success": True, "status": "success"})

            mock_formatter = MagicMock(spec=ResponseFormattingService)
            mock_formatter.format_storage_test.return_value = InterfaceResponse(
                data={"success": True, "message": "Storage test completed successfully"}, exit_code=0
            )

            mock_container = MagicMock()
            mock_container.get.side_effect = lambda t: (
                mock_query_bus if t is not ResponseFormattingService else mock_formatter
            )
            mock_get_container.return_value = mock_container

            result = await handle_test_storage(args)

        assert isinstance(result, InterfaceResponse)
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_handle_storage_health(self):
        """Test storage health check handler."""
        args = Namespace(resource="storage", action="health")

        with patch("orb.interface.storage_command_handlers.get_container") as mock_get_container:
            mock_query_bus = AsyncMock()
            mock_query_bus.execute = AsyncMock(return_value={"status": "healthy"})

            mock_container = MagicMock()
            mock_container.get.return_value = mock_query_bus
            mock_get_container.return_value = mock_container

            result = await handle_storage_health(args)

            assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_handle_storage_metrics(self):
        """Test storage metrics handler."""
        args = Namespace(resource="storage", action="metrics")

        with patch("orb.interface.storage_command_handlers.get_container") as mock_get_container:
            mock_query_bus = AsyncMock()
            mock_query_bus.execute = AsyncMock(return_value={"operations": 100})

            mock_container = MagicMock()
            mock_container.get.return_value = mock_query_bus
            mock_get_container.return_value = mock_container

            result = await handle_storage_metrics(args)

            assert isinstance(result, dict)


class TestStorageHandlerImports:
    """Test that storage handlers can be imported correctly."""

    def test_import_storage_handlers(self):
        """Test that all storage handlers can be imported."""
        from orb.interface.storage_command_handlers import (
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
