"""Configurable Event Publisher - Simple, mode-based event publishing."""

from typing import Callable, Dict, List

from domain.base.events import DomainEvent, EventPublisher
from infrastructure.logging.logger import get_logger


class ConfigurableEventPublisher(EventPublisher):
    """
    Simple, configurable event publisher supporting all deployment modes.

    Modes:
    - "logging": Just log events for audit trail (Script mode)
    - "sync": Call registered handlers synchronously (REST API mode)
    - "async": Publish to message queues (EDA mode - future)
    """

    def __init__(self, mode: str = "logging") -> None:
        """Initialize with publishing mode."""
        self.mode = mode
        self._handlers: Dict[str, List[Callable]] = {}
        self._logger = get_logger(__name__)

        # Validate mode
        valid_modes = ["logging", "sync", "async"]
        if mode not in valid_modes:
            raise ValueError(f"Invalid mode '{mode}'. Must be one of: {valid_modes}")

    def publish(self, event: DomainEvent) -> None:
        """Publish event based on configured mode."""
        try:
            if self.mode == "logging":
                self._log_event(event)
            elif self.mode == "sync":
                self._call_handlers_sync(event)
            elif self.mode == "async":
                self._publish_to_queue(event)
        except Exception as e:
            self._logger.error("Failed to publish event %s: %s", event.event_type, e)
            # Don't re-raise - event publishing failure shouldn't break business
            # operations

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

    def get_registered_handlers(self) -> Dict[str, int]:
        """Get count of registered handlers by event type (for debugging)."""
        return {event_type: len(handlers) for event_type, handlers in self._handlers.items()}


# Factory function for DI container
def create_event_publisher(mode: str = "logging") -> ConfigurableEventPublisher:
    """Create event publisher with specified mode."""
    return ConfigurableEventPublisher(mode=mode)
