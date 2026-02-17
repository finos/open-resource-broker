"""Machine-related command handlers for the interface layer."""

from typing import TYPE_CHECKING, Any

from domain.base.ports.scheduler_port import SchedulerPort
from infrastructure.di.buses import QueryBus
from infrastructure.di.container import get_container
from infrastructure.error.decorators import handle_interface_exceptions

if TYPE_CHECKING:
    import argparse


@handle_interface_exceptions(context="get_machine_status", interface_type="cli")
async def handle_get_machine_status(args: "argparse.Namespace") -> dict[str, Any]:
    """
    Handle get machine status operations for multiple machine IDs.

    Args:
        args: Argument namespace with machine_ids

    Returns:
        Machine status information for all requested machines
    """
    container = get_container()
    query_bus = container.get(QueryBus)
    scheduler_strategy = container.get(SchedulerPort)

    # Validation: Prevent --all with specific IDs
    has_all = getattr(args, "all", False)
    machine_ids_from_args = []
    
    # Handle positional arguments
    if hasattr(args, "machine_ids") and args.machine_ids:
        machine_ids_from_args.extend(args.machine_ids)
    
    # Handle flag arguments
    if hasattr(args, "flag_machine_ids") and args.flag_machine_ids:
        machine_ids_from_args.extend(args.flag_machine_ids)

    has_specific_ids = bool(machine_ids_from_args)
    
    if has_all and has_specific_ids:
        return {
            "error": "Cannot use --all with specific machine IDs",
            "message": "Use either --all or specific IDs, not both"
        }

    if has_all:
        from application.dto.queries import ListMachinesQuery
        
        query = ListMachinesQuery(all_resources=True)
        machine_dtos = await query_bus.execute(query)
        
        return scheduler_strategy.format_machine_status_response(machine_dtos)
    else:
        if not machine_ids_from_args:
            return {"error": "No machine IDs provided", "message": "Machine IDs are required"}

        # Query each machine individually and collect results
        machine_dtos = []
        
        from application.dto.queries import GetMachineQuery

        for machine_id in machine_ids_from_args:
            try:
                query = GetMachineQuery(machine_id=machine_id)
                machine_dto = await query_bus.execute(query)
                if machine_dto:
                    machine_dtos.append(machine_dto)
            except Exception:
                # Continue with other machines if one fails
                continue

        # Format response using scheduler strategy
        return scheduler_strategy.format_machine_status_response(machine_dtos)