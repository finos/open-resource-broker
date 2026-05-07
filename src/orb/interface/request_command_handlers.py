"""Request-related command handlers for the interface layer."""

from typing import TYPE_CHECKING, Any, Union

from orb.application.ports.scheduler_port import SchedulerPort
from orb.infrastructure.di.container import get_container
from orb.infrastructure.error.decorators import handle_interface_exceptions
from orb.interface.response_formatting_service import ResponseFormattingService

if TYPE_CHECKING:
    import argparse

    from orb.application.dto.interface_response import InterfaceResponse


@handle_interface_exceptions(context="get_request_status", interface_type="cli")
async def handle_get_request_status(
    args: "argparse.Namespace",
) -> Union[dict[str, Any], "InterfaceResponse"]:
    """Handle get request status operations with --all support."""
    from orb.application.services.orchestration.dtos import GetRequestStatusInput
    from orb.application.services.orchestration.get_request_status import (
        GetRequestStatusOrchestrator,
    )

    container = get_container()
    orchestrator = container.get(GetRequestStatusOrchestrator)
    formatter = container.get(ResponseFormattingService)

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
            GetRequestStatusInput(all_requests=True, verbose=getattr(args, "verbose", False))
        )
        return formatter.format_request_status(result.requests)

    # Collect request IDs from args or input_data
    scheduler = container.get(SchedulerPort)
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
        if hasattr(args, "flag_request_id") and args.flag_request_id:
            if isinstance(args.flag_request_id, list):
                request_ids.extend(args.flag_request_id)
            else:
                request_ids.append(args.flag_request_id)

    if not request_ids:
        return {"error": "No request ID provided", "message": "Request ID is required"}

    result = await orchestrator.execute(
        GetRequestStatusInput(
            request_ids=[str(rid) for rid in request_ids],
            verbose=getattr(args, "verbose", False),
        )
    )
    return formatter.format_request_status(result.requests)


@handle_interface_exceptions(context="request_machines", interface_type="cli")
async def handle_request_machines(
    args: "argparse.Namespace",
) -> Union[dict[str, Any], "InterfaceResponse"]:
    """Handle request machines operations."""
    from orb.application.services.orchestration.acquire_machines import (
        AcquireMachinesOrchestrator,
    )
    from orb.application.services.orchestration.dtos import AcquireMachinesInput

    container = get_container()
    orchestrator = container.get(AcquireMachinesOrchestrator)
    formatter = container.get(ResponseFormattingService)
    scheduler = container.get(SchedulerPort)

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

    return formatter.format_request_operation(
        {
            "request_id": result.request_id,
            "status": result.status,
            "machine_ids": result.machine_ids,
        },
        result.status,
    )


@handle_interface_exceptions(context="get_return_requests", interface_type="cli")
async def handle_get_return_requests(
    args: "argparse.Namespace",
) -> Union[dict[str, Any], "InterfaceResponse"]:
    """Handle get return requests operations."""
    from orb.application.services.orchestration.dtos import ListReturnRequestsInput
    from orb.application.services.orchestration.list_return_requests import (
        ListReturnRequestsOrchestrator,
    )

    container = get_container()
    orchestrator = container.get(ListReturnRequestsOrchestrator)
    formatter = container.get(ResponseFormattingService)

    result = await orchestrator.execute(
        ListReturnRequestsInput(
            status=getattr(args, "status", None),
            limit=getattr(args, "limit", 50),
        )
    )
    return formatter.format_return_requests(result.requests)


@handle_interface_exceptions(context="request_return_machines", interface_type="cli")
async def handle_request_return_machines(
    args: "argparse.Namespace",
) -> Union[dict[str, Any], "InterfaceResponse"]:
    """Handle request return machines operations."""
    from orb.application.services.orchestration.dtos import ReturnMachinesInput
    from orb.application.services.orchestration.return_machines import (
        ReturnMachinesOrchestrator,
    )

    container = get_container()
    orchestrator = container.get(ReturnMachinesOrchestrator)
    formatter = container.get(ResponseFormattingService)

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
        machine_ids = getattr(args, "machine_ids_flag", None) or getattr(args, "machine_ids", [])

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
            wait=getattr(args, "wait", False),
            timeout_seconds=getattr(args, "timeout", 300),
        )
    )

    return formatter.format_request_operation(
        {
            "request_id": result.request_id,
            "status": result.status,
            "message": result.message,
            "skipped_machines": result.skipped_machines,
        },
        result.status,
    )


