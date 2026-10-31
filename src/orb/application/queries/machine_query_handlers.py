"""Query handlers for machine domain queries."""

from __future__ import annotations

from typing import Any, Optional, cast

from orb.application.base.handlers import BaseQueryHandler
from orb.application.decorators import query_handler
from orb.application.dto.queries import (
    GetMachineHealthQuery,
    GetMachineQuery,
    ListMachinesQuery,
)
from orb.application.dto.responses import MachineDTO, MachineHealthDTO
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.services.machine_sync_service import MachineSyncService
from orb.application.services.orchestration.dtos import Paginated
from orb.application.services.orchestration.page_cursor import KeysetPaginated
from orb.application.services.provider_registry_service import ProviderRegistryService
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.exceptions import EntityNotFoundError
from orb.domain.base.operations import Operation, OperationType
from orb.domain.base.pagination import (
    Position,
    SortSpec,
    StoragePage,
    paginate_in_memory,
)
from orb.domain.base.ports import ErrorHandlingPort, LoggingPort
from orb.domain.services.generic_filter_service import GenericFilterService

# Sentinel used whenever a health dimension cannot be determined. Unknown health
# is never fabricated as "ok" — callers must be able to tell "we don't know".
_HEALTH_NOT_AVAILABLE = "not_available"


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


def _dimension_status(dimension: Any) -> str:
    """Reduce a stored/provider health dimension to a display string.

    Accepts either the coarse persisted form (a plain status string) or the
    richer provider form (``{"status": bool, "details": {"status": str}}``).
    Anything unrecognised collapses to ``not_available`` rather than guessing.
    """
    if dimension is None:
        return _HEALTH_NOT_AVAILABLE
    if isinstance(dimension, str):
        return dimension
    if isinstance(dimension, dict):
        details = dimension.get("details")
        if isinstance(details, dict) and details.get("status"):
            return str(details["status"])
        # A details.reason with no status string means the provider could not
        # determine health (e.g. a transient API error surfaced as a returned
        # dict rather than a raised exception). Report that as unknown instead
        # of collapsing the accompanying ``status: False`` flag into a
        # fabricated "impaired" reading indistinguishable from a real one.
        if isinstance(details, dict) and details.get("reason"):
            return _HEALTH_NOT_AVAILABLE
        status = dimension.get("status")
        if isinstance(status, bool):
            return "ok" if status else "impaired"
        if status is not None:
            return str(status)
    return _HEALTH_NOT_AVAILABLE


@query_handler(GetMachineHealthQuery)
class GetMachineHealthHandler(BaseQueryHandler[GetMachineHealthQuery, MachineHealthDTO]):
    """Handler for getting machine health, from stored data or a live refresh."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        provider_registry_service: ProviderRegistryService,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        self._provider_registry_service = provider_registry_service

    async def execute_query(self, query: GetMachineHealthQuery) -> MachineHealthDTO:
        """Return machine health.

        With ``refresh=False`` the persisted health snapshot on the machine is
        returned. With ``refresh=True`` a live provider health check is
        dispatched and its result mapped into the DTO; if the live check yields
        nothing the stored snapshot is returned as a fallback.
        """
        self.logger.info("Getting machine health: %s", query.machine_id)

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                machine = uow.machines.get_by_id(query.machine_id)
                if not machine:
                    raise EntityNotFoundError("Machine", query.machine_id)

                if query.refresh:
                    refreshed = await self._refresh_health(machine)
                    if refreshed is not None:
                        return refreshed

                return self._health_from_stored(machine)

        except EntityNotFoundError:
            self.logger.error("Machine not found: %s", query.machine_id)
            raise
        except Exception as e:
            self.logger.error("Failed to get machine health: %s", e)
            raise

    def _health_from_stored(self, machine: Any) -> MachineHealthDTO:
        """Build a health DTO from the machine's persisted state."""
        status = machine.status.value if hasattr(machine.status, "value") else str(machine.status)
        provider_data = machine.provider_data or {}
        health_checks = provider_data.get("health_checks") or {}

        overall_status = status
        if isinstance(health_checks, dict) and health_checks.get("status"):
            overall_status = str(health_checks["status"])

        system_status = _HEALTH_NOT_AVAILABLE
        instance_status = _HEALTH_NOT_AVAILABLE
        if isinstance(health_checks, dict):
            if "system" in health_checks:
                system_status = _dimension_status(health_checks.get("system"))
            if "instance" in health_checks:
                instance_status = _dimension_status(health_checks.get("instance"))

        return MachineHealthDTO(
            machine_id=str(machine.machine_id),
            overall_status=overall_status,
            system_status=system_status,
            instance_status=instance_status,
        )

    async def _refresh_health(self, machine: Any) -> MachineHealthDTO | None:
        """Dispatch a live provider health check and map the result.

        Returns ``None`` when the provider yields no usable result so the caller
        can fall back to the stored snapshot.
        """
        operation = Operation(
            operation_type=OperationType.GET_MACHINE_HEALTH,
            parameters={
                "machine": machine,
                "instance_ids": [machine.machine_id],
            },
            context={"machine_id": str(machine.machine_id)},
        )

        try:
            result = await self._provider_registry_service.execute_operation(
                machine.provider_name, operation
            )
        except Exception as exc:
            self.logger.warning("Live health refresh failed for %s: %s", machine.machine_id, exc)
            return None

        if result is None or not result.success or not isinstance(result.data, dict):
            return None

        data = result.data
        system_status = _dimension_status(data.get("system"))
        instance_status = _dimension_status(data.get("instance"))

        known = [s for s in (system_status, instance_status) if s != _HEALTH_NOT_AVAILABLE]
        if not known:
            overall_status = _HEALTH_NOT_AVAILABLE
        elif all(s == "ok" for s in known):
            overall_status = "ok"
        else:
            overall_status = "impaired"

        return MachineHealthDTO(
            machine_id=str(machine.machine_id),
            overall_status=overall_status,
            system_status=system_status,
            instance_status=instance_status,
        )


