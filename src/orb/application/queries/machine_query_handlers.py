"""Query handlers for machine domain queries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

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
from orb.application.services.orchestration.dtos import Paginated
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.exceptions import EntityNotFoundError
from orb.domain.base.ports import ContainerPort, ErrorHandlingPort, LoggingPort
from orb.domain.services.generic_filter_service import GenericFilterService


@query_handler(GetMachineQuery)
class GetMachineHandler(BaseQueryHandler[GetMachineQuery, MachineDTO]):
    """Handler for getting machine details."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory

    async def execute_query(self, query: GetMachineQuery) -> MachineDTO:
        """Execute get machine query."""
        self.logger.info("Getting machine: %s", query.machine_id)

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                machine = uow.machines.get_by_id(query.machine_id)
                if not machine:
                    raise EntityNotFoundError("Machine", query.machine_id)

                machine_dto = MachineDTO.from_domain(machine)

                self.logger.info("Retrieved machine: %s", query.machine_id)
                return machine_dto

        except EntityNotFoundError:
            self.logger.error("Machine not found: %s", query.machine_id)
            raise
        except Exception as e:
            self.logger.error("Failed to get machine: %s", e)
            raise


@query_handler(ListMachinesQuery)
class ListMachinesHandler(BaseQueryHandler[ListMachinesQuery, Paginated[MachineDTO]]):
    """Handler for listing machines."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        container: ContainerPort,
        command_bus: CommandBusPort,
        generic_filter_service: GenericFilterService,
        machine_sync_service: MachineSyncService,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        self.container = container
        self.command_bus = command_bus
        self._generic_filter_service = generic_filter_service
        self._machine_sync_service = machine_sync_service

    async def execute_query(self, query: ListMachinesQuery) -> Paginated[MachineDTO]:
        """Execute list machines query.

        Pipeline: load → provider filter → q → sort → total → slice → DTO
                  → expression filters.

        ``q`` and ``sort`` apply to the full dataset so pagination is
        consistent across pages. ``filter_expressions`` operate on the
        DTO form and therefore run after the slice; they should not be
        relied on for cross-page filtering.
        """
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

                total_unfiltered = len(machines)

                if query.provider_name:
                    machines = [
                        m
                        for m in machines
                        if m.provider_name and query.provider_name in m.provider_name
                    ]

                # q: substring search over user-visible domain fields
                if query.q:
                    needle = query.q.lower()
                    searchable = ("machine_id", "name", "instance_type", "private_ip", "public_ip")
                    machines = [
                        m
                        for m in machines
                        if any(needle in str(getattr(m, f, "") or "").lower() for f in searchable)
                    ]

                # sort: "+field" / "-field"
                if query.sort:
                    descending = query.sort.startswith("-")
                    attr = query.sort.lstrip("-+")

                    def _val(m: Any) -> str:
                        raw = getattr(m, attr, "")
                        return "" if raw is None else str(raw)

                    try:
                        machines = sorted(machines, key=_val, reverse=descending)
                    except TypeError as exc:
                        # Mixed-type column under sort. Fall back to
                        # unsorted results rather than failing the
                        # request; log so the bad column is observable.
                        self.logger.warning(
                            "ListMachines sort failed on attr=%s descending=%s: %s",
                            attr,
                            descending,
                            exc,
                        )

                total_count = len(machines)

                # Slice. None limit → no cap.
                offset = query.offset or 0
                if query.limit is None:
                    machines = machines[offset:]
                else:
                    limit = min(query.limit, 1000)
                    if query.limit > 1000:
                        self.logger.warning(
                            "ListMachinesQuery.limit=%d clamped to 1000; "
                            "total_count=%d. Consumers needing full counts "
                            "should rely on total_count, not len(machines).",
                            query.limit,
                            total_count,
                        )
                    machines = machines[offset : offset + limit] if limit > 0 else []

                machine_dtos = []
                for machine in machines:
                    # Provider refresh is opt-in via ``query.sync`` so list
                    # endpoints stay cheap. When enabled, every machine on
                    # the page (not just running ones) gets a single
                    # DescribeInstances; pending machines that have since
                    # transitioned will surface correctly.
                    if query.sync and machine.request_id:
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
                                        for sm in synced_machines:
                                            if sm.machine_id == machine.machine_id:
                                                machine = sm
                                                break
                        except Exception as e:
                            self.logger.debug(f"Sync failed for machine {machine.machine_id}: {e}")

                    machine_dto = MachineDTO.from_domain(
                        machine, timestamp_format=query.timestamp_format or "auto"
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
                    "Found %s machines (total: %s, unfiltered: %s, offset: %s)",
                    len(machine_dtos),
                    total_count,
                    total_unfiltered,
                    offset,
                )
                return Paginated(
                    items=machine_dtos,
                    total_count=total_count,
                    total_unfiltered=total_unfiltered,
                )

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
