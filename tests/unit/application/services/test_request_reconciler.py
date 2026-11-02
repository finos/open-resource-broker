"""Background request reconciliation: bounds, circuit breaker, config, shutdown."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.services import request_reconciler as reconciler_module
from orb.application.services.request_reconciler import (
    RequestReconciler,
    RequestSyncSettings,
    is_recently_synced,
)
from orb.domain.request.aggregate import Request
from orb.domain.request.request_identifiers import RequestId
from orb.domain.request.request_types import RequestStatus
from orb.domain.request.value_objects import RequestType


def _request(n: int, *, provider: str = "p1", last_check: datetime | None = None) -> Request:
    return Request(
        request_id=RequestId(value=f"req-00000000-0000-0000-0000-{n:012d}"),
        request_type=RequestType.ACQUIRE,
        provider_type="aws",
        provider_name=provider,
        template_id="tmpl",
        requested_count=1,
        status=RequestStatus.IN_PROGRESS,
        last_status_check=last_check,
    )


def _uow_factory(requests: list[Request]) -> MagicMock:
    uow = MagicMock()
    uow.requests.find_by_status.side_effect = lambda status: (
        list(requests) if status == RequestStatus.IN_PROGRESS else []
    )

    @contextmanager
    def _create():
        yield uow

    factory = MagicMock()
    factory.create_unit_of_work.side_effect = _create
    return factory


def _config(**overrides: Any) -> MagicMock:
    values = {
        "reconcile_enabled": True,
        "reconcile_interval_seconds": 60,
        "reconcile_max_concurrency": 4,
        "reconcile_request_timeout_seconds": 30,
        "list_cache_max_age_seconds": 30,
        **overrides,
    }
    config = MagicMock()
    config.get_request_config.return_value = values
    return config


def _breaker_factory(open_for: set[str] | None = None) -> Any:
    open_for = open_for or set()

    def factory(key: str) -> MagicMock:
        breaker = MagicMock()
        breaker.has_state.return_value = True
        breaker.is_open.return_value = key.removeprefix("provider:") in open_for
        return breaker

    return factory


class _Harness:
    def __init__(self, requests: list[Request], *, config: MagicMock | None = None, open_for=None):
        self.synced: list[str] = []
        self.active = 0
        self.max_active = 0
        self.delay = 0.0
        self.fail_for: set[str] = set()

        sync = MagicMock()
        sync.populate_missing_machine_ids = AsyncMock()
        sync.fetch_provider_machines = AsyncMock(side_effect=self._fetch)
        sync.sync_machines_with_provider = AsyncMock(return_value=([], []))
        status = MagicMock()
        status.determine_status_from_machines.return_value = (None, None)
        status.update_request_status = AsyncMock()
        self.reconciler = RequestReconciler(
            uow_factory=_uow_factory(requests),
            machine_sync_service=sync,
            status_service=status,
            config_port=config or _config(),
            circuit_breaker_factory=_breaker_factory(open_for),
            logger=MagicMock(),
        )
        self.reconciler._query_service = MagicMock()
        self.reconciler._query_service.get_request = AsyncMock(
            side_effect=lambda rid: next(r for r in requests if str(r.request_id) == rid)
        )
        self.reconciler._query_service.get_machines_for_request = AsyncMock(return_value=[])

    async def _fetch(self, request: Request, _machines: list[Any]) -> tuple[list[Any], dict]:
        key = str(request.request_id)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if key in self.fail_for:
                raise RuntimeError("provider call failed")
            self.synced.append(key)
            return [], {}
        finally:
            self.active -= 1


@pytest.fixture(autouse=True)
def _reset_running_counter():
    reconciler_module._loops_running = 0
    yield
    reconciler_module._loops_running = 0


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_settings_read_from_request_config() -> None:
    settings = RequestSyncSettings.from_config(
        _config(reconcile_enabled=False, reconcile_interval_seconds=15, reconcile_max_concurrency=2)
    )
    assert settings.enabled is False
    assert settings.interval_seconds == 15
    assert settings.max_concurrency == 2


@pytest.mark.unit
def test_settings_fall_back_to_defaults_when_config_is_unreadable() -> None:
    config = MagicMock()
    config.get_request_config.side_effect = RuntimeError("no config")
    assert RequestSyncSettings.from_config(config) == RequestSyncSettings()
    assert RequestSyncSettings.from_config(None) == RequestSyncSettings()


@pytest.mark.unit
def test_reconciliation_is_opt_in() -> None:
    assert RequestSyncSettings().enabled is False
    config = MagicMock()
    config.get_request_config.return_value = {}
    assert RequestSyncSettings.from_config(config).enabled is False


@pytest.mark.unit
def test_recently_synced_needs_a_fresh_status_check() -> None:
    now = datetime.now(timezone.utc)
    assert is_recently_synced(_request(1, last_check=now - timedelta(seconds=5)), 30, now)
    assert not is_recently_synced(_request(1, last_check=now - timedelta(seconds=60)), 30, now)
    assert not is_recently_synced(_request(1, last_check=None), 30, now)
    assert not is_recently_synced(_request(1, last_check=now), 0, now)


# ---------------------------------------------------------------------------
# One pass
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pass_refreshes_every_in_flight_request() -> None:
    requests = [_request(i) for i in range(1, 4)]
    harness = _Harness(requests)
    summary = await harness.reconciler.reconcile_once()
    assert summary.reconciled == 3
    assert sorted(harness.synced) == sorted(str(r.request_id) for r in requests)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_concurrency_is_bounded() -> None:
    harness = _Harness(
        [_request(i) for i in range(1, 9)], config=_config(reconcile_max_concurrency=2)
    )
    harness.delay = 0.02
    summary = await harness.reconciler.reconcile_once()
    assert summary.reconciled == 8
    assert harness.max_active == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_open_circuit_provider_is_skipped_others_proceed() -> None:
    blocked = _request(1, provider="down")
    healthy = _request(2, provider="up")
    harness = _Harness([blocked, healthy], open_for={"down"})
    summary = await harness.reconciler.reconcile_once()
    assert summary.skipped_open_circuit == 1
    assert harness.synced == [str(healthy.request_id)]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recently_synced_requests_are_not_refreshed_again() -> None:
    fresh = _request(1, last_check=datetime.now(timezone.utc))
    stale = _request(2, last_check=datetime.now(timezone.utc) - timedelta(minutes=10))
    harness = _Harness([fresh, stale])
    summary = await harness.reconciler.reconcile_once()
    assert summary.skipped_recent == 1
    assert harness.synced == [str(stale.request_id)]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_one_failing_request_does_not_stop_the_pass() -> None:
    first, second = _request(1), _request(2)
    harness = _Harness([first, second])
    harness.fail_for = {str(first.request_id)}
    summary = await harness.reconciler.reconcile_once()
    assert summary.failed == 1
    assert summary.reconciled == 1
    assert harness.synced == [str(second.request_id)]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_slow_request_is_cut_off_by_the_per_request_timeout() -> None:
    harness = _Harness([_request(1)], config=_config(reconcile_request_timeout_seconds=0.05))
    harness.delay = 5
    summary = await harness.reconciler.reconcile_once()
    assert summary.timed_out == 1


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_disabled_by_configuration_does_not_start() -> None:
    harness = _Harness([_request(1)], config=_config(reconcile_enabled=False))
    assert harness.reconciler.start() is False
    assert harness.reconciler.running is False
    await asyncio.sleep(0.02)
    assert harness.synced == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_loop_runs_passes_and_shuts_down_cleanly() -> None:
    harness = _Harness([_request(1)], config=_config(reconcile_interval_seconds=0.01))
    assert harness.reconciler.start() is True
    assert harness.reconciler.start() is False  # already running
    for _ in range(100):
        if harness.synced:
            break
        await asyncio.sleep(0.01)
    assert harness.synced
    task = harness.reconciler._task
    await harness.reconciler.stop()
    assert task is not None and task.done()
    assert harness.reconciler.running is False
    await harness.reconciler.stop()  # idempotent


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stop_cancels_an_in_flight_pass() -> None:
    harness = _Harness([_request(1)])
    harness.delay = 10
    harness.reconciler.start()
    for _ in range(100):
        if harness.active:
            break
        await asyncio.sleep(0.01)
    assert harness.active == 1
    await asyncio.wait_for(harness.reconciler.stop(grace_seconds=0.05), timeout=2)
    assert harness.active == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stop_lets_an_in_flight_call_finish_within_the_grace_period() -> None:
    harness = _Harness([_request(1), _request(2)], config=_config(reconcile_max_concurrency=1))
    harness.delay = 0.1
    harness.reconciler.start()
    for _ in range(100):
        if harness.active:
            break
        await asyncio.sleep(0.01)
    await harness.reconciler.stop(grace_seconds=2)
    # The call already running completed and was recorded; the queued one never started.
    assert len(harness.synced) == 1
    assert harness.active == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_provider_failures_feed_the_circuit_breaker() -> None:
    from orb.application.services.machine_sync_service import FailedProviderFetch

    request = _request(1)
    harness = _Harness([request])
    breaker = MagicMock()
    breaker.has_state.return_value = True
    breaker.is_open.return_value = False
    harness.reconciler._circuit_breaker_factory = lambda key: breaker
    sync: Any = harness.reconciler._machine_sync_service
    sync.fetch_provider_machines = AsyncMock(return_value=(FailedProviderFetch(), {}))

    summary = await harness.reconciler.reconcile_once()

    assert summary.failed == 1
    breaker.record_failure.assert_called_once()
    breaker.record_success.assert_not_called()
    sync.sync_machines_with_provider.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_provider_success_is_recorded_on_the_circuit_breaker() -> None:
    harness = _Harness([_request(1)])
    breaker = MagicMock()
    breaker.has_state.return_value = True
    breaker.is_open.return_value = False
    harness.reconciler._circuit_breaker_factory = lambda key: breaker

    await harness.reconciler.reconcile_once()

    breaker.record_success.assert_called_once()
    breaker.record_failure.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_failing_pass_does_not_end_the_loop() -> None:
    harness = _Harness([_request(1)], config=_config(reconcile_interval_seconds=0.01))
    calls = 0
    real = harness.reconciler.reconcile_once

    async def flaky():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("storage unavailable")
        return await real()

    harness.reconciler.reconcile_once = flaky  # type: ignore[method-assign]
    try:
        harness.reconciler.start()
        for _ in range(100):
            if calls >= 2:
                break
            await asyncio.sleep(0.01)
        assert calls >= 2
        assert harness.reconciler.running
    finally:
        await harness.reconciler.stop()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_staleness_window_applies_only_while_a_loop_runs() -> None:
    settings = RequestSyncSettings(enabled=True, list_cache_max_age_seconds=30)
    assert settings.list_staleness_window == 0
    harness = _Harness([_request(1)])
    harness.reconciler.start()
    try:
        assert settings.list_staleness_window == 30
        assert RequestSyncSettings(enabled=False).list_staleness_window == 0
    finally:
        await harness.reconciler.stop()
    assert settings.list_staleness_window == 0


def _fetch_fails_for(harness: _Harness, failing: list[Request]) -> None:
    """Make the provider fetch fail, as the sync service reports it, for ``failing``."""
    from orb.application.services.machine_sync_service import FailedProviderFetch

    keys = {str(r.request_id) for r in failing}

    async def fetch(request: Request, _machines: list[Any]) -> tuple[list[Any], dict]:
        if str(request.request_id) in keys:
            return FailedProviderFetch(), {}
        harness.synced.append(str(request.request_id))
        return [], {}

    harness.reconciler._machine_sync_service.fetch_provider_machines = AsyncMock(side_effect=fetch)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_broken_requests_do_not_open_the_circuit_of_a_provider_that_answers() -> None:
    from orb.infrastructure.resilience.strategy.circuit_breaker import CircuitBreakerStrategy

    provider = "reconcile-mixed-health"
    key = f"provider:{provider}"
    broken = [_request(n, provider=provider) for n in range(1, 7)]
    healthy = _request(7, provider=provider)
    harness = _Harness([*broken, healthy])
    _fetch_fails_for(harness, broken)
    harness.reconciler._circuit_breaker_factory = CircuitBreakerStrategy
    CircuitBreakerStrategy._circuit_states.pop(key, None)
    try:
        for _ in range(3):
            harness.synced.clear()
            summary = await harness.reconciler.reconcile_once()
            assert summary.skipped_open_circuit == 0
            assert harness.synced == [str(healthy.request_id)]
        assert not CircuitBreakerStrategy(key).is_open()
    finally:
        CircuitBreakerStrategy._circuit_states.pop(key, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_provider_that_answers_nothing_counts_one_failure_per_pass() -> None:
    requests = [_request(n) for n in range(1, 4)]
    harness = _Harness(requests)
    _fetch_fails_for(harness, requests)
    breaker = MagicMock()
    breaker.has_state.return_value = True
    breaker.is_open.return_value = False
    harness.reconciler._circuit_breaker_factory = lambda key: breaker

    await harness.reconciler.reconcile_once()

    breaker.record_failure.assert_called_once()
    breaker.record_success.assert_not_called()
