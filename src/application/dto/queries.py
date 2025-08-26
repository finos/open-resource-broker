"""Query DTOs for application layer."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

from application.interfaces.command_query import Query


class GetRequestQuery(Query, BaseModel):
    """Query to get request details."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    long: bool = False


class GetRequestStatusQuery(Query, BaseModel):
    """Query to get request status."""

    model_config = ConfigDict(frozen=True)

    request_id: str


class ListActiveRequestsQuery(Query, BaseModel):
    """Query to list active requests."""

    model_config = ConfigDict(frozen=True)


class ListReturnRequestsQuery(Query, BaseModel):
    """Query to list return requests."""

    model_config = ConfigDict(frozen=True)

    status: Optional[str] = None
    requester_id: Optional[str] = None


class GetTemplateQuery(Query, BaseModel):
    """Query to get template details."""

    model_config = ConfigDict(frozen=True)

    template_id: str


class ListTemplatesQuery(Query, BaseModel):
    """Query to list available templates."""

    model_config = ConfigDict(frozen=True)

    provider_api: Optional[str] = None
    active_only: bool = True
    include_configuration: bool = False


class ValidateTemplateQuery(Query, BaseModel):
    """Query to validate template configuration."""

    model_config = ConfigDict(frozen=True)

    template_config: dict


class GetMachineQuery(Query, BaseModel):
    """Query to get machine details."""

    model_config = ConfigDict(frozen=True)

    machine_id: str


class ListMachinesQuery(Query, BaseModel):
    """Query to list machines."""

    model_config = ConfigDict(frozen=True)

    request_id: Optional[str] = None
    status: Optional[str] = None
    active_only: bool = False


class GetActiveMachineCountQuery(Query, BaseModel):
    """Query to get count of active machines."""

    model_config = ConfigDict(frozen=True)


class GetRequestSummaryQuery(Query, BaseModel):
    """Query to get summary of request status."""

    model_config = ConfigDict(frozen=True)

    request_id: str


class GetMachineHealthQuery(Query, BaseModel):
    """Query to get machine health status."""

    model_config = ConfigDict(frozen=True)

    machine_id: str
