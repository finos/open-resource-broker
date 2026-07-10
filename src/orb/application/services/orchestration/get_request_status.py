"""Orchestrator for getting request status."""

from __future__ import annotations

from orb.application.dto.queries import SyncAndGetRequestQuery, SyncAndListActiveRequestsQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    GetRequestStatusInput,
    GetRequestStatusOutput,
    Paginated,
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
            query = SyncAndListActiveRequestsQuery(all_resources=True, limit=None)
            results = await self._query_bus.execute(query)
            items = results.items if isinstance(results, Paginated) else (results or [])
            return GetRequestStatusOutput(requests=[self._to_dict(r) for r in items])

        request_dicts = []
        for request_id in input.request_ids:
            try:
                # When the caller asks for verbose status (the default for
                # GET /requests/{id}/status and the explicit batch-sync
                # endpoint), bypass the read-through cache. The whole point
                # of those calls is to refresh state from the provider —
                # serving a cached DTO defeats it and leaves the request
                # stuck on stale IN_PROGRESS even after a successful sync.
                # Non-verbose callers (lightweight list rows, etc.) can
                # still hit the cache for speed.
                query = SyncAndGetRequestQuery(  # type: ignore[assignment]
                    request_id=request_id,
                    verbose=input.verbose,
                    skip_cache=bool(input.verbose),
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
