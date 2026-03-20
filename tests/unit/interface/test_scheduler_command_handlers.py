"""Unit tests for scheduler command handlers."""

from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.dto.interface_response import InterfaceResponse
from orb.interface.scheduler_command_handlers import (
    handle_list_scheduler_strategies,
    handle_show_scheduler_config,
    handle_validate_scheduler_config,
)


class TestSchedulerCommandHandlers:
    """Test scheduler command handlers."""

    @pytest.mark.asyncio
    async def test_handle_list_scheduler_strategies(self):
        """Test list scheduler strategies handler."""
        from orb.application.dto.interface_response import InterfaceResponse
        from orb.application.services.orchestration.dtos import ListSchedulerStrategiesOutput
        from orb.application.services.orchestration.list_scheduler_strategies import (
            ListSchedulerStrategiesOrchestrator,
        )
        from orb.interface.response_formatting_service import ResponseFormattingService

        args = Namespace(resource="scheduler", action="list")

        with patch("orb.interface.scheduler_command_handlers.get_container") as mock_get_container:
            mock_container = MagicMock()
            mock_get_container.return_value = mock_container

            mock_orch = AsyncMock(spec=ListSchedulerStrategiesOrchestrator)
            mock_orch.execute.return_value = ListSchedulerStrategiesOutput(
                strategies=["simple", "advanced"], current_strategy="simple", count=2
            )
            mock_formatter = MagicMock(spec=ResponseFormattingService)
            mock_formatter.format_scheduler_strategy_list.return_value = InterfaceResponse(
                data={
                    "strategies": ["simple", "advanced"],
                    "current_strategy": "simple",
                    "count": 2,
                }
            )

            mock_container.get.side_effect = lambda t: {
                ListSchedulerStrategiesOrchestrator: mock_orch,
                ResponseFormattingService: mock_formatter,
            }.get(t, MagicMock())

            result = await handle_list_scheduler_strategies(args)

            assert isinstance(result, InterfaceResponse)
            assert "strategies" in result.data

    @pytest.mark.asyncio
    async def test_handle_show_scheduler_config(self):
        """Test show scheduler configuration handler."""
        from orb.application.dto.interface_response import InterfaceResponse
        from orb.application.services.orchestration.dtos import GetSchedulerConfigOutput
        from orb.application.services.orchestration.get_scheduler_config import (
            GetSchedulerConfigOrchestrator,
        )
        from orb.interface.response_formatting_service import ResponseFormattingService

        args = Namespace(resource="scheduler", action="show")

        with patch("orb.interface.scheduler_command_handlers.get_container") as mock_get_container:
            mock_container = MagicMock()
            mock_get_container.return_value = mock_container

            mock_orch = AsyncMock(spec=GetSchedulerConfigOrchestrator)
            mock_orch.execute.return_value = GetSchedulerConfigOutput(config={"type": "simple"})
            mock_formatter = MagicMock(spec=ResponseFormattingService)
            mock_formatter.format_scheduler_config.return_value = InterfaceResponse(
                data={"config": {"type": "simple"}}
            )

            mock_container.get.side_effect = lambda t: {
                GetSchedulerConfigOrchestrator: mock_orch,
                ResponseFormattingService: mock_formatter,
            }.get(t, MagicMock())

            result = await handle_show_scheduler_config(args)

            assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_handle_validate_scheduler_config(self):
        """Test validate scheduler configuration handler."""
        args = Namespace(resource="scheduler", action="validate")

        with patch("orb.interface.scheduler_command_handlers.get_container") as mock_get_container:
            mock_container = MagicMock()
            mock_get_container.return_value = mock_container

            mock_query_bus = MagicMock()
            mock_query_bus.execute = AsyncMock(return_value=MagicMock())
            mock_container.get.return_value = mock_query_bus

            result = await handle_validate_scheduler_config(args)

            assert isinstance(result, InterfaceResponse)
            assert "validation" in result.data


class TestSchedulerHandlerImports:
    """Test that scheduler handlers can be imported correctly."""

    def test_import_scheduler_handlers(self):
        """Test that all scheduler handlers can be imported."""
        from orb.interface.scheduler_command_handlers import (
            handle_list_scheduler_strategies,
            handle_show_scheduler_config,
            handle_validate_scheduler_config,
        )

        assert callable(handle_list_scheduler_strategies)
        assert callable(handle_show_scheduler_config)
        assert callable(handle_validate_scheduler_config)
