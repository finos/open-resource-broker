"""Infrastructure events package - CQRS-aligned event system."""

from infrastructure.events.publisher import (
    ConfigurableEventPublisher,
    create_event_publisher,
)

# Import new EventBus system
try:
    from application.events import EventBus, create_event_bus

    _NEW_EVENT_SYSTEM_AVAILABLE = True
except ImportError:
    _NEW_EVENT_SYSTEM_AVAILABLE = False
    EventBus = None
    create_event_bus = None


def get_event_publisher() -> ConfigurableEventPublisher:
    """Get event publisher instance from DI container (legacy)."""
    from infrastructure.di.container import get_container

    container = get_container()
    return container.get(ConfigurableEventPublisher)


def get_event_bus():
    """
    Get EventBus instance from DI container (new CQRS-aligned system).

    This is the preferred way to get event handling in the new architecture.
    Falls back to legacy publisher if new system isn't available.
    """
    if not _NEW_EVENT_SYSTEM_AVAILABLE:
        # Fallback to legacy system
        return get_event_publisher()

    try:
        from infrastructure.di.container import get_container

        container = get_container()

        # Try to get EventBus from container
        event_bus = container.get_optional(EventBus)
        if event_bus is not None:
            return event_bus

        # Create EventBus if not in container
        from infrastructure.logging.logger import get_logger

        logger = get_logger(__name__)
        return create_event_bus(logger)
    except Exception:
        # Final fallback to legacy system
        return get_event_publisher()


__all__: list[str] = [
    "ConfigurableEventPublisher",
    "EventBus",
    "create_event_bus",
    "create_event_publisher",
    "get_event_bus",
    "get_event_publisher",
]
