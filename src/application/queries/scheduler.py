"""Scheduler-related queries for administrative operations."""

from typing import Optional

from pydantic import BaseModel, ConfigDict

from application.interfaces.command_query import Query


class ListSchedulerStrategiesQuery(Query, BaseModel):
    """Query to list available scheduler strategies."""

    model_config = ConfigDict(frozen=True)

    include_current: bool = True
    include_details: bool = False


class GetSchedulerConfigurationQuery(Query, BaseModel):
    """Query to get scheduler configuration."""

    model_config = ConfigDict(frozen=True)

    scheduler_name: Optional[str] = None


class ValidateSchedulerConfigurationQuery(Query, BaseModel):
    """Query to validate scheduler configuration."""

    model_config = ConfigDict(frozen=True)

    scheduler_name: Optional[str] = None
