"""Query handlers for machine domain queries."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from orb.application.services.provider_registry_service import ProviderRegistryService

from orb.application.base.handlers import BaseQueryHandler
from orb.application.decorators import query_handler
from orb.application.dto.queries import GetMachineQuery, ListMachinesQuery
from orb.application.dto.responses import MachineDTO
from orb.application.machine.queries import (
    ConvertBatchMachineStatusQuery,
    ConvertMachineStatusQuery,
    ValidateProviderStateQuery,
)
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.services.machine_sync_service import MachineSyncService
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.exceptions import EntityNotFoundError
from orb.domain.base.ports import ContainerPort, ErrorHandlingPort, LoggingPort
from orb.domain.services.generic_filter_service import GenericFilterService
from orb.domain.services.timestamp_service import TimestampService


@query_handler(GetMachineQuery)
class GetMachineHandler(BaseQueryHandler[GetMachineQuery, MachineDTO]):
    """Handler for getting machine details."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        timestamp_service: TimestampService,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        self.timestamp_service = timestamp_service

    async def execute_query(self, query: GetMachineQuery) -> MachineDTO:
        """Execute get machine query."""
        self.logger.info("Getting machine: %s", query.machine_id)

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                machine = uow.machines.get_by_id(query.machine_id)
                if not machine:
                    raise EntityNotFoundError("Machine", query.machine_id)

                machine_dto = MachineDTO(
                    machine_id=str(machine.machine_id),
                    name=machine.name or str(machine.machine_id),
                    status=machine.status.value,
                    instance_type=str(machine.instance_type),
                    private_ip=machine.private_ip or "unknown",
                    public_ip=machine.public_ip,
                    private_dns_name=machine.private_dns_name,
                    public_dns_name=machine.public_dns_name,
                    result=MachineDTO._get_result_status(machine.status.value),
                    launch_time=self.timestamp_service.format_for_dto(machine.launch_time),
                    message=machine.status_reason or "",
                    provider_api=machine.provider_api,
                    provider_name=machine.provider_name,
                    provider_type=machine.provider_type,
                    resource_id=machine.resource_id,
                    request_id=machine.request_id,
                    metadata=machine.metadata or {},
                    template_id=machine.template_id,
                    image_id=machine.image_id,
                    subnet_id=machine.subnet_id,
                    security_group_ids=machine.security_group_ids,
                    status_reason=machine.status_reason,
                    termination_time=machine.termination_time,
                    tags=machine.tags,
                )

                self.logger.info("Retrieved machine: %s", query.machine_id)
                return machine_dto

        except EntityNotFoundError:
            self.logger.error("Machine not found: %s", query.machine_id)
            raise
        except Exception as e:
            self.logger.error("Failed to get machine: %s", e)
            raise


