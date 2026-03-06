"""Request-related command handlers for the interface layer."""

from typing import TYPE_CHECKING, Any, Union

from orb.domain.base.ports.scheduler_port import SchedulerPort
from orb.domain.request.exceptions import RequestNotFoundError
from orb.infrastructure.di.buses import CommandBus, QueryBus
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
    container = get_container()
    query_bus = container.get(QueryBus)
    scheduler_strategy = container.get(SchedulerPort)

    # Validation: Prevent --all with specific IDs
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
        # Create query for all active requests
        from orb.application.dto.queries import ListActiveRequestsQuery

        query = ListActiveRequestsQuery(all_resources=True)
        request_dtos = await query_bus.execute(query)

        # Format response using scheduler strategy
        return scheduler_strategy.format_request_status_response(request_dtos)
    else:
        # Existing specific ID logic
        # Pass raw input data to scheduler strategy (scheduler-agnostic)
        # First precedence is input data, then arguments
        if hasattr(args, "input_data") and args.input_data:
            raw_request_data = args.input_data
        else:
            request_ids_from_args = []

            # Handle request_id that might be a list (from CLI command factory)
            if hasattr(args, "request_id") and args.request_id:
                if isinstance(args.request_id, list):
                    request_ids_from_args.extend(args.request_id)
                else:
                    request_ids_from_args.append(args.request_id)

            # Merge positional and flag arguments
            if hasattr(args, "request_ids") and args.request_ids:
                request_ids_from_args.extend(args.request_ids)
            if hasattr(args, "flag_request_ids") and args.flag_request_ids:
                request_ids_from_args.extend(args.flag_request_ids)

            raw_request_data = {
                "requests": [{"request_id": request_id} for request_id in request_ids_from_args]
            }

        # Let scheduler strategy parse the raw data (each scheduler handles its own format)
        parsed_result = scheduler_strategy.parse_request_data(raw_request_data)

        # Validate parsed data - runtime may return a list despite port typing dict
        parsed_data_list: list[dict[str, Any]] = (
            parsed_result if isinstance(parsed_result, list) else [parsed_result]
        )

        if not parsed_data_list:
            return {"error": "No request ID provided", "message": "Request ID is required"}

        request_dtos = []

        # Extract request IDs from parsed data
        request_ids = [
            item.get("request_id")
            for item in parsed_data_list
            if isinstance(item, dict) and item.get("request_id")
        ]

        if not request_ids:
            return {"error": "No valid request IDs provided", "message": "Request IDs are required"}

        # Use batch query if multiple IDs, individual queries otherwise
        if len(request_ids) == 1:
            from orb.application.dto.queries import GetRequestQuery

            try:
                query = GetRequestQuery(request_id=str(request_ids[0]))
                request_dto = await query_bus.execute(query)
                if request_dto:
                    request_dtos.append(request_dto)
            except RequestNotFoundError:
                from datetime import datetime

                from orb.application.request.dto import RequestDTO

                request_dtos.append(
                    RequestDTO(
                        request_id=str(request_ids[0]),
                        status="pending",
                        requested_count=0,
                        created_at=datetime.utcnow(),
                    )
                )
            except Exception:
                raise
        else:
            # For multiple IDs, we need to query each individually since there's no batch query yet
            from orb.application.dto.queries import GetRequestQuery

            for request_id in request_ids:
                try:
                    query = GetRequestQuery(request_id=str(request_id))
                    request_dto = await query_bus.execute(query)
                    if request_dto:
                        request_dtos.append(request_dto)
                except RequestNotFoundError:
                    from datetime import datetime

                    from orb.application.request.dto import RequestDTO

                    request_dtos.append(
                        RequestDTO(
                            request_id=str(request_id),
                            status="pending",
                            requested_count=0,
                            created_at=datetime.utcnow(),
                        )
                    )
                except Exception:
                    raise

        # Format response using scheduler strategy
        return scheduler_strategy.format_request_status_response(request_dtos)


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
    container = get_container()
    command_bus = container.get(CommandBus)
    scheduler_strategy = container.get(SchedulerPort)

    from orb.application.dto.commands import CreateRequestCommand
    from orb.infrastructure.mocking.dry_run_context import is_dry_run_active

    # Pass raw input data to scheduler strategy (scheduler-agnostic)
    # Each strategy's parse_request_data() owns envelope unwrapping for its own format
    if hasattr(args, "input_data") and args.input_data:
        raw_request_data = args.input_data
    else:
        # Merge positional and flag arguments
        template_id = getattr(args, "template_id", None) or getattr(args, "flag_template_id", None)
        machine_count = getattr(args, "machine_count", None) or getattr(
            args, "flag_machine_count", None
        )
        machine_id = getattr(args, "machine_id", None) or getattr(args, "flag_machine_id", None)

        raw_request_data = {
            "template_id": template_id,
            "requested_count": machine_count,
            "machine_id": machine_id,  # For show operations
        }

    # Let scheduler strategy parse the raw data (each scheduler handles its own format)
    parsed_result = scheduler_strategy.parse_request_data(raw_request_data)
    parsed_data: dict[str, Any] = parsed_result if isinstance(parsed_result, dict) else {}
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

    from orb.api.utils.request_id_generator import generate_request_id
    from orb.domain.request.request_types import RequestType

    request_id = generate_request_id(RequestType.ACQUIRE)

    command = CreateRequestCommand(
        request_id=request_id,
        template_id=template_id,
        requested_count=int(machine_count),
        metadata=metadata,
    )

    # Execute command — CQRS commands return None; use pre-generated request_id
    try:
        await command_bus.execute(command)  # type: ignore[arg-type]
    except Exception as e:
        error_response = {"request_id": request_id, "status": "failed", "status_message": str(e)}
        if scheduler_strategy:
            return scheduler_strategy.format_request_response(error_response), 1
        return error_response, 1

    # Get the request details to include resource ID information
    try:
        from orb.application.dto.queries import GetRequestQuery

        query_bus = container.get(QueryBus)
        query = GetRequestQuery(request_id=request_id)
        request_dto = await query_bus.execute(query)

        # Extract resource IDs for the message
        resource_ids = getattr(request_dto, "resource_ids", []) if request_dto else []

        # Create response data with resource ID information
        status = request_dto.status if request_dto else "pending"
        error_msg = None
        if request_dto and hasattr(request_dto, "metadata"):
            if isinstance(request_dto.metadata, dict):
                error_msg = request_dto.metadata.get("error_message")
            else:
                error_msg = getattr(request_dto.metadata, "error_message", None)

        request_data = {
            "request_id": request_id,
            "resource_ids": resource_ids,
            "template_id": template_id,
            "status": status,
            "status_message": error_msg,
        }

        # Return success response using scheduler strategy formatting
        if scheduler_strategy:
            response = scheduler_strategy.format_request_response(request_data)
            status = request_dto.status if request_dto else "pending"
            exit_code = scheduler_strategy.get_exit_code_for_status(status)
            return response, exit_code
        else:
            # Fallback if no scheduler strategy (shouldn't happen)
            return {
                "error": "No scheduler strategy available",
                "message": "Unable to format response",
            }, 1
    except Exception as e:
        # Fallback if we can't get request details
        from orb.domain.base.ports import LoggingPort

        container.get(LoggingPort).warning("Could not get request details for resource ID: %s", e)
        if scheduler_strategy:
            response = scheduler_strategy.format_request_response({"request_id": request_id})
            return response, 0  # Command succeeded, just couldn't get details
        else:
            return {
                "error": "No scheduler strategy available",
                "message": "Unable to format response",
            }, 1


