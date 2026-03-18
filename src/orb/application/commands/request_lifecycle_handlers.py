"""Command handlers for request lifecycle operations (status, cancel, complete)."""

from __future__ import annotations

from orb.application.base.handlers import BaseCommandHandler
from orb.application.decorators import command_handler
from orb.application.dto.commands import (
    CancelRequestCommand,
    CompleteRequestCommand,
    UpdateRequestStatusCommand,
)
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.exceptions import EntityNotFoundError
from orb.domain.base.ports import (
    ErrorHandlingPort,
    EventPublisherPort,
    LoggingPort,
)
from orb.domain.request.repository import RequestRepository
from orb.domain.request.request_types import RequestStatus


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
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self.uow_factory = uow_factory
        self._request_repository = request_repository

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

        try:
            # Find request in the storage
            with self.uow_factory.create_unit_of_work() as uow:
                request = uow.requests.find_by_id(command.request_id)
                if not request:
                    raise EntityNotFoundError("Request", command.request_id)

            # Update status
            request = request.update_status(
                status=command.status,
                message=command.message or "",
            )

            # Save changes and get extracted events
            with self.uow_factory.create_unit_of_work() as uow:
                events = uow.requests.save(request)
                for event in events:
                    self.event_publisher.publish(event)  # type: ignore[union-attr]

            self.logger.info("Request status updated: %s -> %s", command.request_id, command.status)

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
        request_repository: RequestRepository,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._request_repository = request_repository

    async def validate_command(self, command: CancelRequestCommand) -> None:
        """Validate cancel request command."""
        await super().validate_command(command)
        if not command.request_id:
            raise ValueError("request_id is required")

    async def execute_command(self, command: CancelRequestCommand) -> None:
        """Handle request cancellation command."""
        self.logger.info("Canceling request: %s", command.request_id)

        try:
            request = self._request_repository.find_by_id(command.request_id)
            if not request:
                raise EntityNotFoundError("Request", command.request_id)

            cancelled_request = request.cancel(reason=command.reason)

            events = self._request_repository.save(cancelled_request)
            for event in events or []:
                self.event_publisher.publish(event)  # type: ignore[union-attr]

            self.logger.info("Request canceled: %s", command.request_id)
            command.cancelled = True
            command.final_status = RequestStatus.CANCELLED.value

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
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._request_repository = request_repository

    async def validate_command(self, command: CompleteRequestCommand) -> None:
        """Validate complete request command."""
        await super().validate_command(command)
        if not command.request_id:
            raise ValueError("request_id is required")

    async def execute_command(self, command: CompleteRequestCommand) -> None:
        """Handle request completion command."""
        self.logger.info("Completing request: %s", command.request_id)

        try:
            request = self._request_repository.find_by_id(command.request_id)
            if not request:
                raise EntityNotFoundError("Request", command.request_id)

            request = request.update_status(RequestStatus.COMPLETED, "Request completed")  # type: ignore[attr-defined]

            events = self._request_repository.save(request)
            for event in events or []:
                self.event_publisher.publish(event)  # type: ignore[union-attr]

            self.logger.info("Request completed: %s", command.request_id)

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
