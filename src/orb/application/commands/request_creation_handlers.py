"""Command handlers for request creation operations."""

from __future__ import annotations

from typing import Any

from orb.application.base.handlers import BaseCommandHandler
from orb.application.decorators import command_handler
from orb.application.dto.commands import (
    CreateRequestCommand,
    CreateReturnRequestCommand,
)
from orb.application.ports.query_bus_port import QueryBusPort
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.exceptions import ApplicationError, EntityNotFoundError
from orb.domain.base.ports import (
    ConfigurationPort,
    ContainerPort,
    ErrorHandlingPort,
    EventPublisherPort,
    LoggingPort,
    ProviderConfigPort,
    ProviderSelectionPort,
)


@command_handler(CreateRequestCommand)  # type: ignore[arg-type]
class CreateMachineRequestHandler(BaseCommandHandler[CreateRequestCommand, None]):
    """Handler for creating machine requests.

    CQRS Compliance: This command handler returns None (void).
    The created request_id is stored in command.created_request_id for callers to use.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        container: ContainerPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
        query_bus: QueryBusPort,  # QueryBus is required for template lookup
        provider_selection_port: ProviderSelectionPort,
        provider_config_port: ProviderConfigPort,
    ) -> None:
        """Initialize the instance."""
        super().__init__(logger, event_publisher, error_handler)
        self.uow_factory = uow_factory  # Use UoW factory pattern
        self._container = container
        self._query_bus = query_bus
        self._provider_selection_port = provider_selection_port

        # Initialize services
        from orb.application.services.provider_validation_service import ProviderValidationService
        from orb.application.services.provisioning_orchestration_service import (
            ProvisioningOrchestrationService,
        )
        from orb.application.services.request_creation_service import RequestCreationService
        from orb.application.services.request_status_management_service import (
            RequestStatusManagementService,
        )

        self._request_creation_service = RequestCreationService(logger)
        self._provisioning_service = ProvisioningOrchestrationService(
            container,
            logger,
            provider_selection_port,
            provider_config_port,
            config_port=container.get(ConfigurationPort),
        )
        self._status_service = RequestStatusManagementService(uow_factory, logger)
        self._provider_validation_service = ProviderValidationService(
            container, logger, provider_selection_port
        )

    async def validate_command(self, command: CreateRequestCommand) -> None:
        """Validate create request command."""
        await super().validate_command(command)
        if not command.template_id:
            raise ValueError("template_id is required")
        if not command.requested_count or command.requested_count <= 0:
            raise ValueError("requested_count must be positive")

    async def execute_command(self, command: CreateRequestCommand) -> None:
        """Handle machine request creation by orchestrating services.

        CQRS Compliance: Returns None. The created request_id is stored in
        command.created_request_id for callers to use in subsequent queries.
        """
        self.logger.info("Creating machine request for template: %s", command.template_id)

        # Load template and select provider
        template = await self._load_template(command.template_id)
        selection_result = await self._provider_validation_service.select_and_validate_provider(
            template
        )

        # Create request aggregate
        request = self._request_creation_service.create_machine_request(
            command, template, selection_result
        )

        # Store request_id in command immediately so caller always has it
        command.created_request_id = str(request.request_id)

        # Persist with initial status so get_request_status works even if provisioning throws
        await self._persist_and_publish(request)

        # Handle dry-run fast path
        if request.metadata.get("dry_run", False):
            request = self._handle_dry_run(request)
            await self._persist_and_publish(request)
        else:
            # Execute provisioning and update status
            provisioning_result = await self._provisioning_service.execute_provisioning(
                template, request, selection_result
            )
            request = await self._status_service.update_request_from_provisioning(
                request, provisioning_result
            )
            # Persist final status
            await self._persist_and_publish(request)

        self.logger.info("Machine request created successfully: %s", request.request_id)

    async def _load_template(self, template_id: str) -> Any:
        """Load template using CQRS QueryBus."""
        if not self._query_bus:
            raise ApplicationError("QueryBus is required for template lookup")

        from orb.application.dto.queries import GetTemplateQuery

        template_query = GetTemplateQuery(template_id=template_id)
        template = await self._query_bus.execute(template_query)

        if not template:
            raise EntityNotFoundError("Template", template_id)

        self.logger.debug("Template found: %s (id=%s)", type(template), template.template_id)
        return template

    def _handle_dry_run(self, request: Any) -> Any:
        """Handle dry-run request."""
        from orb.domain.request.value_objects import RequestStatus

        self.logger.info(
            "Skipping actual provisioning for request %s (dry-run mode)",
            request.request_id,
        )
        return request.update_status(
            RequestStatus.COMPLETED, "Request created successfully (dry-run)"
        )

    async def _persist_and_publish(self, request: Any) -> None:
        """Persist request and publish events."""
        with self.uow_factory.create_unit_of_work() as uow:
            events = uow.requests.save(request)

        for event in events or []:
            self.event_publisher.publish(event)  # type: ignore[union-attr]


@command_handler(CreateReturnRequestCommand)  # type: ignore[arg-type]
class CreateReturnRequestHandler(BaseCommandHandler[CreateReturnRequestCommand, None]):
    """Handler for creating return requests.

    CQRS Compliance: This command handler returns None (void).
    Results are stored in command fields for callers to access.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        container: ContainerPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
        query_bus: QueryBusPort,  # Add QueryBus for template lookup
        provider_selection_port: ProviderSelectionPort,  # Use port instead of service
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self.uow_factory = uow_factory
        self._container = container
        self._query_bus = query_bus
        self._provider_selection_port = provider_selection_port

        # Initialize SRP-compliant services
        from orb.application.services.deprovisioning_orchestrator import DeprovisioningOrchestrator
        from orb.application.services.machine_grouping_service import MachineGroupingService

        self._machine_grouping_service = MachineGroupingService(uow_factory, logger)
        self._deprovisioning_orchestrator = DeprovisioningOrchestrator(
            uow_factory, logger, container, query_bus, provider_selection_port
        )

    async def validate_command(self, command: CreateReturnRequestCommand):
        """Validate create return request command."""
        await super().validate_command(command)
        if not command.machine_ids:
            raise ValueError("machine_ids is required and cannot be empty")

        # For single machine operations, maintain strict validation (existing behavior)
        is_single_machine = len(command.machine_ids) == 1

        if is_single_machine:
            # Strict validation for single machine (preserve existing behavior)
            with self.uow_factory.create_unit_of_work() as uow:
                machine_id = command.machine_ids[0]
                machine = uow.machines.get_by_id(machine_id)
                if not machine:
                    raise EntityNotFoundError("Machine", machine_id)

                if machine.return_request_id:
                    if not command.force_return:
                        from orb.domain.request.exceptions import RequestValidationError

                        raise RequestValidationError(
                            f"Machine {machine_id} already has pending return request: {machine.return_request_id}"
                        )
                    # force_return=True — fall through, cancel handled in execute_command
        # For multiple machines, we'll do filtering in execute_command

    async def execute_command(self, command: CreateReturnRequestCommand) -> None:
        """Handle return request creation command.

        CQRS Compliance: Returns None. Results are stored in command fields:
        - command.created_request_ids: List of created request IDs
        - command.processed_machines: List of successfully processed machine IDs
        - command.skipped_machines: List of skipped machines with reasons
        """
        self.logger.info("Creating return request for machines: %s", command.machine_ids)

        # Cancel any existing pending return requests if --force is set
        if command.force_return:
            self._cancel_pending_return_requests(command.machine_ids)

        # Validate and filter machines
        validation_results = self._validate_and_filter_machines(
            command.machine_ids, force_return=command.force_return or False
        )

        # If no valid machines remain after filtering, store results and return
        if not validation_results["valid_machines"]:
            command.created_request_ids = []
            command.processed_machines = []
            command.skipped_machines = validation_results["skipped_machines"]
            return

        try:
            from orb.domain.request.aggregate import Request

            # Group machines by provider using service
            provider_groups = self._machine_grouping_service.group_by_provider(
                validation_results["valid_machines"]
            )

            # Create separate return requests for each provider
            created_requests = []
            for (provider_type, provider_name), machine_ids in provider_groups.items():
                request = Request.create_return_request(
                    machine_ids=machine_ids,
                    provider_type=provider_type,
                    provider_name=provider_name,
                    metadata=command.metadata or {},
                )

                # Persist request and update machines
                self._persist_return_request(request, machine_ids)
                created_requests.append(str(request.request_id))

                self.logger.info(
                    "Return request created for provider %s: %s (%d machines)",
                    provider_name,
                    request.request_id,
                    len(machine_ids),
                )

                # Execute deprovisioning
                await self._execute_deprovisioning_for_request(machine_ids, request, provider_name)

            # Store results in command for caller to access
            command.created_request_ids = created_requests
            command.processed_machines = validation_results["valid_machines"]
            command.skipped_machines = validation_results["skipped_machines"]

        except Exception as e:
            self.logger.error(
                "Failed to create return request for %d machines: %s",
                len(command.machine_ids),
                e,
                exc_info=True,
                extra={
                    "machine_count": len(command.machine_ids),
                    "error_type": type(e).__name__,
                },
            )
            raise

    def _validate_and_filter_machines(
        self, machine_ids: list[str], force_return: bool = False
    ) -> dict[str, Any]:
        """Validate and filter machines for return request."""
        is_single_machine = len(machine_ids) == 1

        if is_single_machine:
            # Single machine - validation already handled in validate_command
            return {"valid_machines": machine_ids, "skipped_machines": []}

        # Multiple machines - filter out invalid ones
        valid_machine_ids = []
        skipped_machines = []

        with self.uow_factory.create_unit_of_work() as uow:
            for machine_id in machine_ids:
                machine = uow.machines.get_by_id(machine_id)
                if not machine:
                    skipped_machines.append(
                        {"machine_id": machine_id, "reason": "Machine not found"}
                    )
                    continue

                if machine.return_request_id:
                    if not force_return:
                        skipped_machines.append(
                            {
                                "machine_id": machine_id,
                                "reason": f"Machine already has pending return request: {machine.return_request_id}",
                            }
                        )
                        continue
                    # force_return=True — include, cancel handled in execute_command

                valid_machine_ids.append(machine_id)

        return {"valid_machines": valid_machine_ids, "skipped_machines": skipped_machines}

    def _cancel_pending_return_requests(self, machine_ids: list[str]) -> None:
        """Cancel any pending return requests for the given machines and clear their return_request_id."""
        with self.uow_factory.create_unit_of_work() as uow:
            for machine_id in machine_ids:
                machine = uow.machines.get_by_id(machine_id)
                if not machine or not machine.return_request_id:
                    continue

                stuck_request_id = machine.return_request_id
                from orb.domain.request.value_objects import RequestId

                stuck_request = uow.requests.get_by_id(RequestId(value=stuck_request_id))
                if stuck_request:
                    try:
                        cancelled = stuck_request.cancel("Superseded by new return request")
                        uow.requests.save(cancelled)
                        self.logger.info(
                            "Cancelled stuck return request %s for machine %s",
                            stuck_request_id,
                            machine_id,
                        )
                    except Exception as e:
                        self.logger.warning(
                            "Could not cancel stuck return request %s: %s", stuck_request_id, e
                        )

                # Clear return_request_id so new request can claim the machine
                cleared_machine = machine.model_copy(update={"return_request_id": None})
                uow.machines.save(cleared_machine)

    def _persist_return_request(self, request: Any, machine_ids: list[str]) -> None:
        """Persist return request and update machine records."""
        with self.uow_factory.create_unit_of_work() as uow:
            events = uow.requests.save(request)

            # CRITICAL: Update machine records with return_request_id
            for machine_id in machine_ids:
                machine = uow.machines.get_by_id(machine_id)
                if machine:
                    updated_machine = machine.model_copy(
                        update={"return_request_id": str(request.request_id)}
                    )
                    uow.machines.save(updated_machine)

            for event in events or []:
                self.event_publisher.publish(event)  # type: ignore[union-attr]

    async def _update_request_to_in_progress(self, request: Any) -> None:
        """Update return request status to in_progress."""
        try:
            from orb.application.dto.commands import UpdateRequestStatusCommand
            from orb.application.ports.command_bus_port import CommandBusPort
            from orb.domain.request.request_types import RequestStatus

            update_command = UpdateRequestStatusCommand(
                request_id=str(request.request_id),
                status=RequestStatus.IN_PROGRESS,
                message="Return request processing started",
            )
            command_bus = self._container.get(CommandBusPort)
            await command_bus.execute(update_command)
            self.logger.info("Updated request %s status to in_progress", request.request_id)
        except Exception as update_error:
            self.logger.error(
                "Failed to update request status to in_progress: %s",
                update_error,
                exc_info=True,
            )

    async def _execute_deprovisioning_for_request(
        self, machine_ids: list[str], request: Any, provider_name: str
    ) -> None:
        """Execute deprovisioning and update request status."""
        try:
            # Transition to IN_PROGRESS before executing deprovisioning so that
            # subsequent status updates (COMPLETED or FAILED) are valid transitions.
            await self._update_request_to_in_progress(request)

            # Group machines by resource context
            resource_groups = self._machine_grouping_service.group_by_resource(machine_ids)

            # Execute deprovisioning using orchestrator
            provisioning_result = await self._deprovisioning_orchestrator.execute_deprovisioning(
                resource_groups, request
            )

            self.logger.info(f"Deprovisioning results for {provider_name}: {provisioning_result}")

            # Update request status based on result
            if provisioning_result.get("success", False):
                self._update_machines_to_pending(machine_ids)
                await self._update_request_to_completed(request)
                self.logger.info("Termination initiated for request %s", request.request_id)
            else:
                await self._update_request_to_failed(request, provisioning_result.get("errors", []))

        except Exception as e:
            self.logger.error(
                "Deprovisioning failed for provider %s request %s: %s",
                provider_name,
                request.request_id,
                e,
                exc_info=True,
            )
            await self._update_request_to_failed(request, [str(e)])

    def _update_machines_to_pending(self, machine_ids: list[str]) -> None:
        """Update machine statuses to shutting-down (termination in progress)."""
        with self.uow_factory.create_unit_of_work() as uow:
            for machine_id in machine_ids:
                machine = uow.machines.get_by_id(machine_id)
                if machine:
                    from orb.domain.machine.machine_status import MachineStatus

                    updated_machine = machine.update_status(
                        MachineStatus.SHUTTING_DOWN, "Termination in progress"
                    )
                    uow.machines.save(updated_machine)

    async def _update_request_to_failed(self, request: Any, errors: list[str]) -> None:
        """Update request status to failed."""
        try:
            from orb.application.dto.commands import UpdateRequestStatusCommand
            from orb.application.ports.command_bus_port import CommandBusPort
            from orb.domain.request.request_types import RequestStatus

            error_message = "; ".join(errors) if errors else "Deprovisioning failed"

            update_command = UpdateRequestStatusCommand(
                request_id=str(request.request_id),
                status=RequestStatus.FAILED,
                message=f"Return request failed: {error_message}",
            )

            command_bus = self._container.get(CommandBusPort)
            await command_bus.execute(update_command)

            self.logger.info("Updated request %s status to failed", request.request_id)

        except Exception as update_error:
            self.logger.error(
                "Failed to update request status: %s",
                update_error,
                exc_info=True,
            )

    async def _update_request_to_completed(self, request: Any) -> None:
        """Update return request status to completed and persist."""
        try:
            from orb.application.dto.commands import UpdateRequestStatusCommand
            from orb.application.ports.command_bus_port import CommandBusPort
            from orb.domain.request.request_types import RequestStatus

            update_command = UpdateRequestStatusCommand(
                request_id=str(request.request_id),
                status=RequestStatus.COMPLETED,
                message="Return request completed: termination initiated",
            )
            command_bus = self._container.get(CommandBusPort)
            await command_bus.execute(update_command)
            self.logger.info("Updated request %s status to completed", request.request_id)
        except Exception as update_error:
            self.logger.error(
                "Failed to update request status to completed: %s",
                update_error,
                exc_info=True,
            )
            # Ensure the request reaches a terminal status so callers don't poll forever
            await self._update_request_to_failed(
                request, [f"Failed to mark completed: {update_error}"]
            )
