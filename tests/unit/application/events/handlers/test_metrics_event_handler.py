"""Unit tests for MetricsEventHandler."""

from unittest.mock import MagicMock

import pytest

from orb.application.events.handlers.metrics_event_handler import MetricsEventHandler
from orb.domain.base.events.domain_events import (
    RequestCompletedEvent,
    RequestCreatedEvent,
    RequestFailedEvent,
)


def _make_collector() -> MagicMock:
    collector = MagicMock()
    # metrics dict used for gauge reads
    collector.metrics = {}
    return collector


def _gauge(value: float) -> MagicMock:
    g = MagicMock()
    g.value = value
    return g


# ---------------------------------------------------------------------------
# RequestCompletedEvent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completed_increments_requests_total():
    collector = _make_collector()
    handler = MetricsEventHandler(collector=collector)

    event = RequestCompletedEvent(
        aggregate_id="req-1",
        aggregate_type="Request",
        request_id="req-1",
        request_type="provision",
        completion_status="success",
        machine_ids=["m1", "m2", "m3"],
    )
    await handler.handle(event)

    collector.increment_counter.assert_any_call("requests_total")


@pytest.mark.asyncio
async def test_completed_sets_active_instances_to_machine_count():
    collector = _make_collector()
    handler = MetricsEventHandler(collector=collector)

    event = RequestCompletedEvent(
        aggregate_id="req-1",
        aggregate_type="Request",
        request_id="req-1",
        request_type="provision",
        completion_status="success",
        machine_ids=["m1", "m2", "m3"],
    )
    await handler.handle(event)

    collector.set_gauge.assert_any_call("active_instances", 3.0)


@pytest.mark.asyncio
async def test_completed_records_provisioning_duration_when_created_seen():
    collector = _make_collector()
    handler = MetricsEventHandler(collector=collector)

    created = RequestCreatedEvent(
        aggregate_id="req-2",
        aggregate_type="Request",
        request_id="req-2",
        request_type="provision",
        template_id="tmpl-1",
        machine_count=1,
    )
    await handler.handle(created)

    completed = RequestCompletedEvent(
        aggregate_id="req-2",
        aggregate_type="Request",
        request_id="req-2",
        request_type="provision",
        completion_status="success",
        machine_ids=["m1"],
    )
    await handler.handle(completed)

    # record_time should have been called with provisioning_duration and a positive float
    calls = collector.record_time.call_args_list
    assert any(c.args[0] == "provisioning_duration" and c.args[1] >= 0 for c in calls)


# ---------------------------------------------------------------------------
# RequestCreatedEvent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_created_increments_pending_requests():
    collector = _make_collector()
    handler = MetricsEventHandler(collector=collector)

    event = RequestCreatedEvent(
        aggregate_id="req-3",
        aggregate_type="Request",
        request_id="req-3",
        request_type="provision",
        template_id="tmpl-1",
        machine_count=2,
    )
    await handler.handle(event)

    collector.increment_gauge.assert_any_call("pending_requests")


# ---------------------------------------------------------------------------
# RequestFailedEvent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_increments_requests_failed_total():
    collector = _make_collector()
    handler = MetricsEventHandler(collector=collector)

    event = RequestFailedEvent(
        aggregate_id="req-4",
        aggregate_type="Request",
        request_id="req-4",
        request_type="provision",
        error_message="timeout",
        failure_reason="timeout",
    )
    await handler.handle(event)

    collector.increment_counter.assert_any_call("requests_failed_total")


# ---------------------------------------------------------------------------
# Unknown event type — should be a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_event_type_is_noop():
    from orb.domain.base.events.base_events import DomainEvent

    collector = _make_collector()
    handler = MetricsEventHandler(collector=collector)

    event = DomainEvent(
        aggregate_id="x",
        aggregate_type="Unknown",
        event_type="SomeOtherEvent",
    )
    # Should not raise
    await handler.handle(event)

    collector.increment_counter.assert_not_called()
    collector.set_gauge.assert_not_called()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completed_with_empty_machine_ids_sets_active_instances_to_zero():
    collector = _make_collector()
    handler = MetricsEventHandler(collector=collector)

    event = RequestCompletedEvent(
        aggregate_id="req-5",
        aggregate_type="Request",
        request_id="req-5",
        request_type="provision",
        completion_status="success",
        machine_ids=[],
    )
    await handler.handle(event)

    collector.set_gauge.assert_any_call("active_instances", 0.0)


@pytest.mark.asyncio
async def test_completed_without_prior_created_does_not_call_record_time():
    collector = _make_collector()
    handler = MetricsEventHandler(collector=collector)

    event = RequestCompletedEvent(
        aggregate_id="req-6",
        aggregate_type="Request",
        request_id="req-6",
        request_type="provision",
        completion_status="success",
        machine_ids=["m1"],
    )
    await handler.handle(event)

    collector.record_time.assert_not_called()


@pytest.mark.asyncio
async def test_failed_decrements_pending_requests_when_nonzero():
    collector = _make_collector()
    handler = MetricsEventHandler(collector=collector)

    event = RequestFailedEvent(
        aggregate_id="req-7",
        aggregate_type="Request",
        request_id="req-7",
        request_type="provision",
        error_message="timeout",
        failure_reason="timeout",
    )
    await handler.handle(event)

    collector.decrement_gauge.assert_any_call("pending_requests")


@pytest.mark.asyncio
async def test_failed_does_not_decrement_pending_requests_when_zero():
    collector = _make_collector()
    handler = MetricsEventHandler(collector=collector)

    event = RequestFailedEvent(
        aggregate_id="req-8",
        aggregate_type="Request",
        request_id="req-8",
        request_type="provision",
        error_message="timeout",
        failure_reason="timeout",
    )
    await handler.handle(event)

    collector.decrement_gauge.assert_any_call("pending_requests")


@pytest.mark.asyncio
async def test_failed_does_not_decrement_pending_requests_when_absent():
    collector = _make_collector()
    handler = MetricsEventHandler(collector=collector)

    event = RequestFailedEvent(
        aggregate_id="req-9",
        aggregate_type="Request",
        request_id="req-9",
        request_type="provision",
        error_message="timeout",
        failure_reason="timeout",
    )
    await handler.handle(event)

    collector.decrement_gauge.assert_any_call("pending_requests")
