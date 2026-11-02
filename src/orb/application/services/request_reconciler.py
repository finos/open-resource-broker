"""Background reconciliation of in-flight requests with provider state.

A long-running server process runs :class:`RequestReconciler` so stored request
and machine state keeps following the provider without a client having to poll
each request. Reads (list views) can then serve stored state that is recent
enough instead of calling the provider per request.

The loop is bounded (a semaphore caps concurrent provider calls and each
request has a time limit), skips providers whose circuit breaker is open, can
be turned off in configuration, and stops cleanly when asked.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from orb.application.ports.resilience_port import CircuitBreakerPort
from orb.application.services.machine_sync_service import FailedProviderFetch, MachineSyncService
from orb.application.services.request_query_service import RequestQueryService
from orb.application.services.request_status_service import RequestStatusService
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.ports import ConfigurationPort, LoggingPort
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestStatus

_DEFAULT_INTERVAL_SECONDS = 60
_DEFAULT_MAX_CONCURRENCY = 4
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 30
_DEFAULT_LIST_CACHE_MAX_AGE_SECONDS = 30


# Number of reconcile loops currently running in this process.
_loops_running = 0


@dataclass(frozen=True)
class RequestSyncSettings:
    """Reconcile-loop and list-cache settings, read from the request config."""

    enabled: bool = False
    interval_seconds: float = _DEFAULT_INTERVAL_SECONDS
    max_concurrency: int = _DEFAULT_MAX_CONCURRENCY
    request_timeout_seconds: float = _DEFAULT_REQUEST_TIMEOUT_SECONDS
    list_cache_max_age_seconds: float = _DEFAULT_LIST_CACHE_MAX_AGE_SECONDS

    @classmethod
    def from_config(cls, config_port: Optional[ConfigurationPort]) -> "RequestSyncSettings":
        """Read the settings; missing or malformed config yields the defaults."""
        if config_port is None:
            return cls()
        try:
            raw = config_port.get_request_config()
            return cls(
                enabled=bool(raw.get("reconcile_enabled", False)),
                interval_seconds=max(0.001, float(raw.get("reconcile_interval_seconds", 60))),
                max_concurrency=max(1, int(raw.get("reconcile_max_concurrency", 4))),
                request_timeout_seconds=max(
                    0.001, float(raw.get("reconcile_request_timeout_seconds", 30))
                ),
                list_cache_max_age_seconds=max(
                    0.0, float(raw.get("list_cache_max_age_seconds", 30))
                ),
            )
        except Exception:
            return cls()

    @property
    def list_staleness_window(self) -> float:
        """How recent stored state must be for a list view to skip the provider.

        Only applies while the background loop is enabled *and* running in this
        process: the loop is what keeps stored state recent, so a process
        without one (a one-shot CLI call, for example) always refreshes.
        """
        return self.list_cache_max_age_seconds if self.enabled and _loops_running > 0 else 0.0


def is_recently_synced(
    request: Request, window_seconds: float, now: Optional[datetime] = None
) -> bool:
    """True when ``request`` was synced from the provider within ``window_seconds``."""
    if window_seconds <= 0 or request.last_status_check is None:
        return False
    current = now or datetime.now(timezone.utc)
    checked = request.last_status_check
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return (current - checked).total_seconds() < window_seconds


class ProviderFetchFailed(RuntimeError):
    """The provider could not be reached for a request during reconciliation."""


@dataclass
class ReconcileSummary:
    """Outcome of one reconcile pass."""

    reconciled: int = 0
    skipped_recent: int = 0
    skipped_open_circuit: int = 0
    skipped_provisioning: int = 0
    timed_out: int = 0
    failed: int = 0


class RequestReconciler:
    """Periodically refreshes non-terminal requests from their providers."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        machine_sync_service: MachineSyncService,
        status_service: RequestStatusService,
        config_port: ConfigurationPort,
        circuit_breaker_factory: Callable[[str], CircuitBreakerPort],
        logger: LoggingPort,
    ) -> None:
        self._uow_factory = uow_factory
        self._machine_sync_service = machine_sync_service
        self._status_service = status_service
        self._config_port = config_port
        self._circuit_breaker_factory = circuit_breaker_factory
        self._logger = logger
        self._query_service = RequestQueryService(uow_factory, logger)
        self._task: Optional["asyncio.Task[None]"] = None
        self._stop_event = asyncio.Event()

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> bool:
        """Start the loop on the running event loop. Returns False when disabled or running."""
        settings = RequestSyncSettings.from_config(self._config_port)
        if not settings.enabled:
            self._logger.info("Background request reconciliation is disabled by configuration")
            return False
        if self.running:
            return False
        global _loops_running
        self._stop_event = asyncio.Event()
        self._task = asyncio.get_running_loop().create_task(
            self._run(), name="orb-request-reconciler"
        )
        _loops_running += 1
        self._logger.info(
            "Background request reconciliation started (interval %.0fs, concurrency %d)",
            settings.interval_seconds,
            settings.max_concurrency,
        )
        return True

    async def stop(self, grace_seconds: float = 5.0) -> None:
        """Stop the loop, letting provider calls already in flight finish.

        No new request is started once stop is requested. Calls already running
        get up to ``grace_seconds`` to finish (a provider call in a worker
        thread cannot be interrupted, and its result is persisted by the same
        task); after that the pass is cancelled.
        """
        global _loops_running
        task, self._task = self._task, None
        if task is None:
            return
        _loops_running = max(0, _loops_running - 1)
        self._stop_event.set()
        _, pending = await asyncio.wait({task}, timeout=grace_seconds)
        if pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._logger.info("Background request reconciliation stopped")

    async def _run(self) -> None:
        while True:
            settings = RequestSyncSettings.from_config(self._config_port)
            if settings.enabled:
                try:
                    await self.reconcile_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._logger.warning("Request reconcile pass failed: %s", exc)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=settings.interval_seconds)
            if self._stop_event.is_set():
                return

    # -- one pass ----------------------------------------------------------

    def _in_flight_requests(self) -> list[Request]:
        requests: list[Request] = []
        seen: set[str] = set()
        with self._uow_factory.create_unit_of_work() as uow:
            for status in RequestStatus:
                if status.is_terminal():
                    continue
                for request in uow.requests.find_by_status(status):
                    key = str(request.request_id)
                    if key not in seen:
                        seen.add(key)
                        requests.append(request)
        return requests

    def _circuit_open(self, provider_name: str) -> bool:
        key = f"provider:{provider_name}"
        try:
            breaker = self._circuit_breaker_factory(key)
            return bool(breaker.has_state(key) and breaker.is_open())
        except Exception as exc:
            self._logger.debug("Circuit state unavailable for %s: %s", provider_name, exc)
            return False

    async def reconcile_once(self) -> ReconcileSummary:
        """Refresh every in-flight request once, within the configured bounds."""
        settings = RequestSyncSettings.from_config(self._config_port)
        summary = ReconcileSummary()
        semaphore = asyncio.Semaphore(settings.max_concurrency)
        open_cache: dict[str, bool] = {}
        # Per provider: did any fetch this pass get an answer, did any fail.
        outcomes: dict[str, list[bool]] = {}

        async def _one(request: Request) -> None:
            if request.awaiting_resources:
                # The create flow still owns it and there is nothing to poll.
                summary.skipped_provisioning += 1
                return
            provider = request.provider_name or ""
            if provider not in open_cache:
                open_cache[provider] = self._circuit_open(provider)
            if open_cache[provider]:
                summary.skipped_open_circuit += 1
                return
            if is_recently_synced(request, settings.interval_seconds):
                summary.skipped_recent += 1
                return
            async with semaphore:
                if self._stop_event.is_set():
                    return
                try:
                    await asyncio.wait_for(
                        self.reconcile_request(request, outcomes),
                        timeout=settings.request_timeout_seconds,
                    )
                    summary.reconciled += 1
                except asyncio.TimeoutError:
                    summary.timed_out += 1
                    self._logger.warning(
                        "Reconcile of request %s timed out after %.0fs",
                        request.request_id,
                        settings.request_timeout_seconds,
                    )
                except Exception as exc:
                    summary.failed += 1
                    self._logger.warning(
                        "Reconcile of request %s failed: %s", request.request_id, exc
                    )

        requests = await asyncio.to_thread(self._in_flight_requests)
        await asyncio.gather(*(_one(r) for r in requests))
        # One outcome per provider per pass: a request whose resources are gone
        # fails on its own, so failures only count against the provider when it
        # answered none of its requests. Several broken requests then cannot
        # open the circuit and starve the healthy requests of the same provider.
        for provider, answered in outcomes.items():
            self._record_provider_outcome(provider, any(answered))
        if summary.failed or summary.timed_out:
            self._logger.info("Request reconcile pass finished: %s", summary)
        else:
            self._logger.debug("Request reconcile pass finished: %s", summary)
        return summary

    def _record_provider_outcome(self, provider_name: str, ok: bool) -> None:
        """Feed a reconcile provider call into the provider's circuit breaker."""
        try:
            breaker = self._circuit_breaker_factory(f"provider:{provider_name}")
            if ok:
                breaker.record_success()
            else:
                breaker.record_failure(time.time())
        except Exception as exc:
            self._logger.debug("Could not record circuit outcome for %s: %s", provider_name, exc)

    async def reconcile_request(
        self, request: Request, outcomes: Optional[dict[str, list[bool]]] = None
    ) -> None:
        """Refresh one request's machines and status from its provider.

        Whether the provider answered is appended to ``outcomes`` under the
        provider name when given (the pass records it once per provider), and
        fed straight into the provider's circuit breaker otherwise.
        """
        await self._machine_sync_service.populate_missing_machine_ids(request)
        refreshed: Any = await self._query_service.get_request(str(request.request_id))
        if refreshed is not None:
            request = refreshed
        if request.status.is_terminal() or request.awaiting_resources:
            return
        db_machines = await self._query_service.get_machines_for_request(request)
        (
            provider_machines,
            provider_metadata,
        ) = await self._machine_sync_service.fetch_provider_machines(request, db_machines)
        fetched = not isinstance(provider_machines, FailedProviderFetch)
        provider = request.provider_name or ""
        if outcomes is None:
            self._record_provider_outcome(provider, fetched)
        else:
            outcomes.setdefault(provider, []).append(fetched)
        if not fetched:
            raise ProviderFetchFailed(f"provider fetch failed for request {request.request_id}")
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
