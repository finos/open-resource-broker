"""Storage command factory for creating storage-related queries."""

from typing import Any, Optional

from application.queries.system import (
    ListStorageStrategiesQuery,
    GetStorageHealthQuery,
    GetStorageMetricsQuery,
)


class StorageCommandFactory:
    """Factory for creating storage-related queries."""

    def create_list_storage_strategies_query(
        self,
        include_health: bool = False,
        include_capabilities: bool = False,
        include_metrics: bool = False,
        filter_expressions: Optional[list] = None,
        **kwargs: Any,
    ) -> ListStorageStrategiesQuery:
        """Create query to list storage strategies."""
        return ListStorageStrategiesQuery(
            include_health=include_health,
            include_capabilities=include_capabilities,
            include_metrics=include_metrics,
            filter_expressions=filter_expressions or [],
        )

    def create_get_storage_health_query(
        self,
        storage_name: Optional[str] = None,
        include_details: bool = True,
        **kwargs: Any,
    ) -> GetStorageHealthQuery:
        """Create query to get storage health."""
        return GetStorageHealthQuery(
            storage_name=storage_name, include_details=include_details
        )

    def create_get_storage_metrics_query(
        self,
        storage_name: Optional[str] = None,
        timeframe: str = "1h",
        detailed: bool = False,
        **kwargs: Any,
    ) -> GetStorageMetricsQuery:
        """Create query to get storage metrics."""
        return GetStorageMetricsQuery(
            storage_name=storage_name, timeframe=timeframe, detailed=detailed
        )