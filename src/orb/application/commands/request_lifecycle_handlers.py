"""Command handlers for request lifecycle operations (status, cancel, complete)."""

from __future__ import annotations

from datetime import datetime, timezone

from orb.application.base.handlers import BaseCommandHandler
from orb.application.decorators import command_handler
from orb.application.dto.commands import (
    CancelRequestCommand,
    CompleteRequestCommand,
    UpdateRequestStatusCommand,
)
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.diagnostic import DiagnosticCategory, FulfilmentDiagnostic
from orb.domain.base.exceptions import ConcurrencyError, EntityNotFoundError
from orb.domain.base.ports import (
    ConfigurationPort,
    ErrorHandlingPort,
    EventPublisherPort,
    LoggingPort,
)
from orb.domain.request.exceptions import InvalidRequestStateError
from orb.domain.request.fulfilment_state_machine import (
    FulfilmentEvent,
    FulfilmentStateMachine,
)
from orb.domain.request.repository import RequestRepository
from orb.domain.request.request_types import RequestStatus

_DEFAULT_CONCURRENCY_MAX_RETRIES = 3


def _resolve_concurrency_max_retries(config_port: ConfigurationPort) -> int:
    """Read the optimistic-concurrency retry limit from configuration.

    Mirrors the provisioning orchestration service's config access
    (``request_config.get(key, default)``) so there is a single, consistent
    way for the application layer to reach request tuning knobs. Falls back to
    the historical default of 3 when unset, keeping behaviour backward-compatible.
    """
    request_config = config_port.get_request_config()
    return int(request_config.get("concurrency_max_retries", _DEFAULT_CONCURRENCY_MAX_RETRIES))


def _apply_status_via_state_machine(
    state_machine: FulfilmentStateMachine,
    request,
    status: RequestStatus,
    message: str,
):
    """Translate a target status into a fulfilment event and apply it.

    CANCEL / FAIL map to their dedicated events; every other status is fed as a
    synthesised provider verdict so the machine's state mapping (including the
    deadline-dependent partial resolution) governs the outcome uniformly.
    """
    from orb.domain.base.provider_fulfilment import ProviderFulfilment

    now = datetime.now(timezone.utc)
    if status == RequestStatus.CANCELLED:
        return state_machine.apply(
            request, FulfilmentEvent.CANCEL, now=now, reason=message or "Request cancelled"
        )
    if status == RequestStatus.TIMEOUT:
        # TIMEOUT is a deadline-driven internal outcome produced exclusively by
        # the state machine's deadline sweep — it is never a legally commanded
        # status. Rejecting it here prevents the previous behaviour of silently
        # rewriting a commanded TIMEOUT into FAILED (which lost the distinction
        # between "timed out" and "hard-failed").
        raise ValueError(
            "TIMEOUT is not a commandable status; it is set only by the deadline sweep"
        )
    if status == RequestStatus.FAILED:
        diag = FulfilmentDiagnostic(
            category=DiagnosticCategory.INTERNAL,
            summary=message or f"Request {status.value}",
            occurred_at=now,
        )
        return state_machine.apply(
            request, FulfilmentEvent.FAIL, now=now, message=message or "", diagnostic=diag
        )

    verdict_state = {
        RequestStatus.COMPLETED: "fulfilled",
        RequestStatus.IN_PROGRESS: "in_progress",
        RequestStatus.ACQUIRING: "in_progress",
        RequestStatus.PARTIAL: "partial",
        RequestStatus.PARTIAL_PENDING: "partial",
    }.get(status)
    if verdict_state is None:
        # PENDING or anything unmapped — no state-machine event; return as-is.
        return request
    verdict = ProviderFulfilment(state=verdict_state, message=message or "")  # type: ignore[arg-type]
    return state_machine.apply(
        request,
        FulfilmentEvent.PROVIDER_VERDICT,
        now=now,
        fulfilment=verdict,
        message=message or "",
    )


