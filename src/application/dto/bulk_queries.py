"""Bulk query DTOs for CQRS compliance."""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from application.interfaces.command_query import Query


class GetMultipleRequestsQuery(Query, BaseModel):
    """Query to get multiple requests by IDs."""

    model_config = ConfigDict(frozen=True)

    provider_name: Optional[str] = None
    request_ids: List[str]
    lightweight: bool = False
    include_machines: bool = True


class GetMultipleTemplatesQuery(Query, BaseModel):
    """Query to get multiple templates by IDs."""

    model_config = ConfigDict(frozen=True)

    provider_name: Optional[str] = None
    template_ids: List[str]
    active_only: bool = True


class GetMultipleMachinesQuery(Query, BaseModel):
    """Query to get multiple machines by IDs."""

    model_config = ConfigDict(frozen=True)

    provider_name: Optional[str] = None
    machine_ids: List[str]
    include_requests: bool = True
