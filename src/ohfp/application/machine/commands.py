"""Machine-related commands for CQRS implementation."""

from typing import Any, Optional

from application.dto.base import BaseCommand


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


class ConvertMachineStatusCommand(BaseCommand):
    """Command to convert provider-specific status to domain status."""

    provider_state: str
    provider_type: str
    metadata: dict[str, Any] = {}


class ConvertBatchMachineStatusCommand(BaseCommand):
    """Command to convert multiple provider states to domain statuses."""

    # List of {'state': str, 'provider_type': str}
    provider_states: list[dict[str, str]]
    metadata: dict[str, Any] = {}


class ValidateProviderStateCommand(BaseCommand):
    """Command to validate provider state."""

    provider_state: str
    provider_type: str
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
