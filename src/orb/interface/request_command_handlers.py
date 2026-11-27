"""Request command handlers for the interface layer."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, Union

from orb.infrastructure.error.decorators import handle_interface_exceptions

if TYPE_CHECKING:
    import argparse

    from orb.application.dto.interface_response import InterfaceResponse
    from orb.application.ports.scheduler_port import SchedulerPort
    from orb.application.services.orchestration.acquire_machines import (
        AcquireMachinesOrchestrator,
    )
    from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator
    from orb.application.services.orchestration.confirm_terminated import (
        ConfirmTerminatedOrchestrator,
    )
    from orb.application.services.orchestration.return_machines import ReturnMachinesOrchestrator
    from orb.domain.base import UnitOfWorkFactory
    from orb.interface.response_formatting_service import ResponseFormattingService


def _cli_operator() -> str:
    """Return ``cli:<user>@<host>``; advisory, as both parts come from the local environment."""
    import getpass
    import socket

    try:
        user = getpass.getuser()
    except Exception:
        user = "unknown"
    return f"cli:{user}@{socket.gethostname()}"


class RequestWriteHandlers:
    """CLI handlers that acquire, return, cancel and confirm provisioning requests."""

    def __init__(
        self,
        acquire_orchestrator: AcquireMachinesOrchestrator,
        return_orchestrator: ReturnMachinesOrchestrator,
        cancel_orchestrator: CancelRequestOrchestrator,
        confirm_orchestrator: ConfirmTerminatedOrchestrator,
        uow_factory: UnitOfWorkFactory,
        formatter: ResponseFormattingService,
        scheduler: SchedulerPort,
    ) -> None:
        self._acquire_orchestrator = acquire_orchestrator
        self._return_orchestrator = return_orchestrator
        self._cancel_orchestrator = cancel_orchestrator
        self._confirm_orchestrator = confirm_orchestrator
        self._uow_factory = uow_factory
        self._formatter = formatter
        self._scheduler = scheduler

    @handle_interface_exceptions(context="request_machines", interface_type="cli")
    async def request_machines(
        self, args: "argparse.Namespace"
    ) -> Union[dict[str, Any], "InterfaceResponse"]:
        """Provision machines from a template, specifying the requested machine count."""
        from orb.application.services.orchestration.dtos import AcquireMachinesInput
        from orb.interface.catalog import OPERATION_CATALOG, Interface

        entry = OPERATION_CATALOG["request_machines"]
        orchestrator = self._acquire_orchestrator
        formatter = self._formatter
        scheduler = self._scheduler
        render = entry.renderer_for(Interface.CLI)

        if hasattr(args, "input_data") and args.input_data:
            parsed_result = scheduler.parse_request_data(args.input_data)
            parsed_data: dict[str, Any] = parsed_result if isinstance(parsed_result, dict) else {}
        else:
            template_id = getattr(args, "template_id", None) or getattr(
                args, "flag_template_id", None
            )
            machine_count = getattr(args, "machine_count", None) or getattr(
                args, "flag_machine_count", None
            )
            parsed_data = {
                "template_id": template_id,
                "requested_count": machine_count,
            }
            node_names = getattr(args, "nodes", None)
            if node_names:
                parsed_data["node_names"] = scheduler.expand_node_range(node_names)

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
                additional_data={
                    k: v
                    for k, v in parsed_data.items()
                    if k not in ("template_id", "requested_count")
                },
            )
        )

        return render(formatter, result)

    @handle_interface_exceptions(context="request_return_machines", interface_type="cli")
    async def request_return_machines(
        self, args: "argparse.Namespace"
    ) -> Union[dict[str, Any], "InterfaceResponse"]:
        """Return machines to the provider by machine IDs, request ID, or all (with --force)."""
        from orb.application.services.orchestration.dtos import ReturnMachinesInput
        from orb.interface.catalog import OPERATION_CATALOG, Interface

        entry = OPERATION_CATALOG["return_machines"]
        orchestrator = self._return_orchestrator
        formatter = self._formatter
        scheduler = self._scheduler

        has_all = getattr(args, "all", False)
        request_id = getattr(args, "request_id", None)
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
            machine_ids = getattr(args, "machine_ids_flag", None) or getattr(
                args, "machine_ids", []
            )

        # Resolve --nodes to machine IDs by querying machine storage by name
        nodes_arg = getattr(args, "nodes", None)
        if nodes_arg and not machine_ids:
            node_names_set = set(scheduler.expand_node_range(nodes_arg))
            with self._uow_factory.create_unit_of_work() as uow:
                all_machines = uow.machines.get_all()
            machine_ids = [
                str(m.machine_id)
                for m in all_machines
                if m.name in node_names_set and not m.status.is_terminal
            ]

        has_specific_ids = bool(machine_ids)

        if request_id and has_specific_ids:
            return {
                "error": "Cannot use --request-id with specific machine IDs",
                "message": "Use either --request-id or specific machine IDs, not both",
            }

        if request_id and has_all:
            return {
                "error": "Cannot use --request-id with --all",
                "message": "Use either --request-id or --all, not both",
            }

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

        if not has_all and not machine_ids and not request_id:
            return {
                "error": "Machine IDs are required",
                "message": "Machine IDs must be provided either as arguments or in JSON file",
            }

        result = await orchestrator.execute(
            ReturnMachinesInput(
                machine_ids=machine_ids,
                request_id=request_id,
                all_machines=has_all,
                force=getattr(args, "force", False),
                wait=getattr(args, "wait", False),
                timeout_seconds=getattr(args, "timeout", 300),
                provider_name=getattr(args, "provider_name", None) or None,
                provider_type=getattr(args, "provider_type", None) or None,
            )
        )

        return entry.renderer_for(Interface.CLI)(formatter, result)

    @handle_interface_exceptions(context="cancel_request", interface_type="cli")
    async def cancel_request(
        self, args: "argparse.Namespace"
    ) -> Union[dict[str, Any], "InterfaceResponse"]:
        """Cancel a provisioning request by ID, with an optional cancellation reason."""
        from orb.application.services.orchestration.dtos import CancelRequestInput
        from orb.interface.catalog import OPERATION_CATALOG, Interface

        entry = OPERATION_CATALOG["cancel_request"]
        orchestrator = self._cancel_orchestrator
        formatter = self._formatter

        request_id = getattr(args, "request_id", None) or getattr(args, "flag_request_id", None)
        if not request_id:
            return {"error": "Request ID is required", "message": "Request ID must be provided"}

        reason = getattr(args, "reason", None) or "Cancelled via API"
        result = await orchestrator.execute(
            CancelRequestInput(
                request_id=request_id, reason=reason, force=getattr(args, "force", False)
            )
        )

        response = entry.renderer_for(Interface.CLI)(formatter, result)
        if (
            (
                getattr(result, "cancel_effective", False)
                or getattr(result, "status", None) == "cancelled"
            )
            and hasattr(response, "exit_code")
            and response.exit_code != 0
        ):
            # A cancel that took effect must exit 0. The generic request-status mapping
            # treats "cancelled" (and the settled partial / timeout states) as problem
            # states, which is right when polling a request but wrong for the command
            # that just cancelled it. The response still reports the request's true
            # status.
            response = dataclasses.replace(response, exit_code=0)
        return response

    @handle_interface_exceptions(context="confirm_terminated", interface_type="cli")
    async def confirm_terminated(
        self, args: "argparse.Namespace"
    ) -> Union[dict[str, Any], "InterfaceResponse"]:
        """Record that machines of a return request are terminated."""
        from orb.application.services.orchestration.dtos import ConfirmTerminatedInput
        from orb.domain.request.termination_confirmation import (
            InstanceStillVisibleError,
            TerminationNotVerifiedError,
        )
        from orb.interface.catalog import OPERATION_CATALOG, Interface

        request_id = getattr(args, "request_id", None) or getattr(args, "flag_request_id", None)
        if not request_id:
            return {"error": "Request ID is required", "message": "Request ID must be provided"}

        entry = OPERATION_CATALOG["confirm_terminated"]
        try:
            result = await self._confirm_orchestrator.execute(
                ConfirmTerminatedInput(
                    request_id=request_id,
                    machine_ids=list(getattr(args, "machine_ids", None) or []),
                    reason=getattr(args, "reason", None) or "",
                    operator=_cli_operator(),
                    role="cli",
                    force=bool(getattr(args, "force", False)),
                )
            )
        except InstanceStillVisibleError as exc:
            return {"error": "Instance still visible", "message": exc.message}
        except TerminationNotVerifiedError as exc:
            return {"error": "Termination not verified", "message": exc.message}
        return entry.renderer_for(Interface.CLI)(self._formatter, result)
