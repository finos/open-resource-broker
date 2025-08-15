"""Storage-related command handlers for the interface layer."""

from __future__ import annotations

from typing import Any, Dict

from domain.base.ports.scheduler_port import SchedulerPort
from infrastructure.di.buses import CommandBus, QueryBus
from infrastructure.di.container import get_container
from infrastructure.error.decorators import handle_interface_exceptions


@handle_interface_exceptions(context="list_storage_strategies", interface_type="cli")
async def handle_list_storage_strategies(args) -> Dict[str, Any]:
    """Handle list storage strategies operations."""
    container = get_container()
    query_bus = container.get(QueryBus)
    container.get(SchedulerPort)

    from application.queries.storage import ListStorageStrategiesQuery

    query = ListStorageStrategiesQuery()
    strategies = await query_bus.execute(query)

    return {
        "strategies": strategies,
        "count": len(strategies),
        "message": "Storage strategies retrieved successfully",
    }


@handle_interface_exceptions(context="show_storage_config", interface_type="cli")
async def handle_show_storage_config(args) -> Dict[str, Any]:
    """
    Handle show storage configuration operations.

    Args:
        args: Argument namespace with resource/action structure

    Returns:
        Storage configuration
    """
    container = get_container()
    query_bus = container.get(QueryBus)

    from application.queries.system import GetStorageConfigQuery

    query = GetStorageConfigQuery()
    config = await query_bus.execute(query)

    return {"config": config, "message": "Storage configuration retrieved successfully"}


@handle_interface_exceptions(context="validate_storage_config", interface_type="cli")
async def handle_validate_storage_config(args) -> Dict[str, Any]:
    """
    Handle validate storage configuration operations.

    Args:
        args: Argument namespace with resource/action structure

    Returns:
        Validation results
    """
    container = get_container()
    query_bus = container.get(QueryBus)

    from application.queries.system import ValidateStorageConfigQuery

    query = ValidateStorageConfigQuery()
    validation = await query_bus.execute(query)

    return {
        "validation": validation,
        "message": "Storage configuration validated successfully",
    }


@handle_interface_exceptions(context="test_storage", interface_type="cli")
async def handle_test_storage(args) -> Dict[str, Any]:
    """
    Handle test storage operations.

    Args:
        args: Argument namespace with resource/action structure

    Returns:
        Test results
    """
    container = get_container()
    command_bus = container.get(CommandBus)

    from application.commands.system import TestStorageCommand

    command = TestStorageCommand()
    result = await command_bus.execute(command)

    return {"test_result": result, "message": "Storage test completed successfully"}


@handle_interface_exceptions(context="storage_health", interface_type="cli")
async def handle_storage_health(args) -> Dict[str, Any]:
    """
    Handle storage health operations.

    Args:
        args: Argument namespace with resource/action structure

    Returns:
        Health status
    """
    container = get_container()
    query_bus = container.get(QueryBus)

    from application.queries.system import GetStorageHealthQuery

    query = GetStorageHealthQuery()
    health = await query_bus.execute(query)

    return {"health": health, "message": "Storage health retrieved successfully"}


@handle_interface_exceptions(context="storage_metrics", interface_type="cli")
async def handle_storage_metrics(args) -> Dict[str, Any]:
    """
    Handle storage metrics operations.

    Args:
        args: Argument namespace with resource/action structure

    Returns:
        Storage metrics
    """
    container = get_container()
    query_bus = container.get(QueryBus)

    from application.queries.system import GetStorageMetricsQuery

    query = GetStorageMetricsQuery()
    metrics = await query_bus.execute(query)

    return {"metrics": metrics, "message": "Storage metrics retrieved successfully"}
