"""Scheduler command factory for creating scheduler-related queries."""

from typing import Any, Optional

from orb.application.queries.scheduler import (
    GetSchedulerConfigurationQuery,
    ListSchedulerStrategiesQuery,
    ValidateSchedulerConfigurationQuery,
)


class SchedulerCommandFactory:
    """Factory for creating scheduler-related queries."""

    def create_list_scheduler_strategies_query(
        self,
        include_current: bool = True,
        include_details: bool = False,
        filter_expressions: Optional[list] = None,
        **kwargs: Any,
    ) -> ListSchedulerStrategiesQuery:
        """Create query to list scheduler strategies."""
        return ListSchedulerStrategiesQuery(
            include_current=include_current,
            include_details=include_details,
            filter_expressions=filter_expressions or [],
        )

    def create_get_scheduler_configuration_query(
        self, scheduler_name: Optional[str] = None, **kwargs: Any
    ) -> GetSchedulerConfigurationQuery:
        """Create query to get scheduler configuration."""
        return GetSchedulerConfigurationQuery(scheduler_name=scheduler_name)

    def create_validate_scheduler_configuration_query(
        self, scheduler_name: Optional[str] = None, **kwargs: Any
    ) -> ValidateSchedulerConfigurationQuery:
        """Create query to validate scheduler configuration."""
        return ValidateSchedulerConfigurationQuery(scheduler_name=scheduler_name)
