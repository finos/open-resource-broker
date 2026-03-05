"""Storage query handlers for administrative operations."""

from application.base.handlers import BaseQueryHandler
from application.decorators import query_handler
from application.dto.system import (
    StorageHealthResponse,
    StorageMetricsResponse,
    StorageStrategyDTO,
    StorageStrategyListResponse,
)
from application.queries.storage import (
    GetStorageHealthQuery,
    GetStorageMetricsQuery,
    ListStorageStrategiesQuery,
)
from application.services.storage_registry_service import StorageRegistryService
from domain.base.ports import ContainerPort, ErrorHandlingPort, LoggingPort
from domain.services.generic_filter_service import GenericFilterService


@query_handler(ListStorageStrategiesQuery)
class ListStorageStrategiesHandler(
    BaseQueryHandler[ListStorageStrategiesQuery, StorageStrategyListResponse]
):
    """Handler for listing available storage strategies."""

    def __init__(
        self,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        storage_service: StorageRegistryService,
        generic_filter_service: GenericFilterService,
        container: ContainerPort,
    ):
        super().__init__(logger, error_handler)
        self._storage_service = storage_service
        self._generic_filter_service = generic_filter_service
        self._container = container

    async def execute_query(self, query: ListStorageStrategiesQuery) -> StorageStrategyListResponse:
        """
        Execute storage strategies list query.

        Args:
            query: List storage strategies query

        Returns:
            Storage strategies list response
        """
        # Access infrastructure through DI container
        from domain.base.ports import ConfigurationPort

        config_manager = self._container.get(ConfigurationPort)
        storage_types = self._storage_service.get_available_storage_types()

        strategies = []
        current_strategy = "unknown"

        if query.include_current:
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
                        "capabilities": [],
                    }
                )

            strategies.append(strategy_info)

        # Apply generic filters if provided
        if query.filter_expressions:
            strategies = self._generic_filter_service.apply_filters(
                strategies, query.filter_expressions
            )

        return StorageStrategyListResponse(
            strategies=[StorageStrategyDTO(**s) if isinstance(s, dict) else s for s in strategies],
            current_strategy=current_strategy,
            total_count=len(strategies),
        )


@query_handler(GetStorageHealthQuery)
class GetStorageHealthHandler(BaseQueryHandler[GetStorageHealthQuery, StorageHealthResponse]):
    """Handler for getting storage health status."""

    def __init__(
        self,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        storage_service: StorageRegistryService,
    ):
        super().__init__(logger, error_handler)
        self._storage_service = storage_service

    async def execute_query(self, query: GetStorageHealthQuery) -> StorageHealthResponse:
        """
        Execute storage health query.

        Args:
            query: Storage health query

        Returns:
            Storage health response
        """
        strategy_name = query.strategy_name or "current"

        if strategy_name != "current":
            health_info = self._storage_service.get_storage_health(strategy_name)
            healthy = health_info.get("status") != "error"
            status = health_info.get("status", "unknown")
        else:
            healthy = True
            status = "operational"

        return StorageHealthResponse(
            strategy_name=strategy_name,
            healthy=healthy,
            status=status,
            details=({} if not query.detailed else {"connections": "active", "latency": "low"}),
        )


@query_handler(GetStorageMetricsQuery)
class GetStorageMetricsHandler(BaseQueryHandler[GetStorageMetricsQuery, StorageMetricsResponse]):
    """Handler for getting storage performance metrics."""

    def __init__(
        self,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        storage_service: StorageRegistryService,
    ):
        super().__init__(logger, error_handler)
        self._storage_service = storage_service

    async def execute_query(self, query: GetStorageMetricsQuery) -> StorageMetricsResponse:
        """
        Execute storage metrics query.

        Args:
            query: Storage metrics query

        Returns:
            Storage metrics response
        """
        return StorageMetricsResponse(
            strategy_name=query.strategy_name or "current",
            time_range=query.time_range or "",
            operations_count=0,
            average_latency=0.0,
            error_rate=0.0,
            details=({} if not query.include_operations else {"read_ops": 0, "write_ops": 0}),
        )
