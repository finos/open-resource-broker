"""Scheduler-related command handlers for the interface layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orb.application.dto.interface_response import InterfaceResponse
from orb.infrastructure.error.decorators import handle_interface_exceptions

if TYPE_CHECKING:
    import argparse

    from orb.application.ports.query_bus_port import QueryBusPort
    from orb.application.services.orchestration.get_scheduler_config import (
        GetSchedulerConfigOrchestrator,
    )
    from orb.application.services.orchestration.list_scheduler_strategies import (
        ListSchedulerStrategiesOrchestrator,
    )
    from orb.interface.response_formatting_service import ResponseFormattingService


class SchedulerHandlers:
    """CLI handlers that list, show and validate scheduler strategies."""

    def __init__(
        self,
        list_orchestrator: ListSchedulerStrategiesOrchestrator,
        config_orchestrator: GetSchedulerConfigOrchestrator,
        query_bus: QueryBusPort,
        formatter: ResponseFormattingService,
    ) -> None:
        self._list_orchestrator = list_orchestrator
        self._config_orchestrator = config_orchestrator
        self._query_bus = query_bus
        self._formatter = formatter

    @handle_interface_exceptions(context="list_scheduler_strategies", interface_type="cli")
    async def list_scheduler_strategies(
        self, args: argparse.Namespace
    ) -> dict[str, Any] | InterfaceResponse:
        """Handle list scheduler strategies operations."""
        from orb.application.services.orchestration.dtos import ListSchedulerStrategiesInput

        orchestrator = self._list_orchestrator
        formatter = self._formatter

        result = await orchestrator.execute(ListSchedulerStrategiesInput())
        return formatter.format_scheduler_strategy_list(
            result.strategies, result.current_strategy, result.count
        )

    @handle_interface_exceptions(context="show_scheduler_config", interface_type="cli")
    async def show_scheduler_config(
        self, args: argparse.Namespace
    ) -> dict[str, Any] | InterfaceResponse:
        """Handle show scheduler configuration operations."""
        from orb.application.services.orchestration.dtos import GetSchedulerConfigInput

        orchestrator = self._config_orchestrator
        formatter = self._formatter

        result = await orchestrator.execute(
            GetSchedulerConfigInput(strategy_name=getattr(args, "strategy", None))
        )
        return formatter.format_scheduler_config(result.config)

    @handle_interface_exceptions(context="validate_scheduler_config", interface_type="cli")
    async def validate_scheduler_config(
        self, args: argparse.Namespace
    ) -> dict[str, Any] | InterfaceResponse:
        """Handle validate scheduler configuration operations."""
        from orb.application.queries.scheduler import ValidateSchedulerConfigurationQuery

        query = ValidateSchedulerConfigurationQuery()
        validation = await self._query_bus.execute(query)

        validation_data = (
            validation.model_dump()
            if hasattr(validation, "model_dump")
            else (validation if isinstance(validation, dict) else vars(validation))
        )
        raw = {
            "validation": validation_data,
            "message": "Scheduler configuration validated successfully",
        }
        if isinstance(validation, dict):
            is_valid = validation.get("valid", True)
        elif hasattr(validation, "model_dump"):
            is_valid = validation.model_dump().get("valid", True)
        elif hasattr(validation, "__dict__"):
            is_valid = vars(validation).get("valid", True)
        else:
            is_valid = True
        return InterfaceResponse(data=raw, exit_code=0 if is_valid else 1)
