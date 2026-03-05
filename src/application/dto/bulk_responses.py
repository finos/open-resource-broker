"""Bulk response DTOs for CQRS compliance."""

from typing import Any, List

from pydantic import BaseModel

from application.dto.responses import MachineDTO, RequestDTO


class BulkRequestResponse(BaseModel):
    """Response for bulk request operations."""

    requests: List[RequestDTO]
    found_count: int
    not_found_ids: List[str]
    total_requested: int


class BulkTemplateResponse(BaseModel):
    """Response for bulk template operations."""

    templates: List[Any]
    found_count: int
    not_found_ids: List[str]
    total_requested: int


class BulkMachineResponse(BaseModel):
    """Response for bulk machine operations."""

    machines: List[MachineDTO]
    found_count: int
    not_found_ids: List[str]
    total_requested: int