@query_handler(ListMachinesQuery)
class ListMachinesHandler(BaseQueryHandler[ListMachinesQuery, list[MachineDTO]]):
    """Handler for listing machines."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        container: ContainerPort,
        command_bus: CommandBusPort,
        timestamp_service: TimestampService,
        generic_filter_service: GenericFilterService,
        machine_sync_service: MachineSyncService,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        self.container = container
        self.command_bus = command_bus
        self.timestamp_service = timestamp_service
        self._generic_filter_service = generic_filter_service
        self._machine_sync_service = machine_sync_service

    async def execute_query(self, query: ListMachinesQuery) -> list[MachineDTO]:
        """Execute list machines query."""
        self.logger.info("Listing machines")

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                if query.all_resources:
                    machines = uow.machines.find_active_machines()
                elif query.status:
                    from orb.domain.machine.value_objects import MachineStatus

                    status_enum = MachineStatus(query.status)
                    machines = uow.machines.find_by_status(status_enum)
                elif query.request_id:
                    machines = uow.machines.find_by_request_id(query.request_id)
                else:
                    machines = uow.machines.get_all()

                if query.provider_name:
                    machines = [
                        m
                        for m in machines
                        if m.provider_name and query.provider_name in m.provider_name
                    ]

                total_count = len(machines)
                limit = min(query.limit or 50, 1000)
                offset = query.offset or 0
                machines = machines[offset : offset + limit]

                machine_dtos = []
                for machine in machines:
                    if machine.status.value == "running" and machine.request_id:
                        try:
                            request = uow.requests.get_by_id(machine.request_id)
                            if request:
                                (
                                    provider_machines,
                                    _,
                                ) = await self._machine_sync_service.fetch_provider_machines(
                                    request, [machine]
                                )
                                if provider_machines:
                                    (
                                        synced_machines,
                                        _,
                                    ) = await self._machine_sync_service.sync_machines_with_provider(
                                        request, [machine], provider_machines
                                    )
                                    if synced_machines:
                                        machine = synced_machines[0]
                        except Exception as e:
                            self.logger.debug(f"Sync failed for machine {machine.machine_id}: {e}")

                    machine_dto = MachineDTO(
                        machine_id=str(machine.machine_id),
                        name=machine.name or str(machine.machine_id),
                        status=machine.status.value,
                        instance_type=str(machine.instance_type),
                        private_ip=machine.private_ip or "unknown",
                        public_ip=machine.public_ip,
                        private_dns_name=machine.private_dns_name,
                        public_dns_name=machine.public_dns_name,
                        price_type=machine.price_type,
                        result=MachineDTO._get_result_status(machine.status.value),
                        launch_time=self.timestamp_service.format_with_type(
                            machine.launch_time, query.timestamp_format or "auto"
                        ),
                        message=machine.status_reason or "",
                        provider_api=machine.provider_api,
                        provider_name=machine.provider_name,
                        provider_type=machine.provider_type,
                        resource_id=machine.resource_id,
                        request_id=machine.request_id,
                        return_request_id=machine.return_request_id,
                        metadata=machine.metadata or {},
                        subnet_id=machine.subnet_id,
                        security_group_ids=machine.security_group_ids or [],
                        template_id=machine.template_id,
                        image_id=machine.image_id,
                        status_reason=machine.status_reason,
                        termination_time=str(machine.termination_time)
                        if machine.termination_time is not None
                        else None,
                        tags=machine.tags,
                    )
                    machine_dtos.append(machine_dto)

                if query.filter_expressions:
                    machine_dtos = cast(
                        list[MachineDTO],
                        self._generic_filter_service.apply_filters(
                            machine_dtos,
                            query.filter_expressions,  # type: ignore[arg-type]
                        ),
                    )

                self.logger.info(
                    "Found %s machines (total: %s, limit: %s, offset: %s)",
                    len(machine_dtos),
                    total_count,
                    limit,
                    offset,
                )
                return machine_dtos

        except Exception as e:
            self.logger.error("Failed to list machines: %s", e)
            raise


@query_handler(ConvertMachineStatusQuery)  # type: ignore[arg-type]
class ConvertMachineStatusQueryHandler(BaseQueryHandler[ConvertMachineStatusQuery, dict[str, str]]):
    """Query handler that converts a provider-specific state to a domain MachineStatus."""

    def __init__(
        self,
        container: ContainerPort,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        provider_registry_service: ProviderRegistryService,
    ) -> None:
        super().__init__(logger, error_handler)
        self._container = container
        self._provider_registry_service = provider_registry_service

    async def execute_query(self, query: ConvertMachineStatusQuery) -> dict[str, str]:
        """Return the domain status for the given provider state."""
        from orb.domain.base.operations import (
            Operation as ProviderOperation,
            OperationType as ProviderOperationType,
        )

        operation = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={"provider_state": query.provider_state, "convert_only": True},
        )
        result = await self._provider_registry_service.execute_operation(
            query.provider_type, operation
        )
        from orb.domain.machine.value_objects import MachineStatus

        status: MachineStatus = (
            result.data.get("status", MachineStatus.UNKNOWN)
            if result.success
            else MachineStatus.UNKNOWN
        )
        return {
            "status": status.value if hasattr(status, "value") else str(status),
            "original_state": query.provider_state,
            "provider_type": query.provider_type,
        }


@query_handler(ConvertBatchMachineStatusQuery)  # type: ignore[arg-type]
class ConvertBatchMachineStatusQueryHandler(BaseQueryHandler[ConvertBatchMachineStatusQuery, dict]):
    """Query handler that converts multiple provider states to domain MachineStatus values."""

    def __init__(
        self,
        container: ContainerPort,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        provider_registry_service: ProviderRegistryService,
    ) -> None:
        super().__init__(logger, error_handler)
        self._container = container
        self._provider_registry_service = provider_registry_service

    async def execute_query(self, query: ConvertBatchMachineStatusQuery) -> dict:
        """Return domain statuses for all provider states in the batch."""
        from orb.domain.base.operations import (
            Operation as ProviderOperation,
            OperationType as ProviderOperationType,
        )
        from orb.domain.machine.value_objects import MachineStatus

        statuses = []
        for state_info in query.provider_states:
            operation = ProviderOperation(
                operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
                parameters={"provider_state": state_info["state"], "convert_only": True},
            )
            result = await self._provider_registry_service.execute_operation(
                state_info["provider_type"], operation
            )
            status: MachineStatus = (
                result.data.get("status", MachineStatus.UNKNOWN)
                if result.success
                else MachineStatus.UNKNOWN
            )
            statuses.append(status.value if hasattr(status, "value") else str(status))
        return {"statuses": statuses, "count": len(statuses)}


@query_handler(ValidateProviderStateQuery)  # type: ignore[arg-type]
class ValidateProviderStateQueryHandler(BaseQueryHandler[ValidateProviderStateQuery, dict]):
    """Query handler that validates whether a provider state maps to a known domain status."""

    def __init__(
        self,
        container: ContainerPort,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        provider_registry_service: ProviderRegistryService,
    ) -> None:
        super().__init__(logger, error_handler)
        self._container = container
        self._provider_registry_service = provider_registry_service

    async def execute_query(self, query: ValidateProviderStateQuery) -> dict:
        """Return whether the provider state is valid."""
        from orb.domain.base.operations import (
            Operation as ProviderOperation,
            OperationType as ProviderOperationType,
        )
        from orb.domain.machine.value_objects import MachineStatus

        try:
            operation = ProviderOperation(
                operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
                parameters={"provider_state": query.provider_state, "convert_only": True},
            )
            result = await self._provider_registry_service.execute_operation(
                query.provider_type, operation
            )
            status: MachineStatus = (
                result.data.get("status", MachineStatus.UNKNOWN)
                if result.success
                else MachineStatus.UNKNOWN
            )
            is_valid = result.success and status != MachineStatus.UNKNOWN
        except Exception:
            is_valid = False
        return {
            "is_valid": is_valid,
            "provider_state": query.provider_state,
            "provider_type": query.provider_type,
        }
