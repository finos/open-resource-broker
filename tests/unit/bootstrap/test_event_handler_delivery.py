"""Every @event_handler class is registered on the shared bus and runs for its event."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from orb.application.events.bus.event_bus import EventBus
from orb.application.events.decorators import EventHandlerRegistry
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
class TestHandlerDelivery:
    def test_every_decorated_handler_runs_when_its_event_is_published(self, container):
        bus = container.get(EventBus)
        ran: dict[str, int] = {}

        def spy_for(event_type):
            async def spy(event):
                ran[event_type] = ran.get(event_type, 0) + 1

            return spy

        registered = EventHandlerRegistry.get_handlers()
        assert registered
        for event_type in registered:
            handlers = bus.get_handlers_for_event(event_type)
            assert handlers, (
                f"{event_type} has a handler class but nothing is registered on the bus"
            )
            for handler in handlers:
                handler.handle = spy_for(event_type)  # type: ignore[method-assign]

        async def run():
            publisher = container.get(EventPublisherPort)
            for event_type in registered:
                publisher.publish(_event(event_type))
            await publisher.drain()  # type: ignore[attr-defined]

        asyncio.run(run())
        assert set(ran) >= set(registered)

    def test_missing_sse_module_is_reported_and_other_handlers_still_register(self, monkeypatch):
        import sys

        monkeypatch.setitem(sys.modules, "orb.api.routers.events", None)
        logger = MagicMock()
        c = DIContainer()
        c.register_instance(LoggingPort, logger)
        register_core_services(c)

        bus = c.get(EventBus)

        assert bus.get_handlers_for_event("RequestCreatedEvent")
        assert not any(
            type(h).__name__ == "_SseEventHandler"
            for h in bus.get_handlers_for_event("RequestStatusChangedEvent")
        )
        assert any(
            "SseEventHandler not registered" in str(c_) for c_ in logger.warning.call_args_list
        )
