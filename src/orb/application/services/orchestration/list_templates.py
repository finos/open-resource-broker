"""Orchestrator for listing templates.

Forwards filter, sort, search, and pagination parameters to the query
handler and encodes the next cursor from the handler's reported
``total_count``.
"""

from __future__ import annotations

from orb.application.dto.queries import ListTemplatesQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    ListTemplatesInput,
    ListTemplatesOutput,
    Paginated,
    decode_cursor,
    encode_cursor,
)
from orb.domain.base.ports.logging_port import LoggingPort


class ListTemplatesOrchestrator(OrchestratorBase[ListTemplatesInput, ListTemplatesOutput]):
    """Orchestrator for listing available templates."""

    def __init__(
        self, command_bus: CommandBusPort, query_bus: QueryBusPort, logger: LoggingPort
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger

    async def execute(self, input: ListTemplatesInput) -> ListTemplatesOutput:  # type: ignore[return]
        self._logger.info(
            "ListTemplatesOrchestrator: active_only=%s provider=%s limit=%s",
            input.active_only,
            input.provider_name,
            input.limit,
        )

        # Cursor takes precedence over a bare offset.
        offset = decode_cursor(input.cursor) if input.cursor else input.offset

        query = ListTemplatesQuery(
            active_only=input.active_only,
            provider_name=input.provider_name,
            provider_type=input.provider_type,
            provider_api=input.provider_api,
            limit=input.limit,
            offset=offset,
            filter_expressions=input.filter_expressions,
            q=input.q,
            sort=input.sort,
        )
        result = await self._query_bus.execute(query)

        # Handler returns Paginated. Older callers might still see list[T];
        # tolerate both shapes so this lands without flipping every caller.
        if isinstance(result, Paginated):
            items = result.items
            total_count = result.total_count
        else:
            items = list(result or [])
            total_count = len(items)

        # next_cursor only if there are more rows past the current page.
        next_cursor: str | None = None
        effective_limit = input.limit if input.limit is not None else total_count
        if effective_limit and total_count > offset + effective_limit:
            next_cursor = encode_cursor(offset + effective_limit)

        return ListTemplatesOutput(
            templates=items,
            count=len(items),
            next_cursor=next_cursor,
            total_count=total_count,
        )
