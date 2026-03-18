"""Query DTOs for application layer."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from orb.application.interfaces.command_query import Query


class GetRequestQuery(Query, BaseModel):
    """Query to get request details."""

    model_config = ConfigDict(frozen=True)

    provider_name: Optional[str] = None
    request_id: str
    long: bool = False
    lightweight: bool = False


class ListActiveRequestsQuery(Query, BaseModel):
    """Query to list active requests."""

    model_config = ConfigDict(frozen=True)

    provider_name: Optional[str] = None
    status: Optional[str] = None
    filter_expressions: list[str] = []
    all_resources: bool = False
    limit: Optional[int] = 50  # Default: 50, Max: 1000
    offset: Optional[int] = 0


class ListReturnRequestsQuery(Query, BaseModel):
    """Query to list return requests."""

    model_config = ConfigDict(frozen=True)

    provider_name: Optional[str] = None
    status: Optional[str] = None
    requester_id: Optional[str] = None
    machine_names: list[str] = []
    filter_expressions: list[str] = []
    limit: Optional[int] = 50  # Default: 50, Max: 1000
    offset: Optional[int] = 0


class GetTemplateQuery(Query, BaseModel):
    """Query to get template details."""

    model_config = ConfigDict(frozen=True)

    provider_name: Optional[str] = None
    template_id: str


class ListTemplatesQuery(Query, BaseModel):
    """Query to list available templates."""

    model_config = ConfigDict(frozen=True)

    provider_name: Optional[str] = None
    provider_api: Optional[str] = None
    active_only: bool = True
    filter_expressions: list[str] = []
    limit: Optional[int] = 50  # Default: 50, Max: 1000
    offset: Optional[int] = 0


class ValidateTemplateQuery(Query, BaseModel):
    """Query to validate template configuration."""

    model_config = ConfigDict(frozen=True)

    template_config: dict[str, Any] = {}
    template_id: Optional[str] = None  # For validating loaded templates


class GetMachineQuery(Query, BaseModel):
    """Query to get machine details."""

    model_config = ConfigDict(frozen=True)

    provider_name: Optional[str] = None
    machine_id: str


class ListMachinesQuery(Query, BaseModel):
    """Query to list machines."""

    model_config = ConfigDict(frozen=True)

    provider_name: Optional[str] = None
    request_id: Optional[str] = None
    status: Optional[str] = None
    active_only: bool = False
    filter_expressions: list[str] = []  # Generic filters
    all_resources: bool = False
    timestamp_format: Optional[str] = None
    limit: Optional[int] = 50  # Default: 50, Max: 1000
    offset: Optional[int] = 0


class GetActiveMachineCountQuery(Query, BaseModel):
    """Query to get count of active machines."""

    model_config = ConfigDict(frozen=True)


class GetConfigurationQuery(Query, BaseModel):
    """Query to get configuration value."""

    model_config = ConfigDict(frozen=True)

    key: str
    default: Optional[str] = None


class GetRequestSummaryQuery(Query, BaseModel):
    """Query to get summary of request status."""

    model_config = ConfigDict(frozen=True)

    request_id: str


class GetMachineHealthQuery(Query, BaseModel):
    """Query to get machine health status."""

    model_config = ConfigDict(frozen=True)

    machine_id: str


class ValidateStorageQuery(Query, BaseModel):
    """Query to validate storage connectivity."""

    model_config = ConfigDict(frozen=True)


class ValidateMCPQuery(Query, BaseModel):
    """Query to validate MCP configuration."""

    model_config = ConfigDict(frozen=True)


# Cleanup Queries for CQRS compliance
class ListCleanableRequestsQuery(Query, BaseModel):
    """Query to list requests eligible for cleanup."""

    model_config = ConfigDict(frozen=True)

    older_than_days: int


class ListCleanableResourcesQuery(Query, BaseModel):
    """Query to list resources eligible for cleanup."""

    model_config = ConfigDict(frozen=True)


# Template Result Queries
class GetTemplateValidationResultQuery(Query, BaseModel):
    """Query to get template validation results."""

    model_config = ConfigDict(frozen=True)

    template_id: str
