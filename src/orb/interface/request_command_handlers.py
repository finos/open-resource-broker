"""Request-related command handlers for the interface layer."""

from typing import TYPE_CHECKING, Any, Union

from orb.application.ports.scheduler_port import SchedulerPort
from orb.infrastructure.di.container import get_container
from orb.infrastructure.error.decorators import handle_interface_exceptions

if TYPE_CHECKING:
    import argparse


@handle_interface_exceptions(context="get_request_status", interface_type="cli")
async def handle_get_request_status(
    args: "argparse.Namespace",
) -> Union[dict[str, Any], tuple[dict[str, Any], int], list[Any]]:
    """
    Handle get request status operations with --all support.

    Args:
        args: Argument namespace with resource/action structure

    Returns:
        Request status information
    """
    from orb.application.services.orchestration.dtos import GetRequestStatusInput
    from orb.application.services.orchestration.get_request_status import (
        GetRequestStatusOrchestrator,
    )

    container = get_container()
    orchestrator = container.get(GetRequestStatusOrchestrator)
    scheduler = container.get(SchedulerPort)

    has_all = getattr(args, "all", False)
    has_specific_ids = bool(
        getattr(args, "request_ids", []) or getattr(args, "flag_request_ids", [])
    )

    if has_all and has_specific_ids:
        return {
            "error": "Cannot use --all with specific request IDs",
            "message": "Use either --all or specific IDs, not both",
        }

    if has_all:
        result = await orchestrator.execute(
            GetRequestStatusInput(all_requests=True, detailed=getattr(args, "detailed", False))
        )
        return scheduler.format_request_status_response(result.requests)

    # Collect request IDs from args or input_data
    if hasattr(args, "input_data") and args.input_data:
        raw = args.input_data
        parsed = scheduler.parse_request_data(raw)
        parsed_list: list[dict[str, Any]] = parsed if isinstance(parsed, list) else [parsed]
        request_ids = [
            item.get("request_id")
            for item in parsed_list
            if isinstance(item, dict) and item.get("request_id")
        ]
    else:
        request_ids = []
        if hasattr(args, "request_id") and args.request_id:
            if isinstance(args.request_id, list):
                request_ids.extend(args.request_id)
            else:
                request_ids.append(args.request_id)
        if hasattr(args, "request_ids") and args.request_ids:
            request_ids.extend(args.request_ids)
        if hasattr(args, "flag_request_ids") and args.flag_request_ids:
            request_ids.extend(args.flag_request_ids)

    if not request_ids:
        return {"error": "No request ID provided", "message": "Request ID is required"}

    result = await orchestrator.execute(
        GetRequestStatusInput(
            request_ids=[str(rid) for rid in request_ids],
            detailed=getattr(args, "detailed", False),
        )
    )
    return scheduler.format_request_status_response(result.requests)


@handle_interface_exceptions(context="request_machines", interface_type="cli")
async def handle_request_machines(
    args: "argparse.Namespace",
) -> Union[dict[str, Any], tuple[dict[str, Any], int]]:
    """
    Handle request machines operations.

    Args:
        args: Argument namespace with resource/action structure

    Returns:
        Machine request results in HostFactory format
    """
    from orb.application.services.orchestration.acquire_machines import (
        AcquireMachinesOrchestrator,
    )
    from orb.application.services.orchestration.dtos import AcquireMachinesInput

    container = get_container()
    orchestrator = container.get(AcquireMachinesOrchestrator)
    scheduler = container.get(SchedulerPort)

    # Parse template_id and machine_count from input_data or args
    if hasattr(args, "input_data") and args.input_data:
        parsed_result = scheduler.parse_request_data(args.input_data)
        parsed_data: dict[str, Any] = parsed_result if isinstance(parsed_result, dict) else {}
    else:
        template_id = getattr(args, "template_id", None) or getattr(args, "flag_template_id", None)
        machine_count = getattr(args, "machine_count", None) or getattr(
            args, "flag_machine_count", None
        )
        parsed_data = {
            "template_id": template_id,
            "requested_count": machine_count,
        }

    template_id = parsed_data.get("template_id")
    machine_count = parsed_data.get("requested_count", 1)

    if not template_id:
        return {
            "error": "Template ID is required",
            "message": "Template ID must be provided",
        }

    if not machine_count:
        return {
            "error": "Machine count is required",
            "message": "Machine count must be provided",
        }

    wait = getattr(args, "wait", False)
    timeout_seconds = getattr(args, "timeout", 300)

    result = await orchestrator.execute(
        AcquireMachinesInput(
            template_id=str(template_id),
            requested_count=int(machine_count),
            wait=bool(wait),
            timeout_seconds=int(timeout_seconds),
        )
    )

    response = scheduler.format_request_response(result.raw)
    exit_code = scheduler.get_exit_code_for_status(result.status)
    return response, exit_code


