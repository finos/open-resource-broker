"""Machine-related commands for CQRS implementation."""

from typing import Any, Dict, List, Optional

from application.dto.base import BaseCommand


class UpdateMachineStatusCommand(BaseCommand):
    """Command to update machine status."""

    machine_id: str
    status: str
    metadata: Dict[str, Any] = {}


class CleanupMachineResourcesCommand(BaseCommand):
    """Command to cleanup machine resources."""

    machine_ids: List[str]
    force_cleanup: bool = False
    metadata: Dict[str, Any] = {}


class ConvertMachineStatusCommand(BaseCommand):
    """Command to convert provider-specific status to domain status."""

    provider_state: str
    provider_type: str
    metadata: Dict[str, Any] = {}


class ConvertBatchMachineStatusCommand(BaseCommand):
    """Command to convert multiple provider states to domain statuses."""

    # List of {'state': str, 'provider_type': str}
    provider_states: List[Dict[str, str]]
    metadata: Dict[str, Any] = {}


class ValidateProviderStateCommand(BaseCommand):
    """Command to validate provider state."""

    provider_state: str
    provider_type: str
    metadata: Dict[str, Any] = {}


class RegisterMachineCommand(BaseCommand):
    """Command to register a new machine."""

    machine_id: str
    instance_id: str
    template_id: str
    provider_data: Dict[str, Any]
    metadata: Dict[str, Any] = {}


class DeregisterMachineCommand(BaseCommand):
    """Command to deregister a machine."""

    machine_id: str
    reason: Optional[str] = None
    metadata: Dict[str, Any] = {}
