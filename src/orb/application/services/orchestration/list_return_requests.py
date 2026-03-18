"""Orchestrator for listing return requests."""

from __future__ import annotations

from orb.application.dto.queries import ListReturnRequestsQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    ListReturnRequestsInput,
    ListReturnRequestsOutput,
)
from orb.domain.base.ports.logging_port import LoggingPort

_DEFAULT_GRACE_PERIOD = 300
_SPOT_GRACE_PERIOD = 120


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
            "ListReturnRequestsOrchestrator: status=%s limit=%d",
            input.status,
            input.limit,
        )

        query = ListReturnRequestsQuery(status=input.status, limit=input.limit)
        results = await self._query_bus.execute(query)
        requests = [self._enrich(self._to_dict(r)) for r in (results or [])]
        return ListReturnRequestsOutput(requests=requests)

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
        if hasattr(obj, "model_dump"):
            return obj.model_dump()  # type: ignore[union-attr]
        if hasattr(obj, "to_dict"):
            return obj.to_dict()  # type: ignore[union-attr]
        return dict(obj) if isinstance(obj, dict) else {"data": str(obj)}
