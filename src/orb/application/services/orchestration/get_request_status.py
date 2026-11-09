"""Orchestrator for getting request status."""

from __future__ import annotations

from orb.application.dto.queries import SyncAndGetRequestQuery, SyncAndListActiveRequestsQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import (
    MAX_CONSECUTIVE_POLL_ERRORS as _MAX_CONSECUTIVE_POLL_ERRORS,
    TERMINAL_STATUSES as _TERMINAL_STATUSES,
    OrchestratorBase,
)
from orb.application.services.orchestration.dtos import (
    GetRequestStatusInput,
    GetRequestStatusOutput,
    Paginated,
)
from orb.domain.base.clock import Clock, SystemClock
from orb.domain.base.exceptions import EntityNotFoundError
from orb.domain.base.ports.logging_port import LoggingPort


class GetRequestStatusOrchestrator(OrchestratorBase[GetRequestStatusInput, GetRequestStatusOutput]):
    """Orchestrator for retrieving request status."""

    def __init__(
        self,
        command_bus: CommandBusPort,
        query_bus: QueryBusPort,
        logger: LoggingPort,
        clock: Clock | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger

    async def execute(self, input: GetRequestStatusInput) -> GetRequestStatusOutput:  # type: ignore[return]
        self._logger.info("GetRequestStatusOrchestrator: all=%s", input.all_requests)

        if input.all_requests:
            query = SyncAndListActiveRequestsQuery(all_resources=True, limit=None)
            results = await self._dispatch(
                "GetRequestStatus.listActive", self._query_bus.execute(query)
            )
            items = results.items if isinstance(results, Paginated) else (results or [])
            return GetRequestStatusOutput(requests=[self._to_dict(r) for r in items])

        if input.wait and input.request_ids:
            request_dicts = await self._poll_until_terminal(input)
            return GetRequestStatusOutput(requests=request_dicts)

        return GetRequestStatusOutput(requests=await self._fetch_once(input))

    async def _fetch_once(self, input: GetRequestStatusInput) -> list[dict]:
        """Fetch a single status snapshot for every requested ID.

        Bulk per-request path: a single bad ID must NOT fail the whole batch,
        so each request is queried independently and any failure is folded
        into that request's own result dict rather than re-raised. This is a
        deliberate divergence from the single-dispatch _dispatch() re-raise
        pattern used elsewhere — multi-ID callers rely on partial results.
        Every failure is still logged before being captured (see below).
        """
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
            except EntityNotFoundError as exc:
                # A genuinely non-existent request. Tag it with an explicit
                # ``not_found`` flag so single-ID callers can distinguish it
                # from an existing request that merely errored during sync.
                # A real RequestDTO never carries a "not_found" key, so this
                # flag is an unambiguous synthetic-only discriminator.
                self._logger.info("Request %s not found: %s", request_id, exc)
                request_dicts.append(
                    {"request_id": request_id, "not_found": True, "error": str(exc)}
                )
            except Exception as exc:
                # The request may well exist but its provider sync failed
                # (e.g. ProviderContractError, infra error). Emit an error
                # entry WITHOUT the not_found flag so batch callers still see
                # a per-ID partial failure and single-ID callers do not 404 a
                # request that actually exists.
                self._logger.error("Failed to get status for %s: %s", request_id, exc)
                request_dicts.append({"request_id": request_id, "error": str(exc)})

        return request_dicts

    async def _poll_until_terminal(self, input: GetRequestStatusInput) -> list[dict]:
        """Poll every requested ID until all reach a terminal state or timeout.

        Elapsed time is measured as true wall-clock via ``self._clock.monotonic()``
        deltas from the first poll against ``input.timeout_seconds``, so the real
        latency of each ``_fetch_once`` counts toward the budget and the total
        wait never overruns the caller's cap. (An interval-counting approach that
        added a constant interval per loop ignored fetch
        latency and could overshoot the timeout substantially.) The last snapshot
        is returned when the cap is reached even if some requests are still
        non-terminal, so callers always get the freshest known state.

        A transient sync error (an ``error`` entry with no terminal status and
        no ``not_found`` flag) does NOT immediately end the poll — the provider
        may simply have blipped. Up to ``_MAX_CONSECUTIVE_POLL_ERRORS``
        consecutive polls carrying such a transient error are tolerated (in
        line with the acquire/return sibling loops) before the poll gives up
        and returns the last snapshot. A genuine terminal state (a real
        ``failed``/``complete`` status, or ``not_found``) still stops the poll
        immediately.
        """
        start = self._clock.monotonic()
        request_dicts = await self._fetch_once(input)
        consecutive_errors = 1 if self._has_transient_error(request_dicts) else 0
        while (
            not self._all_terminal(request_dicts)
            and (self._clock.monotonic() - start) < input.timeout_seconds
        ):
            if consecutive_errors >= _MAX_CONSECUTIVE_POLL_ERRORS:
                # Persistent transient failures — stop retrying and surface the
                # last snapshot rather than blocking until timeout.
                break
            await self._clock.async_sleep(self._poll_delay(start, input.timeout_seconds))
            request_dicts = await self._fetch_once(input)
            if self._has_transient_error(request_dicts):
                consecutive_errors += 1
            else:
                consecutive_errors = 0
        return request_dicts

    @staticmethod
    def _has_transient_error(request_dicts: list[dict]) -> bool:
        """Return True if any entry is a transient sync error.

        A transient sync error is an ``error`` entry that is neither a
        ``not_found`` (a genuine 404) nor accompanied by a terminal status (a
        genuine request failure). Those two cases are real terminal outcomes
        and must not be treated as retryable blips.
        """
        for entry in request_dicts:
            if entry.get("not_found"):
                continue
            if not entry.get("error"):
                continue
            status = str(entry.get("status", "")).lower()
            if status not in _TERMINAL_STATUSES:
                return True
        return False

    @staticmethod
    def _all_terminal(request_dicts: list[dict]) -> bool:
        """Return True once every entry reaches a terminal outcome.

        Terminal outcomes are: ``not_found`` (a genuine 404), an entry carrying
        a real terminal ``status`` (e.g. ``failed``/``complete``), or a
        non-error entry whose status is terminal. A transient sync error (an
        ``error`` entry with no terminal status) is NOT terminal, so polling
        continues and can recover once the provider responds again.
        """
        for entry in request_dicts:
            if entry.get("not_found"):
                continue
            status = str(entry.get("status", "")).lower()
            if entry.get("error"):
                # Only a terminal status makes an errored entry terminal;
                # a bare transient error keeps the poll going.
                if status in _TERMINAL_STATUSES:
                    continue
                return False
            if status not in _TERMINAL_STATUSES:
                return False
        return True

    @staticmethod
    def _to_dict(obj: object) -> dict:
        if hasattr(obj, "to_dict"):
            return obj.to_dict()  # type: ignore[union-attr]
        if hasattr(obj, "model_dump"):
            return obj.model_dump()  # type: ignore[union-attr]
        return dict(obj) if isinstance(obj, dict) else {"data": str(obj)}
