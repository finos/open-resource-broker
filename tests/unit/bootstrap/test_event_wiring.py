"""Domain events published through the DI-resolved port reach the live handlers."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from orb.application.events.bus.event_bus import EventBus
from orb.bootstrap.core_services import register_core_services
from orb.domain.base.events import DomainEvent
from orb.domain.base.ports import EventPublisherPort, LoggingPort
from orb.infrastructure.di.container import DIContainer


@pytest.fixture
def container() -> DIContainer:
    c = DIContainer()
    c.register_instance(LoggingPort, MagicMock())
    register_core_services(c)
    return c


def _event(name: str) -> DomainEvent:
    return DomainEvent(aggregate_id="agg-1", aggregate_type="Test", event_type=name)


@pytest.mark.unit
class TestPublisherWiring:
    def test_port_resolves_to_one_shared_publisher_bound_to_the_bus(self, container):
        publisher = container.get(EventPublisherPort)
        assert publisher is container.get(EventPublisherPort)
        assert publisher._event_bus is container.get(EventBus)  # type: ignore[attr-defined]

    def test_published_event_reaches_sse_subscribers(self, container):
        from orb.api.routers.events import sse_event_bus

        async def run():
            queue = await sse_event_bus.subscribe()
            try:
                publisher = container.get(EventPublisherPort)
                publisher.publish(_event("RequestStatusChangedEvent"))
                await publisher.drain()  # type: ignore[attr-defined]
                event_type, payload, _seq = await asyncio.wait_for(queue.get(), timeout=2)
                assert event_type == "RequestStatusChangedEvent"
                assert payload["aggregate_id"] == "agg-1"
            finally:
                await sse_event_bus.unsubscribe(queue)

        asyncio.run(run())

    def test_published_event_reaches_metrics_handler(self, container):
        bus = container.get(EventBus)
        metrics = [
            h
            for h in bus.get_handlers_for_event("RequestCreatedEvent")
            if type(h).__name__ == "MetricsEventHandler"
        ]
        assert metrics, "metrics handler must be registered for RequestCreatedEvent"
        calls: list[str] = []

        async def spy(event):
            calls.append(event.event_type)

        metrics[0].handle = spy  # type: ignore[method-assign]

        async def run():
            publisher = container.get(EventPublisherPort)
            publisher.publish(_event("RequestCreatedEvent"))
            await publisher.drain()  # type: ignore[attr-defined]

        asyncio.run(run())
        assert calls == ["RequestCreatedEvent"]
