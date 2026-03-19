"""Scheduler-related command handlers for the interface layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Union

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.services.response_formatting_service import ResponseFormattingService
from orb.infrastructure.di.container import get_container
from orb.infrastructure.error.decorators import handle_interface_exceptions

if TYPE_CHECKING:
    import argparse


@handle_interface_exceptions(context="list_scheduler_strategies", interface_type="cli")
async def handle_list_scheduler_strategies(
    args: "argparse.Namespace",
) -> Union[dict[str, Any], InterfaceResponse]:
    """Handle list scheduler strategies operations."""
    from orb.application.services.orchestration.dtos import ListSchedulerStrategiesInput
    from orb.application.services.orchestration.list_scheduler_strategies import (
        ListSchedulerStrategiesOrchestrator,
    )

    container = get_container()
    orchestrator = container.get(ListSchedulerStrategiesOrchestrator)
    formatter = container.get(ResponseFormattingService)

    result = await orchestrator.execute(ListSchedulerStrategiesInput())
    return formatter.format_scheduler_strategy_list(
        result.strategies, result.current_strategy, result.count
    )


@handle_interface_exceptions(context="show_scheduler_config", interface_type="cli")
async def handle_show_scheduler_config(
    args: "argparse.Namespace",
) -> Union[dict[str, Any], InterfaceResponse]:
    """Handle show scheduler configuration operations."""
    from orb.application.services.orchestration.dtos import GetSchedulerConfigInput
    from orb.application.services.orchestration.get_scheduler_config import (
        GetSchedulerConfigOrchestrator,
    )

    container = get_container()
    orchestrator = container.get(GetSchedulerConfigOrchestrator)
    formatter = container.get(ResponseFormattingService)

    result = await orchestrator.execute(GetSchedulerConfigInput())
    return formatter.format_scheduler_config(result.config)


@handle_interface_exceptions(context="validate_scheduler_config", interface_type="cli")
async def handle_validate_scheduler_config(args: "argparse.Namespace") -> dict[str, Any]:
    """Handle validate scheduler configuration operations."""
    from orb.infrastructure.di.buses import QueryBus

    container = get_container()
    query_bus = container.get(QueryBus)

    from orb.application.queries.scheduler import ValidateSchedulerConfigurationQuery

    query = ValidateSchedulerConfigurationQuery()
    validation = await query_bus.execute(query)

    return {
        "validation": validation,
        "message": "Scheduler configuration validated successfully",
    }
