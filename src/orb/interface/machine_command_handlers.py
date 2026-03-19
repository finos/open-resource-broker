"""Machine-related command handlers for the interface layer."""

import asyncio
from typing import TYPE_CHECKING, Any, Union

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.services.response_formatting_service import ResponseFormattingService
from orb.infrastructure.di.container import get_container
from orb.infrastructure.error.decorators import handle_interface_exceptions

if TYPE_CHECKING:
    import argparse


@handle_interface_exceptions(context="get_machine_status", interface_type="cli")
async def handle_get_machine_status(
    args: "argparse.Namespace",
) -> Union[dict[str, Any], InterfaceResponse]:
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
    formatter = container.get(ResponseFormattingService)

    has_all = getattr(args, "all", False)
    machine_ids_from_args = []

    if hasattr(args, "machine_ids") and args.machine_ids:
        machine_ids_from_args.extend(args.machine_ids)
    if hasattr(args, "machine_ids_flag") and args.machine_ids_flag:
        machine_ids_from_args.extend(args.machine_ids_flag)

    has_specific_ids = bool(machine_ids_from_args)

    if has_all and has_specific_ids:
        return {
            "error": "Cannot use --all with specific machine IDs",
            "message": "Use either --all or specific IDs, not both",
        }

    if has_all:
        orchestrator = container.get(ListMachinesOrchestrator)
        result = await orchestrator.execute(ListMachinesInput())
        return formatter.format_machine_list(result.machines)

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

    return formatter.format_machine_list(machine_dtos)


@handle_interface_exceptions(context="list_machines", interface_type="cli")
async def handle_list_machines(
    args: "argparse.Namespace",
) -> Union[dict[str, Any], InterfaceResponse]:
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
    formatter = container.get(ResponseFormattingService)

    _limit = getattr(args, "limit", None)
    _offset = getattr(args, "offset", None)
    limit: int = int(_limit) if _limit is not None else 100
    offset: int = int(_offset) if _offset is not None else 0
    result = await orchestrator.execute(
        ListMachinesInput(
            status=getattr(args, "status", None),
            provider_name=getattr(args, "provider", None),
            request_id=getattr(args, "request_id", None),
            limit=limit,
            offset=offset,
        )
    )
    return formatter.format_machine_list(result.machines)


@handle_interface_exceptions(context="stop_machines", interface_type="cli")
async def handle_stop_machines(args: "argparse.Namespace") -> Union[dict[str, Any], InterfaceResponse]:
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
    machine_ids_from_args = (getattr(args, "machine_ids", []) or []) + (
        getattr(args, "machine_ids_flag", []) or []
    )

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
    formatter = container.get(ResponseFormattingService)
    result = await orchestrator.execute(
        StopMachinesInput(
            machine_ids=machine_ids_from_args,
            all_machines=has_all,
            force=has_force,
        )
    )
    return formatter.format_machine_operation({
        "success": result.success,
        "message": result.message,
        "stopped_machines": result.stopped_machines,
        "failed_machines": result.failed_machines,
    })


@handle_interface_exceptions(context="start_machines", interface_type="cli")
async def handle_start_machines(args: "argparse.Namespace") -> Union[dict[str, Any], InterfaceResponse]:
    """
    Handle start machines operations.

    Args:
        args: Argument namespace with machine_ids and all flags

    Returns:
        Start operation results
    """
    # Validation: Cannot use both --all and specific IDs
    has_all = getattr(args, "all", False)
    machine_ids_from_args = (getattr(args, "machine_ids", []) or []) + (
        getattr(args, "machine_ids_flag", []) or []
    )

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
    formatter = container.get(ResponseFormattingService)
    result = await orchestrator.execute(
        StartMachinesInput(
            machine_ids=machine_ids_from_args,
            all_machines=has_all,
        )
    )
    return formatter.format_machine_operation({
        "success": result.success,
        "message": result.message,
        "started_machines": result.started_machines,
        "failed_machines": result.failed_machines,
    })


@handle_interface_exceptions(context="get_machine", interface_type="cli")
async def handle_get_machine(args: "argparse.Namespace") -> Union[dict[str, Any], InterfaceResponse]:
    """Handle machines show — fetch a single machine and wrap in InterfaceResponse."""
    from orb.application.services.orchestration.dtos import GetMachineInput
    from orb.application.services.orchestration.get_machine import GetMachineOrchestrator

    container = get_container()
    orchestrator = container.get(GetMachineOrchestrator)
    formatter = container.get(ResponseFormattingService)

    machine_id = getattr(args, "machine_id", None) or getattr(args, "flag_machine_id", None)
    if not machine_id:
        return formatter.format_error("Machine ID is required")

    result = await orchestrator.execute(GetMachineInput(machine_id=machine_id))
    if result.machine is None:
        return formatter.format_error("Machine not found")
    raw = result.machine.model_dump() if hasattr(result.machine, "model_dump") else vars(result.machine)
    return formatter.format_machine_operation(raw)
