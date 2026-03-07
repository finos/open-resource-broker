"""Storage command factory for creating storage-related queries."""

from typing import Any, Optional

from orb.application.queries.storage import (
    GetStorageHealthQuery,
    GetStorageMetricsQuery,
    ListStorageStrategiesQuery,
)


class StorageCommandFactory:
    """Factory for creating storage-related queries."""

    def create_list_storage_strategies_query(
        self,
        include_current: bool = True,
        include_details: bool = False,
        filter_expressions: Optional[list] = None,
        **kwargs: Any,
    ) -> ListStorageStrategiesQuery:
        """Create query to list storage strategies."""
        return ListStorageStrategiesQuery(
            include_current=include_current,
            include_details=include_details,
            filter_expressions=filter_expressions or [],
        )

    def create_get_storage_health_query(
        self,
        strategy_name: Optional[str] = None,
        detailed: bool = False,
        **kwargs: Any,
    ) -> GetStorageHealthQuery:
        """Create query to get storage health."""
        return GetStorageHealthQuery(strategy_name=strategy_name, detailed=detailed)

    def create_get_storage_metrics_query(
        self,
        strategy_name: Optional[str] = None,
        time_range: str = "1h",
        include_operations: bool = True,
        **kwargs: Any,
    ) -> GetStorageMetricsQuery:
        """Create query to get storage metrics."""
        return GetStorageMetricsQuery(
            strategy_name=strategy_name,
            time_range=time_range,
            include_operations=include_operations,
        )