@handle_interface_exceptions(context="list_requests", interface_type="cli")
async def handle_list_requests(
    args: "argparse.Namespace",
) -> Union[dict[str, Any], "InterfaceResponse"]:
    """List all active provisioning requests."""
    from orb.application.services.orchestration.dtos import ListRequestsInput
    from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator

    container = get_container()
    orchestrator = container.get(ListRequestsOrchestrator)
    formatter = container.get(ResponseFormattingService)

    result = await orchestrator.execute(
        ListRequestsInput(
            status=getattr(args, "status", None),
            limit=getattr(args, "limit", None) or 50,
            sync=getattr(args, "sync", False),
            offset=getattr(args, "offset", None) or 0,
            template_id=getattr(args, "template_id", None),
            request_type=getattr(args, "request_type", None),
        )
    )
    return formatter.format_request_status(result.requests)


@handle_interface_exceptions(context="cancel_request", interface_type="cli")
async def handle_cancel_request(
    args: "argparse.Namespace",
) -> Union[dict[str, Any], "InterfaceResponse"]:
    """Handle cancel request operations."""
    from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator
    from orb.application.services.orchestration.dtos import CancelRequestInput

    container = get_container()
    orchestrator = container.get(CancelRequestOrchestrator)
    formatter = container.get(ResponseFormattingService)

    request_id = getattr(args, "request_id", None) or getattr(args, "flag_request_id", None)
    if not request_id:
        return {"error": "Request ID is required", "message": "Request ID must be provided"}

    reason = getattr(args, "reason", None) or "Cancelled via API"
    result = await orchestrator.execute(
        CancelRequestInput(
            request_id=request_id, reason=reason, force=getattr(args, "force", False)
        )
    )

    return formatter.format_request_operation(
        {"request_id": result.request_id, "status": result.status},
        result.status,
    )


