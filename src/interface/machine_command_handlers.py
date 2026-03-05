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
            "message": "Use either --all or specific IDs, not both",
        }

    if has_all:
        from application.dto.queries import ListMachinesQuery
        from application.machine.dto import MachineDTO

        query = ListMachinesQuery(all_resources=True)
        machine_dicts = await query_bus.execute(query)

        # Convert dictionaries back to MachineDTO objects for scheduler formatting
        machine_dtos = []
        for machine_dict in machine_dicts:
            machine_dto = MachineDTO(**machine_dict)
            machine_dtos.append(machine_dto)

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


@handle_interface_exceptions(context="list_machines", interface_type="cli")
async def handle_list_machines(args: "argparse.Namespace") -> dict[str, Any]:
    """
    Handle list machines operations with scheduler-aware formatting.

    Args:
        args: Argument namespace with filtering options

    Returns:
        Machines list formatted for scheduler compatibility
    """
    container = get_container()
    query_bus = container.get(QueryBus)
    scheduler_strategy = container.get(SchedulerPort)

    from application.dto.queries import ListMachinesQuery
    from application.dto.responses import MachineDTO

    # Create query with filters from args
    query = ListMachinesQuery(
        provider_name=getattr(args, "provider", None),
        status=getattr(args, "status", None),
        request_id=getattr(args, "request_id", None) or getattr(args, "template_id", None),
    )

    # Execute query to get machine dictionaries
    machine_dicts = await query_bus.execute(query)

    # Convert dictionaries back to MachineDTO objects for scheduler formatting
    machine_dtos = []
    for machine_dict in machine_dicts:
        # Create MachineDTO from dictionary
        machine_dto = MachineDTO(**machine_dict)
        machine_dtos.append(machine_dto)

    # Format response using scheduler strategy for proper field mapping
    return scheduler_strategy.format_machine_status_response(machine_dtos)


@handle_interface_exceptions(context="stop_machines", interface_type="cli")
async def handle_stop_machines(args: "argparse.Namespace") -> dict[str, Any]:
    """
    Handle stop machines operations.

    Args:
        args: Argument namespace with machine_ids, all, and force flags

    Returns:
        Stop operation results
    """
    # Validation: --all requires --force
    has_all = getattr(args, "all", False)
    has_force = getattr(args, "force", False)
    machine_ids_from_args = getattr(args, "machine_ids", []) or []

    if has_all and not has_force:
        return {
            "error": "Cannot use --all without --force flag",
            "message": "Use --force with --all to confirm stopping all machines",
        }

    # Validation: Cannot use both --all and specific IDs
    if has_all and machine_ids_from_args:
        return {
            "error": "Cannot use --all with specific machine IDs",
            "message": "Use either --all or specific IDs, not both",
        }

    # Validation: Must specify either --all or specific IDs
    if not has_all and not machine_ids_from_args:
        return {
            "error": "No machines specified",
            "message": "Specify machine IDs or use --all --force",
        }

    container = get_container()
    query_bus = container.get(QueryBus)

    # Get machines to stop
    if has_all:
        from application.dto.queries import ListMachinesQuery

        query = ListMachinesQuery(status="running")
        machine_dtos = await query_bus.execute(query)
        machine_ids = [machine["machine_id"] for machine in machine_dtos]
    else:
        machine_ids = machine_ids_from_args

    if not machine_ids:
        return {
            "success": True,
            "message": "No machines to stop",
            "stopped_machines": [],
        }

    # Stop machines using AWS instance manager
    from providers.aws.managers.aws_instance_manager import AWSInstanceManager

    instance_manager = container.get(AWSInstanceManager)
    stop_results = instance_manager.stop_instances(machine_ids)

    # Update machine status to "stopping" for successfully stopped machines
    from application.machine.commands import UpdateMachineStatusCommand
    from infrastructure.di.buses import CommandBus

    command_bus = container.get(CommandBus)

    stopped_machines = []
    failed_machines = []

    for machine_id, success in stop_results.items():
        if success:
            # Update status to stopping
            command = UpdateMachineStatusCommand(machine_id=machine_id, status="stopping")
            await command_bus.execute(command)  # type: ignore[arg-type]
            stopped_machines.append(machine_id)
        else:
            failed_machines.append(machine_id)

    return {
        "success": len(failed_machines) == 0,
        "message": f"Stopped {len(stopped_machines)} machines"
        + (f", failed to stop {len(failed_machines)}" if failed_machines else ""),
        "stopped_machines": stopped_machines,
        "failed_machines": failed_machines,
    }


@handle_interface_exceptions(context="start_machines", interface_type="cli")
async def handle_start_machines(args: "argparse.Namespace") -> dict[str, Any]:
    """
    Handle start machines operations.

    Args:
        args: Argument namespace with machine_ids and all flags

    Returns:
        Start operation results
    """
    # Validation: Cannot use both --all and specific IDs
    has_all = getattr(args, "all", False)
    machine_ids_from_args = getattr(args, "machine_ids", []) or []

    if has_all and machine_ids_from_args:
        return {
            "error": "Cannot use --all with specific machine IDs",
            "message": "Use either --all or specific IDs, not both",
        }

    # Validation: Must specify either --all or specific IDs
    if not has_all and not machine_ids_from_args:
        return {
            "error": "No machines specified",
            "message": "Specify machine IDs or use --all",
        }

    container = get_container()
    query_bus = container.get(QueryBus)

    # Get machines to start
    if has_all:
        from application.dto.queries import ListMachinesQuery

        query = ListMachinesQuery(status="stopped")
        machine_dtos = await query_bus.execute(query)
        machine_ids = [machine["machine_id"] for machine in machine_dtos]
    else:
        machine_ids = machine_ids_from_args

    if not machine_ids:
        return {
            "success": True,
            "message": "No machines to start",
            "started_machines": [],
        }

    # Start machines using AWS instance manager
    from providers.aws.managers.aws_instance_manager import AWSInstanceManager

    instance_manager = container.get(AWSInstanceManager)
    start_results = instance_manager.start_instances(machine_ids)

    # Update machine status to "pending" for successfully started machines
    from application.machine.commands import UpdateMachineStatusCommand
    from infrastructure.di.buses import CommandBus

    command_bus = container.get(CommandBus)

    started_machines = []
    failed_machines = []

    for machine_id, success in start_results.items():
        if success:
            # Update status to pending (starting)
            command = UpdateMachineStatusCommand(machine_id=machine_id, status="pending")
            await command_bus.execute(command)  # type: ignore[arg-type]
            started_machines.append(machine_id)
        else:
            failed_machines.append(machine_id)

    return {
        "success": len(failed_machines) == 0,
        "message": f"Started {len(started_machines)} machines"
        + (f", failed to start {len(failed_machines)}" if failed_machines else ""),
        "started_machines": started_machines,
        "failed_machines": failed_machines,
    }
