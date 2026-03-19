"""Orchestrator for listing requests."""

from __future__ import annotations

from orb.application.dto.queries import ListActiveRequestsQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.request.queries import ListRequestsQuery
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import ListRequestsInput, ListRequestsOutput
from orb.domain.base.ports.logging_port import LoggingPort


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

        if input.sync:
            query = ListActiveRequestsQuery(
                limit=input.limit, offset=input.offset, all_resources=True, status=input.status
            )
        else:
            query = ListRequestsQuery(status=input.status, limit=input.limit, offset=input.offset)  # type: ignore[assignment]

        results = await self._query_bus.execute(query)
        requests = [self._to_dict(r) for r in (results or [])]
        return ListRequestsOutput(requests=requests, count=len(requests))

    @staticmethod
    def _to_dict(obj: object) -> dict:
        if hasattr(obj, "to_dict"):
            return obj.to_dict()  # type: ignore[union-attr]
        if hasattr(obj, "model_dump"):
            return obj.model_dump()  # type: ignore[union-attr]
        return dict(obj) if isinstance(obj, dict) else {"data": str(obj)}