@handle_interface_exceptions(context="watch_request_status", interface_type="cli")
async def handle_watch_request_status(
    args: "argparse.Namespace",
) -> Union[dict[str, Any], "InterfaceResponse"]:
    """Handle watch request status command with progress bar display."""
    import asyncio
    from datetime import datetime, timezone

    from rich.console import Group
    from rich.live import Live
    from rich.progress import Progress

    from orb.application.services.orchestration.dtos import (
        WatchRequestStatusInput,
        WatchRequestStatusOutput,
    )
    from orb.application.services.orchestration.watch_request_status import (
        WatchRequestStatusOrchestrator,
    )
    from orb.cli.progress_bar import DotPreciseBar, render_az_bars

    container = get_container()
    orchestrator = container.get(WatchRequestStatusOrchestrator)

    request_id = getattr(args, "request_id", None)
    if not request_id:
        from orb.application.services.orchestration.dtos import ListRequestsInput
        from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator

        list_orchestrator = container.get(ListRequestsOrchestrator)
        list_result = await list_orchestrator.execute(ListRequestsInput(limit=100))
        if not list_result.requests:
            return {"error": "No requests found", "message": "No requests to watch"}
        latest = max(list_result.requests, key=lambda r: r.get("created_at", ""))
        request_id = latest.get("request_id")

    interval = getattr(args, "interval", 5)
    watch_input = WatchRequestStatusInput(
        request_id=str(request_id),
    )

    consecutive_errors = 0
    max_errors = 3

    try:
        snapshot = await orchestrator.execute(watch_input)
        total = snapshot.requested_count or 1

        created_at = None
        if snapshot.created_at:
            created_at = datetime.fromisoformat(snapshot.created_at)

        def _elapsed() -> str:
            if not created_at:
                return ""
            delta = datetime.now(timezone.utc) - created_at
            total_secs = int(delta.total_seconds())
            mins, secs = divmod(total_secs, 60)
            hours, mins = divmod(mins, 60)
            if hours:
                return f"{hours}:{mins:02d}:{secs:02d}"
            return f"{mins}:{secs:02d}"

        from orb.cli.console import print_info

        print_info(f"Watching: {request_id}")

        from rich.text import Text as RichText

        def _build_stats_line(snap: WatchRequestStatusOutput) -> RichText:
            line = RichText()
            if snap.weighted:
                line.append(f"{snap.fulfilled_capacity}/{total} units", style="bold #4c7aa7")
                line.append("  ")
                line.append(f"OD:{snap.od_capacity} units", style="bold bright_green")
                line.append(" ")
                line.append(f"SP:{snap.spot_capacity} units", style="bold #ffdb47")
            else:
                line.append(f"{snap.fulfilled_count}/{total} machines", style="bold #4c7aa7")
                line.append("  ")
                line.append(f"OD:{snap.od_machines} machines", style="bold bright_green")
                line.append(" ")
                line.append(f"SP:{snap.spot_machines} machines", style="bold #ffdb47")
            line.append("  ")
            line.append(f"{snap.fulfilled_vcpus} vCPUs", style="bold magenta")
            line.append("  ")
            line.append(f"{snap.fulfilled_count} machines", style="bold orange3")
            line.append("  ")
            line.append(snap.status, style="bold dark_green")
            line.append("  ")
            line.append(_elapsed(), style="dim")
            return line

        top_bar = DotPreciseBar(bar_width=30)
        progress = Progress(top_bar, auto_refresh=False)
        bar_completed = (
            snapshot.fulfilled_capacity if snapshot.weighted else snapshot.fulfilled_count
        )
        task = progress.add_task(
            "",
            total=total,
            completed=bar_completed,
            od_cap=snapshot.od_capacity,
            spot_cap=snapshot.spot_capacity,
            od_vcpus=snapshot.od_vcpus,
            spot_vcpus=snapshot.spot_vcpus,
            od_machines=snapshot.od_machines,
            spot_machines=snapshot.spot_machines,
        )

        def _build_bars_line(snap: WatchRequestStatusOutput) -> RichText:
            progress.update(
                task,
                completed=(snap.fulfilled_capacity if snap.weighted else snap.fulfilled_count),
                od_cap=snap.od_capacity,
                spot_cap=snap.spot_capacity,
                od_vcpus=snap.od_vcpus,
                spot_vcpus=snap.spot_vcpus,
                od_machines=snap.od_machines,
                spot_machines=snap.spot_machines,
            )
            az = render_az_bars(snap.az_stats, total)
            combined = RichText()
            combined.append_text(top_bar.render(progress.tasks[0]))
            combined.append(" ")
            combined.append_text(az)
            return combined

        with Live(
            Group(_build_bars_line(snapshot), _build_stats_line(snapshot)),
            refresh_per_second=2,
        ) as live:
            seconds_since_poll = 0
            while not snapshot.terminal:
                await asyncio.sleep(1)
                seconds_since_poll += 1
                live.update(Group(_build_bars_line(snapshot), _build_stats_line(snapshot)))

                if seconds_since_poll >= interval:
                    seconds_since_poll = 0
                    try:
                        snapshot = await orchestrator.execute(watch_input)
                        consecutive_errors = 0
                        live.update(
                            Group(
                                _build_bars_line(snapshot),
                                _build_stats_line(snapshot),
                            )
                        )
                    except Exception as exc:
                        from orb.infrastructure.logging.logger import get_logger

                        get_logger(__name__).warning(
                            "Watch poll error (%d/%d): %s",
                            consecutive_errors + 1,
                            max_errors,
                            exc,
                        )
                        consecutive_errors += 1
                        if consecutive_errors >= max_errors:
                            return {
                                "request_id": str(request_id),
                                "status": "error",
                                "error": str(exc),
                            }

        return {
            "request_id": snapshot.request_id,
            "status": snapshot.status,
            "fulfilled_count": snapshot.fulfilled_count,
            "requested_count": snapshot.requested_count,
            "terminal": snapshot.terminal,
        }
    except KeyboardInterrupt:
        return {"request_id": str(request_id), "status": "cancelled"}
