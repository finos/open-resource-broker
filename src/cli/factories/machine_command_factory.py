"""Machine command factory for creating machine-related commands and queries."""

from typing import Any, Optional

from application.dto.queries import GetMachineQuery
from application.dto.bulk_queries import GetMultipleMachinesQuery
from application.machine.queries import ListMachinesQuery
from application.machine.commands import UpdateMachineStatusCommand


class MachineCommandFactory:
    """Factory for creating machine-related commands and queries."""

    def create_list_machines_query(
        self,
        provider: Optional[str] = None,
        status: Optional[str] = None,
        request_id: Optional[str] = None,
        **kwargs: Any,
    ) -> ListMachinesQuery:
        """Create query to list machines."""
        provider_name = kwargs.get("provider_name") or provider
        return ListMachinesQuery(
            provider_name=provider_name,
            status=status,
            request_id=request_id,
        )

    def create_get_machine_query(
        self, machine_id: str, **kwargs: Any
    ) -> GetMachineQuery:
        """Create query to get machine by ID."""
        return GetMachineQuery(machine_id=machine_id)

    def create_update_machine_status_command(
        self, machine_id: str, status: str, **kwargs: Any
    ) -> UpdateMachineStatusCommand:
        """Create command to update machine status."""
        return UpdateMachineStatusCommand(machine_id=machine_id, status=status)

    def create_get_multiple_machines_query(
        self,
        machine_ids: list[str],
        provider_name: Optional[str] = None,
        include_requests: bool = True,
        **kwargs: Any,
    ) -> GetMultipleMachinesQuery:
        """Create query to get multiple machines by IDs."""
        return GetMultipleMachinesQuery(
            machine_ids=machine_ids, provider_name=provider_name, include_requests=include_requests
        )