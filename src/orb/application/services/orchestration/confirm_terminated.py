"""Orchestrator for confirming that return-request machines are terminated.

Some providers answer "not found" both for a deleted instance and for one the
caller cannot read, so a return request cannot settle on its own. Before a
confirmation is recorded, the provider is asked, through an optional visibility
probe on the instance-status operation, what it can say about each machine:

* reported gone or terminated: recorded as ``verified``;
* still present: refused;
* unknown (the provider has no probe, the probe failed, or it could not tell):
  refused unless the operator forces it, then recorded as ``operator_attested``.

Providers need no code for this: one that ignores the probe flag yields
``unknown`` and the confirmation then needs the explicit override.
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
    VERIFICATION_OPERATOR_ATTESTED,
    VERIFICATION_VERIFIED,
    InstanceStillVisibleError,
    TerminationNotVerifiedError,
    confirmed_machine_ids,
    confirmed_records,
    normalize_machine_ids,
    sanitize_identity,
    sanitize_reason,
    validate_confirmable,
)

_GONE_STATES = frozenset({"gone", "terminated"})
_UNKNOWN_STATES = frozenset({"", "unknown"})
_ATTESTED_WARNING = (
    "The provider could not verify these machines are gone; they are recorded as "
    "operator-attested: "
)


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

        verifications, warnings = await self._verify(
            provider_name, input.request_id, pending, force=input.force
        )

        command = ConfirmMachinesTerminatedCommand(
            request_id=input.request_id,
            machine_ids=machine_ids,
            reason=reason,
            operator=operator,
            role=role,
            verifications=verifications,
        )
        await self._dispatch("ConfirmMachinesTerminated", self._command_bus.execute(command))
        return ConfirmTerminatedOutput(
            request_id=input.request_id,
            confirmed=list(command.newly_confirmed),
            already_confirmed=list(command.already_confirmed),
            records=list(command.records),
            warnings=warnings if command.newly_confirmed else [],
        )

    async def _verify(
        self, provider_name: str, request_id: str, machine_ids: list[str], *, force: bool
    ) -> tuple[dict[str, str], list[str]]:
        """Return (verification per machine, warnings); raise when a machine is refused."""
        states = await self._probe(provider_name, request_id, machine_ids)

        present = sorted(m for m in machine_ids if self._is_present(states.get(m)))
        if present:
            raise InstanceStillVisibleError(
                "The provider still shows these instances as not terminated: " + ", ".join(present),
                details={"machine_ids": present},
            )

        unknown = [m for m in machine_ids if states.get(m) not in _GONE_STATES]
        if unknown and not force:
            raise TerminationNotVerifiedError(
                "The provider could not verify these instances are gone: "
                + ", ".join(sorted(unknown))
                + ". Use the force option to record them as operator-attested.",
                details={"machine_ids": sorted(unknown)},
            )

        verifications = {
            m: VERIFICATION_OPERATOR_ATTESTED if m in unknown else VERIFICATION_VERIFIED
            for m in machine_ids
        }
        warnings = [_ATTESTED_WARNING + ", ".join(unknown)] if unknown else []
        return verifications, warnings

    @staticmethod
    def _is_present(state: str | None) -> bool:
        return state is not None and state not in _GONE_STATES and state not in _UNKNOWN_STATES

    async def _probe(
        self, provider_name: str, request_id: str, machine_ids: list[str]
    ) -> dict[str, str]:
        """Ask the provider about each machine; return its lower-cased state by machine ID.

        A machine missing from the result, or any failure of the probe, leaves the
        machine unknown.
        """
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
            self._logger.warning("Termination probe failed for %s: %s", request_id, exc)
            return {}

        if not result.success:
            self._logger.warning(
                "Termination probe unavailable for %s: %s", request_id, result.error_code
            )
            return {}

        instances: list[dict[str, Any]] = (result.data or {}).get("instances") or []
        return {
            str(i["instance_id"]): str(i.get("status") or "").lower()
            for i in instances
            if i.get("instance_id") is not None
        }
