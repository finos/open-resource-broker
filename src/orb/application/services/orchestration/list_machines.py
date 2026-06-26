"""Orchestrator for listing machines.

Forwards filter, sort, search, and pagination parameters to the query
handler and encodes the next cursor from the handler's reported
``total_count``.
"""

from __future__ import annotations

from orb.application.dto.queries import ListMachinesQuery
from orb.application.machine.dto import MachineDTO
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    ListMachinesInput,
    ListMachinesOutput,
    Paginated,
    decode_cursor,
    encode_cursor,
)
from orb.domain.base.exceptions import ValidationError
from orb.domain.base.ports.logging_port import LoggingPort

_DEFAULT_SORT = "-launch_time"


class ListMachinesOrchestrator(OrchestratorBase[ListMachinesInput, ListMachinesOutput]):
    """Orchestrator for listing machines."""

    def __init__(
        self, command_bus: CommandBusPort, query_bus: QueryBusPort, logger: LoggingPort
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger

    async def execute(self, input: ListMachinesInput) -> ListMachinesOutput:  # type: ignore[return]
        self._logger.info(
            "ListMachinesOrchestrator: status=%s provider=%s request_id=%s limit=%s",
            input.status,
            input.provider_name,
            input.request_id,
            input.limit,
        )

        if input.sync and (input.q or input.sort):
            raise ValidationError("q and sort are not supported with sync=true")

        offset = decode_cursor(input.cursor) if input.cursor else input.offset

        query = ListMachinesQuery(
            status=input.status,
            provider_name=input.provider_name,
            request_id=input.request_id,
            limit=input.limit,
            offset=offset,
            q=input.q,
            sort=input.sort if input.sort else _DEFAULT_SORT,
            sync=input.sync,
        )
        result = await self._query_bus.execute(query)

        if isinstance(result, Paginated):
            items: list[MachineDTO] = result.items
            total_count = result.total_count
        else:
            items = list(result or [])
            total_count = len(items)

        next_cursor: str | None = None
        effective_limit = input.limit if input.limit is not None else total_count
        if effective_limit and total_count > offset + effective_limit:
            next_cursor = encode_cursor(offset + effective_limit)

        return ListMachinesOutput(
            machines=items,
            count=len(items),
            next_cursor=next_cursor,
            total_count=total_count,
        )
