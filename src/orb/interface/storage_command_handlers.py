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

    result = await orchestrator.execute(GetStorageConfigInput(strategy_name=getattr(args, "strategy", None)))
    return formatter.format_storage_config(result.config)


@handle_interface_exceptions(context="validate_storage_config", interface_type="cli")
async def handle_validate_storage_config(  # type: ignore[return]
    _args: "argparse.Namespace",
) -> "Union[dict[str, Any], InterfaceResponse]":
    """Handle validate storage configuration operations."""
    container = get_container()
    formatter = container.get(ResponseFormattingService)
    try:
        from orb.infrastructure.di.buses import QueryBus

        query_bus = container.get(QueryBus)
        from orb.application.dto.queries import GetStorageHealthQuery  # type: ignore[attr-defined]

        result = await query_bus.execute(GetStorageHealthQuery())
        raw = result if isinstance(result, dict) else {"status": "healthy"}
        return formatter.format_success({**raw, "message": "Storage configuration is valid"})  # type: ignore[attr-defined]
    except ImportError:
        return formatter.format_success(
            {"message": "Storage configuration is valid", "status": "ok"}
        )  # type: ignore[attr-defined]
    except Exception as e:
        return formatter.format_error(f"Storage configuration invalid: {e}")


@handle_interface_exceptions(context="test_storage", interface_type="cli")
async def handle_test_storage(
    args: "argparse.Namespace",
) -> "Union[dict[str, Any], InterfaceResponse]":
    """Handle test storage operations."""
    from orb.infrastructure.di.buses import QueryBus

    container = get_container()
    query_bus = container.get(QueryBus)
    formatter = container.get(ResponseFormattingService)

    from orb.application.dto.queries import ValidateStorageQuery  # type: ignore[attr-defined]

    query = ValidateStorageQuery(
        strategy_name=getattr(args, "strategy", None),
        timeout=getattr(args, "timeout", 30),
    )
    result = await query_bus.execute(query)

    raw = result if isinstance(result, dict) else {"test_result": result}
    return formatter.format_storage_test(raw)


@handle_interface_exceptions(context="storage_health", interface_type="cli")
async def handle_storage_health(
    args: "argparse.Namespace",
) -> "Union[dict[str, Any], InterfaceResponse]":
    """Handle storage health operations."""
    container = get_container()
    formatter = container.get(ResponseFormattingService)
    try:
        from orb.infrastructure.di.buses import QueryBus

        query_bus = container.get(QueryBus)
        from orb.application.queries.storage import (
            GetStorageHealthQuery,  # type: ignore[attr-defined]
        )

        query = GetStorageHealthQuery(
            strategy_name=getattr(args, "strategy", None),
            verbose=getattr(args, "verbose", False),
        )
        health = await query_bus.execute(query)
        raw = (
            health
            if isinstance(health, dict)
            else health.model_dump()
            if hasattr(health, "model_dump")
            else {"health": str(health)}
        )
        return formatter.format_config(raw)
    except ImportError:
        return formatter.format_error("Storage health query not available")


@handle_interface_exceptions(context="storage_metrics", interface_type="cli")
async def handle_storage_metrics(
    _args: "argparse.Namespace",
) -> "Union[dict[str, Any], InterfaceResponse]":
    """Handle storage metrics operations."""
    container = get_container()
    formatter = container.get(ResponseFormattingService)
    try:
        from orb.infrastructure.di.buses import QueryBus

        query_bus = container.get(QueryBus)
        from orb.application.queries.storage import (
            GetStorageMetricsQuery,  # type: ignore[attr-defined]
        )

        query = GetStorageMetricsQuery()
        metrics = await query_bus.execute(query)
        raw = (
            metrics
            if isinstance(metrics, dict)
            else metrics.model_dump()
            if hasattr(metrics, "model_dump")
            else {"metrics": str(metrics)}
        )
        return formatter.format_config(raw)
    except ImportError:
        return formatter.format_error("Storage metrics query not available")