@handle_interface_exceptions(context="get_return_requests", interface_type="cli")
async def handle_get_return_requests(args: "argparse.Namespace") -> dict[str, Any]:
    """
    Handle get return requests operations.

    Args:
        args: Argument namespace with resource/action structure

    Returns:
        Return requests list in scheduler format
    """
    from orb.application.services.orchestration.dtos import ListReturnRequestsInput
    from orb.application.services.orchestration.list_return_requests import (
        ListReturnRequestsOrchestrator,
    )

    container = get_container()
    orchestrator = container.get(ListReturnRequestsOrchestrator)
    scheduler = container.get(SchedulerPort)

    result = await orchestrator.execute(ListReturnRequestsInput())
    return scheduler.format_request_status_response(result.requests)


@handle_interface_exceptions(context="request_return_machines", interface_type="cli")
async def handle_request_return_machines(args: "argparse.Namespace") -> dict[str, Any]:
    """
    Handle request return machines operations.

    Args:
        args: Argument namespace with resource/action structure

    Returns:
        Return request results
    """
    from orb.application.services.orchestration.dtos import ReturnMachinesInput
    from orb.application.services.orchestration.return_machines import (
        ReturnMachinesOrchestrator,
    )

    container = get_container()
    orchestrator = container.get(ReturnMachinesOrchestrator)
    scheduler = container.get(SchedulerPort)

    has_all = getattr(args, "all", False)
    machine_ids: list[str] = []

    if hasattr(args, "input_data") and args.input_data:
        raw_request_data = args.input_data
        if "machines" in raw_request_data:
            machine_ids = [
                machine.get("machineId") or machine.get("machine_id")
                for machine in raw_request_data["machines"]
                if machine.get("machineId") or machine.get("machine_id")
            ]
    else:
        machine_ids = getattr(args, "machine_ids", [])

    has_specific_ids = bool(machine_ids)

    if has_all and has_specific_ids:
        return {
            "error": "Cannot use --all with specific machine IDs",
            "message": "Use either --all or specific IDs, not both",
        }

    if has_all:
        has_force = getattr(args, "force", False)
        if not has_force:
            return {
                "error": "Destructive operation requires --force flag",
                "message": "Use --force to confirm returning all machines",
            }

    if not has_all and not machine_ids:
        return {
            "error": "Machine IDs are required",
            "message": "Machine IDs must be provided either as arguments or in JSON file",
        }

    result = await orchestrator.execute(
        ReturnMachinesInput(
            machine_ids=machine_ids,
            all_machines=has_all,
            force=getattr(args, "force", False),
        )
    )

    return scheduler.format_request_response(result.raw)


@handle_interface_exceptions(context="list_requests", interface_type="cli")
async def handle_list_requests(args: "argparse.Namespace") -> dict[str, Any]:
    """List all active provisioning requests."""
    from orb.application.services.orchestration.dtos import ListRequestsInput
    from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator

    container = get_container()
    orchestrator = container.get(ListRequestsOrchestrator)
    scheduler = container.get(SchedulerPort)

    result = await orchestrator.execute(ListRequestsInput())
    return scheduler.format_request_status_response(result.requests)


@handle_interface_exceptions(context="cancel_request", interface_type="cli")
async def handle_cancel_request(args: "argparse.Namespace") -> dict[str, Any]:
    """Handle cancel request operations."""
    from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator
    from orb.application.services.orchestration.dtos import CancelRequestInput

    container = get_container()
    orchestrator = container.get(CancelRequestOrchestrator)
    scheduler = container.get(SchedulerPort)

    request_id = getattr(args, "request_id", None) or getattr(args, "flag_request_id", None)
    if not request_id:
        return {"error": "Request ID is required", "message": "Request ID must be provided"}

    reason = getattr(args, "reason", None) or "Cancelled via API"
    result = await orchestrator.execute(CancelRequestInput(request_id=request_id, reason=reason))

    return scheduler.format_request_response(result.raw)
