"""Configurable Event Publisher - Simple, mode-based event publishing."""

import asyncio
import contextvars
import threading
import time
from typing import Any, Callable, Optional

from orb.domain.base.events import DomainEvent, EventPublisher
from orb.infrastructure.logging.logger import get_logger

_DEFAULT_MAX_PENDING = 1000

# Set while a bus delivery is running so a handler that publishes again does not
# wait on the very chain it belongs to.
_in_delivery: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "orb_event_delivery", default=False
)


class ConfigurableEventPublisher(EventPublisher):
    """
    Simple, configurable event publisher supporting all deployment modes.

    Modes:
    - "logging": Just log events for audit trail (Script mode)
    - "sync": Call registered handlers synchronously (REST API mode)
    - "async": Publish to message queues (EDA mode - future)

    When an ``event_bus`` is supplied every published event is also forwarded to
    it, after the audit log line, so handlers registered on the bus (live SSE
    push, metrics) see events raised by command handlers.

    Forwarding guarantees:
    - FIFO: events are delivered to the bus in publish order (each delivery waits
      for the previous one), so status transitions are never reordered.
    - Bounded: at most ``max_pending`` deliveries are outstanding; beyond that
      the newest event is dropped from bus delivery (it is still audit-logged)
      and counted.
    - Non-blocking and non-raising: publish never raises into the business
      operation. ``drain()`` waits for outstanding deliveries (call at shutdown).
    - Thread-aware: from a thread without a running loop the delivery is handed
      to the recorded home loop; only when no home loop exists (CLI/scripts, no
      live subscribers) is it run to completion inline.
    """

    def __init__(
        self,
        mode: str = "logging",
        event_bus: Optional[Any] = None,
        max_pending: int = _DEFAULT_MAX_PENDING,
    ) -> None:
        """Initialize with publishing mode and an optional event bus to forward to."""
        self.mode = mode
        self._handlers: dict[str, list[Callable]] = {}
        self._logger = get_logger(__name__)
        self._event_bus = event_bus
        self._max_pending = max_pending
        self._tail: Optional[asyncio.Task[None]] = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._home_loop: Optional[asyncio.AbstractEventLoop] = None
        self._dropped = 0
        self._handoffs = 0
        self._handoff_lock = threading.Lock()

        # Validate mode
        valid_modes = ["logging", "sync", "async"]
        if mode not in valid_modes:
            raise ValueError(f"Invalid mode '{mode}'. Must be one of: {valid_modes}")

    def publish(self, event: DomainEvent) -> None:
        """Publish event based on configured mode."""
        try:
            if self.mode == "logging":
                self._log_event(event)
                self._forward_to_bus(event)
            elif self.mode == "sync":
                self._call_handlers_sync(event)
            elif self.mode == "async":
                self._publish_to_queue(event)
        except Exception as e:
            self._logger.error("Failed to publish event %s: %s", event.event_type, e)
            # Don't re-raise - event publishing failure shouldn't break business
            # operations

    @property
    def dropped_events(self) -> int:
        """Number of events not delivered to the bus because the backlog was full."""
        return self._dropped

    def bind_loop(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Record the loop that owns the bus subscribers (the server loop)."""
        self._home_loop = loop or asyncio.get_running_loop()

    async def drain(self, timeout: float = 5.0) -> None:
        """Wait for outstanding bus deliveries so terminal events are not lost at shutdown.

        Includes events handed over from other threads that the loop has not yet
        turned into deliveries.
        """
        loop = asyncio.get_running_loop()
        deadline = time.monotonic() + timeout
        while True:
            pending = {t for t in self._tasks if not t.done() and t.get_loop() is loop}
            with self._handoff_lock:
                handoffs = self._handoffs
            if not pending and handoffs == 0:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._logger.warning(
                    "Event drain timed out with %d deliveries and %d handoffs outstanding",
                    len(pending),
                    handoffs,
                )
                return
            if pending:
                await asyncio.wait(pending, timeout=remaining)
            else:
                await asyncio.sleep(0.001)

    def _forward_to_bus(self, event: DomainEvent) -> None:
        """Forward ``event`` to the event bus without blocking or raising."""
        bus = self._event_bus
        if bus is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        home = self._home_loop
        home_live = home is not None and home.is_running() and not home.is_closed()

        if loop is not None and (not home_live or loop is home):
            # On the home loop (or no live home loop yet): this loop owns delivery.
            self._home_loop = loop
            self._schedule(loop, event)
            return

        if home_live:
            # A thread without a loop, or one running its own loop: the bus
            # subscribers belong to the home loop, so deliver there.
            self._hand_off(home, event)  # type: ignore[arg-type]
            return

        # No loop anywhere (CLI/script): nothing can be subscribed to a live
        # queue, so deliver to the bus handlers inline.
        asyncio.run(bus.publish(event))

    def _hand_off(self, home: asyncio.AbstractEventLoop, event: DomainEvent) -> None:
        """Schedule delivery on ``home`` from another thread; ``drain`` waits for it."""
        with self._handoff_lock:
            self._handoffs += 1
        try:
            home.call_soon_threadsafe(self._run_handoff, home, event)
        except RuntimeError:
            # The home loop closed between the check and the call.
            with self._handoff_lock:
                self._handoffs -= 1
            self._record_drop(event)

    def _run_handoff(self, home: asyncio.AbstractEventLoop, event: DomainEvent) -> None:
        try:
            self._schedule(home, event)
        finally:
            with self._handoff_lock:
                self._handoffs -= 1

    def _schedule(self, loop: asyncio.AbstractEventLoop, event: DomainEvent) -> None:
        """Queue a delivery on ``loop`` (must be called from the loop's thread)."""
        outstanding = sum(1 for t in self._tasks if not t.done())
        if outstanding >= self._max_pending:
            self._record_drop(event)
            return

        detached = _in_delivery.get()
        prev = None if detached else self._tail
        if prev is not None and (prev.done() or prev.get_loop() is not loop):
            prev = None

        task = loop.create_task(self._deliver(prev, event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if not detached:
            self._tail = task

    async def _deliver(self, prev: Optional[asyncio.Task[None]], event: DomainEvent) -> None:
        if prev is not None:
            await asyncio.wait({prev})
        token = _in_delivery.set(True)
        try:
            await self._event_bus.publish(event)  # type: ignore[union-attr]
        except Exception as e:
            self._logger.error("Event bus delivery failed for %s: %s", event.event_type, e)
        finally:
            _in_delivery.reset(token)

    def _record_drop(self, event: DomainEvent) -> None:
        self._dropped += 1
        record = getattr(self._event_bus, "record_dropped_event", None)
        if callable(record):
            record()
        if self._dropped == 1 or self._dropped % 100 == 0:
            self._logger.warning(
                "Event backlog full (%d); %d event(s) not delivered to handlers (latest: %s)",
                self._max_pending,
                self._dropped,
                event.event_type,
            )

    def register_handler(self, event_type: str, handler: Callable[[DomainEvent], None]) -> None:
        """Register event handler for specific event type."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)
        self._logger.debug("Registered handler for %s", event_type)

    def _log_event(self, event: DomainEvent) -> None:
        """Log event for audit trail (Script mode)."""
        self._logger.info(
            "Event: %s | Aggregate: %s:%s | Time: %s",
            event.event_type,
            event.aggregate_type,
            event.aggregate_id,
            event.occurred_at.isoformat(),
        )

    def _call_handlers_sync(self, event: DomainEvent) -> None:
        """Call handlers synchronously (REST API mode)."""
        handlers = self._handlers.get(event.event_type, [])

        if not handlers:
            self._logger.debug("No handlers registered for %s", event.event_type)
            return

        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                self._logger.error("Event handler failed for %s: %s", event.event_type, e)
                # Continue with other handlers

    def _publish_to_queue(self, event: DomainEvent) -> None:
        """Publish to message queue (EDA mode - future implementation)."""
        # Future implementation for message queue publishing
        self._logger.info("Would publish to queue: %s", event.event_type)

    def get_registered_handlers(self) -> dict[str, int]:
        """Get count of registered handlers by event type (for debugging)."""
        return {event_type: len(handlers) for event_type, handlers in self._handlers.items()}


# Factory function for DI container
def create_event_publisher(mode: str = "logging") -> ConfigurableEventPublisher:
    """Create event publisher with specified mode."""
    return ConfigurableEventPublisher(mode=mode)
