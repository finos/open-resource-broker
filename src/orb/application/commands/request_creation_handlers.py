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
from orb.application.services.provisioning_orchestration_service import (
    ProvisioningOrchestrationService,
)
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.configuration_service import DomainConfigurationService
from orb.domain.base.exceptions import ApplicationError, EntityNotFoundError
from orb.domain.base.ports import (
    ContainerPort,
    ErrorHandlingPort,
    EventPublisherPort,
    LoggingPort,
    ProviderSelectionPort,
)
from orb.domain.request.request_identifiers import RequestId
from orb.domain.request.value_objects import RequestType


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
        provisioning_service: ProvisioningOrchestrationService,
    ) -> None:
        """Initialize the instance."""
        super().__init__(logger, event_publisher, error_handler)
        self.uow_factory = uow_factory
        self._container = container
        self._query_bus = query_bus
        self._provider_selection_port = provider_selection_port

        # Initialize services
        from orb.application.services.provider_validation_service import ProviderValidationService
        from orb.application.services.request_creation_service import RequestCreationService
        from orb.application.services.request_status_management_service import (
            RequestStatusManagementService,
        )

        self._request_creation_service = RequestCreationService(logger)
        self._provisioning_service = provisioning_service
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
        """Persist request and publish events.

        The persist step is not retried — a DB failure here is fatal and will
        propagate to the caller.  Event publishing is retried up to 3 times with
        exponential backoff (0.1 s, 0.2 s, 0.4 s).  If all retries fail the
        error is logged at ERROR level but NOT re-raised because the request was
        already successfully persisted.
        """
        import asyncio

        with self.uow_factory.create_unit_of_work() as uow:
            events = uow.requests.save(request)

        _max_attempts = 3
        _backoff_base = 0.1  # seconds; doubles each retry: 0.1, 0.2, 0.4

        for event in events or []:
            last_exc: Exception | None = None
            for attempt in range(1, _max_attempts + 1):
                try:
                    self.event_publisher.publish(event)  # type: ignore[union-attr]
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt < _max_attempts:
                        await asyncio.sleep(_backoff_base * (2 ** (attempt - 1)))
            if last_exc is not None:
                self.logger.error(
                    "Failed to publish event after %d attempts for request %s: "
                    "event_type=%s error=%s",
                    _max_attempts,
                    request.request_id,
                    type(event).__name__,
                    last_exc,
                )


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

        try:
            from orb.domain.request.aggregate import Request

            domain_config = self._container.get(DomainConfigurationService)
            prefix = domain_config.get_return_request_prefix()
            force_return = command.force_return or False
            is_single_machine = len(command.machine_ids) == 1

            # For single-machine requests validation already ran in validate_command.
            # For multi-machine we need to filter invalid machines first (one UoW).
            if is_single_machine:
                valid_machines = command.machine_ids
                skipped_machines: list[dict[str, Any]] = []
            else:
                valid_machines, skipped_machines = self._filter_machines(
                    command.machine_ids, force_return=force_return
                )

            if not valid_machines:
                command.created_request_ids = []
                command.processed_machines = []
                command.skipped_machines = skipped_machines
                return

            # Group machines by provider using service
            provider_groups = self._machine_grouping_service.group_by_provider(valid_machines)

            created_requests: list[str] = []
            pending_deprovision: list[tuple[list[str], Any, str]] = []

            for (provider_type, provider_name), machine_ids in provider_groups.items():
                return_request_id = str(RequestId.generate(RequestType.RETURN, prefix=prefix))
                request = Request.create_return_request(
                    machine_ids=machine_ids,
                    provider_type=provider_type,
                    provider_name=provider_name,
                    metadata=command.metadata or {},
                    request_id=return_request_id,
                )

                # Cancel stale requests, validate machines, and persist the new
                # return request all inside a single UoW to close the race window.
                self._cancel_validate_and_persist(
                    machine_ids=machine_ids,
                    request=request,
                    force_return=force_return,
                )
                created_requests.append(str(request.request_id))
                pending_deprovision.append((machine_ids, request, provider_name))

                self.logger.info(
                    "Return request created for provider %s: %s (%d machines)",
                    provider_name,
                    request.request_id,
                    len(machine_ids),
                )

            # Populate command fields BEFORE spawning background tasks so the
            # caller always has the request IDs immediately after execute_command returns.
            command.created_request_ids = created_requests
            command.processed_machines = valid_machines
            command.skipped_machines = skipped_machines

            # Await deprovisioning sequentially — one per provider group.
            for machine_ids, request, provider_name in pending_deprovision:
                await self._execute_deprovisioning_for_request(machine_ids, request, provider_name)

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

    def _filter_machines(
        self, machine_ids: list[str], force_return: bool = False
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Filter machines for a multi-machine return request (single UoW).

        Returns (valid_machine_ids, skipped_machines).
        """
        valid_machine_ids: list[str] = []
        skipped_machines: list[dict[str, Any]] = []

        with self.uow_factory.create_unit_of_work() as uow:
            for machine_id in machine_ids:
                machine = uow.machines.get_by_id(machine_id)
                if not machine:
                    skipped_machines.append(
                        {"machine_id": machine_id, "reason": "Machine not found"}
                    )
                    continue

                if machine.return_request_id and not force_return:
                    skipped_machines.append(
                        {
                            "machine_id": machine_id,
                            "reason": f"Machine already has pending return request: {machine.return_request_id}",
                        }
                    )
                    continue

                valid_machine_ids.append(machine_id)

        return valid_machine_ids, skipped_machines

    def _cancel_validate_and_persist(
        self, machine_ids: list[str], request: Any, force_return: bool
    ) -> None:
        """Cancel stale return requests, validate machine state, and persist the
        new return request — all within a single UoW transaction to eliminate the
        race window between these three previously separate transactions.
        """
        with self.uow_factory.create_unit_of_work() as uow:
            for machine_id in machine_ids:
                machine = uow.machines.get_by_id(machine_id)
                if not machine:
                    continue

                # Cancel any stuck return request if force_return is set
                if machine.return_request_id and force_return:
                    stuck_request = uow.requests.get_by_id(
                        RequestId(value=machine.return_request_id)
                    )
                    if stuck_request:
                        try:
                            cancelled = stuck_request.cancel("Superseded by new return request")
                            uow.requests.save(cancelled)
                            self.logger.info(
                                "Cancelled stuck return request %s for machine %s",
                                machine.return_request_id,
                                machine_id,
                            )
                        except Exception as e:
                            self.logger.warning(
                                "Could not cancel stuck return request %s: %s",
                                machine.return_request_id,
                                e,
                            )
                    # Clear return_request_id so the new request can claim the machine
                    machine = machine.model_copy(update={"return_request_id": None})
                    uow.machines.save(machine)

            # Persist the new return request
            events = uow.requests.save(request)

            # CRITICAL: Update machine records with the new return_request_id
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

            # Group machines by resource context; collect any skipped machine IDs
            resource_groups, skipped_ids = self._machine_grouping_service.group_by_resource(
                machine_ids
            )

            # Execute deprovisioning using orchestrator
            provisioning_result = await self._deprovisioning_orchestrator.execute_deprovisioning(
                resource_groups, request
            )

            self.logger.info(f"Deprovisioning results for {provider_name}: {provisioning_result}")

            # Update request status based on result
            if provisioning_result.get("success", False):
                self._update_machines_to_pending(machine_ids)
                if skipped_ids:
                    from orb.application.dto.commands import UpdateRequestStatusCommand
                    from orb.application.ports.command_bus_port import CommandBusPort
                    from orb.domain.request.request_types import RequestStatus

                    skipped_str = ", ".join(skipped_ids)
                    update_command = UpdateRequestStatusCommand(
                        request_id=str(request.request_id),
                        status=RequestStatus.PARTIAL,
                        message=(
                            f"Return request partially completed: termination initiated, "
                            f"but {len(skipped_ids)} machine(s) were skipped "
                            f"(missing provider context): {skipped_str}"
                        ),
                    )
                    command_bus = self._container.get(CommandBusPort)
                    await command_bus.execute(update_command)
                    self.logger.warning(
                        "Request %s marked PARTIAL: %d machine(s) skipped: %s",
                        request.request_id,
                        len(skipped_ids),
                        skipped_str,
                    )
                else:
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
            # Force-write terminal status directly to prevent permanent stuck state
            try:
                from orb.domain.request.request_types import RequestStatus

                with self.uow_factory.create_unit_of_work() as uow:
                    stuck_request = uow.requests.get_by_id(request.request_id)
                    if stuck_request:
                        stuck_request = stuck_request.update_status(
                            RequestStatus.FAILED,
                            "System error: failed to update status after double failure",
                            force=True,
                        )
                        uow.requests.save(stuck_request)
            except Exception as final_error:
                self.logger.critical(
                    "CRITICAL: Failed to mark request %s as failed after double failure. "
                    "Request is stuck in IN_PROGRESS. Manual intervention required. Error: %s",
                    request.request_id,
                    final_error,
                )
                # Nothing more we can do

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
