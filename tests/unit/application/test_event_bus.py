"""Unit tests for application/events/bus/event_bus.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.application.events.base.event_handler import EventHandler
from orb.application.events.bus.event_bus import EventBus
from orb.domain.base.events import DomainEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(event_type: str = "test.event", event_id: str = "evt-001") -> DomainEvent:
    evt = MagicMock(spec=DomainEvent)
    evt.event_type = event_type
    evt.event_id = event_id
    return evt


class _OkHandler(EventHandler):
    def __init__(self, logger=None):
        super().__init__(logger)
        # Disable retries so tests are fast and deterministic
        self.retry_count = 1
        self.retry_delay = 0.0
        self.handled: list[Any] = []

    async def process_event(self, event: DomainEvent) -> None:
        self.handled.append(event)


class _ErrorHandler(EventHandler):
    def __init__(self, logger=None):
        super().__init__(logger)
        # Disable retries so tests don't sleep
        self.retry_count = 1
        self.retry_delay = 0.0

    async def process_event(self, event: DomainEvent) -> None:
        raise RuntimeError("handler failed")


# ---------------------------------------------------------------------------
# Tests: register_handler / get_handlers_for_event
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEventBusRegistration:
    def test_register_single_handler(self):
        bus = EventBus()
        h = _OkHandler()
        bus.register_handler("my.event", h)
        assert h in bus.get_handlers_for_event("my.event")

    def test_register_multiple_handlers_for_same_type(self):
        bus = EventBus()
        h1, h2 = _OkHandler(), _OkHandler()
        bus.register_handler("ev", h1)
        bus.register_handler("ev", h2)
        assert len(bus.get_handlers_for_event("ev")) == 2

    def test_get_handlers_returns_empty_for_unknown_type(self):
        bus = EventBus()
        assert bus.get_handlers_for_event("no.such.event") == []

    def test_get_registered_event_types(self):
        bus = EventBus()
        bus.register_handler("a", _OkHandler())
        bus.register_handler("b", _OkHandler())
        types = bus.get_registered_event_types()
        assert "a" in types
        assert "b" in types

    def test_register_handler_class_creates_instance(self):
        bus = EventBus()
        bus.register_handler_class("ev", _OkHandler)
        assert len(bus.get_handlers_for_event("ev")) == 1

    def test_register_handler_class_reuses_instance(self):
        bus = EventBus()
        bus.register_handler_class("ev1", _OkHandler)
        bus.register_handler_class("ev2", _OkHandler)
        # Both registrations share one instance
        assert len(bus._handler_instances) == 1

    def test_register_handler_class_logs_debug_when_logger_given(self):
        logger = MagicMock()
        bus = EventBus(logger=logger)
        bus.register_handler("ev", _OkHandler())
        logger.debug.assert_called()


# ---------------------------------------------------------------------------
# Tests: publish
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEventBusPublish:
    @pytest.mark.asyncio
    async def test_publish_calls_handler(self):
        bus = EventBus()
        h = _OkHandler()
        bus.register_handler("test.event", h)
        evt = _make_event("test.event")
        await bus.publish(evt)
        assert len(h.handled) == 1

    @pytest.mark.asyncio
    async def test_publish_calls_all_handlers(self):
        bus = EventBus()
        h1, h2 = _OkHandler(), _OkHandler()
        bus.register_handler("ev", h1)
        bus.register_handler("ev", h2)
        evt = _make_event("ev")
        await bus.publish(evt)
        assert len(h1.handled) == 1
        assert len(h2.handled) == 1

    @pytest.mark.asyncio
    async def test_publish_to_no_handlers_is_silent(self):
        logger = MagicMock()
        bus = EventBus(logger=logger)
        evt = _make_event("no.handler")
        await bus.publish(evt)  # must not raise
        logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_failing_handler_does_not_block_other_handlers(self):
        bus = EventBus()
        ok = _OkHandler()
        bad = _ErrorHandler()
        bus.register_handler("ev", bad)
        bus.register_handler("ev", ok)
        evt = _make_event("ev")
        await bus.publish(evt)  # must not raise
        assert len(ok.handled) == 1

    @pytest.mark.asyncio
    async def test_failing_handler_increments_events_failed(self):
        bus = EventBus()
        bus.register_handler("ev", _ErrorHandler())
        await bus.publish(_make_event("ev"))
        assert bus._events_failed == 1

    @pytest.mark.asyncio
    async def test_successful_publish_increments_events_processed(self):
        bus = EventBus()
        bus.register_handler("ev", _OkHandler())
        await bus.publish(_make_event("ev"))
        assert bus._events_processed == 1

    @pytest.mark.asyncio
    async def test_failing_handler_logs_error(self):
        logger = MagicMock()
        bus = EventBus(logger=logger)
        bus.register_handler("ev", _ErrorHandler())
        await bus.publish(_make_event("ev"))
        logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_publish_uses_class_name_as_event_type_fallback(self):
        """When event has no event_type attribute, class name is used."""
        bus = EventBus()
        h = _OkHandler()

        class _MyEvent(DomainEvent):
            pass

        # Register by class name
        bus.register_handler("_MyEvent", h)
        evt = _MyEvent(
            aggregate_id="agg-1",
            aggregate_type="TestAggregate",
        )
        await bus.publish(evt)
        assert len(h.handled) == 1


# ---------------------------------------------------------------------------
# Tests: get_statistics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEventBusStatistics:
    @pytest.mark.asyncio
    async def test_statistics_default_values(self):
        bus = EventBus()
        stats = bus.get_statistics()
        assert stats["events_processed"] == 0
        assert stats["events_failed"] == 0
        # With 0 processed, success_rate = 0/1 * 100 = 0.0 per the formula
        assert stats["success_rate"] == 0.0
        assert stats["registered_event_types"] == 0

    @pytest.mark.asyncio
    async def test_statistics_after_success(self):
        bus = EventBus()
        bus.register_handler("ev", _OkHandler())
        await bus.publish(_make_event("ev"))
        stats = bus.get_statistics()
        assert stats["events_processed"] == 1
        assert stats["events_failed"] == 0
        assert stats["success_rate"] == pytest.approx(100.0)
        assert stats["total_handlers"] == 1

    @pytest.mark.asyncio
    async def test_statistics_after_failure(self):
        bus = EventBus()
        bus.register_handler("ev", _ErrorHandler())
        await bus.publish(_make_event("ev"))
        stats = bus.get_statistics()
        assert stats["events_failed"] == 1
        assert stats["success_rate"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Tests: clear_handlers / clear_statistics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEventBusClear:
    def test_clear_handlers_removes_all(self):
        bus = EventBus()
        bus.register_handler("ev", _OkHandler())
        bus.clear_handlers()
        assert bus.get_handlers_for_event("ev") == []
        assert bus._handler_instances == {}

    @pytest.mark.asyncio
    async def test_clear_statistics_resets_counters(self):
        bus = EventBus()
        bus.register_handler("ev", _OkHandler())
        await bus.publish(_make_event("ev"))
        bus.clear_statistics()
        stats = bus.get_statistics()
        assert stats["events_processed"] == 0
        assert stats["average_processing_time"] == 0.0

    def test_clear_handlers_logs_debug(self):
        logger = MagicMock()
        bus = EventBus(logger=logger)
        bus.clear_handlers()
        logger.debug.assert_called()

    def test_clear_statistics_logs_debug(self):
        logger = MagicMock()
        bus = EventBus(logger=logger)
        bus.clear_statistics()
        logger.debug.assert_called()
