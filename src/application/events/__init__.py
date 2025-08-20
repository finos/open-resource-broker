"""
Event System - CQRS-aligned event handling architecture.

This module provides a complete event handling system that follows the same
architectural patterns as the CommandBus and QueryBus, ensuring consistency
across the CQRS implementation.

Key Components:
- EventBus: Central event dispatching (like CommandBus/QueryBus)
- @event_handler: Decorator for handler registration (like @command_handler)
- Base handlers: Template Method pattern for consistent behavior
- Auto-discovery: Automatic handler registration from decorators

Usage:
    # Define a handler
    @event_handler("MachineCreatedEvent")
    class MachineCreatedHandler(LoggingEventHandler):
        def format_message(self, event):
            return f"Machine created: {event.aggregate_id}"

    # Use the event bus
    event_bus = EventBus(logger)
    event_bus.auto_register_handlers()
    await event_bus.publish(machine_created_event)
"""

from .base import ActionEventHandler, EventHandler, LoggingEventHandler

# Import core components
from .bus import EventBus
from .decorators import EventHandlerRegistry, event_handler

# Import all handlers to ensure they're registered
from .handlers import (
    infrastructure_handlers,
    machine_handlers,
    request_handlers,
    system_handlers,
    template_handlers,
)

__all__: list[str] = [
    # Core components
    "EventBus",
    "event_handler",
    "EventHandlerRegistry",
    # Base classes
    "EventHandler",
    "LoggingEventHandler",
    "ActionEventHandler",
    # Handler modules (imported for registration)
    "machine_handlers",
    "request_handlers",
    "system_handlers",
    "infrastructure_handlers",
    "template_handlers",
]


def create_event_bus(logger=None):
    """
    Create and configure an EventBus with all handlers registered.

    This is a convenience function that creates an EventBus and automatically
    registers all handlers decorated with @event_handler.

    Args:
        logger: Logger instance to inject into handlers

    Returns:
        Configured EventBus instance
    """
    event_bus = EventBus(logger)
    event_bus.auto_register_handlers(logger)
    return event_bus


def get_registered_handlers():
    """
    Get all handlers registered with @event_handler decorator.

    Returns:
        Dictionary mapping event types to handler classes
    """
    return EventHandlerRegistry.get_handlers()


def get_event_statistics(event_bus: EventBus):
    """
    Get event processing statistics from an EventBus.

    Args:
        event_bus: The EventBus instance

    Returns:
        Dictionary with processing statistics
    """
    return event_bus.get_statistics()
