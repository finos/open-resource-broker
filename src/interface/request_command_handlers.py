"""Request-related command handlers for the interface layer."""

from typing import TYPE_CHECKING, Any, Dict

from domain.base.ports.scheduler_port import SchedulerPort
from infrastructure.di.buses import CommandBus, QueryBus
from infrastructure.di.container import get_container
from infrastructure.error.decorators import handle_interface_exceptions

if TYPE_CHECKING:
    import argparse


@handle_interface_exceptions(context="get_request_status", interface_type="cli")
async def handle_get_request_status(args: "argparse.Namespace") -> Dict[str, Any]:
    """
    Handle get request status operations.

    Args:
        args: Argument namespace with resource/action structure

    Returns:
        Request status information
    """
    container = get_container()
    query_bus = container.get(QueryBus)
    scheduler_strategy = container.get(SchedulerPort)

    # Pass raw input data to scheduler strategy (scheduler-agnostic)
    if hasattr(args, "input_data") and args.input_data:
        raw_request_data = args.input_data
    else:
        request_id_value = None
        if hasattr(args, "request_ids") and args.request_ids:
            request_id_value = args.request_ids[0]
        elif hasattr(args, "request_id") and args.request_id:
            request_id_value = args.request_id

        raw_request_data = {
            "request_id": request_id_value,
        }

    # Let scheduler strategy parse the raw data (each scheduler handles its own format)
    parsed_data = scheduler_strategy.parse_request_data(raw_request_data)
    request_id = parsed_data.get("request_id")

    if not request_id:
        return {"error": "No request ID provided", "message": "Request ID is required"}

    from application.dto.queries import GetRequestQuery

    query = GetRequestQuery(request_id=request_id)
    request_dto = await query_bus.execute(query)

    # Pass domain DTO to scheduler strategy - NO formatting logic here
    return scheduler_strategy.format_request_status_response([request_dto])


@handle_interface_exceptions(context="request_machines", interface_type="cli")
async def handle_request_machines(args: "argparse.Namespace") -> Dict[str, Any]:
    """
    Handle request machines operations.

    Args:
        args: Argument namespace with resource/action structure

    Returns:
        Machine request results in HostFactory format
    """
    container = get_container()
    command_bus = container.get(CommandBus)
    scheduler_strategy = container.get(SchedulerPort)

    from application.dto.commands import CreateRequestCommand
    from infrastructure.mocking.dry_run_context import is_dry_run_active

    # Pass raw input data to scheduler strategy (scheduler-agnostic)
    if hasattr(args, "input_data") and args.input_data:
        raw_request_data = args.input_data
    else:
        raw_request_data = {
            "template_id": getattr(args, "template_id", None),
            "requested_count": getattr(args, "machine_count", None),
        }

    # Let scheduler strategy parse the raw data (each scheduler handles its own format)
    parsed_data = scheduler_strategy.parse_request_data(raw_request_data)
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

    # Check if dry-run is active and add to metadata
    metadata = getattr(args, "metadata", {})
    metadata["dry_run"] = is_dry_run_active()

    command = CreateRequestCommand(
        template_id=template_id, requested_count=int(machine_count), metadata=metadata
    )

    # Execute command and get request ID - let exceptions bubble up
    request_id = await command_bus.execute(command)

    # Get the request details to include resource ID information
    try:
        from application.dto.queries import GetRequestQuery

        query_bus = container.get(QueryBus)
        query = GetRequestQuery(request_id=request_id)
        request_dto = await query_bus.execute(query)

        # Extract resource IDs for the message
        resource_ids = getattr(request_dto, "resource_ids", []) if request_dto else []

        # Create response data with resource ID information
        request_data = {
            "request_id": request_id,
            "resource_ids": resource_ids,
            "template_id": template_id,
        }

        # Return success response using scheduler strategy formatting
        if scheduler_strategy:
            return scheduler_strategy.format_request_response(request_data)
        else:
            # Fallback if no scheduler strategy (shouldn't happen)
            return {
                "error": "No scheduler strategy available",
                "message": "Unable to format response",
            }
    except Exception as e:
        # Fallback if we can't get request details
        from domain.base.ports import LoggingPort

        container.get(LoggingPort).warning("Could not get request details for resource ID: %s", e)
        if scheduler_strategy:
            return scheduler_strategy.format_request_response({"request_id": request_id})
        else:
            return {
                "error": "No scheduler strategy available",
                "message": "Unable to format response",
            }


@handle_interface_exceptions(context="get_return_requests", interface_type="cli")
async def handle_get_return_requests(args: "argparse.Namespace") -> Dict[str, Any]:
    """
    Handle get return requests operations.

    Args:
        args: Argument namespace with resource/action structure

    Returns:
        Return requests list
    """
    container = get_container()
    query_bus = container.get(QueryBus)
    container.get(SchedulerPort)

    from application.dto.queries import ListReturnRequestsQuery

    query = ListReturnRequestsQuery()
    requests = await query_bus.execute(query)

    return {
        "requests": requests,
        "count": len(requests),
        "message": "Return requests retrieved successfully",
    }


@handle_interface_exceptions(context="request_return_machines", interface_type="cli")
async def handle_request_return_machines(args: "argparse.Namespace") -> Dict[str, Any]:
    """
    Handle request return machines operations.

    Args:
        args: Argument namespace with resource/action structure

    Returns:
        Return request results
    """
    container = get_container()
    command_bus = container.get(CommandBus)
    container.get(SchedulerPort)

    from application.dto.commands import CreateReturnRequestCommand

    command = CreateReturnRequestCommand(
        request_id=getattr(args, "request_id", None),
        machine_ids=getattr(args, "machine_ids", []),
    )
    result = await command_bus.execute(command)

    return {"result": result, "message": "Return request created successfully"}
