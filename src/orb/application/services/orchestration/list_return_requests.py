"""Orchestrator for listing return requests."""

from __future__ import annotations

from orb.application.dto.queries import ListReturnRequestsQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    ListReturnRequestsInput,
    ListReturnRequestsOutput,
    Paginated,
    decode_cursor,
    encode_cursor,
)
from orb.domain.base.ports.logging_port import LoggingPort

_DEFAULT_GRACE_PERIOD = 300
_SPOT_GRACE_PERIOD = 120

_DEFAULT_SORT = "-created_at"


class ListReturnRequestsOrchestrator(
    OrchestratorBase[ListReturnRequestsInput, ListReturnRequestsOutput]
):
    """Orchestrator for listing return requests."""

    def __init__(
        self,
        command_bus: CommandBusPort,
        query_bus: QueryBusPort,
        logger: LoggingPort,
        default_grace_period: int = _DEFAULT_GRACE_PERIOD,
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger
        self._default_grace_period = default_grace_period

    async def execute(self, input: ListReturnRequestsInput) -> ListReturnRequestsOutput:  # type: ignore[return]
        self._logger.info(
            "ListReturnRequestsOrchestrator: status=%s limit=%s",
            input.status,
            input.limit,
        )

        offset = decode_cursor(input.cursor) if input.cursor else input.offset
        sort = input.sort if input.sort else _DEFAULT_SORT

        query = ListReturnRequestsQuery(
            status=input.status,
            limit=input.limit,
            offset=offset,
            q=input.q,
            sort=sort,
        )
        result = await self._query_bus.execute(query)

        if isinstance(result, Paginated):
            items = [self._enrich(self._to_dict(r)) for r in result.items]
            total_count = result.total_count
        else:
            items = [self._enrich(self._to_dict(r)) for r in (result or [])]
            total_count = len(items)

        next_cursor: str | None = None
        effective_limit = input.limit if input.limit is not None else total_count
        if effective_limit and total_count > offset + effective_limit:
            next_cursor = encode_cursor(offset + effective_limit)

        return ListReturnRequestsOutput(
            requests=items,
            next_cursor=next_cursor,
            total_count=total_count,
        )

    def _enrich(self, data: dict) -> dict:
        """Add grace_period to a return request dict.

        Uses spot override (120s) if price_type is 'spot', otherwise the
        configured default (300s).
        """
        if "grace_period" not in data:
            is_spot = data.get("price_type") == "spot"
            data["grace_period"] = _SPOT_GRACE_PERIOD if is_spot else self._default_grace_period
        return data

    @staticmethod
    def _to_dict(obj: object) -> dict:
        if hasattr(obj, "to_dict"):
            return obj.to_dict()  # type: ignore[union-attr]
        if hasattr(obj, "model_dump"):
            return obj.model_dump()  # type: ignore[union-attr]
        return dict(obj) if isinstance(obj, dict) else {"data": str(obj)}
