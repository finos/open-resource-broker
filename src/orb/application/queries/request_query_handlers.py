"""Query handlers for request domain queries."""

from __future__ import annotations

import asyncio
from typing import Any

from orb.application.base.handlers import BaseQueryHandler
from orb.application.decorators import query_handler
from orb.application.dto.queries import (
    SyncAndGetRequestQuery,
    SyncAndListActiveRequestsQuery,
    SyncAndListReturnRequestsQuery,
)
from orb.application.dto.responses import RequestDTO
from orb.application.factories.request_dto_factory import RequestDTOFactory
from orb.application.request.queries import ListRequestsQuery
from orb.application.services.machine_sync_service import MachineSyncService
from orb.application.services.orchestration.dtos import Paginated
from orb.application.services.provider_registry_service import ProviderRegistryService
from orb.application.services.request_query_service import RequestQueryService
from orb.application.services.request_status_service import RequestStatusService
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.exceptions import EntityNotFoundError, ProviderContractError
from orb.domain.base.ports import ContainerPort, ErrorHandlingPort, LoggingPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.services.generic_filter_service import GenericFilterService


def _resolve_state_machine(container: ContainerPort, logger: LoggingPort):
    """Resolve the shared FulfilmentStateMachine from DI.

    Falls back to a default-grace-period machine when DI does not provide one
    (lightweight test wiring) so the read-path deadline sweep always has a
    usable machine.
    """
    from orb.domain.request.fulfilment_state_machine import FulfilmentStateMachine

    try:
        return container.get(FulfilmentStateMachine)
    except Exception as exc:
        logger.debug("FulfilmentStateMachine unavailable in DI: %s", exc)
        return FulfilmentStateMachine(grace_period_seconds=3600)


