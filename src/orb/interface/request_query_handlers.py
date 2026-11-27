"""Request query command handlers for the interface layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Union

from orb.infrastructure.error.decorators import handle_interface_exceptions
from orb.interface.pagination_hint import print_cursor_hint

if TYPE_CHECKING:
    import argparse

    from orb.application.dto.interface_response import InterfaceResponse
    from orb.application.ports.query_bus_port import QueryBusPort
    from orb.application.ports.scheduler_port import SchedulerPort
    from orb.application.services.orchestration.get_request_status import (
        GetRequestStatusOrchestrator,
    )
    from orb.application.services.orchestration.list_reclaimed_machines import (
        ListReclaimedMachinesOrchestrator,
    )
    from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator
    from orb.application.services.orchestration.list_return_requests import (
        ListReturnRequestsOrchestrator,
    )
    from orb.application.services.orchestration.watch_request_status import (
        WatchRequestStatusOrchestrator,
    )
    from orb.interface.response_formatting_service import ResponseFormattingService


class RequestReadHandlers:
    """CLI handlers that read provisioning requests."""

    def __init__(
        self,
        status_orchestrator: GetRequestStatusOrchestrator,
        list_requests_orchestrator: ListRequestsOrchestrator,
        list_return_requests_orchestrator: ListReturnRequestsOrchestrator,
        reclaimed_orchestrator: ListReclaimedMachinesOrchestrator,
        watch_orchestrator: WatchRequestStatusOrchestrator,
        query_bus: QueryBusPort,
        formatter: ResponseFormattingService,
        scheduler: SchedulerPort,
    ) -> None:
        self._status_orchestrator = status_orchestrator
        self._list_requests_orchestrator = list_requests_orchestrator
        self._list_return_requests_orchestrator = list_return_requests_orchestrator
        self._reclaimed_orchestrator = reclaimed_orchestrator
        self._watch_orchestrator = watch_orchestrator
        self._query_bus = query_bus
        self._formatter = formatter
        self._scheduler = scheduler

    @handle_interface_exceptions(context="get_request_status", interface_type="cli")
    async def get_request_status(
        self, args: "argparse.Namespace"
    ) -> Union[dict[str, Any], "InterfaceResponse"]:
        """Get the status of one or more provisioning requests by ID, or all requests."""
        from orb.application.services.orchestration.dtos import GetRequestStatusInput
        from orb.interface.catalog import OPERATION_CATALOG, Interface

        entry = OPERATION_CATALOG["get_request_status"]
        orchestrator = self._status_orchestrator
        formatter = self._formatter
        render = entry.renderer_for(Interface.CLI)

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
            return render(formatter, result)

        # Collect request IDs from args or input_data
        scheduler = self._scheduler
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
                wait=bool(getattr(args, "wait", False)),
                timeout_seconds=int(getattr(args, "timeout", 300)),
            )
        )
        return render(formatter, result)

    @handle_interface_exceptions(context="get_return_requests", interface_type="cli")
    async def get_return_requests(
        self, args: "argparse.Namespace"
    ) -> Union[dict[str, Any], "InterfaceResponse"]:
        """List machine return requests, optionally filtered by status, provider, or filter expressions.

        When the active scheduler recognises the call as its poll for provider-reclaimed
        machines, answer that instead (see ``list_reclaimed_machines``).
        """
        from orb.application.services.orchestration.dtos import ListReturnRequestsInput
        from orb.interface.catalog import OPERATION_CATALOG, Interface

        input_data = getattr(args, "input_data", None)
        if self._scheduler.is_provider_reclaim_poll(input_data):
            return await self._list_reclaimed_machines(input_data)

        entry = OPERATION_CATALOG["list_return_requests"]
        orchestrator = self._list_return_requests_orchestrator
        formatter = self._formatter

        result = await orchestrator.execute(
            ListReturnRequestsInput(
                status=getattr(args, "status", None),
                limit=getattr(args, "limit", 50),
                offset=getattr(args, "offset", None) or 0,
                cursor=getattr(args, "cursor", None),
                provider_name=getattr(args, "provider_name", None),
                provider_type=getattr(args, "provider_type", None),
                filter_expressions=getattr(args, "filter", None) or [],
            )
        )
        print_cursor_hint(args, result)
        return entry.renderer_for(Interface.CLI)(formatter, result)

    async def _list_reclaimed_machines(
        self, input_data: Any
    ) -> Union[dict[str, Any], "InterfaceResponse"]:
        """Answer which of the caller's machines the provider has reclaimed."""
        from orb.application.services.orchestration.dtos import ListReclaimedMachinesInput
        from orb.interface.catalog import OPERATION_CATALOG, Interface

        entry = OPERATION_CATALOG["list_reclaimed_machines"]
        machines = input_data.get("machines") if isinstance(input_data, dict) else None
        identifiers: list[str] = []
        for machine in machines or []:
            if isinstance(machine, dict):
                identifiers.extend(
                    str(v)
                    for v in (
                        machine.get("name"),
                        machine.get("machineId"),
                        machine.get("machine_id"),
                    )
                    if v
                )
        result = await self._reclaimed_orchestrator.execute(
            ListReclaimedMachinesInput(identifiers=identifiers)
        )
        return entry.renderer_for(Interface.CLI)(self._formatter, result)

    @handle_interface_exceptions(context="list_requests", interface_type="cli")
    async def list_requests(
        self, args: "argparse.Namespace"
    ) -> Union[dict[str, Any], "InterfaceResponse"]:
        """List provisioning requests, optionally filtered by status with pagination."""
        from orb.application.services.orchestration.dtos import ListRequestsInput
        from orb.interface.catalog import OPERATION_CATALOG, Interface

        entry = OPERATION_CATALOG["list_requests"]
        orchestrator = self._list_requests_orchestrator
        formatter = self._formatter

        result = await orchestrator.execute(
            ListRequestsInput(
                status=getattr(args, "status", None),
                limit=getattr(args, "limit", None) or 50,
                sync=getattr(args, "sync", False),
                offset=getattr(args, "offset", None) or 0,
                cursor=getattr(args, "cursor", None),
                template_id=getattr(args, "template_id", None),
                request_type=getattr(args, "request_type", None),
                provider_name=getattr(args, "provider_name", None),
                provider_type=getattr(args, "provider_type", None),
                filter_expressions=getattr(args, "filter", None) or [],
                start_time=getattr(args, "start_time", None),
                end_time=getattr(args, "end_time", None),
            )
        )
        print_cursor_hint(args, result)
        return entry.renderer_for(Interface.CLI)(formatter, result)

    @handle_interface_exceptions(context="watch_request_status", interface_type="cli")
    async def watch_request_status(
        self, args: "argparse.Namespace"
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
        from orb.cli.progress_bar import DotPreciseBar, render_az_bars

        orchestrator = self._watch_orchestrator

        request_id = getattr(args, "request_id", None)
        if not request_id:
            from orb.application.services.orchestration.dtos import ListRequestsInput

            list_result = await self._list_requests_orchestrator.execute(
                ListRequestsInput(limit=100)
            )
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

    @handle_interface_exceptions(context="get_multiple_requests", interface_type="cli")
    async def get_multiple_requests(
        self, args: "argparse.Namespace"
    ) -> Union[dict[str, Any], "InterfaceResponse"]:
        """Fetch multiple requests by ID via GetMultipleRequestsQuery through the query bus."""
        from orb.application.dto.bulk_queries import GetMultipleRequestsQuery

        request_ids: list[str] = []
        if hasattr(args, "request_ids") and args.request_ids:
            request_ids.extend(args.request_ids)
        if hasattr(args, "flag_request_ids") and args.flag_request_ids:
            request_ids.extend(args.flag_request_ids)
        if hasattr(args, "flag_ids") and args.flag_ids:
            request_ids.extend(args.flag_ids)

        if not request_ids:
            return {
                "error": "No request IDs provided",
                "message": "At least one request ID required",
            }

        query = GetMultipleRequestsQuery(
            request_ids=[str(rid) for rid in request_ids],
            include_machines=bool(getattr(args, "include_machines", True)),
        )
        result = await self._query_bus.execute(query)
        return {
            "requests": [
                r.model_dump() if hasattr(r, "model_dump") else vars(r) for r in result.requests
            ],
            "found_count": result.found_count,
            "not_found_ids": result.not_found_ids,
            "total_requested": result.total_requested,
        }
