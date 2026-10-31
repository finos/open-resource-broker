"""The shared event publisher forwards domain events to the event bus."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock

import pytest

from orb.application.events.bus.event_bus import EventBus
from orb.domain.base.events import DomainEvent
from orb.infrastructure.events.publisher import ConfigurableEventPublisher


def _event(name: str = "RequestCreatedEvent", aggregate_id: str = "r-1") -> DomainEvent:
    return DomainEvent(aggregate_id=aggregate_id, aggregate_type="Request", event_type=name)


class _RecordingBus:
    """Bus double: records delivery order; earlier events are slower to expose reordering."""

    def __init__(self, delays: dict[str, float] | None = None) -> None:
        self.delivered: list[str] = []
        self.delays = delays or {}
        self.dropped = 0

    async def publish(self, event: DomainEvent) -> None:
        await asyncio.sleep(self.delays.get(event.aggregate_id, 0))
        self.delivered.append(event.aggregate_id)

    def record_dropped_event(self) -> None:
        self.dropped += 1


@pytest.mark.unit
class TestForwarding:
    def test_event_reaches_bus_handlers(self):
        async def run():
            bus = EventBus()
            seen: list[str] = []

            class _Handler:
                async def handle(self, event):
                    seen.append(event.event_type)

            bus.register_handler("RequestCreatedEvent", _Handler())  # type: ignore[arg-type]
            pub = ConfigurableEventPublisher(mode="logging", event_bus=bus)
            pub.publish(_event())
            await pub.drain()
            assert seen == ["RequestCreatedEvent"]

        asyncio.run(run())

    def test_audit_log_still_written_and_without_bus_nothing_forwards(self):
        pub = ConfigurableEventPublisher(mode="logging")
        pub._logger = MagicMock()
        pub.publish(_event())
        pub._logger.info.assert_called_once()

    def test_delivery_preserves_publish_order(self):
        async def run():
            bus = _RecordingBus({"a": 0.05, "b": 0.01, "c": 0.0})
            pub = ConfigurableEventPublisher(mode="logging", event_bus=bus)
            for name in ("a", "b", "c"):
                pub.publish(_event(aggregate_id=name))
            await pub.drain()
            assert bus.delivered == ["a", "b", "c"]

        asyncio.run(run())

    def test_bus_failure_never_raises_and_chain_continues(self):
        class _Failing(_RecordingBus):
            async def publish(self, event):
                if event.aggregate_id == "boom":
                    raise RuntimeError("handler exploded")
                await super().publish(event)

        async def run():
            bus = _Failing()
            pub = ConfigurableEventPublisher(mode="logging", event_bus=bus)
            pub.publish(_event(aggregate_id="boom"))
            pub.publish(_event(aggregate_id="ok"))
            await pub.drain()
            assert bus.delivered == ["ok"]

        asyncio.run(run())

    def test_backlog_cap_drops_newest_and_counts(self):
        async def run():
            bus = _RecordingBus({"1": 0.05})
            pub = ConfigurableEventPublisher(mode="logging", event_bus=bus, max_pending=2)
            for name in ("1", "2", "3", "4"):
                pub.publish(_event(aggregate_id=name))
            await pub.drain()
            assert bus.delivered == ["1", "2"]
            assert pub.dropped_events == 2
            assert bus.dropped == 2

        asyncio.run(run())

    def test_handler_publishing_through_the_port_does_not_deadlock(self):
        async def run():
            delivered: list[str] = []

            class _ReentrantBus:
                pub: ConfigurableEventPublisher

                async def publish(self, event):
                    delivered.append(event.aggregate_id)
                    if event.aggregate_id == "outer":
                        self.pub.publish(_event(aggregate_id="inner"))

            bus = _ReentrantBus()
            pub = ConfigurableEventPublisher(mode="logging", event_bus=bus)
            bus.pub = pub
            pub.publish(_event(aggregate_id="outer"))
            await asyncio.wait_for(pub.drain(), timeout=2)
            await asyncio.sleep(0)
            await pub.drain()
            assert delivered == ["outer", "inner"]

        asyncio.run(run())

    def test_drain_waits_for_outstanding_deliveries(self):
        async def run():
            bus = _RecordingBus({"slow": 0.05})
            pub = ConfigurableEventPublisher(mode="logging", event_bus=bus)
            pub.publish(_event(aggregate_id="slow"))
            assert bus.delivered == []
            await pub.drain()
            assert bus.delivered == ["slow"]

        asyncio.run(run())


@pytest.mark.unit
class TestNoRunningLoop:
    def test_without_any_loop_events_are_delivered_inline(self):
        bus = _RecordingBus()
        pub = ConfigurableEventPublisher(mode="logging", event_bus=bus)
        pub.publish(_event(aggregate_id="cli"))
        assert bus.delivered == ["cli"]

    def test_from_a_worker_thread_delivery_runs_on_the_home_loop(self):
        async def run():
            loops: list[asyncio.AbstractEventLoop] = []

            class _Bus(_RecordingBus):
                async def publish(self, event):
                    loops.append(asyncio.get_running_loop())
                    await super().publish(event)

            bus = _Bus()
            pub = ConfigurableEventPublisher(mode="logging", event_bus=bus)
            pub.bind_loop()
            home = asyncio.get_running_loop()

            t = threading.Thread(target=lambda: pub.publish(_event(aggregate_id="thread")))
            t.start()
            await asyncio.get_running_loop().run_in_executor(None, t.join)
            await asyncio.sleep(0.05)
            await pub.drain()
            assert bus.delivered == ["thread"]
            assert loops == [home]

        asyncio.run(run())

    def test_drain_right_after_a_thread_publish_waits_for_the_handoff(self):
        async def run():
            bus = _RecordingBus({"late": 0.02})
            pub = ConfigurableEventPublisher(mode="logging", event_bus=bus)
            pub.bind_loop()

            started = threading.Event()

            def publish_then_signal():
                pub.publish(_event(aggregate_id="late"))
                started.set()

            t = threading.Thread(target=publish_then_signal)
            t.start()
            # Block the loop thread until the worker has published, so the
            # handoff is queued but not yet run when drain starts.
            started.wait(2)
            await pub.drain()
            t.join()
            assert bus.delivered == ["late"]

        asyncio.run(run())

    def test_publish_from_a_worker_thread_with_its_own_loop_goes_to_the_home_loop(self):
        async def run():
            loops: list[asyncio.AbstractEventLoop] = []

            class _Bus(_RecordingBus):
                async def publish(self, event):
                    loops.append(asyncio.get_running_loop())
                    await super().publish(event)

            bus = _Bus()
            pub = ConfigurableEventPublisher(mode="logging", event_bus=bus)
            pub.bind_loop()
            home = asyncio.get_running_loop()

            async def worker_main():
                pub.publish(_event(aggregate_id="from-worker-loop"))

            t = threading.Thread(target=lambda: asyncio.run(worker_main()))
            t.start()
            await home.run_in_executor(None, t.join)
            await pub.drain()
            assert bus.delivered == ["from-worker-loop"]
            assert loops == [home]

        asyncio.run(run())
