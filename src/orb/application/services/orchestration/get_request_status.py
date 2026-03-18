"""Orchestrator for getting request status."""

from __future__ import annotations

from orb.application.dto.queries import GetRequestQuery, ListActiveRequestsQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    GetRequestStatusInput,
    GetRequestStatusOutput,
)
from orb.domain.base.ports.logging_port import LoggingPort


class GetRequestStatusOrchestrator(OrchestratorBase[GetRequestStatusInput, GetRequestStatusOutput]):
    """Orchestrator for retrieving request status."""

    def __init__(
        self, command_bus: CommandBusPort, query_bus: QueryBusPort, logger: LoggingPort
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger

    async def execute(self, input: GetRequestStatusInput) -> GetRequestStatusOutput:  # type: ignore[return]
        self._logger.info("GetRequestStatusOrchestrator: all=%s", input.all_requests)

        if input.all_requests:
            query = ListActiveRequestsQuery(all_resources=True, limit=None)
            results = await self._query_bus.execute(query)
            return GetRequestStatusOutput(requests=[self._to_dict(r) for r in (results or [])])

        request_dicts = []
        for request_id in input.request_ids:
            try:
                query = GetRequestQuery(  # type: ignore[assignment]
                    request_id=request_id,
                    long=input.detailed,
                )
                result = await self._query_bus.execute(query)
                request_dicts.append(self._to_dict(result))
            except Exception as exc:
                self._logger.error("Failed to get status for %s: %s", request_id, exc)
                request_dicts.append({"request_id": request_id, "error": str(exc)})

        return GetRequestStatusOutput(requests=request_dicts)

    @staticmethod
    def _to_dict(obj: object) -> dict:
        if hasattr(obj, "to_dict"):
            return obj.to_dict()  # type: ignore[union-attr]
        if hasattr(obj, "model_dump"):
            return obj.model_dump()  # type: ignore[union-attr]
        return dict(obj) if isinstance(obj, dict) else {"data": str(obj)}
