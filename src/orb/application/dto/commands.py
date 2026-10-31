"""Command DTOs for application layer."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict

from orb.application.dto.base import BaseCommand
from orb.application.interfaces.command_query import Command
from orb.domain.request.value_objects import RequestStatus


class CreateRequestCommand(BaseCommand):
    """Command to create a new request.

    CQRS: Commands should not return data. After executing this command,
    use SyncAndGetRequestQuery with the request_id to retrieve the created request.
    """

    request_id: Optional[str] = None
    template_id: str
    requested_count: int
    timeout: Optional[int] = 3600
    tags: Optional[Dict[str, Any]] = None
    additional_data: Optional[Dict[str, Any]] = None

    # Store created request_id for caller to use in subsequent query
    created_request_id: Optional[str] = None


class CreateReturnRequestCommand(BaseCommand):
    """Command to create a return request.

    CQRS: Commands should not return data. After executing this command,
    use GetReturnRequestStatusQuery to retrieve the operation results.
    """

    machine_ids: list[str]
    timeout: Optional[int] = 3600
    force_return: Optional[bool] = False

    # Store results for caller to access after command execution
    created_request_ids: Optional[list[str]] = None
    processed_machines: Optional[list[str]] = None
    skipped_machines: Optional[list[dict[str, Any]]] = None
    # Return requests whose deprovisioning failed and whose failed status could
    # not be recorded either; they would otherwise look like accepted terminations.
    unrecorded_failure_request_ids: list[str] = []


class UpdateRequestStatusCommand(Command, BaseModel):
    """Command to update request status."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    status: RequestStatus
    message: Optional[str] = None


class CancelRequestCommand(BaseCommand):
    """Command to cancel a request."""

    request_id: str
    reason: str

    # Store results for caller to access after command execution
    cancelled: bool = False
    final_status: Optional[str] = None


class SyncRequestCommand(Command, BaseModel):
    """Command to sync request with provider state."""

    model_config = ConfigDict(frozen=True)

    request_id: str


class CompleteRequestCommand(Command, BaseModel):
    """Command to mark a request as completed."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    result_data: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


class PopulateMachineIdsCommand(Command, BaseModel):
    """Command to populate request with machine IDs from resources."""

    model_config = ConfigDict(frozen=True)

    request_id: str
