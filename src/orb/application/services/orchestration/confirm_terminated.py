"""Orchestrator for operator confirmation that return-request machines are terminated.

Providers such as OCI answer "not found" both for a deleted instance and for
one the caller cannot read, so a return request cannot settle on its own. The
operator states that the instances are gone; before accepting, a best-effort
visibility probe asks the provider through a different path than get-by-ID. An
instance that is still visible and not terminated rejects the confirmation. A
probe that is unavailable, or that cannot see the instance either, accepts it
with a warning: the operator takes responsibility for the claim.
"""

from __future__ import annotations

from typing import Any

from orb.application.dto.commands import ConfirmMachinesTerminatedCommand
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    ConfirmTerminatedInput,
    ConfirmTerminatedOutput,
)
from orb.application.services.provider_registry_service import ProviderRegistryService
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.exceptions import EntityNotFoundError
from orb.domain.base.operations import (
    Operation as ProviderOperation,
    OperationType as ProviderOperationType,
)
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.request.termination_confirmation import (
    InstanceStillVisibleError,
    confirmed_machine_ids,
    confirmed_records,
    normalize_machine_ids,
    sanitize_identity,
    sanitize_reason,
    validate_confirmable,
)

_PROBE_UNAVAILABLE = (
    "Could not check the provider for these instances; the confirmation rests on the operator."
)
_PROBE_NOT_VISIBLE = "not_visible"
_PROBE_PROVIDER_TERMINATED = "provider_reports_terminated"
_PROBE_UNAVAILABLE_TAG = "probe_unavailable"
_TERMINATED_STATES = frozenset({"terminated"})


class ConfirmTerminatedOrchestrator(
    OrchestratorBase[ConfirmTerminatedInput, ConfirmTerminatedOutput]
):
    def __init__(
        self,
        command_bus: CommandBusPort,
        uow_factory: UnitOfWorkFactory,
        provider_registry_service: ProviderRegistryService,
        logger: LoggingPort,
    ) -> None:
        self._command_bus = command_bus
        self._uow_factory = uow_factory
        self._provider_registry_service = provider_registry_service
        self._logger = logger

    async def execute(self, input: ConfirmTerminatedInput) -> ConfirmTerminatedOutput:  # type: ignore[return]
        machine_ids = normalize_machine_ids(input.machine_ids)
        reason = sanitize_reason(input.reason)
        operator = sanitize_identity(input.operator)
        role = sanitize_identity(input.role, default="unknown")

        with self._uow_factory.create_unit_of_work() as uow:
            request = uow.requests.find_by_id(input.request_id)
            if not request:
                raise EntityNotFoundError("Request", input.request_id)
            validate_confirmable(request, machine_ids)
            already = confirmed_machine_ids(request.metadata)
            existing = [r for r in confirmed_records(request) if r.get("machine_id") in machine_ids]
            provider_name = request.provider_name or ""

        pending = [m for m in machine_ids if m not in already]
        if not pending:
            return ConfirmTerminatedOutput(
                request_id=input.request_id,
                already_confirmed=list(machine_ids),
                records=existing,
            )

        verification, warnings = await self._probe(provider_name, input.request_id, pending)

        command = ConfirmMachinesTerminatedCommand(
            request_id=input.request_id,
            machine_ids=machine_ids,
            reason=reason,
            operator=operator,
            role=role,
            verification=verification,
        )
        await self._dispatch("ConfirmMachinesTerminated", self._command_bus.execute(command))
        return ConfirmTerminatedOutput(
            request_id=input.request_id,
            confirmed=list(command.newly_confirmed),
            already_confirmed=list(command.already_confirmed),
            records=list(command.records),
            warnings=warnings if command.newly_confirmed else [],
        )

    async def _probe(
        self, provider_name: str, request_id: str, machine_ids: list[str]
    ) -> tuple[str, list[str]]:
        """Return (verification tag, warnings); raise if an instance is still live."""
        try:
            result = await self._provider_registry_service.execute_operation(
                provider_name,
                ProviderOperation(
                    operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
                    parameters={"instance_ids": machine_ids, "visibility_probe": True},
                    context={"request_id": request_id},
                ),
            )
        except Exception as exc:
            self._logger.warning("Termination visibility probe failed for %s: %s", request_id, exc)
            return _PROBE_UNAVAILABLE_TAG, [_PROBE_UNAVAILABLE]

        if not result.success:
            self._logger.warning(
                "Termination visibility probe unavailable for %s: %s",
                request_id,
                result.error_code,
            )
            return _PROBE_UNAVAILABLE_TAG, [_PROBE_UNAVAILABLE]

        visible: list[dict[str, Any]] = (result.data or {}).get("instances", [])
        live = [
            str(i.get("instance_id"))
            for i in visible
            if str(i.get("status", "")).lower() not in _TERMINATED_STATES
        ]
        if live:
            raise InstanceStillVisibleError(
                "The provider still shows these instances as not terminated: "
                + ", ".join(sorted(live)),
                details={"machine_ids": sorted(live)},
            )
        return (_PROBE_PROVIDER_TERMINATED if visible else _PROBE_NOT_VISIBLE), []