@command_handler(UpdateRequestStatusCommand)  # type: ignore[arg-type]
class UpdateRequestStatusHandler(BaseCommandHandler[UpdateRequestStatusCommand, None]):  # type: ignore[type-var]
    """Handler for updating request status."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        request_repository: RequestRepository,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
        state_machine: FulfilmentStateMachine,
        config_port: ConfigurationPort,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self.uow_factory = uow_factory
        self._request_repository = request_repository
        self._state_machine = state_machine
        self._config_port = config_port

    async def validate_command(self, command: UpdateRequestStatusCommand) -> None:
        """Validate update request status command."""
        await super().validate_command(command)
        if not command.request_id:
            raise ValueError("request_id is required")
        if not command.status:
            raise ValueError("status is required")

    async def execute_command(self, command: UpdateRequestStatusCommand) -> None:
        """Handle request status update command."""
        self.logger.info("Updating request status: %s -> %s", command.request_id, command.status)

        _MAX_CONCURRENCY_RETRIES = _resolve_concurrency_max_retries(self._config_port)

        for attempt in range(_MAX_CONCURRENCY_RETRIES + 1):
            try:
                # Find, update, and save within a single UoW to avoid race conditions.
                # The entire block is safely replayable: each iteration re-reads the
                # current aggregate state from the DB and re-applies the same idempotent
                # status mutation, satisfying the OCC reload-reapply contract.
                with self.uow_factory.create_unit_of_work() as uow:
                    request = uow.requests.find_by_id(command.request_id)
                    if not request:
                        raise EntityNotFoundError("Request", command.request_id)

                    # Route the requested status through the state machine so
                    # timestamps (started_at / deadline_at / last_transition_at)
                    # and the transition table are enforced in one place.
                    request = _apply_status_via_state_machine(
                        self._state_machine, request, command.status, command.message or ""
                    )

                    events = uow.requests.save(request)
                    for event in events:
                        self.event_publisher.publish(event)  # type: ignore[union-attr]

                self.logger.info(
                    "Request status updated: %s -> %s", command.request_id, command.status
                )
                return

            except ConcurrencyError as exc:
                if attempt >= _MAX_CONCURRENCY_RETRIES:
                    self.logger.error(
                        "ConcurrencyError updating request status for %s after %d retries: %s",
                        command.request_id,
                        _MAX_CONCURRENCY_RETRIES,
                        exc,
                    )
                    raise
                self.logger.warning(
                    "ConcurrencyError updating request status for %s (attempt %d/%d); "
                    "reloading and retrying.",
                    command.request_id,
                    attempt + 1,
                    _MAX_CONCURRENCY_RETRIES,
                )
                continue

            except EntityNotFoundError:
                self.logger.error(
                    "Request not found for status update: %s",
                    command.request_id,
                    extra={"request_id": command.request_id},
                )
                raise
            except Exception as e:
                self.logger.error(
                    "Failed to update request status for %s: %s",
                    command.request_id,
                    e,
                    exc_info=True,
                    extra={
                        "request_id": command.request_id,
                        "target_status": command.status,
                        "error_type": type(e).__name__,
                    },
                )
                raise


@command_handler(CancelRequestCommand)  # type: ignore[arg-type]
class CancelRequestHandler(BaseCommandHandler[CancelRequestCommand, None]):  # type: ignore[type-var]
    """Handler for canceling requests."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
        state_machine: FulfilmentStateMachine,
        config_port: ConfigurationPort,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self.uow_factory = uow_factory
        self._state_machine = state_machine
        self._config_port = config_port

    async def validate_command(self, command: CancelRequestCommand) -> None:
        """Validate cancel request command."""
        await super().validate_command(command)
        if not command.request_id:
            raise ValueError("request_id is required")

    async def execute_command(self, command: CancelRequestCommand) -> None:
        """Handle request cancellation command."""
        self.logger.info("Canceling request: %s", command.request_id)

        _MAX_CONCURRENCY_RETRIES = _resolve_concurrency_max_retries(self._config_port)

        for attempt in range(_MAX_CONCURRENCY_RETRIES + 1):
            try:
                with self.uow_factory.create_unit_of_work() as uow:
                    request = uow.requests.find_by_id(command.request_id)
                    if not request:
                        raise EntityNotFoundError("Request", command.request_id)

                    try:
                        cancelled_request = self._state_machine.apply(
                            request,
                            FulfilmentEvent.CANCEL,
                            now=datetime.now(timezone.utc),
                            reason=command.reason,
                        )
                    except InvalidRequestStateError:
                        # The request is already in a terminal state that cannot
                        # transition to CANCELLED (e.g. terminal PARTIAL / TIMEOUT
                        # / COMPLETED / FAILED). Cancelling a settled request is
                        # an idempotent no-op — report its current state rather
                        # than raising, mirroring RequestStatusService's handling
                        # of illegal transitions.
                        self.logger.info(
                            "Cancel is a no-op for request %s already in terminal state %s",
                            command.request_id,
                            request.status.value,
                        )
                        command.cancelled = False
                        command.final_status = request.status.value
                        return

                    events = uow.requests.save(cancelled_request)
                    for event in events or []:
                        self.event_publisher.publish(event)  # type: ignore[union-attr]

                self.logger.info("Request canceled: %s", command.request_id)
                command.cancelled = True
                command.final_status = RequestStatus.CANCELLED.value
                return

            except ConcurrencyError as exc:
                if attempt >= _MAX_CONCURRENCY_RETRIES:
                    self.logger.error(
                        "ConcurrencyError canceling request %s after %d retries: %s",
                        command.request_id,
                        _MAX_CONCURRENCY_RETRIES,
                        exc,
                    )
                    raise
                self.logger.warning(
                    "ConcurrencyError canceling request %s (attempt %d/%d); reloading and retrying.",
                    command.request_id,
                    attempt + 1,
                    _MAX_CONCURRENCY_RETRIES,
                )
                continue
            except EntityNotFoundError:
                self.logger.error(
                    "Request not found for cancellation: %s",
                    command.request_id,
                    extra={"request_id": command.request_id},
                )
                raise
            except Exception as e:
                self.logger.error(
                    "Failed to cancel request %s: %s",
                    command.request_id,
                    e,
                    exc_info=True,
                    extra={
                        "request_id": command.request_id,
                        "reason": command.reason if hasattr(command, "reason") else None,
                        "error_type": type(e).__name__,
                    },
                )
                raise


