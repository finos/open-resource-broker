"""Storage-related command handlers for the interface layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Union

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.services.response_formatting_service import ResponseFormattingService
from orb.infrastructure.di.container import get_container
from orb.infrastructure.error.decorators import handle_interface_exceptions

if TYPE_CHECKING:
    import argparse


@handle_interface_exceptions(context="list_storage_strategies", interface_type="cli")
async def handle_list_storage_strategies(
    args: "argparse.Namespace",
) -> Union[dict[str, Any], InterfaceResponse]:
    """Handle list storage strategies operations."""
    from orb.application.services.orchestration.dtos import ListStorageStrategiesInput
    from orb.application.services.orchestration.list_storage_strategies import (
        ListStorageStrategiesOrchestrator,
    )

    container = get_container()
    orchestrator = container.get(ListStorageStrategiesOrchestrator)
    formatter = container.get(ResponseFormattingService)

    result = await orchestrator.execute(ListStorageStrategiesInput())
    return formatter.format_storage_strategy_list(
        result.strategies, result.current_strategy, result.count
    )


@handle_interface_exceptions(context="show_storage_config", interface_type="cli")
async def handle_show_storage_config(
    args: "argparse.Namespace",
) -> Union[dict[str, Any], InterfaceResponse]:
    """Handle show storage configuration operations."""
    from orb.application.services.orchestration.dtos import GetStorageConfigInput
    from orb.application.services.orchestration.get_storage_config import (
        GetStorageConfigOrchestrator,
    )

    container = get_container()
    orchestrator = container.get(GetStorageConfigOrchestrator)
    formatter = container.get(ResponseFormattingService)

    result = await orchestrator.execute(GetStorageConfigInput())
    return formatter.format_storage_config(result.config)


@handle_interface_exceptions(context="validate_storage_config", interface_type="cli")
async def handle_validate_storage_config(args: "argparse.Namespace") -> dict[str, Any]:
    """Handle validate storage configuration operations."""
    from orb.infrastructure.di.buses import QueryBus

    container = get_container()
    query_bus = container.get(QueryBus)

    from orb.application.queries.system import (
        ValidateProviderConfigQuery as ValidateStorageConfigQuery,  # type: ignore[attr-defined]
    )

    query = ValidateStorageConfigQuery()
    validation = await query_bus.execute(query)

    return {
        "validation": validation,
        "message": "Storage configuration validated successfully",
    }


@handle_interface_exceptions(context="test_storage", interface_type="cli")
async def handle_test_storage(args: "argparse.Namespace") -> "Union[dict[str, Any], InterfaceResponse]":
    """Handle test storage operations."""
    from orb.infrastructure.di.buses import QueryBus

    container = get_container()
    query_bus = container.get(QueryBus)
    formatter = container.get(ResponseFormattingService)

    from orb.application.dto.queries import ValidateStorageQuery  # type: ignore[attr-defined]

    query = ValidateStorageQuery()
    result = await query_bus.execute(query)

    raw = result if isinstance(result, dict) else {"test_result": result}
    return formatter.format_storage_test(raw)


@handle_interface_exceptions(context="storage_health", interface_type="cli")
async def handle_storage_health(args: "argparse.Namespace") -> dict[str, Any]:
    """Handle storage health operations."""
    from orb.infrastructure.di.buses import QueryBus

    container = get_container()
    query_bus = container.get(QueryBus)

    from orb.application.queries.storage import GetStorageHealthQuery  # type: ignore[attr-defined]

    query = GetStorageHealthQuery()
    health = await query_bus.execute(query)

    return {"health": health, "message": "Storage health retrieved successfully"}


@handle_interface_exceptions(context="storage_metrics", interface_type="cli")
async def handle_storage_metrics(args: "argparse.Namespace") -> dict[str, Any]:
    """Handle storage metrics operations."""
    from orb.infrastructure.di.buses import QueryBus

    container = get_container()
    query_bus = container.get(QueryBus)

    from orb.application.queries.storage import GetStorageMetricsQuery  # type: ignore[attr-defined]

    query = GetStorageMetricsQuery()
    metrics = await query_bus.execute(query)

    return {"metrics": metrics, "message": "Storage metrics retrieved successfully"}
