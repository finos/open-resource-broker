"""
Event Bus - CQRS-aligned event dispatching system.

This EventBus follows the same architectural patterns as CommandBus and QueryBus,
providing consistent event handling with dependency injection, error handling,
and middleware support.
"""

import asyncio
import time
from typing import Any, Dict, List, Optional, Type

# Import types - using string imports to avoid circular dependencies
try:
    from src.application.events.base.event_handler import EventHandler
    from src.application.events.decorators import EventHandlerRegistry
    from src.domain.base.events import DomainEvent
    from src.domain.base.ports import LoggingPort
except ImportError:
    # Fallback for testing or when dependencies aren't available
    DomainEvent = Any
    LoggingPort = Any
    EventHandler = Any
    EventHandlerRegistry = Any


class EventBus:
    """
    Event bus for dispatching events to handlers (CQRS-aligned).

    This follows the same pattern as CommandBus and QueryBus:
    - Dependency injection for handlers and logger
    - Consistent error handling and logging
    - Support for multiple handlers per event type
    - Async processing with proper error isolation
    - Metrics and monitoring integration points
    """

    def __init__(self, logger: Optional[LoggingPort] = None):
        """
        Initialize event bus.

        Args:
            logger: Logger for event processing messages
        """
        self.logger = logger
        self._handlers: Dict[str, List[EventHandler]] = {}
        self._handler_instances: Dict[Type[EventHandler], EventHandler] = {}

        # Statistics for monitoring
        self._events_processed = 0
        self._events_failed = 0
        self._processing_times: List[float] = []

    def register_handler(self, event_type: str, handler: EventHandler) -> None:
        """
        Register event handler for specific event type.

        Multiple handlers can be registered for the same event type.
        They will all be executed when an event of that type is published.

        Args:
            event_type: The event type to handle
            handler: The handler instance
        """
        if event_type not in self._handlers:
            self._handlers[event_type] = []

        self._handlers[event_type].append(handler)

        if self.logger:
            self.logger.debug(f"Registered handler {handler.__class__.__name__} for {event_type}")

    def register_handler_class(
        self,
        event_type: str,
        handler_class: Type[EventHandler],
        logger: Optional[LoggingPort] = None,
    ) -> None:
        """
        Register event handler class (will be instantiated).

        This is useful when you want to register handler classes and let
        the bus manage their instantiation and lifecycle.

        Args:
            event_type: The event type to handle
            handler_class: The handler class to instantiate
            logger: Logger to inject into handler
        """
        # Create or reuse handler instance
        if handler_class not in self._handler_instances:
            self._handler_instances[handler_class] = handler_class(logger or self.logger)

        handler_instance = self._handler_instances[handler_class]
        self.register_handler(event_type, handler_instance)

    def auto_register_handlers(self, logger: Optional[LoggingPort] = None) -> None:
        """
        Auto-register all handlers decorated with @event_handler.

        This discovers all handlers registered with the @event_handler decorator
        and automatically registers them with the bus.

        Args:
            logger: Logger to inject into handlers
        """
        if not EventHandlerRegistry:
            if self.logger:
                self.logger.warning("EventHandlerRegistry not available for auto-registration")
            return

        registered_handlers = EventHandlerRegistry.get_handlers()

        for event_type, handler_class in registered_handlers.items():
            self.register_handler_class(event_type, handler_class, logger or self.logger)

        if self.logger:
            self.logger.info(f"Auto-registered {len(registered_handlers)} event handlers")

    async def publish(self, event: DomainEvent) -> None:
        """
        Publish event to all registered handlers.

        All handlers for the event type will be executed concurrently.
        If any handler fails, it won't affect other handlers, but the
        error will be logged and tracked.

        Args:
            event: The domain event to publish
        """
        start_time = time.time()
        event_type = getattr(event, "event_type", event.__class__.__name__)
        event_id = getattr(event, "event_id", "unknown")

        handlers = self._handlers.get(event_type, [])

        if not handlers:
            if self.logger:
                self.logger.debug(f"No handlers registered for event: {event_type}")
            return

        if self.logger:
            self.logger.debug(
                f"Publishing event {event_type} (ID: {event_id}) to { len(handlers)} handlers"
            )

        # Execute all handlers concurrently
        tasks = []
        for handler in handlers:
            task = asyncio.create_task(self._handle_with_error_isolation(handler, event))
            tasks.append(task)

        # Wait for all handlers to complete
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results and update statistics
        success_count = 0
        error_count = 0

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                error_count += 1
                if self.logger:
                    handler_name = handlers[i].__class__.__name__
                    self.logger.error(
                        f"Handler {handler_name} failed for event {event_type}: { str(result)}"
                    )
            else:
                success_count += 1

        # Update statistics
        duration = time.time() - start_time
        self._events_processed += 1
        self._processing_times.append(duration)

        if error_count > 0:
            self._events_failed += 1

        if self.logger:
            self.logger.debug(
                f"Event {event_type} processed: {success_count} succeeded, "
                f"{error_count} failed in {duration:.3f}s"
            )

    async def _handle_with_error_isolation(self, handler: EventHandler, event: DomainEvent) -> None:
        """
        Handle event with error isolation.

        This ensures that if one handler fails, it doesn't affect other handlers.

        Args:
            handler: The event handler
            event: The domain event
        """
        try:
            await handler.handle(event)
        except Exception as e:
            # Error is logged by the handler itself, we just need to isolate it
            # The exception will be caught by asyncio.gather() above
            raise e

    def get_handlers_for_event(self, event_type: str) -> List[EventHandler]:
        """
        Get all handlers registered for a specific event type.

        Args:
            event_type: The event type

        Returns:
            List of handlers for the event type
        """
        return self._handlers.get(event_type, []).copy()

    def get_registered_event_types(self) -> List[str]:
        """
        Get all event types that have registered handlers.

        Returns:
            List of event types with handlers
        """
        return list(self._handlers.keys())

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get event processing statistics.

        Returns:
            Dictionary with processing statistics
        """
        avg_processing_time = 0.0
        if self._processing_times:
            avg_processing_time = sum(self._processing_times) / len(self._processing_times)

        return {
            "events_processed": self._events_processed,
            "events_failed": self._events_failed,
            "success_rate": (
                (self._events_processed - self._events_failed) / max(self._events_processed, 1)
            )
            * 100,
            "average_processing_time": avg_processing_time,
            "registered_event_types": len(self._handlers),
            "total_handlers": sum(len(handlers) for handlers in self._handlers.values()),
        }

    def clear_handlers(self) -> None:
        """
        Clear all registered handlers.

        This is primarily useful for testing.
        """
        self._handlers.clear()
        self._handler_instances.clear()

        if self.logger:
            self.logger.debug("Cleared all event handlers")

    def clear_statistics(self) -> None:
        """
        Clear processing statistics.

        This is useful for resetting metrics in long-running applications.
        """
        self._events_processed = 0
        self._events_failed = 0
        self._processing_times.clear()

        if self.logger:
            self.logger.debug("Cleared event processing statistics")
