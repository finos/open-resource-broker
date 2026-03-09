"""Domain cleanup command handlers following CQRS pattern."""

from __future__ import annotations

from datetime import datetime, timedelta

from orb.application.base.handlers import BaseCommandHandler
from orb.application.decorators import command_handler
from orb.application.dto.commands import (
    CleanupAllResourcesCommand,
    CleanupOldRequestsCommand,
)
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.ports import ErrorHandlingPort, EventPublisherPort, LoggingPort
from orb.domain.machine.repository import MachineRepository
from orb.domain.request.repository import RequestRepository
from orb.infrastructure.events.infrastructure_events import ResourcesCleanedEvent


@command_handler(CleanupOldRequestsCommand)  # type: ignore[arg-type]
class CleanupOldRequestsHandler(BaseCommandHandler[CleanupOldRequestsCommand, None]):  # type: ignore[type-var]
    """Handler for cleaning up old requests using domain commands.

    CQRS Compliance: Returns None. Results are stored in command fields.
    """

    def __init__(
        self,
        request_repository: RequestRepository,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        """Initialize the instance."""
        super().__init__(logger, event_publisher, error_handler)
        self._request_repository = request_repository
        self._uow_factory = uow_factory

    async def validate_command(self, command: CleanupOldRequestsCommand) -> None:
        """Validate cleanup old requests command."""
        await super().validate_command(command)
        if not hasattr(command, "older_than_days") or command.older_than_days <= 0:
            raise ValueError("older_than_days must be positive")

    async def execute_command(self, command: CleanupOldRequestsCommand) -> None:
        """Handle cleanup old requests command.

        CQRS Compliance: Returns None. Results stored in command fields:
        - command.requests_cleaned: Number of requests cleaned
        - command.request_ids_found: List of request IDs found (dry run)
        """
        self.logger.info("Cleaning up requests older than %s days", command.older_than_days)
        cutoff_date = datetime.utcnow() - timedelta(days=command.older_than_days)

        try:
            with self._uow_factory.create_unit_of_work() as uow:
                # Find old requests to cleanup
                old_requests = uow.requests.find_old_requests(
                    cutoff_date=cutoff_date, statuses=command.statuses_to_cleanup
                )

                if command.dry_run:
                    self.logger.info("DRY RUN: Would cleanup %s requests", len(old_requests))
                    command.requests_cleaned = 0
                    command.request_ids_found = [str(req.request_id) for req in old_requests]
                    return

                # Actually cleanup requests
                cleaned_count = 0
                for request in old_requests:
                    try:
                        uow.requests.delete(request.request_id)
                        cleaned_count += 1
                        self.logger.debug("Cleaned up request: %s", request.request_id)
                    except Exception as e:
                        # Per-item exception handling - appropriate to keep
                        self.logger.error("Failed to cleanup request %s: %s", request.request_id, e)

                uow.commit()

                # Publish cleanup event
                cleanup_event = ResourcesCleanedEvent(
                    aggregate_id="cleanup-operation",
                    aggregate_type="CleanupOperation",
                    resource_type="Request",
                    resource_id="multiple",
                    provider="system",
                    resource_count=cleaned_count,
                    cleanup_reason=f"Cleanup requests older than {command.older_than_days} days",
                )
                if self.event_publisher is not None:
                    self.event_publisher.publish(cleanup_event)

                # Store results in command
                command.requests_cleaned = cleaned_count
                command.request_ids_found = []

                self.logger.info("Successfully cleaned up %s old requests", cleaned_count)

        except Exception as e:
            self.logger.error("Failed to cleanup old requests: %s", e)
            raise


@command_handler(CleanupAllResourcesCommand)  # type: ignore[arg-type]
class CleanupAllResourcesHandler(BaseCommandHandler[CleanupAllResourcesCommand, None]):  # type: ignore[type-var]
    """Handler for cleaning up all resources (requests and machines).

    CQRS Compliance: Returns None. Results are stored in command fields.
    """

    def __init__(
        self,
        request_repository: RequestRepository,
        machine_repository: MachineRepository,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._request_repository = request_repository
        self._machine_repository = machine_repository
        self._uow_factory = uow_factory

    async def validate_command(self, command: CleanupAllResourcesCommand) -> None:
        """Validate cleanup all resources command."""
        await super().validate_command(command)
        if command.older_than_days <= 0:
            raise ValueError("older_than_days must be positive")

    async def execute_command(self, command: CleanupAllResourcesCommand) -> None:
        """Handle cleanup all resources command.

        CQRS Compliance: Returns None. Results stored in command fields:
        - command.requests_cleaned: Number of requests cleaned
        - command.machines_cleaned: Number of machines cleaned
        - command.total_cleaned: Total resources cleaned
        """
        self.logger.info("Cleaning up all resources older than %s days", command.older_than_days)
        cutoff_date = datetime.utcnow() - timedelta(days=command.older_than_days)

        try:
            with self._uow_factory.create_unit_of_work() as uow:
                # Find resources to cleanup
                old_requests = uow.requests.find_old_requests(
                    cutoff_date=cutoff_date, include_pending=command.include_pending
                )

                old_machines = uow.machines.find_old_machines(
                    cutoff_date=cutoff_date,
                    statuses=(["terminated", "failed"] if not command.include_pending else None),
                )

                if command.dry_run:
                    self.logger.info(
                        "DRY RUN: Would cleanup %s requests and %s machines",
                        len(old_requests),
                        len(old_machines),
                    )
                    command.requests_cleaned = 0
                    command.machines_cleaned = 0
                    command.total_cleaned = 0
                    return

                # Cleanup resources
                requests_cleaned = 0
                machines_cleaned = 0

                # Cleanup requests
                for request in old_requests:
                    try:
                        uow.requests.delete(request.request_id)
                        requests_cleaned += 1
                    except Exception as e:
                        # Per-item exception handling - appropriate to keep
                        self.logger.error("Failed to cleanup request %s: %s", request.request_id, e)

                # Cleanup machines
                for machine in old_machines:
                    try:
                        uow.machines.delete(machine.machine_id)
                        machines_cleaned += 1
                    except Exception as e:
                        # Per-item exception handling - appropriate to keep
                        self.logger.error("Failed to cleanup machine %s: %s", machine.machine_id, e)

                uow.commit()

                # Publish cleanup event
                cleanup_event = ResourcesCleanedEvent(
                    aggregate_id="cleanup-operation",
                    aggregate_type="CleanupOperation",
                    resource_type="Multiple",
                    resource_id="all",
                    provider="system",
                    resource_count=requests_cleaned + machines_cleaned,
                    cleanup_reason=f"Cleanup all resources older than {command.older_than_days} days",
                )
                if self.event_publisher is not None:
                    self.event_publisher.publish(cleanup_event)

                # Store results in command
                command.requests_cleaned = requests_cleaned
                command.machines_cleaned = machines_cleaned
                command.total_cleaned = requests_cleaned + machines_cleaned

                self.logger.info(
                    "Successfully cleaned up %s requests and %s machines",
                    requests_cleaned,
                    machines_cleaned,
                )

        except Exception as e:
            self.logger.error("Failed to cleanup all resources: %s", e)
            raise
