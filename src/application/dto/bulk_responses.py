"""Bulk response DTOs for CQRS compliance."""

from typing import List
from pydantic import BaseModel
from application.dto.responses import RequestDTO, MachineDTO
from infrastructure.template.dtos import TemplateDTO


class BulkRequestResponse(BaseModel):
    """Response for bulk request operations."""

    requests: List[RequestDTO]
    found_count: int
    not_found_ids: List[str]
    total_requested: int


class BulkTemplateResponse(BaseModel):
    """Response for bulk template operations."""

    templates: List[TemplateDTO]
    found_count: int
    not_found_ids: List[str]
    total_requested: int


class BulkMachineResponse(BaseModel):
    """Response for bulk machine operations."""

    machines: List[MachineDTO]
    found_count: int
    not_found_ids: List[str]
    total_requested: int
