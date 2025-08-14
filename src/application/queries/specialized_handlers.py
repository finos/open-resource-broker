"""Specialized query handlers for application services."""

from __future__ import annotations

from src.application.base.handlers import BaseQueryHandler
from src.application.decorators import query_handler
from src.application.dto.queries import (
    GetActiveMachineCountQuery,
    GetMachineHealthQuery,
    GetRequestSummaryQuery,
)
from src.application.dto.responses import MachineHealthDTO, RequestSummaryDTO
from src.domain.base import UnitOfWorkFactory
from src.domain.base.exceptions import EntityNotFoundError
from src.domain.base.ports import ErrorHandlingPort, LoggingPort

# Exception handling infrastructure
from src.domain.machine.value_objects import MachineStatus
from src.infrastructure.ports.resource_provisioning_port import ResourceProvisioningPort


@query_handler(GetActiveMachineCountQuery)
class GetActiveMachineCountHandler(BaseQueryHandler[GetActiveMachineCountQuery, int]):
    """Handler for getting count of active machines."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ):
        """
        Initialize get active machine count handler.

        Args:
            uow_factory: Unit of work factory for data access
            logger: Logging port for operation logging
            error_handler: Error handling port for exception management
        """
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory

    async def execute_query(self, query: GetActiveMachineCountQuery) -> int:
        """Execute active machine count query."""
        self.logger.info("Getting active machine count")

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                # Get active machines from repository
                active_statuses = [
                    MachineStatus.RUNNING,
                    MachineStatus.PENDING,
                    MachineStatus.STARTING,
                ]

                active_machines = uow.machines.find_by_statuses(active_statuses)
                count = len(active_machines)

                self.logger.info(f"Found {count} active machines")
                return count

        except Exception as e:
            self.logger.error(f"Failed to get active machine count: {e}")
            raise


@query_handler(GetRequestSummaryQuery)
class GetRequestSummaryHandler(BaseQueryHandler[GetRequestSummaryQuery, RequestSummaryDTO]):
    """Handler for getting request summary information."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ):
        """
        Initialize get request summary handler.

        Args:
            uow_factory: Unit of work factory for data access
            logger: Logging port for operation logging
            error_handler: Error handling port for exception management
        """
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory

    async def execute_query(self, query: GetRequestSummaryQuery) -> RequestSummaryDTO:
        """Execute request summary query."""
        self.logger.info(f"Getting request summary for request: {query.request_id}")

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                # Get request from repository
                request = uow.requests.get_by_id(query.request_id)
                if not request:
                    raise EntityNotFoundError("Request", query.request_id)

                # Get associated machines
                machines = uow.machines.find_by_request_id(query.request_id)

                # Calculate summary statistics
                total_machines = len(machines)
                running_machines = len([m for m in machines if m.status == MachineStatus.RUNNING])
                failed_machines = len([m for m in machines if m.status == MachineStatus.FAILED])

                # Create summary DTO
                summary = RequestSummaryDTO(
                    request_id=str(request.request_id),
                    status=request.status.value,
                    template_id=request.template_id,
                    requested_count=request.machine_count,
                    total_machines=total_machines,
                    running_machines=running_machines,
                    failed_machines=failed_machines,
                    created_at=request.created_at,
                    updated_at=request.updated_at,
                    metadata=request.metadata or {},
                )

                self.logger.info(
                    f"Generated summary for request {query.request_id}: {total_machines} machines"
                )
                return summary

        except EntityNotFoundError:
            self.logger.error(f"Request not found: {query.request_id}")
            raise
        except Exception as e:
            self.logger.error(f"Failed to get request summary: {e}")
            raise


@query_handler(GetMachineHealthQuery)
class GetMachineHealthHandler(BaseQueryHandler[GetMachineHealthQuery, MachineHealthDTO]):
    """Handler for getting machine health information."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        provisioning_port: ResourceProvisioningPort,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ):
        """
        Initialize get machine health handler.

        Args:
            uow_factory: Unit of work factory for data access
            provisioning_port: Resource provisioning port for health checks
            logger: Logging port for operation logging
            error_handler: Error handling port for exception management
        """
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        self.provisioning_port = provisioning_port

    async def execute_query(self, query: GetMachineHealthQuery) -> MachineHealthDTO:
        """Execute machine health query."""
        self.logger.info(f"Getting health information for machine: {query.machine_id}")

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                # Get machine from repository
                machine = uow.machines.get_by_id(query.machine_id)
                if not machine:
                    raise EntityNotFoundError("Machine", query.machine_id)

                # Get health information
                health_status = "unknown"
                health_details = {}
                last_health_check = None

                try:
                    # Try to get health from provisioning service
                    if hasattr(self.provisioning_port, "get_machine_health"):
                        health_info = self.provisioning_port.get_machine_health(machine.provider_id)
                        health_status = health_info.get("status", "unknown")
                        health_details = health_info.get("details", {})
                        last_health_check = health_info.get("timestamp")
                    else:
                        # Fallback: derive health from machine status
                        if machine.status == MachineStatus.RUNNING:
                            health_status = "healthy"
                        elif machine.status in [
                            MachineStatus.FAILED,
                            MachineStatus.TERMINATED,
                        ]:
                            health_status = "unhealthy"
                        else:
                            health_status = "unknown"

                        health_details = {
                            "machine_status": machine.status.value,
                            "provider_id": machine.provider_id,
                        }

                except Exception as health_error:
                    self.logger.warning(
                        f"Could not get detailed health for machine {query.machine_id}: {health_error}"
                    )
                    health_status = "unknown"
                    health_details = {"error": str(health_error)}

                # Create health DTO
                health_dto = MachineHealthDTO(
                    machine_id=str(machine.machine_id),
                    provider_id=machine.provider_id,
                    status=machine.status.value,
                    health_status=health_status,
                    health_details=health_details,
                    last_health_check=last_health_check,
                    created_at=machine.created_at,
                    updated_at=machine.updated_at,
                )

                self.logger.info(
                    f"Retrieved health for machine {query.machine_id}: {health_status}"
                )
                return health_dto

        except EntityNotFoundError:
            self.logger.error(f"Machine not found: {query.machine_id}")
            raise
        except Exception as e:
            self.logger.error(f"Failed to get machine health: {e}")
            raise
