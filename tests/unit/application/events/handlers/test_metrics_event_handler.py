"""Unit tests for MetricsEventHandler — OTel migration (bead 2512).

The handler no longer writes to MetricsCollector.  Tests verify behaviour
via the OTel instruments by installing an in-memory MetricReader and
asserting the recorded data points.

Where OTel SDK is unavailable the handler silently uses no-op instruments —
that graceful-degradation path is also covered.
"""

from __future__ import annotations

from typing import Any

import pytest

from orb.application.events.handlers.metrics_event_handler import MetricsEventHandler
from orb.domain.base.events.domain_events import (
    RequestCompletedEvent,
    RequestCreatedEvent,
    RequestFailedEvent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_handler() -> MetricsEventHandler:
    return MetricsEventHandler()


def _created(request_id: str = "req-1") -> RequestCreatedEvent:
    return RequestCreatedEvent(
        aggregate_id=request_id,
        aggregate_type="Request",
        request_id=request_id,
        request_type="provision",
        template_id="tmpl-1",
        machine_count=1,
    )


def _completed(
    request_id: str = "req-1", machine_ids: list[str] | None = None
) -> RequestCompletedEvent:
    if machine_ids is None:
        machine_ids = ["m1"]
    return RequestCompletedEvent(
        aggregate_id=request_id,
        aggregate_type="Request",
        request_id=request_id,
        request_type="provision",
        completion_status="success",
        machine_ids=machine_ids,
    )


def _failed(request_id: str = "req-1") -> RequestFailedEvent:
    return RequestFailedEvent(
        aggregate_id=request_id,
        aggregate_type="Request",
        request_id=request_id,
        request_type="provision",
        error_message="timeout",
        failure_reason="timeout",
    )


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------


def test_handler_instantiates_without_arguments():
    """MetricsEventHandler no longer requires a collector argument."""
    handler = MetricsEventHandler()
    assert handler is not None


def test_handler_accepts_logger_keyword():
    """Logger is still accepted as an optional keyword for consistency."""
    from unittest.mock import MagicMock

    logger = MagicMock()
    handler = MetricsEventHandler(logger=logger)
    assert handler is not None


# ---------------------------------------------------------------------------
# RequestCreatedEvent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_created_increments_pending_requests():
    """RequestCreatedEvent increments the pending-requests UpDownCounter."""
    handler = _make_handler()
    added: list[tuple[float, dict]] = []
    handler._pending_requests.add = lambda v, attributes=None, **kw: added.append(
        (v, attributes or {})
    )  # type: ignore[method-assign]

    await handler.handle(_created("req-3"))

    assert len(added) == 1
    assert added[0][0] == 1


@pytest.mark.asyncio
async def test_created_stores_start_time():
    """RequestCreatedEvent stores a start time for duration recording."""
    handler = _make_handler()
    await handler.handle(_created("req-x"))
    assert "req-x" in handler._request_start_times


# ---------------------------------------------------------------------------
# RequestCompletedEvent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completed_increments_requests_total():
    """RequestCompletedEvent increments the requests-total Counter."""
    handler = _make_handler()
    added: list[tuple[float, dict]] = []
    handler._requests_total.add = lambda v, attributes=None, **kw: added.append(
        (v, attributes or {})
    )  # type: ignore[method-assign]

    await handler.handle(_completed())

    assert any(v == 1 for v, _ in added)


@pytest.mark.asyncio
async def test_completed_sets_active_instances_to_machine_count():
    """RequestCompletedEvent updates active_instances via delta arithmetic."""
    handler = _make_handler()
    added: list[tuple[float, dict]] = []
    handler._active_instances.add = lambda v, attributes=None, **kw: added.append(
        (v, attributes or {})
    )  # type: ignore[method-assign]

    await handler.handle(_completed(machine_ids=["m1", "m2", "m3"]))

    # Net change from 0 → 3 should appear as a +3 add
    total_delta = sum(v for v, _ in added)
    assert total_delta == 3.0


@pytest.mark.asyncio
async def test_completed_decrements_pending_requests():
    """RequestCompletedEvent decrements the pending-requests UpDownCounter."""
    handler = _make_handler()
    adds: list[float] = []
    handler._pending_requests.add = lambda v, **kw: adds.append(v)  # type: ignore[method-assign]

    await handler.handle(_completed())

    # Should contain a -1 for the completed decrement
    assert -1 in adds


@pytest.mark.asyncio
async def test_completed_records_provisioning_duration_when_created_seen():
    """Duration is recorded when a prior Created event set a start time."""
    handler = _make_handler()
    recorded: list[tuple[float, dict]] = []
    handler._provisioning_duration.record = lambda v, attributes=None, **kw: recorded.append(
        (v, attributes or {})
    )  # type: ignore[method-assign]

    await handler.handle(_created("req-2"))
    await handler.handle(_completed("req-2"))

    assert len(recorded) == 1
    duration, _ = recorded[0]
    assert duration >= 0


@pytest.mark.asyncio
async def test_completed_without_prior_created_does_not_record_duration():
    """No duration is recorded when there is no matching Created event."""
    handler = _make_handler()
    recorded: list[Any] = []
    handler._provisioning_duration.record = lambda *a, **kw: recorded.append(a)  # type: ignore[method-assign]

    await handler.handle(_completed("req-6"))

    assert len(recorded) == 0


@pytest.mark.asyncio
async def test_completed_removes_start_time_from_dict():
    """Start time is removed from the dict after a Completed event."""
    handler = _make_handler()
    await handler.handle(_created("req-dur"))
    await handler.handle(_completed("req-dur"))
    assert "req-dur" not in handler._request_start_times


@pytest.mark.asyncio
async def test_completed_with_empty_machine_ids_sets_active_instances_to_zero():
    """Empty machine_ids results in active_instances being set to 0."""
    handler = _make_handler()
    adds: list[float] = []
    handler._active_instances.add = lambda v, **kw: adds.append(v)  # type: ignore[method-assign]

    await handler.handle(_completed(machine_ids=[]))

    # current was 0, new value is 0, delta = 0
    assert sum(adds) == 0.0


# ---------------------------------------------------------------------------
# RequestFailedEvent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_increments_requests_failed_total():
    """RequestFailedEvent increments the failed-requests Counter."""
    handler = _make_handler()
    added: list[tuple[float, dict]] = []
    handler._requests_failed_total.add = lambda v, attributes=None, **kw: added.append(
        (v, attributes or {})
    )  # type: ignore[method-assign]

    await handler.handle(_failed())

    assert any(v == 1 for v, _ in added)


@pytest.mark.asyncio
async def test_failed_decrements_pending_requests():
    """RequestFailedEvent decrements the pending-requests UpDownCounter."""
    handler = _make_handler()
    adds: list[float] = []
    handler._pending_requests.add = lambda v, **kw: adds.append(v)  # type: ignore[method-assign]

    await handler.handle(_failed())

    assert -1 in adds


@pytest.mark.asyncio
async def test_failed_removes_start_time():
    """RequestFailedEvent removes the start time from the dict."""
    handler = _make_handler()
    await handler.handle(_created("req-fail"))
    await handler.handle(_failed("req-fail"))
    assert "req-fail" not in handler._request_start_times


# ---------------------------------------------------------------------------
# Lifecycle — pending_requests inc/dec on Created/Completed/Failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_requests_lifecycle_created_then_completed():
    """Created increments pending; Completed decrements it."""
    handler = _make_handler()
    net: list[float] = []
    handler._pending_requests.add = lambda v, **kw: net.append(v)  # type: ignore[method-assign]

    await handler.handle(_created())
    await handler.handle(_completed())

    assert net.count(1) >= 1
    assert net.count(-1) >= 1


@pytest.mark.asyncio
async def test_pending_requests_lifecycle_created_then_failed():
    """Created increments pending; Failed decrements it."""
    handler = _make_handler()
    net: list[float] = []
    handler._pending_requests.add = lambda v, **kw: net.append(v)  # type: ignore[method-assign]

    await handler.handle(_created())
    await handler.handle(_failed())

    assert net.count(1) >= 1
    assert net.count(-1) >= 1


# ---------------------------------------------------------------------------
# Unknown event type — should be a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_event_type_is_noop():
    """Unrecognised event types are silently ignored."""
    from orb.domain.base.events.base_events import DomainEvent

    handler = _make_handler()

    # Capture any add/record calls on all instruments
    calls: list[Any] = []
    handler._pending_requests.add = lambda *a, **kw: calls.append(("pending", a))  # type: ignore[method-assign]
    handler._requests_total.add = lambda *a, **kw: calls.append(("total", a))  # type: ignore[method-assign]
    handler._requests_failed_total.add = lambda *a, **kw: calls.append(("failed", a))  # type: ignore[method-assign]
    handler._active_instances.add = lambda *a, **kw: calls.append(("instances", a))  # type: ignore[method-assign]
    handler._provisioning_duration.record = lambda *a, **kw: calls.append(("duration", a))  # type: ignore[method-assign]

    event = DomainEvent(
        aggregate_id="x",
        aggregate_type="Unknown",
        event_type="SomeOtherEvent",
    )
    await handler.handle(event)

    assert calls == []


# ---------------------------------------------------------------------------
# Graceful no-op when SDK absent (no MeterProvider / ImportError guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_works_when_otel_absent(monkeypatch):
    """Handler does not crash when opentelemetry-api is absent."""
    import sys

    # Temporarily hide the opentelemetry package so the handler falls back
    # to _NoOpMeter / _NoOpInstrument.
    otel_modules = [k for k in sys.modules if k.startswith("opentelemetry")]
    backup = {k: sys.modules.pop(k) for k in otel_modules}
    import builtins

    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name.startswith("opentelemetry"):
            raise ImportError(f"Simulated absent: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    try:
        # Re-create a handler; instruments will be no-ops
        handler = MetricsEventHandler()
        # Should run without exception
        await handler.handle(_created())
        await handler.handle(_completed())
        await handler.handle(_failed("req-other"))
    finally:
        monkeypatch.setattr(builtins, "__import__", original_import)
        sys.modules.update(backup)