@query_handler(SyncAndGetRequestQuery)
class SyncAndGetRequestHandler(BaseQueryHandler[SyncAndGetRequestQuery, RequestDTO]):
    """Handler for getting request details with live provider sync.

    For non-terminal requests this handler refreshes machine state from the
    provider and persists any changes before returning. See ADR-0001.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        container: ContainerPort,
        provider_registry_service: ProviderRegistryService,
        machine_sync_service: MachineSyncService,
    ) -> None:
        """Initialize the instance."""
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        self._container = container
        self._provider_registry_service = provider_registry_service
        self._machine_sync_service = machine_sync_service
        self._cache_service = self._get_cache_service()
        self.event_publisher = self._get_event_publisher()

        self._query_service = RequestQueryService(uow_factory, logger)
        self._state_machine = _resolve_state_machine(container, logger)
        self._status_service = RequestStatusService(
            uow_factory, logger, state_machine=self._state_machine
        )
        self._dto_factory = RequestDTOFactory()

    async def execute_query(self, query: SyncAndGetRequestQuery) -> RequestDTO:
        """Execute sync-and-get request query."""
        self.logger.info("Getting request details for: %s", query.request_id)

        try:
            if (
                not query.skip_cache
                and self._cache_service
                and self._cache_service.is_caching_enabled()
            ):
                cached_result = self._cache_service.get_cached_request(query.request_id)
                if cached_result:
                    self.logger.info("Cache hit for request: %s", query.request_id)
                    return cached_result

            request = await self._query_service.get_request(query.request_id)

            if query.lightweight:
                # No provider round-trip on the lightweight path, so the only
                # signal that can move an expired request is the deadline sweep.
                # It is safe to run here because there is no later provider
                # verdict that it could stale-short-circuit.
                request = await self._evaluate_deadline_and_persist(request)
                request_dto = self._dto_factory.create_from_domain(request, [])
                self.logger.info("Retrieved lightweight request: %s", query.request_id)
                return request_dto

            # Read-through sync: refresh the read model (DB) from live AWS state before
            # returning. This is intentional — the DB is a cache of provider state, and
            # status queries must reflect reality. Do NOT remove this in the name of
            # "CQRS purity". A query refreshing its own read model is not a domain command;
            # no domain invariants are enforced here, no domain events are raised.
            # The alternative (background polling) requires infrastructure that doesn't
            # exist yet. If you want to remove this, implement a background sync first.
            #
            # Terminal requests are immutable — skip the provider describe round-trip
            # and return the persisted machines directly.
            db_machines: list = []
            if request.status.is_terminal():
                db_machines = await self._query_service.get_machines_for_request(request)
                # Terminal requests skip the live provider sync — return persisted data.
                # first/last_status_check are stamped by MachineSyncService during the
                # sync cycle that produced the terminal status transition.
                return self._dto_factory.create_from_domain(request, db_machines)
            try:
                await self._machine_sync_service.populate_missing_machine_ids(request)
                # populate_missing_machine_ids writes to the DB via the
                # PopulateMachineIdsCommand but does not refresh the local
                # ``request`` variable. Re-read so subsequent steps see
                # the updated machine_ids — without this, the status
                # update path below sees stale (often empty) machine_ids
                # and the successful_count reconciliation can't fire.
                request = await self._query_service.get_request(query.request_id)
                db_machines = await self._query_service.get_machines_for_request(request)
                (
                    provider_machines,
                    provider_metadata,
                ) = await self._machine_sync_service.fetch_provider_machines(request, db_machines)
                synced_machines, _ = await self._machine_sync_service.sync_machines_with_provider(
                    request, db_machines, provider_machines
                )
                new_status, status_message = self._status_service.determine_status_from_machines(
                    db_machines, synced_machines, request, provider_metadata
                )
                if new_status:
                    await self._status_service.update_request_status(
                        request, new_status, status_message or "", provider_metadata
                    )
                request = await self._query_service.get_request(query.request_id)

                # Deadline sweep runs AFTER the provider sync so the latest
                # provider verdict is applied first. A pre-sync sweep on stale DB
                # counts could flip the request to a terminal outcome and then
                # permanently short-circuit further reconciliation — the exact
                # bug the PARTIAL_PENDING holding state exists to prevent. Only a
                # request that is genuinely still non-terminal AND past its
                # deadline after the fresh sync is swept here.
                request = await self._evaluate_deadline_and_persist(request)
            except ProviderContractError:
                raise
            except Exception as sync_err:
                self.logger.warning(
                    "Error syncing request %s, returning stored state: %s",
                    query.request_id,
                    sync_err,
                )
                return self._dto_factory.create_from_domain(request, db_machines)

            # first/last_status_check are now stamped by MachineSyncService.sync_machines_with_provider
            # which already runs above and performs the write in the correct command layer.
            machine_objects = await self._query_service.get_machines_for_request(request)
            request_dto = self._dto_factory.create_from_domain(request, machine_objects)

            # Only cache terminal requests.  Non-terminal requests (in_progress,
            # pending, provisioning) must be re-synced against the provider on
            # every poll so that status transitions (→ completed, → failed) are
            # picked up promptly.  Caching in_progress responses causes the
            # poll loop to receive stale status for the full TTL window and the
            # request appears stuck until the cache expires.
            if (
                self._cache_service
                and self._cache_service.is_caching_enabled()
                and request.status.is_terminal()
            ):
                self._cache_service.cache_request(query.request_id, request_dto)

            self.logger.info("Retrieved request: %s", query.request_id)
            return request_dto

        except EntityNotFoundError:
            self.logger.error("Request not found: %s", query.request_id)
            raise
        except Exception as e:
            self.logger.error("Failed to get request: %s", e)
            raise

    async def _evaluate_deadline_and_persist(self, request):
        """Lazily sweep an expired request to terminal and persist the flip.

        Only writes when the status actually changes and the request is not
        already terminal. Uses a short OCC retry loop so a concurrent poll does
        not surface a raw ConcurrencyError on a read path. A persist failure is
        logged and the in-memory swept request is still returned.
        """
        from datetime import datetime, timezone

        from orb.domain.base.exceptions import ConcurrencyError

        if request.status.is_terminal():
            return request

        state_machine = self._state_machine
        try:
            swept = state_machine.evaluate_deadline(request, now=datetime.now(timezone.utc))
        except Exception as exc:
            # A malformed / test-double request whose deadline_at is not a real
            # datetime must never break the read path — skip the sweep.
            self.logger.debug("Deadline evaluation skipped: %s", exc)
            return request
        if swept is request or swept.status == request.status:
            return request

        _MAX_RETRIES = 3
        for attempt in range(_MAX_RETRIES + 1):
            try:
                with self.uow_factory.create_unit_of_work() as uow:
                    events = uow.requests.save(swept)
                    for event in events or []:
                        self.event_publisher.publish(event)
                return swept
            except ConcurrencyError:
                if attempt >= _MAX_RETRIES:
                    self.logger.warning(
                        "Deadline sweep persist lost the race for %s; returning in-memory state",
                        request.request_id.value,
                    )
                    return swept
                # Reload, re-evaluate, retry.
                reloaded = await self._query_service.get_request(str(request.request_id.value))
                if reloaded is None or reloaded.status.is_terminal():
                    return reloaded or swept
                swept = state_machine.evaluate_deadline(reloaded, now=datetime.now(timezone.utc))
                if swept is reloaded or swept.status == reloaded.status:
                    return reloaded
            except Exception as exc:
                self.logger.warning("Deadline sweep persist failed: %s", exc)
                return swept
        return swept

    def _get_cache_service(self):
        try:
            from orb.application.ports.cache_service_port import CacheServicePort

            return self._container.get(CacheServicePort)
        except Exception as e:
            self.logger.warning("Failed to initialize cache service: %s", e)
            return None

    def _get_event_publisher(self):
        try:
            from orb.domain.base.ports import EventPublisherPort

            return self._container.get(EventPublisherPort)
        except Exception as e:
            self.logger.warning("Failed to initialize event publisher: %s", e)

            class NoOpEventPublisher:
                """No-operation event publisher that discards events."""

                def publish(self, event) -> None:
                    """Publish event (no-op implementation)."""

            return NoOpEventPublisher()


@query_handler(ListRequestsQuery)  # type: ignore[arg-type]
class ListRequestsHandler(BaseQueryHandler[ListRequestsQuery, Paginated[RequestDTO]]):
    """Handler for listing requests with filtering."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        generic_filter_service: GenericFilterService,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        self._generic_filter_service = generic_filter_service

    async def execute_query(self, query: ListRequestsQuery) -> Paginated[RequestDTO]:
        """Execute list requests query.

        Pipeline: load → status/template/type/provider filters → q → sort
                  → total → slice → DTO factory → filter_expressions.

        Pagination metadata reflects the post-filter total before the
        slice. ``filter_expressions`` operate on the DTO form and run
        after the slice; they should not be relied on for cross-page
        filtering.
        """
        self.logger.info("Listing requests with filters")

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                requests = uow.requests.find_all()
                total_unfiltered = len(requests)

                if query.provider_name:
                    requests = [r for r in requests if r.provider_name == query.provider_name]

                if query.provider_type:
                    requests = [r for r in requests if r.provider_type == query.provider_type]

                if query.status:
                    from orb.domain.request.value_objects import RequestStatus

                    status_filter = RequestStatus(query.status)
                    requests = [r for r in requests if r.status == status_filter]

                if query.template_id:
                    requests = [r for r in requests if r.template_id == query.template_id]

                if query.request_type:
                    requests = [
                        r
                        for r in requests
                        if getattr(r, "request_type", None) == query.request_type
                    ]

                # q: substring across user-visible fields
                if getattr(query, "q", None):
                    needle = query.q.lower()  # type: ignore[union-attr]
                    searchable = ("request_id", "template_id", "provider_api", "provider_name")
                    requests = [
                        r
                        for r in requests
                        if any(needle in str(getattr(r, f, "") or "").lower() for f in searchable)
                    ]

                # sort: "+field" / "-field"
                sort_key_attr = getattr(query, "sort", None)
                if sort_key_attr:
                    sort_key = str(sort_key_attr)
                    descending = sort_key.startswith("-")
                    attr = sort_key.lstrip("-+")

                    def _val(r: Any) -> str:
                        raw = getattr(r, attr, "")
                        return "" if raw is None else str(raw)

                    try:
                        requests = sorted(requests, key=_val, reverse=descending)
                    except TypeError as exc:
                        self.logger.warning(
                            "ListRequests sort failed on attr=%s descending=%s: %s",
                            attr,
                            descending,
                            exc,
                        )

                total_count = len(requests)

                start_idx = query.offset or 0
                if query.limit is None:
                    requests = requests[start_idx:]
                else:
                    limit = min(query.limit, 1000)
                    if query.limit > 1000:
                        self.logger.warning(
                            "ListRequestsQuery.limit=%d clamped to 1000; "
                            "total_count=%d. Consumers needing full counts "
                            "should rely on total_count, not len(requests).",
                            query.limit,
                            total_count,
                        )
                    end_idx = start_idx + limit
                    requests = requests[start_idx:end_idx] if limit > 0 else []

                dto_factory = RequestDTOFactory()
                request_dtos = []
                for request in requests:
                    machines = []
                    if request.machine_ids:
                        machines = uow.machines.find_by_ids(request.machine_ids)

                    request_dto = dto_factory.create_from_domain(request, machines)
                    request_dtos.append(request_dto)

                if query.filter_expressions:
                    request_dicts = [dto.model_dump() for dto in request_dtos]
                    filtered_dicts = self._generic_filter_service.apply_filters(
                        request_dicts, query.filter_expressions
                    )
                    request_dtos = [RequestDTO.model_validate(d) for d in filtered_dicts]

                self.logger.info(
                    "Found %s requests (total: %s, unfiltered: %s)",
                    len(request_dtos),
                    total_count,
                    total_unfiltered,
                )
                return Paginated(
                    items=request_dtos,
                    total_count=total_count,
                    total_unfiltered=total_unfiltered,
                )

        except Exception as e:
            self.logger.error("Failed to list requests: %s", e)
            raise