@handle_interface_exceptions(context="get_return_requests", interface_type="cli")
async def handle_get_return_requests(args: "argparse.Namespace") -> dict[str, Any]:
    """
    Handle get return requests operations.

    Args:
        args: Argument namespace with resource/action structure

    Returns:
        Return requests list in scheduler format
    """
    container = get_container()
    query_bus = container.get(QueryBus)
    scheduler_strategy = container.get(SchedulerPort)

    from orb.application.dto.queries import ListReturnRequestsQuery

    # Extract machine name filter from JSON input if provided
    machine_names: list[str] = []
    if hasattr(args, "input_data") and args.input_data:
        raw = args.input_data
        if "machines" in raw:
            machine_names = [
                name
                for m in raw["machines"]
                if isinstance(m, dict)
                for name in [m.get("name") or m.get("machineId") or m.get("machine_id")]
                if name is not None
            ]

    query = ListReturnRequestsQuery(machine_names=machine_names)
    request_dtos = await query_bus.execute(query)

    return scheduler_strategy.format_request_status_response(request_dtos)


@handle_interface_exceptions(context="request_return_machines", interface_type="cli")
async def handle_request_return_machines(args: "argparse.Namespace") -> dict[str, Any]:
    """
    Handle request return machines operations.

    Args:
        args: Argument namespace with resource/action structure

    Returns:
        Return request results
    """
    container = get_container()
    command_bus = container.get(CommandBus)
    query_bus = container.get(QueryBus)

    from orb.application.dto.commands import CreateReturnRequestCommand

    # Validation: Prevent --all with specific IDs
    has_all = getattr(args, "all", False)
    machine_ids = []

    # Handle input data from -f flag (HostFactory compatibility)
    if hasattr(args, "input_data") and args.input_data:
        # Extract machine IDs from JSON input data
        raw_request_data = args.input_data
        if "machines" in raw_request_data:
            machine_ids = [
                machine.get("machineId") or machine.get("machine_id")
                for machine in raw_request_data["machines"]
                if machine.get("machineId") or machine.get("machine_id")
            ]
    else:
        # Use positional arguments
        machine_ids = getattr(args, "machine_ids", [])

    has_specific_ids = bool(machine_ids)

    if has_all and has_specific_ids:
        return {
            "error": "Cannot use --all with specific machine IDs",
            "message": "Use either --all or specific IDs, not both",
        }

    if has_all:
        # Safety confirmation for destructive --all operations
        has_force = getattr(args, "force", False)
        if not has_force:
            return {
                "error": "Destructive operation requires --force flag",
                "message": "Use --force to confirm returning all machines",
            }

        # Get all active machines
        from orb.application.dto.queries import ListMachinesQuery

        query = ListMachinesQuery(all_resources=True, active_only=True)
        machine_dtos = await query_bus.execute(query)

        # Extract machine IDs from DTOs (handle both dict and object DTOs)
        machine_ids = []
        for machine in machine_dtos:
            if isinstance(machine, dict):
                machine_id = machine.get("machine_id")
            else:
                machine_id = getattr(machine, "machine_id", None)

            if machine_id:
                machine_ids.append(machine_id)

        if not machine_ids:
            return {
                "error": "No active machines found",
                "message": "No machines available to return",
            }

    if not machine_ids:
        return {
            "error": "Machine IDs are required",
            "message": "Machine IDs must be provided either as arguments or in JSON file",
        }

    command = CreateReturnRequestCommand(
        machine_ids=machine_ids,
        force_return=getattr(args, "force", False),
    )

    # Execute command — CQRS commands return None; read results from command fields
    await command_bus.execute(command)  # type: ignore[arg-type]

    created_ids = getattr(command, "created_request_ids", None) or []
    request_id = created_ids[0] if created_ids else None

    scheduler_strategy = container.get(SchedulerPort)
    request_data = {"request_id": request_id, "status": "pending"}
    if scheduler_strategy:
        return scheduler_strategy.format_request_response(request_data)
    return {"requestId": request_id, "message": "Return request created successfully"}


@handle_interface_exceptions(context="cancel_request", interface_type="cli")
async def handle_cancel_request(args: "argparse.Namespace") -> dict[str, Any]:
    """Handle cancel request operations."""
    container = get_container()
    command_bus = container.get(CommandBus)
    scheduler_strategy = container.get(SchedulerPort)

    from orb.application.dto.commands import CancelRequestCommand

    request_id = getattr(args, "request_id", None) or getattr(args, "flag_request_id", None)
    if not request_id:
        return {"error": "Request ID is required", "message": "Request ID must be provided"}

    reason = getattr(args, "reason", None) or ""
    command = CancelRequestCommand(request_id=request_id, reason=reason)
    await command_bus.execute(command)  # type: ignore[arg-type]

    request_data = {"request_id": request_id, "status": "cancelled"}
    if scheduler_strategy:
        return scheduler_strategy.format_request_response(request_data)
    return {"request_id": request_id, "message": "Request cancelled successfully"}
