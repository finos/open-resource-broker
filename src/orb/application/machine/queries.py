"""Machine-related queries for CQRS implementation."""

from typing import Optional

from pydantic import Field

from orb.application.dto.base import BaseQuery


class ListMachinesQuery(BaseQuery):
    """Query to list machines with optional filtering."""

    provider_name: Optional[str] = None
    template_id: Optional[str] = None
    status: Optional[str] = None
    request_id: Optional[str] = None
    filter_expressions: list[str] = Field(default_factory=list)  # Raw filter expressions from CLI
    timestamp_format: Optional[str] = None
    limit: int = 50
    offset: int = 0
    all_resources: bool = False
