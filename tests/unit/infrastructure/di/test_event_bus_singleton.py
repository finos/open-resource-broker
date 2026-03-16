"""Tests verifying EventBus is registered as a singleton in the DI container."""

import asyncio

from orb.application.events.base.event_handler import EventHandler
from orb.application.events.bus.event_bus import EventBus
from orb.domain.base.events import DomainEvent
from orb.infrastructure.di.container import DIContainer


class _TestEvent(DomainEvent):
    aggregate_id: str = "test-agg"
    aggregate_type: str = "TestAggregate"


class _CapturingHandler(EventHandler):
    def __init__(self) -> None:
        super().__init__(logger=None)
        self.received: list[DomainEvent] = []

    async def process_event(self, event: DomainEvent) -> None:
        self.received.append(event)


def _make_container() -> DIContainer:
    """Minimal container with only EventBus registered as singleton."""
    container = DIContainer()
    container.register_singleton(EventBus, lambda _c: EventBus(logger=None))
    return container


class TestEventBusSingleton:
    def test_same_instance_returned_on_repeated_get(self) -> None:
        """Two calls to container.get(EventBus) must return the identical object."""
        container = _make_container()
        bus1 = container.get(EventBus)
        bus2 = container.get(EventBus)
        assert bus1 is bus2

    def test_subscribe_on_one_alias_publish_on_other_calls_handler(self) -> None:
        """Handler registered via bus1 must be invoked when event published via bus2."""
        container = _make_container()
        bus1 = container.get(EventBus)
        bus2 = container.get(EventBus)

        handler = _CapturingHandler()
        bus1.register_handler(_TestEvent.__name__, handler)

        event = _TestEvent()
        asyncio.run(bus2.publish(event))

        assert len(handler.received) == 1
        assert handler.received[0] is event
