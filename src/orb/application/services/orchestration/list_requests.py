"""Orchestrator for listing requests.

Forwards filter, sort, search, and pagination parameters to the query
handler and encodes the next cursor from the handler's reported
``total_count``.
"""

from __future__ import annotations

from orb.application.dto.queries import ListActiveRequestsQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.request.queries import ListRequestsQuery
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    ListRequestsInput,
    ListRequestsOutput,
    Paginated,
    decode_cursor,
    encode_cursor,
)
from orb.domain.base.exceptions import ValidationError
from orb.domain.base.ports.logging_port import LoggingPort

_DEFAULT_SORT = "-created_at"


class ListRequestsOrchestrator(OrchestratorBase[ListRequestsInput, ListRequestsOutput]):
    """Orchestrator for listing requests."""

    def __init__(
        self, command_bus: CommandBusPort, query_bus: QueryBusPort, logger: LoggingPort
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger

    async def execute(self, input: ListRequestsInput) -> ListRequestsOutput:  # type: ignore[return]
        self._logger.info(
            "ListRequestsOrchestrator: status=%s limit=%s sync=%s",
            input.status,
            input.limit,
            input.sync,
        )

        if input.sync and (input.q or input.sort):
            raise ValidationError("q and sort are not supported with sync=true")

        offset = decode_cursor(input.cursor) if input.cursor else input.offset
        sort = input.sort if input.sort else _DEFAULT_SORT

        if input.sync:
            # ListActiveRequestsQuery doesn't support q/sort (handler slices
            # before sync for perf — see handler docstring). Pagination
            # metadata still flows through Paginated.
            query = ListActiveRequestsQuery(
                limit=input.limit,
                offset=offset,
                all_resources=True,
                status=input.status,
            )
        else:
            query = ListRequestsQuery(  # type: ignore[assignment]
                status=input.status,
                limit=input.limit,
                offset=offset,
                template_id=input.template_id,
                q=input.q,
                sort=sort,
            )

        result = await self._query_bus.execute(query)
        if isinstance(result, Paginated):
            items = [self._to_dict(r) for r in result.items]
            total_count = result.total_count
        else:
            items = [self._to_dict(r) for r in (result or [])]
            total_count = len(items)

        next_cursor: str | None = None
        effective_limit = input.limit if input.limit is not None else total_count
        if effective_limit and total_count > offset + effective_limit:
            next_cursor = encode_cursor(offset + effective_limit)

        return ListRequestsOutput(
            requests=items,
            count=len(items),
            next_cursor=next_cursor,
            total_count=total_count,
        )

    @staticmethod
    def _to_dict(obj: object) -> dict:
        if hasattr(obj, "to_dict"):
            return obj.to_dict()  # type: ignore[union-attr]
        if hasattr(obj, "model_dump"):
            return obj.model_dump()  # type: ignore[union-attr]
        return dict(obj) if isinstance(obj, dict) else {"data": str(obj)}
