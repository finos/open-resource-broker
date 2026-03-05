"""Specialized query handlers for application services."""

from __future__ import annotations

from typing import Any

from application.base.handlers import BaseQueryHandler
from application.decorators import query_handler
from application.dto.queries import (
    GetActiveMachineCountQuery,
    GetMachineHealthQuery,
    GetRequestSummaryQuery,
)
from application.dto.responses import MachineHealthDTO, RequestSummaryDTO
from domain.base import UnitOfWorkFactory
from domain.base.exceptions import EntityNotFoundError
from domain.base.ports import ErrorHandlingPort, LoggingPort

# Exception handling infrastructure
from domain.machine.value_objects import MachineStatus


@query_handler(GetActiveMachineCountQuery)
class GetActiveMachineCountHandler(BaseQueryHandler[GetActiveMachineCountQuery, int]):
    """Handler for getting count of active machines."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
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
                    MachineStatus.LAUNCHING,
                ]

                active_machines = uow.machines.find_by_statuses(active_statuses)
                count = len(active_machines)

                self.logger.info("Found %s active machines", count)
                return count

        except Exception as e:
            self.logger.error("Failed to get active machine count: %s", e)
            raise


@query_handler(GetRequestSummaryQuery)
class GetRequestSummaryHandler(BaseQueryHandler[GetRequestSummaryQuery, RequestSummaryDTO]):
    """Handler for getting request summary information."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
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
        self.logger.info("Getting request summary for request: %s", query.request_id)

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
                    total_machines=total_machines,
                    machine_statuses={
                        "running": running_machines,
                        "failed": failed_machines,
                    },
                    created_at=request.created_at,
                )

                self.logger.info(
                    "Generated summary for request %s: %s machines",
                    query.request_id,
                    total_machines,
                )
                return summary

        except EntityNotFoundError:
            self.logger.error("Request not found: %s", query.request_id)
            raise
        except Exception as e:
            self.logger.error("Failed to get request summary: %s", e)
            raise


@query_handler(GetMachineHealthQuery)
class GetMachineHealthHandler(BaseQueryHandler[GetMachineHealthQuery, MachineHealthDTO]):
    """Handler for getting machine health information."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        provisioning_port: Any,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
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
        self.logger.info("Getting health information for machine: %s", query.machine_id)

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
                        health_info = self.provisioning_port.get_machine_health(
                            machine.provider_name
                        )
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
                            "provider_id": machine.provider_name,
                        }

                except Exception as health_error:
                    self.logger.warning(
                        "Could not get detailed health for machine %s: %s",
                        query.machine_id,
                        health_error,
                    )
                    health_status = "unknown"
                    health_details = {"error": str(health_error)}

                # Create health DTO
                from datetime import datetime

                health_dto = MachineHealthDTO(
                    machine_id=str(machine.machine_id),
                    overall_status=health_status,
                    system_status=health_status,
                    instance_status=health_status,
                    metrics=[health_details] if health_details else [],
                    last_check=last_health_check or datetime.utcnow(),
                )

                self.logger.info(
                    "Retrieved health for machine %s: %s",
                    query.machine_id,
                    health_status,
                )
                return health_dto

        except EntityNotFoundError:
            self.logger.error("Machine not found: %s", query.machine_id)
            raise
        except Exception as e:
            self.logger.error("Failed to get machine health: %s", e)
            raise
