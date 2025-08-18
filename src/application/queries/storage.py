"""Storage-related queries for administrative operations."""

from typing import Optional

from pydantic import BaseModel, ConfigDict

from application.interfaces.command_query import Query


class ListStorageStrategiesQuery(Query, BaseModel):
    """Query to list available storage strategies."""

    model_config = ConfigDict(frozen=True)

    include_current: bool = True
    include_details: bool = False


class GetStorageHealthQuery(Query, BaseModel):
    """Query to get storage health status."""

    model_config = ConfigDict(frozen=True)

    strategy_name: Optional[str] = None
    detailed: bool = False


class GetStorageMetricsQuery(Query, BaseModel):
    """Query to get storage performance metrics."""

    model_config = ConfigDict(frozen=True)

    strategy_name: Optional[str] = None
    time_range: Optional[str] = "1h"
    include_operations: bool = True