@query_handler(SyncAndListReturnRequestsQuery)
class SyncAndListReturnRequestsHandler(
    BaseQueryHandler[SyncAndListReturnRequestsQuery, Paginated[RequestDTO]]
):
    """Handler for listing return requests with live provider sync.

    Each non-terminal return request is refreshed from the provider and any
    state changes (including status transitions) are persisted before the
    response is assembled. See ADR-0001.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        generic_filter_service: GenericFilterService,
        machine_sync_service: MachineSyncService,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        self._generic_filter_service = generic_filter_service
        self._machine_sync_service = machine_sync_service
        self._status_service = RequestStatusService(uow_factory, logger)
        self._query_service = RequestQueryService(uow_factory, logger)
        self._dto_factory = RequestDTOFactory()

    async def execute_query(self, query: SyncAndListReturnRequestsQuery) -> Paginated[RequestDTO]:
        """Execute sync-and-list return requests query."""
        self.logger.info("Listing return requests")

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                from orb.domain.request.value_objects import RequestType

                return_requests = uow.requests.find_by_type(RequestType.RETURN)

            # Read-through sync: refresh each non-terminal return request from
            # live provider state so a return that has actually completed at the
            # provider transitions to COMPLETED in the DB.  Without this, a
            # caller polling list_return_requests would see a request stuck in
            # IN_PROGRESS forever and may retry the return — triggering
            # provider-side double-decrement (e.g. ASG capacity off by one).
            for request in return_requests:
                if request.status.is_terminal():
                    continue
                try:
                    db_machines = await self._query_service.get_machines_for_request(request)
                    (
                        provider_machines,
                        provider_metadata,
                    ) = await self._machine_sync_service.fetch_provider_machines(
                        request, db_machines
                    )
                    (
                        synced_machines,
                        _,
                    ) = await self._machine_sync_service.sync_machines_with_provider(
                        request, db_machines, provider_machines
                    )
                    new_status, status_message = (
                        self._status_service.determine_status_from_machines(
                            db_machines, synced_machines, request, provider_metadata
                        )
                    )
                    if new_status:
                        await self._status_service.update_request_status(
                            request, new_status, status_message or "", provider_metadata
                        )
                except Exception as sync_err:
                    self.logger.warning(
                        "Sync failed for return request %s, returning stored state: %s",
                        request.request_id.value,
                        sync_err,
                    )

            with self.uow_factory.create_unit_of_work() as uow:
                from orb.domain.request.value_objects import RequestType

                return_requests = uow.requests.find_by_type(RequestType.RETURN)

                request_dtos = []
                for request in return_requests:
                    machines = []
                    if request.machine_ids:
                        machines = uow.machines.find_by_ids(request.machine_ids)

                    request_dto = self._dto_factory.create_from_domain(request, machines)
                    request_dtos.append(request_dto)

                if query.provider_name:
                    request_dtos = [
                        r for r in request_dtos if r.provider_name == query.provider_name
                    ]

                if query.provider_type:
                    request_dtos = [
                        r for r in request_dtos if r.provider_type == query.provider_type
                    ]

                if query.machine_names:
                    machine_name_set = set(query.machine_names)
                    filtered = []
                    for dto in request_dtos:
                        dto_dict = dto.model_dump()
                        machines = (
                            dto_dict.get("machines") or dto_dict.get("machine_references") or []
                        )
                        names = {
                            m.get("name") or m.get("machine_id") or m.get("instance_id")
                            for m in machines
                            if isinstance(m, dict)
                        }
                        if names & machine_name_set:
                            filtered.append(dto)
                    request_dtos = filtered

                if query.filter_expressions:
                    request_dicts = [dto.model_dump() for dto in request_dtos]
                    filtered_dicts = self._generic_filter_service.apply_filters(
                        request_dicts, query.filter_expressions
                    )
                    request_dtos = [RequestDTO.model_validate(d) for d in filtered_dicts]

                # q + sort BEFORE slice so pagination is honest.
                if getattr(query, "q", None):
                    needle = str(query.q).lower()  # type: ignore[union-attr]
                    searchable = (
                        "request_id",
                        "template_id",
                        "provider_api",
                        "provider_name",
                    )
                    request_dtos = [
                        d
                        for d in request_dtos
                        if any(needle in str(getattr(d, f, "") or "").lower() for f in searchable)
                    ]

                sort_key_attr = getattr(query, "sort", None)
                if sort_key_attr:
                    sort_key = str(sort_key_attr)
                    descending = sort_key.startswith("-")
                    attr = sort_key.lstrip("-+")

                    def _val(d: Any) -> str:
                        raw = getattr(d, attr, "")
                        return "" if raw is None else str(raw)

                    try:
                        request_dtos = sorted(request_dtos, key=_val, reverse=descending)
                    except TypeError as exc:
                        self.logger.warning(
                            "ListReturnRequests sort failed on attr=%s descending=%s: %s",
                            attr,
                            descending,
                            exc,
                        )

                total_count = len(request_dtos)
                offset = query.offset or 0  # type: ignore[union-attr]
                if query.limit is None:
                    request_dtos = request_dtos[offset:]
                else:
                    limit = min(query.limit, 1000)
                    if query.limit > 1000:
                        self.logger.warning(
                            "SyncAndListReturnRequestsQuery.limit=%d clamped to 1000; "
                            "total_count=%d. Consumers needing full counts "
                            "should rely on total_count, not len(requests).",
                            query.limit,
                            total_count,
                        )
                    request_dtos = request_dtos[offset : offset + limit] if limit > 0 else []

                self.logger.info(
                    "Found %s return requests (total: %s, offset: %s)",
                    len(request_dtos),
                    total_count,
                    offset,
                )
                return Paginated(
                    items=request_dtos,
                    total_count=total_count,
                )

        except Exception as e:
            self.logger.error("Failed to list return requests: %s", e)
            raise


@query_handler(SyncAndListActiveRequestsQuery)
class SyncAndListActiveRequestsHandler(
    BaseQueryHandler[SyncAndListActiveRequestsQuery, Paginated[RequestDTO]]
):
    """Handler for listing active requests with live provider sync per request.

    Each non-terminal request on the returned page is refreshed from the provider
    in parallel and any state changes are persisted before the response is
    assembled. See ADR-0001.
    """

    _DEFAULT_SYNC_TIMEOUT: float = 30.0

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        generic_filter_service: GenericFilterService,
        machine_sync_service: MachineSyncService,
        config: ConfigurationPort | None = None,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        self._generic_filter_service = generic_filter_service
        self._machine_sync_service = machine_sync_service
        self._status_service = RequestStatusService(uow_factory, logger)
        self._query_service = RequestQueryService(uow_factory, logger)
        self._dto_factory = RequestDTOFactory()
        self._sync_timeout: float = self._resolve_sync_timeout(config)

    def _resolve_sync_timeout(self, config: ConfigurationPort | None) -> float:
        """Read sync_timeout_seconds from performance config, falling back to default."""
        if config is None:
            return self._DEFAULT_SYNC_TIMEOUT
        try:
            app_cfg = config.app_config
            if app_cfg is not None and hasattr(app_cfg, "performance"):
                return float(app_cfg.performance.sync_timeout_seconds)
        except Exception:
            # Missing / malformed performance config falls back to the default
            # timeout rather than crashing the query handler on cold start.
            return self._DEFAULT_SYNC_TIMEOUT
        return self._DEFAULT_SYNC_TIMEOUT

    async def execute_query(self, query: SyncAndListActiveRequestsQuery) -> Paginated[RequestDTO]:
        """Execute sync-and-list active requests query.

        Pagination is applied before the per-row read-through sync to
        bound the AWS API call cost to one round per page rather than
        one per active request. As a consequence, ``q`` and ``sort`` are
        not honoured on this code path. ``total_count`` reflects the
        post-status-filter total.
        """
        self.logger.info("Listing active requests")

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                if query.all_resources:
                    requests = uow.requests.find_all()
                else:
                    from orb.domain.request.value_objects import RequestStatus

                    active_statuses = [RequestStatus.PENDING, RequestStatus.IN_PROGRESS]
                    all_requests = uow.requests.find_all()
                    requests = [r for r in all_requests if r.status in active_statuses]

                if query.template_id:
                    requests = [r for r in requests if r.template_id == query.template_id]

                total_count = len(requests)
                limit = min(query.limit or 50, 1000)  # type: ignore[union-attr]
                offset = query.offset or 0  # type: ignore[union-attr]
                requests = requests[offset : offset + limit]

            # Read-through sync: refresh each request's read model from live AWS state.
            # See SyncAndGetRequestHandler for rationale — do NOT remove in the name of CQRS purity.
            # Run in parallel bounded by a semaphore so a 50-request page
            # stops costing 50× the AWS round-trip latency. The cap (8)
            # comes from typical EC2 DescribeInstances rate-limit
            # headroom; raise if the provider permits more.
            _sync_concurrency = asyncio.Semaphore(8)

            async def _sync_one(request):
                async with _sync_concurrency:

                    async def _do_sync():
                        nonlocal request
                        await self._machine_sync_service.populate_missing_machine_ids(request)
                        refreshed = await self._query_service.get_request(
                            str(request.request_id.value)
                        )
                        if refreshed is not None:
                            request = refreshed
                        db_machines = await self._query_service.get_machines_for_request(request)
                        (
                            provider_machines,
                            provider_metadata,
                        ) = await self._machine_sync_service.fetch_provider_machines(
                            request, db_machines
                        )
                        (
                            synced_machines,
                            _,
                        ) = await self._machine_sync_service.sync_machines_with_provider(
                            request, db_machines, provider_machines
                        )
                        new_status, status_message = (
                            self._status_service.determine_status_from_machines(
                                db_machines, synced_machines, request, provider_metadata
                            )
                        )
                        if new_status:
                            await self._status_service.update_request_status(
                                request, new_status, status_message or "", provider_metadata
                            )

                    try:
                        await asyncio.wait_for(_do_sync(), timeout=self._sync_timeout)
                    except asyncio.TimeoutError:
                        self.logger.warning(
                            "Sync timed out for request %s (machine_ids=%s, timeout_seconds=%s),"
                            " returning last known stored state",
                            request.request_id.value,
                            request.machine_ids,
                            self._sync_timeout,
                        )
                    except Exception as sync_err:
                        self.logger.warning(
                            "Sync failed for request %s, returning stored state: %s",
                            request.request_id.value,
                            sync_err,
                        )

            # Use return_exceptions=True so that a BaseException raised inside a
            # coroutine (KeyboardInterrupt, SystemExit, etc.) is returned as a
            # value rather than propagated through gather, which would otherwise
            # cancel all sibling coroutines and surface an unhandled exception at
            # the caller.  CancelledError is re-raised so the event loop can still
            # clean up normally when the task itself is cancelled.
            _sync_results = await asyncio.gather(
                *(_sync_one(r) for r in requests), return_exceptions=True
            )
            for _sync_exc in _sync_results:
                if isinstance(_sync_exc, BaseException) and not isinstance(_sync_exc, Exception):
                    # Non-Exception BaseException (KeyboardInterrupt, SystemExit, …).
                    # Log at ERROR level with context, then re-raise so the runtime
                    # can shut down cleanly.
                    self.logger.error(
                        "ListActiveRequests fanout hit non-recoverable BaseException "
                        "(request_ids=%s): %r",
                        [str(r.request_id.value) for r in requests],
                        _sync_exc,
                    )
                    raise _sync_exc

            request_dtos = []
            for request in requests:
                request = await self._query_service.get_request(str(request.request_id.value))
                db_machines = await self._query_service.get_machines_for_request(request)

                request_dto = self._dto_factory.create_from_domain(request, db_machines)
                request_dtos.append(request_dto)

            if query.provider_name:
                request_dtos = [r for r in request_dtos if r.provider_name == query.provider_name]

            if query.provider_type:
                request_dtos = [r for r in request_dtos if r.provider_type == query.provider_type]

            if query.filter_expressions:
                request_dicts = [dto.model_dump() for dto in request_dtos]
                filtered_dicts = self._generic_filter_service.apply_filters(
                    request_dicts, query.filter_expressions
                )
                request_dtos = [RequestDTO.model_validate(d) for d in filtered_dicts]

            self.logger.info(
                "Found %s active requests (total: %s, limit: %s, offset: %s)",
                len(request_dtos),
                total_count,
                limit,
                offset,
            )
            return Paginated(items=request_dtos, total_count=total_count)

        except Exception as e:
            self.logger.error("Failed to list active requests: %s", e)
            raise
