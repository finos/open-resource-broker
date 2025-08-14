"""Request-related command handlers for the interface layer."""

from typing import TYPE_CHECKING, Any, Dict

from src.domain.base.ports.scheduler_port import SchedulerPort
from src.infrastructure.di.buses import CommandBus, QueryBus
from src.infrastructure.di.container import get_container
from src.infrastructure.error.decorators import handle_interface_exceptions

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

    # Extract request ID from args or input_data (HostFactory compatibility)
    request_id = None

    # Check for input data from -f/--data flags first (HostFactory style)
    if hasattr(args, "input_data") and args.input_data:
        input_data = args.input_data
        request_id = input_data.get("request_id") or input_data.get("requestId")
    else:
        # Use command line arguments
        if hasattr(args, "request_ids") and args.request_ids:
            # Take the first request ID from the list
            request_id = args.request_ids[0]
        elif hasattr(args, "request_id") and args.request_id:
            request_id = args.request_id

    if not request_id:
        return {"error": "No request ID provided", "message": "Request ID is required"}

    from src.application.dto.queries import GetRequestQuery

    query = GetRequestQuery(request_id=request_id)
    request_dto = await query_bus.execute(query)

    # Pass domain DTO to scheduler strategy - NO formatting logic here
    return scheduler_strategy.convert_domain_to_hostfactory_output("getRequestStatus", request_dto)


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

    from src.application.dto.commands import CreateRequestCommand
    from src.infrastructure.mocking.dry_run_context import is_dry_run_active

    # Extract parameters from args or input_data (HostFactory compatibility)
    template_id = None
    machine_count = None

    # Check for input data from -f/--data flags first (HostFactory style)
    if hasattr(args, "input_data") and args.input_data:
        input_data = args.input_data
        # Support both HostFactory format and direct format
        if "template" in input_data:
            # HostFactory nested format: {"template": {"templateId": "...",
            # "machineCount": ...}}
            template_data = input_data["template"]
            template_id = template_data.get(
                "templateId") or template_data.get("template_id")
            machine_count = template_data.get(
                "machineCount") or template_data.get("machine_count")
        else:
            # Direct format: {"template_id": "...", "machine_count": ...}
            template_id = input_data.get("template_id") or input_data.get("templateId")
            machine_count = input_data.get(
                "machine_count") or input_data.get("machineCount")
    else:
        # Use command line arguments
        template_id = getattr(args, "template_id", None)
        machine_count = getattr(args, "machine_count", None)

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
        from src.application.dto.queries import GetRequestQuery

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

        # Return success response in HostFactory format with resource ID info
        if scheduler_strategy:
            return scheduler_strategy.convert_domain_to_hostfactory_output(
                "requestMachines", request_data
            )
        else:
            # Fallback to HostFactory format if no scheduler strategy
            resource_id_msg = f" Resource ID: {resource_ids[0]}" if resource_ids else ""
            return {
                "requestId": str(request_id),
                "message": f"Request VM success from AWS.{resource_id_msg}",
            }
    except Exception as e:
        # Fallback if we can't get request details
        from src.domain.base.ports import LoggingPort

        container.get(LoggingPort).warning(
            f"Could not get request details for resource ID: {e}")
        if scheduler_strategy:
            return scheduler_strategy.convert_domain_to_hostfactory_output(
                "requestMachines", request_id
            )
        else:
            return {
                "requestId": str(request_id),
                "message": "Request VM success from AWS.",
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

    from src.application.dto.queries import ListReturnRequestsQuery

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

    from src.application.dto.commands import CreateReturnRequestCommand

    command = CreateReturnRequestCommand(
        request_id=getattr(args, "request_id", None),
        machine_ids=getattr(args, "machine_ids", []),
    )
    result = await command_bus.execute(command)

    return {"result": result, "message": "Return request created successfully"}
