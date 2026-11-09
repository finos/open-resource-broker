"""Command handler recording operator-confirmed machine termination."""

from __future__ import annotations

from orb.application.base.handlers import BaseCommandHandler
from orb.application.commands.request_lifecycle_handlers import _resolve_concurrency_max_retries
from orb.application.decorators import command_handler
from orb.application.dto.commands import ConfirmMachinesTerminatedCommand
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.exceptions import ConcurrencyError, EntityNotFoundError
from orb.domain.base.ports import (
    ConfigurationPort,
    ErrorHandlingPort,
    EventPublisherPort,
    LoggingPort,
)
from orb.domain.request.termination_confirmation import (
    CONFIRMED_TERMINATED_KEY,
    VERIFICATION_OPERATOR_ATTESTED,
    build_record,
    confirmed_machine_ids,
    confirmed_records,
    validate_confirmable,
)


@command_handler(ConfirmMachinesTerminatedCommand)  # type: ignore[arg-type]
class ConfirmMachinesTerminatedHandler(
    BaseCommandHandler[ConfirmMachinesTerminatedCommand, None]  # type: ignore[type-var]
):
    """Append confirmation records to a return request.

    The request is re-read and re-validated inside every write attempt and
    saved with its version, so concurrent confirmations never overwrite each
    other: the loser of a version race reloads and appends to the winner's
    result. IDs that are already confirmed are skipped, which makes a repeated
    confirmation a no-op that writes nothing.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
        config_port: ConfigurationPort,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self.uow_factory = uow_factory
        self._config_port = config_port

    async def execute_command(self, command: ConfirmMachinesTerminatedCommand) -> None:
        retries = _resolve_concurrency_max_retries(self._config_port)
        for attempt in range(retries + 1):
            try:
                with self.uow_factory.create_unit_of_work() as uow:
                    request = uow.requests.find_by_id(command.request_id)
                    if not request:
                        raise EntityNotFoundError("Request", command.request_id)

                    validate_confirmable(request, command.machine_ids)
                    existing = confirmed_machine_ids(request.metadata)
                    fresh = [m for m in command.machine_ids if m not in existing]
                    command.already_confirmed = [m for m in command.machine_ids if m in existing]
                    command.newly_confirmed = fresh
                    if not fresh:
                        command.records = [
                            r
                            for r in confirmed_records(request)
                            if r.get("machine_id") in command.machine_ids
                        ]
                        return

                    new_records = [
                        build_record(
                            machine_id,
                            operator=command.operator,
                            role=command.role,
                            reason=command.reason,
                            verification=command.verifications.get(
                                machine_id, VERIFICATION_OPERATOR_ATTESTED
                            ),
                        )
                        for machine_id in fresh
                    ]
                    updated = request.update_metadata(
                        {CONFIRMED_TERMINATED_KEY: [*confirmed_records(request), *new_records]}
                    )
                    uow.requests.save(updated)

                command.records = new_records
                for record in new_records:
                    self.logger.warning(
                        "Operator confirmed machine terminated: request=%s machine=%s "
                        "operator=%s role=%s verification=%s reason=%s",
                        command.request_id,
                        record["machine_id"],
                        record["operator"],
                        record["role"],
                        record["verification"],
                        record["reason"],
                    )
                return
            except ConcurrencyError:
                if attempt >= retries:
                    raise
                self.logger.warning(
                    "ConcurrencyError confirming termination on %s (attempt %d/%d); retrying.",
                    command.request_id,
                    attempt + 1,
                    retries,
                )
