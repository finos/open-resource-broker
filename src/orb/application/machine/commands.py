"""Machine-related commands for CQRS implementation."""

from typing import Any, Optional

from orb.application.dto.base import BaseCommand


class UpdateMachineStatusCommand(BaseCommand):
    """Command to update machine status."""

    machine_id: str
    status: str
    metadata: dict[str, Any] = {}


class CleanupMachineResourcesCommand(BaseCommand):
    """Command to cleanup machine resources."""

    machine_ids: list[str]
    force_cleanup: bool = False
    metadata: dict[str, Any] = {}



class RegisterMachineCommand(BaseCommand):
    """Command to register a new machine."""

    machine_id: str
    instance_id: str
    template_id: str
    provider_data: dict[str, Any]
    metadata: dict[str, Any] = {}


class DeregisterMachineCommand(BaseCommand):
    """Command to deregister a machine."""

    machine_id: str
    reason: Optional[str] = None
    metadata: dict[str, Any] = {}