@query_handler(ListMachinesQuery)
class ListMachinesHandler(BaseQueryHandler[ListMachinesQuery, Paginated[MachineDTO]]):
    """Handler for listing machines."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        command_bus: CommandBusPort,
        generic_filter_service: GenericFilterService,
        machine_sync_service: MachineSyncService,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
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
                spec = SortSpec.parse(query.sort)
                offset = query.offset or 0
                next_position: Optional[Position] = None
                pushed = self._push_down(uow, query, spec)

                if pushed is not None:
                    # The database filtered, ordered and cut the page
                    machines = pushed.items
                    total_count = pushed.total_count
                    total_unfiltered = None if query.provider_type else pushed.total_count
                    next_position = pushed.next_position
                else:
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

                    if query.provider_type:
                        machines = [m for m in machines if m.provider_type == query.provider_type]

                    # q: substring search over user-visible domain fields
                    if query.q:
                        needle = query.q.lower()
                        searchable = (
                            "machine_id",
                            "name",
                            "instance_type",
                            "private_ip",
                            "public_ip",
                        )
                        machines = [
                            m
                            for m in machines
                            if any(
                                needle in str(getattr(m, f, "") or "").lower() for f in searchable
                            )
                        ]

                    total_count = len(machines)

                    # Slice. None limit → no cap.
                    limit: Optional[int] = None
                    if query.limit is not None:
                        limit = min(query.limit, 1000)
                        if query.limit > 1000:
                            self.logger.warning(
                                "ListMachinesQuery.limit=%d clamped to 1000; "
                                "total_count=%d. Consumers needing full counts "
                                "should rely on total_count, not len(machines).",
                                query.limit,
                                total_count,
                            )

                    if spec is not None:
                        # Ordered by (sort text, id) after the cursor; the whole
                        # filtered set is held in memory for this backend/query.
                        in_memory = paginate_in_memory(
                            machines,
                            spec,
                            query.after,
                            limit,
                            id_of=lambda m: str(m.machine_id),
                            skip=0 if query.after is not None else offset,
                            json_datetimes=True,  # machines are stored in pydantic's JSON form
                        )
                        machines = in_memory.items
                        next_position = in_memory.next_position
                    elif limit is None:
                        machines = machines[offset:]
                    elif limit > 0:
                        machines = machines[offset : offset + limit]
                    else:
                        machines = []

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
                if spec is not None:
                    return KeysetPaginated(
                        items=machine_dtos,
                        total_count=total_count,
                        total_unfiltered=total_unfiltered,
                        next_position=next_position,
                    )
                return Paginated(
                    items=machine_dtos,
                    total_count=total_count,
                    total_unfiltered=total_unfiltered,
                )

        except Exception as e:
            self.logger.error("Failed to list machines: %s", e)
            raise

    @staticmethod
    def _push_down(uow: Any, query: ListMachinesQuery, spec: Optional[SortSpec]) -> Any:
        """The page from the database, or None when this query must page in process.

        Only plain equality filters on status, provider type and request can be
        pushed down; a substring match, free-text search, expression filter,
        provider refresh or an offset without a cursor cannot.
        """
        if (
            spec is None
            or query.limit is None
            or query.limit <= 0
            or query.all_resources
            or query.provider_name
            or query.q
            or query.filter_expressions
            or query.sync
            or (query.offset and query.after is None)
        ):
            return None
        find_page = getattr(uow.machines, "find_page", None)
        if not callable(find_page):
            return None

        criteria: dict[str, str] = {}
        if query.status:
            from orb.domain.machine.value_objects import MachineStatus

            criteria["status"] = MachineStatus(query.status).value
        elif query.request_id:
            criteria["request_id"] = query.request_id
        if query.provider_type:
            criteria["provider_type"] = query.provider_type

        page = find_page(criteria, spec, query.after, min(query.limit, 1000))
        return page if isinstance(page, StoragePage) else None
