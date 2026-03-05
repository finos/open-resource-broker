"""Command handlers for request sync and machine ID population operations."""

from __future__ import annotations

from application.base.handlers import BaseCommandHandler
from application.decorators import command_handler
from application.dto.commands import (
    PopulateMachineIdsCommand,
    SyncRequestCommand,
)
from domain.base import UnitOfWorkFactory
from domain.base.exceptions import EntityNotFoundError
from domain.base.ports import (
    ContainerPort,
    ErrorHandlingPort,
    EventPublisherPort,
    LoggingPort,
    ProviderSelectionPort,
)
from domain.base.ports.asg_query_port import ASGQueryPort


@command_handler(PopulateMachineIdsCommand)  # type: ignore[arg-type]
class PopulateMachineIdsHandler(BaseCommandHandler[PopulateMachineIdsCommand, None]):  # type: ignore[type-var]
    """Handler for populating requests with machine IDs."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        container: ContainerPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
        provider_selection_port: ProviderSelectionPort,
    ):
        super().__init__(logger, event_publisher, error_handler)
        self.uow_factory = uow_factory
        self._container = container
        self._provider_selection_port = provider_selection_port

    async def execute_command(self, command: PopulateMachineIdsCommand) -> None:
        """Discover and store machine IDs from provider resources."""

        with self.uow_factory.create_unit_of_work() as uow:
            from domain.request.value_objects import RequestId

            request = uow.requests.get_by_id(RequestId(value=command.request_id))
            if not request or not request.needs_machine_id_population():
                return

            discovered_ids = await self._discover_machine_ids(request)
            if discovered_ids:
                updated_request = request.update_machine_ids(discovered_ids)
                uow.requests.save(updated_request)

                self.logger.info(
                    "Populated request %s with %d machine IDs",
                    command.request_id,
                    len(discovered_ids),
                )

    async def _discover_machine_ids(self, request) -> list[str]:
        """Discover machine IDs from provider resources."""
        try:
            from domain.base.operations import (
                Operation as ProviderOperation,
                OperationType as ProviderOperationType,
            )

            if not request.resource_ids:
                return []

            operation = ProviderOperation(
                operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
                parameters={"resource_ids": request.resource_ids},
            )

            from domain.base.ports.configuration_port import ConfigurationPort

            self._container.get(ConfigurationPort)

            result = await self._provider_selection_port.execute_operation(
                request.provider_name, operation
            )

            if result.success and result.data and "instances" in result.data:
                return [
                    instance.get("instance_id")
                    for instance in result.data["instances"]
                    if instance.get("instance_id")
                ]

            return []

        except Exception as e:
            self.logger.error(
                "Failed to discover machine IDs for request %s: %s",
                request.request_id,
                e,
                exc_info=True,
                extra={
                    "request_id": str(request.request_id),
                    "provider_name": request.provider_name
                    if hasattr(request, "provider_name")
                    else None,
                    "resource_count": len(request.resource_ids)
                    if hasattr(request, "resource_ids") and request.resource_ids
                    else 0,
                    "error_type": type(e).__name__,
                },
            )
            return []


@command_handler(SyncRequestCommand)  # type: ignore[arg-type]
class SyncRequestHandler(BaseCommandHandler[SyncRequestCommand, None]):  # type: ignore[type-var]
    """Handler for syncing request with provider state."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        container: ContainerPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
        asg_query_port: ASGQueryPort,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self.uow_factory = uow_factory
        self._container = container

        from application.services.asg_metadata_service import ASGMetadataService

        self._asg_metadata_service = ASGMetadataService(uow_factory, asg_query_port, logger)

    async def execute_command(self, command: SyncRequestCommand) -> None:
        """Execute sync request command."""
        self.logger.info("Syncing request with provider: %s", command.request_id)

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                from domain.request.value_objects import RequestId

                request = uow.requests.get_by_id(RequestId(value=command.request_id))

                if not request:
                    raise EntityNotFoundError("Request", command.request_id)

            from application.services.request_query_service import RequestQueryService

            query_service = RequestQueryService(self.uow_factory, self.logger)
            db_machines = await query_service.get_machines_for_request(request)

            from application.services.machine_sync_service import MachineSyncService
            from application.services.request_status_service import RequestStatusService

            machine_sync_service = self._container.get(MachineSyncService)
            status_service = self._container.get(RequestStatusService)

            (
                provider_machines,
                provider_metadata,
            ) = await machine_sync_service.fetch_provider_machines(request, db_machines)

            synced_machines, _ = await machine_sync_service.sync_machines_with_provider(
                request, db_machines, provider_machines
            )

            new_status, status_message = status_service.determine_status_from_machines(
                db_machines, synced_machines, request, provider_metadata
            )

            if new_status:
                await status_service.update_request_status(
                    request, new_status, status_message or ""
                )

            if request.metadata.get("provider_api") == "ASG":
                await self._asg_metadata_service.update_asg_metadata_if_needed(
                    request, synced_machines
                )

            self.logger.info("Successfully synced request: %s", command.request_id)

        except EntityNotFoundError:
            self.logger.error(
                "Request not found for sync: %s",
                command.request_id,
                extra={"request_id": command.request_id},
            )
            raise
        except Exception as e:
            self.logger.error(
                "Failed to sync request %s with provider: %s",
                command.request_id,
                e,
                exc_info=True,
                extra={
                    "request_id": command.request_id,
                    "error_type": type(e).__name__,
                },
            )
            raise