@command_handler(CompleteRequestCommand)  # type: ignore[arg-type]
class CompleteRequestHandler(BaseCommandHandler[CompleteRequestCommand, None]):  # type: ignore[type-var]
    """Handler for completing requests."""

    def __init__(
        self,
        request_repository: RequestRepository,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
        state_machine: FulfilmentStateMachine,
        config_port: ConfigurationPort,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._request_repository = request_repository
        self._state_machine = state_machine
        self._config_port = config_port

    async def validate_command(self, command: CompleteRequestCommand) -> None:
        """Validate complete request command."""
        await super().validate_command(command)
        if not command.request_id:
            raise ValueError("request_id is required")

    async def execute_command(self, command: CompleteRequestCommand) -> None:
        """Handle request completion command."""
        self.logger.info("Completing request: %s", command.request_id)

        _MAX_CONCURRENCY_RETRIES = _resolve_concurrency_max_retries(self._config_port)

        for attempt in range(_MAX_CONCURRENCY_RETRIES + 1):
            try:
                request = self._request_repository.find_by_id(command.request_id)
                if not request:
                    raise EntityNotFoundError("Request", command.request_id)

                from orb.domain.base.provider_fulfilment import ProviderFulfilment

                completed = self._state_machine.apply(
                    request,
                    FulfilmentEvent.PROVIDER_VERDICT,
                    now=datetime.now(timezone.utc),
                    fulfilment=ProviderFulfilment(state="fulfilled", message="Request completed"),
                    message="Request completed",
                )

                events = self._request_repository.save(completed)
                for event in events or []:
                    self.event_publisher.publish(event)  # type: ignore[union-attr]

                self.logger.info("Request completed: %s", command.request_id)
                return

            except ConcurrencyError as exc:
                if attempt >= _MAX_CONCURRENCY_RETRIES:
                    self.logger.error(
                        "ConcurrencyError completing request %s after %d retries: %s",
                        command.request_id,
                        _MAX_CONCURRENCY_RETRIES,
                        exc,
                    )
                    raise
                self.logger.warning(
                    "ConcurrencyError completing request %s (attempt %d/%d); reloading and retrying.",
                    command.request_id,
                    attempt + 1,
                    _MAX_CONCURRENCY_RETRIES,
                )
                continue
            except EntityNotFoundError:
                self.logger.error(
                    "Request not found for completion: %s",
                    command.request_id,
                    extra={"request_id": command.request_id},
                )
                raise
            except Exception as e:
                self.logger.error(
                    "Failed to complete request %s: %s",
                    command.request_id,
                    e,
                    exc_info=True,
                    extra={
                        "request_id": command.request_id,
                        "error_type": type(e).__name__,
                    },
                )
                raise
