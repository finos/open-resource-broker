"""Storage query handlers for administrative operations."""

from application.base.handlers import BaseQueryHandler
from application.decorators import query_handler
from application.dto.system import (
    StorageHealthResponse,
    StorageMetricsResponse,
    StorageStrategyListResponse,
)
from application.queries.storage import (
    GetStorageHealthQuery,
    GetStorageMetricsQuery,
    ListStorageStrategiesQuery,
)


@query_handler(ListStorageStrategiesQuery)
class ListStorageStrategiesHandler(
    BaseQueryHandler[ListStorageStrategiesQuery, StorageStrategyListResponse]
):
    """Handler for listing available storage strategies."""

    async def execute_query(self, query: ListStorageStrategiesQuery) -> StorageStrategyListResponse:
        """
        Execute storage strategies list query.

        Args:
            query: List storage strategies query

        Returns:
            Storage strategies list response
        """
        # Access infrastructure through application layer
        from config.manager import get_config_manager
        from infrastructure.registry.storage_registry import get_storage_registry

        registry = get_storage_registry()
        storage_types = registry.get_registered_types()

        strategies = []
        current_strategy = "unknown"

        if query.include_current:
            config_manager = get_config_manager()
            current_strategy = config_manager.get("storage.strategy", "unknown")

        for storage_type in storage_types:
            strategy_info = {
                "name": storage_type,
                "active": (storage_type == current_strategy if query.include_current else False),
                "registered": True,
            }

            if query.include_details:
                # Add additional details if requested
                strategy_info.update(
                    {
                        "description": f"{storage_type} storage strategy",
                        "capabilities": (
                            registry.get_strategy_capabilities(storage_type)
                            if hasattr(registry, "get_strategy_capabilities")
                            else []
                        ),
                    }
                )

            strategies.append(strategy_info)

        return StorageStrategyListResponse(
            strategies=strategies,
            current_strategy=current_strategy,
            total_count=len(strategies),
        )


@query_handler(GetStorageHealthQuery)
class GetStorageHealthHandler(BaseQueryHandler[GetStorageHealthQuery, StorageHealthResponse]):
    """Handler for getting storage health status."""

    async def execute_query(self, query: GetStorageHealthQuery) -> StorageHealthResponse:
        """
        Execute storage health query.

        Args:
            query: Storage health query

        Returns:
            Storage health response
        """
        # Implementation would check storage health
        # This is a placeholder for the actual health check logic
        return StorageHealthResponse(
            strategy_name=query.strategy_name or "current",
            healthy=True,
            status="operational",
            details=({} if not query.detailed else {"connections": "active", "latency": "low"}),
        )


@query_handler(GetStorageMetricsQuery)
class GetStorageMetricsHandler(BaseQueryHandler[GetStorageMetricsQuery, StorageMetricsResponse]):
    """Handler for getting storage performance metrics."""

    async def execute_query(self, query: GetStorageMetricsQuery) -> StorageMetricsResponse:
        """
        Execute storage metrics query.

        Args:
            query: Storage metrics query

        Returns:
            Storage metrics response
        """
        # Implementation would collect storage metrics
        # This is a placeholder for the actual metrics collection logic
        return StorageMetricsResponse(
            strategy_name=query.strategy_name or "current",
            time_range=query.time_range,
            operations_count=0,
            average_latency=0.0,
            error_rate=0.0,
            details=({} if not query.include_operations else {"read_ops": 0, "write_ops": 0}),
        )
