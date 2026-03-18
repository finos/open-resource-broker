"""Machine-related command handlers for the interface layer."""

import asyncio
from typing import TYPE_CHECKING, Any

from orb.application.ports.scheduler_port import SchedulerPort
from orb.infrastructure.di.container import get_container
from orb.infrastructure.error.decorators import handle_interface_exceptions

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
    from orb.application.services.orchestration.dtos import GetMachineInput, ListMachinesInput
    from orb.application.services.orchestration.get_machine import GetMachineOrchestrator
    from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator

    container = get_container()
    scheduler = container.get(SchedulerPort)

    has_all = getattr(args, "all", False)
    machine_ids_from_args = []

    if hasattr(args, "machine_ids") and args.machine_ids:
        machine_ids_from_args.extend(args.machine_ids)
    if hasattr(args, "flag_machine_ids") and args.flag_machine_ids:
        machine_ids_from_args.extend(args.flag_machine_ids)

    has_specific_ids = bool(machine_ids_from_args)

    if has_all and has_specific_ids:
        return {
            "error": "Cannot use --all with specific machine IDs",
            "message": "Use either --all or specific IDs, not both",
        }

    if has_all:
        orchestrator = container.get(ListMachinesOrchestrator)
        result = await orchestrator.execute(ListMachinesInput())
        return scheduler.format_machine_status_response(result.machines)

    if not machine_ids_from_args:
        return {"error": "No machine IDs provided", "message": "Machine IDs are required"}

    orchestrator = container.get(GetMachineOrchestrator)
    results = await asyncio.gather(
        *[orchestrator.execute(GetMachineInput(machine_id=mid)) for mid in machine_ids_from_args],
        return_exceptions=True,
    )
    machine_dtos = [
        r.machine for r in results if not isinstance(r, BaseException) and r.machine is not None
    ]

    return scheduler.format_machine_status_response(machine_dtos)


@handle_interface_exceptions(context="list_machines", interface_type="cli")
async def handle_list_machines(args: "argparse.Namespace") -> dict[str, Any]:
    """
    Handle list machines operations with scheduler-aware formatting.

    Args:
        args: Argument namespace with filtering options

    Returns:
        Machines list formatted for scheduler compatibility
    """
    from orb.application.services.orchestration.dtos import ListMachinesInput
    from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator

    container = get_container()
    orchestrator = container.get(ListMachinesOrchestrator)
    scheduler = container.get(SchedulerPort)

    result = await orchestrator.execute(
        ListMachinesInput(
            status=getattr(args, "status", None),
            provider_name=getattr(args, "provider", None),
            request_id=getattr(args, "request_id", None) or getattr(args, "template_id", None),
        )
    )
    return scheduler.format_machine_status_response(result.machines)


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

    from orb.application.services.orchestration.dtos import StopMachinesInput
    from orb.application.services.orchestration.stop_machines import StopMachinesOrchestrator

    container = get_container()
    orchestrator = container.get(StopMachinesOrchestrator)
    result = await orchestrator.execute(
        StopMachinesInput(
            machine_ids=machine_ids_from_args,
            all_machines=has_all,
            force=has_force,
        )
    )
    return {
        "success": result.success,
        "message": result.message,
        "stopped_machines": result.stopped_machines,
        "failed_machines": result.failed_machines,
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

    from orb.application.services.orchestration.dtos import StartMachinesInput
    from orb.application.services.orchestration.start_machines import StartMachinesOrchestrator

    container = get_container()
    orchestrator = container.get(StartMachinesOrchestrator)
    result = await orchestrator.execute(
        StartMachinesInput(
            machine_ids=machine_ids_from_args,
            all_machines=has_all,
        )
    )
    return {
        "success": result.success,
        "message": result.message,
        "started_machines": result.started_machines,
        "failed_machines": result.failed_machines,
    }
