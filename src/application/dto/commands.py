"""Command DTOs for application layer."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from application.dto.base import BaseCommand
from application.interfaces.command_query import Command
from domain.request.value_objects import RequestStatus


class CreateRequestCommand(BaseCommand):
    """Command to create a new request."""

    template_id: str
    requested_count: int
    timeout: Optional[int] = 3600
    tags: Optional[Dict[str, Any]] = None


class CreateReturnRequestCommand(BaseCommand):
    """Command to create a return request."""

    machine_ids: List[str]
    timeout: Optional[int] = 3600
    force_return: Optional[bool] = False


class UpdateRequestStatusCommand(Command, BaseModel):
    """Command to update request status."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    status: RequestStatus
    message: Optional[str] = None


class CancelRequestCommand(Command, BaseModel):
    """Command to cancel a request."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    reason: str


class CleanupOldRequestsCommand(Command, BaseModel):
    """Command to clean up old requests."""

    model_config = ConfigDict(frozen=True)

    age_hours: int = 24


class CleanupTerminatedMachinesCommand(Command, BaseModel):
    """Command to clean up terminated machines."""

    model_config = ConfigDict(frozen=True)

    age_hours: int = 24


class CleanupAllResourcesCommand(Command, BaseModel):
    """Command to clean up all resources."""

    model_config = ConfigDict(frozen=True)


class CompleteRequestCommand(Command, BaseModel):
    """Command to mark a request as completed."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    result_data: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
